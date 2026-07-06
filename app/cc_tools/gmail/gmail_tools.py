"""
Gmail Tools for Claude Code SDK
Claude can manage emails in Gmail
"""

import json
import os
import base64
from typing import Any, Dict
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from claude_agent_sdk import create_sdk_mcp_server, tool
from googleapiclient.errors import HttpError

from app.cc_tools.gmail.auth_helper import get_gmail_service


def _get_error_message(e: HttpError) -> str:
    """HttpError에서 사용자 친화적 에러 메시지 추출"""
    if e.resp.status == 403:
        return "Gmail 접근 권한이 없습니다. Domain-Wide Delegation 설정을 확인해주세요."
    elif e.resp.status == 404:
        return "메일을 찾을 수 없습니다."
    elif e.resp.status == 400:
        return "잘못된 요청입니다. 파라미터를 확인해주세요."
    elif e.resp.status == 401:
        return "인증이 만료되었습니다. 서비스 계정 설정을 확인해주세요."
    else:
        return f"Gmail API 오류 (HTTP {e.resp.status})"


def _decode_message_body(payload: dict) -> str:
    """메일 본문을 디코딩"""
    body = ""

    if 'body' in payload and payload['body'].get('data'):
        body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8')
    elif 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain' and part['body'].get('data'):
                body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
                break
            elif part['mimeType'] == 'text/html' and part['body'].get('data') and not body:
                body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8')
            elif 'parts' in part:
                body = _decode_message_body(part)
                if body:
                    break

    return body


def _get_header_value(headers: list, name: str) -> str:
    """헤더에서 특정 값 추출"""
    for header in headers:
        if header['name'].lower() == name.lower():
            return header['value']
    return ""


def _create_message(to: str, subject: str, body: str, cc: str = "", bcc: str = "",
                    thread_id: str = None, references: str = None, in_reply_to: str = None) -> dict:
    """이메일 메시지 생성"""
    message = MIMEMultipart('alternative')
    message['to'] = to
    message['subject'] = subject

    if cc:
        message['cc'] = cc
    if bcc:
        message['bcc'] = bcc
    if in_reply_to:
        message['In-Reply-To'] = in_reply_to
    if references:
        message['References'] = references

    # HTML 지원
    if '<html' in body.lower() or '<p>' in body.lower() or '<br' in body.lower():
        msg_part = MIMEText(body, 'html', 'utf-8')
    else:
        msg_part = MIMEText(body, 'plain', 'utf-8')

    message.attach(msg_part)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    result = {'raw': raw}
    if thread_id:
        result['threadId'] = thread_id

    return result


