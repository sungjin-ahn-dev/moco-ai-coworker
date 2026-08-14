"""
Google Docs Tools for Claude Code SDK
Claude can read and write Google Docs documents
"""

import json
from typing import Any, Dict, List

from claude_agent_sdk import create_sdk_mcp_server, tool
from googleapiclient.errors import HttpError

from app.cc_tools.google_drive.auth_helper import get_docs_service, get_drive_service


def extract_text_from_body(body: Dict[str, Any]) -> str:
    """Extract plain text from a body/tab content structure"""
    text_parts = []
    content = body.get('content', [])
    for element in content:
        if 'paragraph' in element:
            paragraph = element['paragraph']
            for elem in paragraph.get('elements', []):
                text_run = elem.get('textRun')
                if text_run:
                    text_parts.append(text_run.get('content', ''))
        elif 'table' in element:
            table = element['table']
            for row in table.get('tableRows', []):
                for cell in row.get('tableCells', []):
                    for cell_content in cell.get('content', []):
                        if 'paragraph' in cell_content:
                            for elem in cell_content['paragraph'].get('elements', []):
                                text_run = elem.get('textRun')
                                if text_run:
                                    text_parts.append(text_run.get('content', ''))
    return ''.join(text_parts)


def extract_text_from_document(document: Dict[str, Any]) -> str:
    """
    Extract plain text from Google Docs document structure

    Args:
        document: Document object from Docs API

    Returns:
        Plain text content
    """
    text_parts = []

    content = document.get('body', {}).get('content', [])

    for element in content:
        if 'paragraph' in element:
            paragraph = element['paragraph']
            for elem in paragraph.get('elements', []):
                text_run = elem.get('textRun')
                if text_run:
                    text_parts.append(text_run.get('content', ''))
        elif 'table' in element:
            # Handle tables
            table = element['table']
            for row in table.get('tableRows', []):
                for cell in row.get('tableCells', []):
                    for cell_content in cell.get('content', []):
                        if 'paragraph' in cell_content:
                            for elem in cell_content['paragraph'].get('elements', []):
                                text_run = elem.get('textRun')
                                if text_run:
                                    text_parts.append(text_run.get('content', ''))

    return ''.join(text_parts)