@tool(
    "gmail_list_messages",
    "Gmail 메일 목록을 조회합니다. 받은편지함, 보낸편지함, 또는 특정 라벨의 메일을 가져옵니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 Gmail에 접근합니다.",
            },
            "label": {
                "type": "string",
                "description": "라벨 (INBOX, SENT, DRAFT, SPAM, TRASH 또는 커스텀 라벨)",
            },
            "query": {
                "type": "string",
                "description": "검색 쿼리 (예: 'from:someone@example.com', 'is:unread', 'after:2024/01/01')",
            },
            "max_results": {
                "type": "integer",
                "description": "가져올 메일 수 (기본값 20, 최대 100)",
            },
        },
        "required": [],
    },
)
async def gmail_list_messages(args: Dict[str, Any]) -> Dict[str, Any]:
    """메일 목록 조회"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_gmail_service(slack_user_id=slack_user_id)

        label = args.get("label", "INBOX")
        query = args.get("query", "")
        max_results = min(args.get("max_results", 20), 100)

        # 메일 목록 조회
        results = service.users().messages().list(
            userId='me',
            labelIds=[label] if label else None,
            q=query if query else None,
            maxResults=max_results
        ).execute()

        messages = results.get('messages', [])
        detailed_messages = []

        # 각 메일의 기본 정보 조회
        for msg in messages[:max_results]:
            msg_detail = service.users().messages().get(
                userId='me',
                id=msg['id'],
                format='metadata',
                metadataHeaders=['From', 'To', 'Subject', 'Date']
            ).execute()

            headers = msg_detail.get('payload', {}).get('headers', [])
            detailed_messages.append({
                'id': msg['id'],
                'threadId': msg.get('threadId'),
                'snippet': msg_detail.get('snippet', ''),
                'from': _get_header_value(headers, 'From'),
                'to': _get_header_value(headers, 'To'),
                'subject': _get_header_value(headers, 'Subject'),
                'date': _get_header_value(headers, 'Date'),
                'labelIds': msg_detail.get('labelIds', [])
            })

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "count": len(detailed_messages),
                    "messages": detailed_messages
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": _get_error_message(e)
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"메일 목록 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "gmail_get_message",
    "Gmail 메일 상세 내용을 조회합니다. 본문과 첨부파일 정보를 포함합니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 Gmail에 접근합니다.",
            },
            "message_id": {
                "type": "string",
                "description": "메일 ID",
            },
        },
        "required": ["message_id"],
    },
)
async def gmail_get_message(args: Dict[str, Any]) -> Dict[str, Any]:
    """메일 상세 조회"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_gmail_service(slack_user_id=slack_user_id)
        message_id = args["message_id"]

        # 메일 상세 조회
        msg = service.users().messages().get(
            userId='me',
            id=message_id,
            format='full'
        ).execute()

        headers = msg.get('payload', {}).get('headers', [])
        body = _decode_message_body(msg.get('payload', {}))

        # 첨부파일 정보 추출
        attachments = []
        payload = msg.get('payload', {})
        if 'parts' in payload:
            for part in payload['parts']:
                if part.get('filename'):
                    attachments.append({
                        'filename': part['filename'],
                        'mimeType': part.get('mimeType'),
                        'size': part.get('body', {}).get('size', 0),
                        'attachmentId': part.get('body', {}).get('attachmentId')
                    })

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "message": {
                        'id': msg['id'],
                        'threadId': msg.get('threadId'),
                        'from': _get_header_value(headers, 'From'),
                        'to': _get_header_value(headers, 'To'),
                        'cc': _get_header_value(headers, 'Cc'),
                        'subject': _get_header_value(headers, 'Subject'),
                        'date': _get_header_value(headers, 'Date'),
                        'body': body,
                        'labelIds': msg.get('labelIds', []),
                        'attachments': attachments
                    }
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": _get_error_message(e)
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"메일 상세 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "gmail_send_message",
    "새 이메일을 작성하여 보냅니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 Gmail에 접근합니다.",
            },
            "to": {
                "type": "string",
                "description": "받는 사람 이메일 주소 (여러 명일 경우 쉼표로 구분)",
            },
            "subject": {
                "type": "string",
                "description": "메일 제목",
            },
            "body": {
                "type": "string",
                "description": "메일 본문 (HTML 지원)",
            },
            "cc": {
                "type": "string",
                "description": "참조 (선택)",
            },
            "bcc": {
                "type": "string",
                "description": "숨은 참조 (선택)",
            },
        },
        "required": ["to", "subject", "body"],
    },
)
async def gmail_send_message(args: Dict[str, Any]) -> Dict[str, Any]:
    """메일 보내기"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_gmail_service(slack_user_id=slack_user_id)

        to = args["to"]
        subject = args["subject"]
        body = args["body"]
        cc = args.get("cc", "")
        bcc = args.get("bcc", "")

        message = _create_message(to, subject, body, cc, bcc)

        # 메일 전송
        sent_message = service.users().messages().send(
            userId='me',
            body=message
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "message_id": sent_message['id'],
                    "thread_id": sent_message.get('threadId'),
                    "message": f"메일 전송 완료: {to}"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": _get_error_message(e)
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"메일 전송 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "gmail_reply_message",
    "기존 메일에 답장합니다. 원본 메일의 스레드에 답장이 추가됩니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 Gmail에 접근합니다.",
            },
            "message_id": {
                "type": "string",
                "description": "답장할 원본 메일 ID",
            },
            "body": {
                "type": "string",
                "description": "답장 본문 (HTML 지원)",
            },
            "reply_all": {
                "type": "boolean",
                "description": "전체 답장 여부 (기본값: false)",
            },
        },
        "required": ["message_id", "body"],
    },
)
async def gmail_reply_message(args: Dict[str, Any]) -> Dict[str, Any]:
    """메일 답장"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_gmail_service(slack_user_id=slack_user_id)

        message_id = args["message_id"]
        body = args["body"]
        reply_all = args.get("reply_all", False)

        # 원본 메일 조회
        original = service.users().messages().get(
            userId='me',
            id=message_id,
            format='metadata',
            metadataHeaders=['From', 'To', 'Cc', 'Subject', 'Message-ID']
        ).execute()

        headers = original.get('payload', {}).get('headers', [])
        original_from = _get_header_value(headers, 'From')
        original_to = _get_header_value(headers, 'To')
        original_cc = _get_header_value(headers, 'Cc')
        original_subject = _get_header_value(headers, 'Subject')
        original_message_id = _get_header_value(headers, 'Message-ID')
        thread_id = original.get('threadId')

        # 답장 수신자 설정
        to = original_from
        cc = ""
        if reply_all:
            # 전체 답장: 원본 수신자들도 CC에 추가
            all_recipients = [original_to, original_cc]
            cc = ", ".join([r for r in all_recipients if r])

        # 제목에 Re: 추가
        subject = original_subject
        if not subject.lower().startswith('re:'):
            subject = f"Re: {subject}"

        message = _create_message(
            to, subject, body, cc,
            thread_id=thread_id,
            in_reply_to=original_message_id,
            references=original_message_id
        )

        # 답장 전송
        sent_message = service.users().messages().send(
            userId='me',
            body=message
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "message_id": sent_message['id'],
                    "thread_id": sent_message.get('threadId'),
                    "message": f"답장 전송 완료: {to}"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": _get_error_message(e)
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"답장 전송 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "gmail_forward_message",
    "메일을 다른 사람에게 전달합니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 Gmail에 접근합니다.",
            },
            "message_id": {
                "type": "string",
                "description": "전달할 메일 ID",
            },
            "to": {
                "type": "string",
                "description": "전달받을 사람 이메일 주소",
            },
            "additional_message": {
                "type": "string",
                "description": "추가 메시지 (선택)",
            },
        },
        "required": ["message_id", "to"],
    },
)
async def gmail_forward_message(args: Dict[str, Any]) -> Dict[str, Any]:
    """메일 전달"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_gmail_service(slack_user_id=slack_user_id)

        message_id = args["message_id"]
        to = args["to"]
        additional_message = args.get("additional_message", "")

        # 원본 메일 조회
        original = service.users().messages().get(
            userId='me',
            id=message_id,
            format='full'
        ).execute()

        headers = original.get('payload', {}).get('headers', [])
        original_from = _get_header_value(headers, 'From')
        original_to = _get_header_value(headers, 'To')
        original_subject = _get_header_value(headers, 'Subject')
        original_date = _get_header_value(headers, 'Date')
        original_body = _decode_message_body(original.get('payload', {}))

        # 전달 제목
        subject = original_subject
        if not subject.lower().startswith('fwd:'):
            subject = f"Fwd: {subject}"

        # 전달 본문 구성
        forward_header = f"\n\n---------- Forwarded message ---------\n"
        forward_header += f"From: {original_from}\n"
        forward_header += f"Date: {original_date}\n"
        forward_header += f"Subject: {original_subject}\n"
        forward_header += f"To: {original_to}\n\n"

        body = additional_message + forward_header + original_body

        message = _create_message(to, subject, body)

        # 전달 전송
        sent_message = service.users().messages().send(
            userId='me',
            body=message
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "message_id": sent_message['id'],
                    "thread_id": sent_message.get('threadId'),
                    "message": f"메일 전달 완료: {to}"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": _get_error_message(e)
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"메일 전달 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "gmail_search_messages",
    "Gmail에서 메일을 검색합니다. 다양한 검색 조건을 지원합니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 Gmail에 접근합니다.",
            },
            "query": {
                "type": "string",
                "description": "검색 쿼리 (예: 'from:someone@example.com subject:meeting after:2024/01/01')",
            },
            "max_results": {
                "type": "integer",
                "description": "가져올 메일 수 (기본값 20, 최대 100)",
            },
        },
        "required": ["query"],
    },
)
async def gmail_search_messages(args: Dict[str, Any]) -> Dict[str, Any]:
    """메일 검색"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_gmail_service(slack_user_id=slack_user_id)

        query = args["query"]
        max_results = min(args.get("max_results", 20), 100)

        # 검색 실행
        results = service.users().messages().list(
            userId='me',
            q=query,
            maxResults=max_results
        ).execute()

        messages = results.get('messages', [])
        detailed_messages = []

        for msg in messages[:max_results]:
            msg_detail = service.users().messages().get(
                userId='me',
                id=msg['id'],
                format='metadata',
                metadataHeaders=['From', 'To', 'Subject', 'Date']
            ).execute()

            headers = msg_detail.get('payload', {}).get('headers', [])
            detailed_messages.append({
                'id': msg['id'],
                'threadId': msg.get('threadId'),
                'snippet': msg_detail.get('snippet', ''),
                'from': _get_header_value(headers, 'From'),
                'to': _get_header_value(headers, 'To'),
                'subject': _get_header_value(headers, 'Subject'),
                'date': _get_header_value(headers, 'Date'),
                'labelIds': msg_detail.get('labelIds', [])
            })

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "query": query,
                    "count": len(detailed_messages),
                    "messages": detailed_messages
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": _get_error_message(e)
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"메일 검색 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "gmail_get_labels",
    "Gmail 라벨 목록을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 Gmail에 접근합니다.",
            },
        },
        "required": [],
    },
)
async def gmail_get_labels(args: Dict[str, Any]) -> Dict[str, Any]:
    """라벨 목록 조회"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_gmail_service(slack_user_id=slack_user_id)

        results = service.users().labels().list(userId='me').execute()
        labels = results.get('labels', [])

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "count": len(labels),
                    "labels": labels
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": _get_error_message(e)
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"라벨 목록 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "gmail_add_label",
    "메일에 라벨을 추가합니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 Gmail에 접근합니다.",
            },
            "message_id": {
                "type": "string",
                "description": "메일 ID",
            },
            "label_id": {
                "type": "string",
                "description": "추가할 라벨 ID (예: 'STARRED', 'IMPORTANT' 또는 커스텀 라벨 ID)",
            },
        },
        "required": ["message_id", "label_id"],
    },
)
async def gmail_add_label(args: Dict[str, Any]) -> Dict[str, Any]:
    """메일에 라벨 추가"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_gmail_service(slack_user_id=slack_user_id)

        message_id = args["message_id"]
        label_id = args["label_id"]

        service.users().messages().modify(
            userId='me',
            id=message_id,
            body={'addLabelIds': [label_id]}
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "message_id": message_id,
                    "label_id": label_id,
                    "message": f"라벨 추가 완료: {label_id}"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": _get_error_message(e)
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"라벨 추가 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "gmail_remove_label",
    "메일에서 라벨을 제거합니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 Gmail에 접근합니다.",
            },
            "message_id": {
                "type": "string",
                "description": "메일 ID",
            },
            "label_id": {
                "type": "string",
                "description": "제거할 라벨 ID",
            },
        },
        "required": ["message_id", "label_id"],
    },
)
async def gmail_remove_label(args: Dict[str, Any]) -> Dict[str, Any]:
    """메일에서 라벨 제거"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_gmail_service(slack_user_id=slack_user_id)

        message_id = args["message_id"]
        label_id = args["label_id"]

        service.users().messages().modify(
            userId='me',
            id=message_id,
            body={'removeLabelIds': [label_id]}
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "message_id": message_id,
                    "label_id": label_id,
                    "message": f"라벨 제거 완료: {label_id}"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": _get_error_message(e)
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"라벨 제거 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "gmail_mark_as_read",
    "메일을 읽음으로 표시합니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 Gmail에 접근합니다.",
            },
            "message_id": {
                "type": "string",
                "description": "메일 ID",
            },
        },
        "required": ["message_id"],
    },
)
async def gmail_mark_as_read(args: Dict[str, Any]) -> Dict[str, Any]:
    """읽음 처리"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_gmail_service(slack_user_id=slack_user_id)

        message_id = args["message_id"]

        service.users().messages().modify(
            userId='me',
            id=message_id,
            body={'removeLabelIds': ['UNREAD']}
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "message_id": message_id,
                    "message": "메일을 읽음으로 표시했습니다"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": _get_error_message(e)
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"읽음 처리 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "gmail_mark_as_unread",
    "메일을 안읽음으로 표시합니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 Gmail에 접근합니다.",
            },
            "message_id": {
                "type": "string",
                "description": "메일 ID",
            },
        },
        "required": ["message_id"],
    },
)
async def gmail_mark_as_unread(args: Dict[str, Any]) -> Dict[str, Any]:
    """안읽음 처리"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_gmail_service(slack_user_id=slack_user_id)

        message_id = args["message_id"]

        service.users().messages().modify(
            userId='me',
            id=message_id,
            body={'addLabelIds': ['UNREAD']}
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "message_id": message_id,
                    "message": "메일을 안읽음으로 표시했습니다"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": _get_error_message(e)
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"안읽음 처리 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "gmail_trash_message",
    "메일을 휴지통으로 이동합니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 Gmail에 접근합니다.",
            },
            "message_id": {
                "type": "string",
                "description": "메일 ID",
            },
        },
        "required": ["message_id"],
    },
)
async def gmail_trash_message(args: Dict[str, Any]) -> Dict[str, Any]:
    """휴지통으로 이동"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_gmail_service(slack_user_id=slack_user_id)

        message_id = args["message_id"]

        service.users().messages().trash(
            userId='me',
            id=message_id
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "message_id": message_id,
                    "message": "메일을 휴지통으로 이동했습니다"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": _get_error_message(e)
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"휴지통 이동 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "gmail_get_attachment",
    "메일의 첨부파일을 다운로드합니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 Gmail에 접근합니다.",
            },
            "message_id": {
                "type": "string",
                "description": "메일 ID",
            },
            "attachment_id": {
                "type": "string",
                "description": "첨부파일 ID (gmail_get_message에서 확인 가능)",
            },
            "output_path": {
                "type": "string",
                "description": "저장할 파일 경로 (전체 경로)",
            },
        },
        "required": ["message_id", "attachment_id", "output_path"],
    },
)
async def gmail_get_attachment(args: Dict[str, Any]) -> Dict[str, Any]:
    """첨부파일 다운로드"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_gmail_service(slack_user_id=slack_user_id)

        message_id = args["message_id"]
        attachment_id = args["attachment_id"]
        output_path = args["output_path"]

        # 첨부파일 가져오기
        attachment = service.users().messages().attachments().get(
            userId='me',
            messageId=message_id,
            id=attachment_id
        ).execute()

        file_data = base64.urlsafe_b64decode(attachment['data'])

        # 디렉토리 생성
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # 파일 저장
        with open(output_path, 'wb') as f:
            f.write(file_data)

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "output_path": output_path,
                    "size_bytes": len(file_data),
                    "message": f"첨부파일 다운로드 완료: {output_path}"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": _get_error_message(e)
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"첨부파일 다운로드 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


# MCP Server
gmail_tools = [
    gmail_list_messages,
    gmail_get_message,
    gmail_send_message,
    gmail_reply_message,
    gmail_forward_message,
    gmail_search_messages,
    gmail_get_labels,
    gmail_add_label,
    gmail_remove_label,
    gmail_mark_as_read,
    gmail_mark_as_unread,
    gmail_trash_message,
    gmail_get_attachment,
]


def create_gmail_mcp_server():
    """Claude Code SDK Gmail MCP server"""
    return create_sdk_mcp_server(
        name="gmail",
        version="1.0.0",
        tools=gmail_tools
    )