@tool(
    "google_docs_read_document",
    "Read the full content of a Google Docs document. tab_id를 지정하면 특정 탭의 내용만 읽습니다. 탭 목록은 google_docs_list_tabs로 먼저 확인하세요.",
    {
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "Google Docs document ID (from the URL)",
            },
            "tab_id": {
                "type": "string",
                "description": "특정 탭 ID (선택). google_docs_list_tabs로 확인 가능. 미지정 시 전체 문서.",
            },
        },
        "required": ["document_id"],
    },
)
async def google_docs_read_document(args: Dict[str, Any]) -> Dict[str, Any]:
    """Read content from a Google Docs document (탭 지원)"""
    try:
        docs_service = get_docs_service()
        document_id = args["document_id"]
        tab_id = args.get("tab_id")

        # includeTabsContent=True로 탭 포함 조회
        document = docs_service.documents().get(
            documentId=document_id,
            includeTabsContent=True
        ).execute()

        title = document.get('title', 'Untitled')

        # 탭이 있는 경우
        tabs = document.get('tabs', [])
        if tab_id and tabs:
            # 특정 탭 찾기
            target_tab = None
            for tab in tabs:
                if tab.get('tabProperties', {}).get('tabId') == tab_id:
                    target_tab = tab
                    break
                # 하위 탭 검색
                for child in tab.get('childTabs', []):
                    if child.get('tabProperties', {}).get('tabId') == tab_id:
                        target_tab = child
                        break

            if target_tab:
                tab_title = target_tab.get('tabProperties', {}).get('title', 'Untitled Tab')
                body = target_tab.get('documentTab', {}).get('body', {})
                text_content = extract_text_from_body(body)
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": True,
                            "document_id": document_id,
                            "title": f"{title} > {tab_title}",
                            "tab_id": tab_id,
                            "content": text_content,
                            "message": f"탭 읽기 완료: {tab_title}"
                        }, ensure_ascii=False, indent=2)
                    }]
                }
            else:
                return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": True, "message": f"탭 ID '{tab_id}'를 찾을 수 없습니다. google_docs_list_tabs로 탭 목록을 확인하세요."}, ensure_ascii=False)}], "error": True}

        # 탭 미지정: 기본 본문 읽기
        text_content = extract_text_from_document(document)

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "document_id": document_id,
                    "title": title,
                    "content": text_content,
                    "tabs_available": len(tabs),
                    "message": f"문서 읽기 완료: {title}" + (f" ({len(tabs)}개 탭 있음 — google_docs_list_tabs로 확인)" if tabs else "")
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        if e.resp.status == 404:
            error_message = "문서를 찾을 수 없습니다."
        elif e.resp.status == 403:
            error_message = "문서 접근 권한이 없습니다."
        else:
            error_message = f"Docs API 오류 (HTTP {e.resp.status}): {e.error_details}"

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": error_message
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
                    "message": f"문서 읽기 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "google_docs_create_with_content",
    "Create a new Google Docs document with initial content.",
    {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Title of the document",
            },
            "content": {
                "type": "string",
                "description": "Initial content to write in the document",
            },
            "parent_folder_id": {
                "type": "string",
                "description": "Parent folder ID in Google Drive (optional)",
            },
        },
        "required": ["title", "content"],
    },
)
async def google_docs_create_with_content(args: Dict[str, Any]) -> Dict[str, Any]:
    """Create a Google Docs document with content"""
    try:
        drive_service = get_drive_service()
        docs_service = get_docs_service()

        title = args["title"]
        content = args["content"]
        parent_folder_id = args.get("parent_folder_id")

        # Step 1: Create empty document via Drive API
        file_metadata = {
            "name": title,
            "mimeType": "application/vnd.google-apps.document"
        }
        if parent_folder_id:
            file_metadata["parents"] = [parent_folder_id]

        document = drive_service.files().create(
            body=file_metadata,
            fields="id, name, webViewLink",
            supportsAllDrives=True
        ).execute()

        document_id = document.get('id')

        # Step 2: Insert content via Docs API
        requests = [
            {
                'insertText': {
                    'location': {
                        'index': 1,
                    },
                    'text': content
                }
            }
        ]

        docs_service.documents().batchUpdate(
            documentId=document_id,
            body={'requests': requests}
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "document_id": document_id,
                    "title": document.get('name'),
                    "web_view_link": document.get('webViewLink'),
                    "message": f"문서 생성 및 내용 작성 완료: {document.get('name')}"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        error_message = f"Docs/Drive API 오류 (HTTP {e.resp.status}): {e.error_details}"
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": error_message
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
                    "message": f"문서 생성 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "google_docs_append_text",
    "Append text to the end of an existing Google Docs document.",
    {
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "Google Docs document ID",
            },
            "text": {
                "type": "string",
                "description": "Text to append to the document",
            },
        },
        "required": ["document_id", "text"],
    },
)
async def google_docs_append_text(args: Dict[str, Any]) -> Dict[str, Any]:
    """Append text to a Google Docs document"""
    try:
        docs_service = get_docs_service()
        document_id = args["document_id"]
        text = args["text"]

        # Get current document to find end index
        document = docs_service.documents().get(documentId=document_id).execute()
        end_index = document.get('body', {}).get('content', [{}])[-1].get('endIndex', 1)

        # Append text
        requests = [
            {
                'insertText': {
                    'location': {
                        'index': end_index - 1,
                    },
                    'text': text
                }
            }
        ]

        docs_service.documents().batchUpdate(
            documentId=document_id,
            body={'requests': requests}
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "document_id": document_id,
                    "message": "텍스트 추가 완료"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except HttpError as e:
        if e.resp.status == 403:
            error_message = "문서 편집 권한이 없습니다."
        else:
            error_message = f"Docs API 오류 (HTTP {e.resp.status}): {e.error_details}"

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": error_message
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
                    "message": f"텍스트 추가 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }



@tool(
    "google_docs_list_tabs",
    "Google Docs 문서의 탭 목록을 조회합니다. 탭이 여러 개인 문서에서 특정 탭을 읽으려면 먼저 이 도구로 tab_id를 확인하세요.",
    {
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "Google Docs document ID",
            },
        },
        "required": ["document_id"],
    },
)
async def google_docs_list_tabs(args: Dict[str, Any]) -> Dict[str, Any]:
    """List all tabs in a Google Docs document"""
    try:
        docs_service = get_docs_service()
        document_id = args["document_id"]

        document = docs_service.documents().get(
            documentId=document_id,
            includeTabsContent=True
        ).execute()

        title = document.get('title', 'Untitled')
        tabs = document.get('tabs', [])

        tab_list = []
        for tab in tabs:
            props = tab.get('tabProperties', {})
            tab_info = {
                "tab_id": props.get('tabId', ''),
                "title": props.get('title', 'Untitled'),
                "index": props.get('index', 0),
            }
            # 하위 탭
            children = []
            for child in tab.get('childTabs', []):
                child_props = child.get('tabProperties', {})
                children.append({
                    "tab_id": child_props.get('tabId', ''),
                    "title": child_props.get('title', 'Untitled'),
                    "index": child_props.get('index', 0),
                })
            if children:
                tab_info["children"] = children
            tab_list.append(tab_info)

        result = f"문서: {title}\n탭 {len(tab_list)}개:\n\n"
        for t in tab_list:
            result += f"• {t['title']} (tab_id: {t['tab_id']})\n"
            for c in t.get('children', []):
                result += f"  └─ {c['title']} (tab_id: {c['tab_id']})\n"

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "document_id": document_id,
                    "title": title,
                    "tabs": tab_list,
                    "tab_count": len(tab_list),
                    "message": result,
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        return {
            "content": [{"type": "text", "text": json.dumps({"success": False, "error": True, "message": f"탭 목록 조회 실패: {str(e)}"}, ensure_ascii=False)}],
            "error": True
        }


@tool(
    "google_docs_write_to_tab",
    "Google Docs 문서의 특정 탭에 텍스트를 추가합니다. tab_id는 google_docs_list_tabs로 확인하세요.",
    {
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "Google Docs document ID",
            },
            "tab_id": {
                "type": "string",
                "description": "탭 ID (google_docs_list_tabs로 확인)",
            },
            "text": {
                "type": "string",
                "description": "추가할 텍스트 내용",
            },
        },
        "required": ["document_id", "tab_id", "text"],
    },
)
async def google_docs_write_to_tab(args: Dict[str, Any]) -> Dict[str, Any]:
    """Write text to a specific tab in a Google Docs document"""
    try:
        docs_service = get_docs_service()
        document_id = args["document_id"]
        tab_id = args["tab_id"]
        text = args["text"]

        # 탭에 텍스트 추가
        requests = [
            {
                'insertText': {
                    'location': {
                        'index': 1,
                        'tabId': tab_id,
                    },
                    'text': text + "\n"
                }
            }
        ]

        docs_service.documents().batchUpdate(
            documentId=document_id,
            body={'requests': requests}
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "document_id": document_id,
                    "tab_id": tab_id,
                    "message": f"탭에 텍스트 추가 완료 ({len(text)}자)"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        return {
            "content": [{"type": "text", "text": json.dumps({"success": False, "error": True, "message": f"탭 쓰기 실패: {str(e)}"}, ensure_ascii=False)}],
            "error": True
        }


# MCP Server
google_docs_tools = [
    google_docs_read_document,
    google_docs_create_with_content,
    google_docs_append_text,
    google_docs_list_tabs,
    google_docs_write_to_tab,
]


def create_google_docs_mcp_server():
    """Claude Code SDK Google Docs MCP server"""
    return create_sdk_mcp_server(
        name="google-docs",
        version="1.0.0",
        tools=google_docs_tools
    )
