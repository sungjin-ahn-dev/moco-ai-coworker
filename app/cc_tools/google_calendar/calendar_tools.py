"""
Google Calendar Tools for Claude Code SDK
Claude can manage events in Google Calendar
"""

import json
import os
from typing import Any, Dict
from datetime import datetime, timedelta
import re

from claude_agent_sdk import create_sdk_mcp_server, tool
from googleapiclient.errors import HttpError

from app.cc_tools.google_calendar.auth_helper import get_calendar_service


# 회의실 목록: Google Workspace 회의실 리소스를 환경변수 CALENDAR_ROOMS(JSON)로 주입.
# 예) CALENDAR_ROOMS='[{"name": "Room A (6인)", "email": "<id>@resource.calendar.google.com"}]'
def _load_rooms():
    raw = os.environ.get("CALENDAR_ROOMS", "").strip()
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            pass
    return [
        {"name": "Room A (6인)", "email": "room-a@resource.calendar.google.com"},
        {"name": "Room B (8인)", "email": "room-b@resource.calendar.google.com"},
    ]


ROOMS = _load_rooms()


def _get_error_message(e: HttpError) -> str:
    """HttpError에서 사용자 친화적 에러 메시지 추출"""
    if e.resp.status == 403:
        return "Google Calendar 접근 권한이 없습니다. Domain-Wide Delegation 설정을 확인해주세요."
    elif e.resp.status == 404:
        return "일정을 찾을 수 없습니다."
    elif e.resp.status == 400:
        return "잘못된 요청입니다. 날짜/시간 형식을 확인해주세요."
    elif e.resp.status == 401:
        return "인증이 만료되었습니다. 서비스 계정 설정을 확인해주세요."
    elif e.resp.status == 409:
        return "일정 충돌이 발생했습니다."
    else:
        return f"Calendar API 오류 (HTTP {e.resp.status})"


def _parse_datetime(dt_str: str, timezone: str = "Asia/Seoul") -> dict:
    """
    날짜/시간 문자열을 Google Calendar API 형식으로 변환

    지원 형식:
    - ISO 8601: '2024-01-15T14:00:00Z'
    - 간단 형식: '2024-01-15 14:00'
    - 날짜만: '2024-01-15' (종일 일정)
    """
    dt_str = dt_str.strip()

    # 날짜만 있는 경우 (종일 일정)
    if re.match(r'^\d{4}-\d{2}-\d{2}$', dt_str):
        return {"date": dt_str}

    # ISO 8601 형식 (Z 포함)
    if 'T' in dt_str and dt_str.endswith('Z'):
        return {"dateTime": dt_str, "timeZone": "UTC"}

    # ISO 8601 형식 (타임존 오프셋 포함)
    if 'T' in dt_str and ('+' in dt_str or '-' in dt_str[-6:]):
        return {"dateTime": dt_str}

    # 간단 형식: '2024-01-15 14:00'
    try:
        if ' ' in dt_str:
            dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M')
            return {"dateTime": dt.isoformat(), "timeZone": timezone}
    except ValueError:
        pass

    # 기본: 그대로 반환 (ISO 형식 가정)
    if 'T' in dt_str:
        return {"dateTime": dt_str, "timeZone": timezone}

    raise ValueError(f"날짜/시간 형식을 인식할 수 없습니다: {dt_str}")


def _format_event(event: dict) -> dict:
    """이벤트를 읽기 쉬운 형식으로 변환"""
    start = event.get('start', {})
    end = event.get('end', {})

    return {
        'id': event.get('id'),
        'summary': event.get('summary'),
        'description': event.get('description'),
        'location': event.get('location'),
        'start': start.get('dateTime') or start.get('date'),
        'end': end.get('dateTime') or end.get('date'),
        'timeZone': start.get('timeZone'),
        'status': event.get('status'),
        'htmlLink': event.get('htmlLink'),
        'creator': event.get('creator', {}).get('email'),
        'organizer': event.get('organizer', {}).get('email'),
        'attendees': [
            {
                'email': a.get('email'),
                'responseStatus': a.get('responseStatus'),
                'displayName': a.get('displayName')
            }
            for a in event.get('attendees', [])
        ],
        'recurringEventId': event.get('recurringEventId'),
        'conferenceData': event.get('conferenceData')
    }


@tool(
    "calendar_list_events",
    "Google Calendar 일정 목록을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 캘린더에 접근합니다.",
            },
            "calendar_id": {
                "type": "string",
                "description": "캘린더 ID (기본값: 'primary')",
            },
            "time_min": {
                "type": "string",
                "description": "시작 시간 (ISO 형식, 예: '2024-01-01T00:00:00Z' 또는 '2024-01-01')",
            },
            "time_max": {
                "type": "string",
                "description": "종료 시간 (ISO 형식)",
            },
            "max_results": {
                "type": "integer",
                "description": "가져올 일정 수 (기본값 10, 최대 100)",
            },
            "query": {
                "type": "string",
                "description": "검색어 (일정 제목에서 검색)",
            },
        },
        "required": [],
    },
)
async def calendar_list_events(args: Dict[str, Any]) -> Dict[str, Any]:
    """일정 목록 조회"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_calendar_service(slack_user_id=slack_user_id)

        calendar_id = args.get("calendar_id", "primary")
        time_min = args.get("time_min")
        time_max = args.get("time_max")
        max_results = min(args.get("max_results", 10), 100)
        query = args.get("query")

        # 기본값: 오늘부터
        if not time_min:
            time_min = datetime.utcnow().isoformat() + 'Z'
        elif not time_min.endswith('Z') and 'T' not in time_min:
            time_min = time_min + 'T00:00:00Z'

        params = {
            'calendarId': calendar_id,
            'timeMin': time_min,
            'maxResults': max_results,
            'singleEvents': True,
            'orderBy': 'startTime'
        }

        if time_max:
            if not time_max.endswith('Z') and 'T' not in time_max:
                time_max = time_max + 'T23:59:59Z'
            params['timeMax'] = time_max

        if query:
            params['q'] = query

        results = service.events().list(**params).execute()
        events = results.get('items', [])

        formatted_events = [_format_event(e) for e in events]

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "calendar_id": calendar_id,
                    "count": len(formatted_events),
                    "events": formatted_events
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
                    "message": f"일정 목록 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "calendar_get_event",
    "Google Calendar 일정 상세 정보를 조회합니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 캘린더에 접근합니다.",
            },
            "event_id": {
                "type": "string",
                "description": "일정 ID",
            },
            "calendar_id": {
                "type": "string",
                "description": "캘린더 ID (기본값: 'primary')",
            },
        },
        "required": ["event_id"],
    },
)
async def calendar_get_event(args: Dict[str, Any]) -> Dict[str, Any]:
    """일정 상세 조회"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_calendar_service(slack_user_id=slack_user_id)

        event_id = args["event_id"]
        calendar_id = args.get("calendar_id", "primary")

        event = service.events().get(
            calendarId=calendar_id,
            eventId=event_id
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "event": _format_event(event)
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
                    "message": f"일정 상세 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "calendar_create_event",
    "새 일정을 생성합니다. room_email을 지정하면 회의실도 함께 예약합니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 캘린더에 접근합니다.",
            },
            "summary": {
                "type": "string",
                "description": "일정 제목",
            },
            "start_time": {
                "type": "string",
                "description": "시작 시간 (ISO 형식 또는 '2024-01-15 14:00')",
            },
            "end_time": {
                "type": "string",
                "description": "종료 시간",
            },
            "description": {
                "type": "string",
                "description": "일정 설명 (선택)",
            },
            "location": {
                "type": "string",
                "description": "장소 (선택)",
            },
            "attendees": {
                "type": "string",
                "description": "참석자 이메일 (쉼표로 구분, 선택)",
            },
            "room_email": {
                "type": "string",
                "description": "예약할 회의실 리소스 이메일 (선택). calendar_find_available_room으로 조회한 이메일을 사용하세요.",
            },
            "calendar_id": {
                "type": "string",
                "description": "캘린더 ID (기본값: 'primary')",
            },
            "timezone": {
                "type": "string",
                "description": "타임존 (기본값: 'Asia/Seoul')",
            },
        },
        "required": ["summary", "start_time", "end_time"],
    },
)
async def calendar_create_event(args: Dict[str, Any]) -> Dict[str, Any]:
    """일정 생성"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_calendar_service(slack_user_id=slack_user_id)

        summary = args["summary"]
        start_time = args["start_time"]
        end_time = args["end_time"]
        description = args.get("description", "")
        location = args.get("location", "")
        attendees_str = args.get("attendees", "")
        room_email = args.get("room_email", "")
        calendar_id = args.get("calendar_id", "primary")
        timezone = args.get("timezone", "Asia/Seoul")

        # 이벤트 본문 구성
        event = {
            'summary': summary,
            'start': _parse_datetime(start_time, timezone),
            'end': _parse_datetime(end_time, timezone),
        }

        if description:
            event['description'] = description
        if location:
            event['location'] = location

        attendees = []
        if attendees_str:
            attendees = [
                {'email': email.strip()}
                for email in attendees_str.split(',')
                if email.strip()
            ]

        # 회의실 리소스 추가
        if room_email:
            attendees.append({'email': room_email, 'resource': True})

        if attendees:
            event['attendees'] = attendees

        # 일정 생성
        has_attendees = bool(attendees_str or room_email)
        created_event = service.events().insert(
            calendarId=calendar_id,
            body=event,
            sendUpdates='all' if has_attendees else 'none'
        ).execute()

        result = {
            "success": True,
            "event_id": created_event.get('id'),
            "html_link": created_event.get('htmlLink'),
            "message": f"일정 생성 완료: {summary}"
        }

        # 회의실 정보 포함
        if room_email:
            room_name = room_email
            for room in ROOMS:
                if room.get('email') == room_email:
                    room_name = room.get('name', room_email)
                    break
            result["room"] = room_name
            result["room_email"] = room_email
            result["message"] = f"일정 생성 완료: {summary} (회의실: {room_name})"

        return {
            "content": [{
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False, indent=2)
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
    except ValueError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": str(e)
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
                    "message": f"일정 생성 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "calendar_update_event",
    "기존 일정을 수정합니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 캘린더에 접근합니다.",
            },
            "event_id": {
                "type": "string",
                "description": "일정 ID",
            },
            "summary": {
                "type": "string",
                "description": "일정 제목 (선택)",
            },
            "start_time": {
                "type": "string",
                "description": "시작 시간 (선택)",
            },
            "end_time": {
                "type": "string",
                "description": "종료 시간 (선택)",
            },
            "description": {
                "type": "string",
                "description": "일정 설명 (선택)",
            },
            "location": {
                "type": "string",
                "description": "장소 (선택)",
            },
            "calendar_id": {
                "type": "string",
                "description": "캘린더 ID (기본값: 'primary')",
            },
            "timezone": {
                "type": "string",
                "description": "타임존 (기본값: 'Asia/Seoul')",
            },
        },
        "required": ["event_id"],
    },
)
async def calendar_update_event(args: Dict[str, Any]) -> Dict[str, Any]:
    """일정 수정"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_calendar_service(slack_user_id=slack_user_id)

        event_id = args["event_id"]
        calendar_id = args.get("calendar_id", "primary")
        timezone = args.get("timezone", "Asia/Seoul")

        # 기존 이벤트 조회
        event = service.events().get(
            calendarId=calendar_id,
            eventId=event_id
        ).execute()

        # 업데이트할 필드만 수정
        if "summary" in args:
            event['summary'] = args["summary"]
        if "description" in args:
            event['description'] = args["description"]
        if "location" in args:
            event['location'] = args["location"]
        if "start_time" in args:
            event['start'] = _parse_datetime(args["start_time"], timezone)
        if "end_time" in args:
            event['end'] = _parse_datetime(args["end_time"], timezone)

        # 일정 업데이트
        updated_event = service.events().update(
            calendarId=calendar_id,
            eventId=event_id,
            body=event,
            sendUpdates='all' if event.get('attendees') else 'none'
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "event_id": updated_event.get('id'),
                    "html_link": updated_event.get('htmlLink'),
                    "message": f"일정 수정 완료: {updated_event.get('summary')}"
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
    except ValueError as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": str(e)
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
                    "message": f"일정 수정 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "calendar_delete_event",
    "일정을 삭제합니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 캘린더에 접근합니다.",
            },
            "event_id": {
                "type": "string",
                "description": "삭제할 일정 ID",
            },
            "calendar_id": {
                "type": "string",
                "description": "캘린더 ID (기본값: 'primary')",
            },
        },
        "required": ["event_id"],
    },
)
async def calendar_delete_event(args: Dict[str, Any]) -> Dict[str, Any]:
    """일정 삭제"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_calendar_service(slack_user_id=slack_user_id)

        event_id = args["event_id"]
        calendar_id = args.get("calendar_id", "primary")

        # 일정 정보 먼저 조회 (삭제 전 이름 확인)
        event = service.events().get(
            calendarId=calendar_id,
            eventId=event_id
        ).execute()
        event_summary = event.get('summary', event_id)

        # 일정 삭제
        service.events().delete(
            calendarId=calendar_id,
            eventId=event_id,
            sendUpdates='all' if event.get('attendees') else 'none'
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "event_id": event_id,
                    "message": f"일정 삭제 완료: {event_summary}"
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
                    "message": f"일정 삭제 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "calendar_search_events",
    "일정을 검색합니다. 제목에서 검색어를 찾습니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 캘린더에 접근합니다.",
            },
            "query": {
                "type": "string",
                "description": "검색어",
            },
            "calendar_id": {
                "type": "string",
                "description": "캘린더 ID (기본값: 'primary')",
            },
            "time_min": {
                "type": "string",
                "description": "검색 시작 시간 (기본값: 현재)",
            },
            "time_max": {
                "type": "string",
                "description": "검색 종료 시간 (기본값: 1년 후)",
            },
            "max_results": {
                "type": "integer",
                "description": "최대 결과 수 (기본값: 20)",
            },
        },
        "required": ["query"],
    },
)
async def calendar_search_events(args: Dict[str, Any]) -> Dict[str, Any]:
    """일정 검색"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_calendar_service(slack_user_id=slack_user_id)

        query = args["query"]
        calendar_id = args.get("calendar_id", "primary")
        max_results = min(args.get("max_results", 20), 100)

        # 기본 시간 범위: 현재부터 1년
        time_min = args.get("time_min")
        time_max = args.get("time_max")

        if not time_min:
            time_min = datetime.utcnow().isoformat() + 'Z'
        elif not time_min.endswith('Z') and 'T' not in time_min:
            time_min = time_min + 'T00:00:00Z'

        if not time_max:
            time_max = (datetime.utcnow() + timedelta(days=365)).isoformat() + 'Z'
        elif not time_max.endswith('Z') and 'T' not in time_max:
            time_max = time_max + 'T23:59:59Z'

        results = service.events().list(
            calendarId=calendar_id,
            q=query,
            timeMin=time_min,
            timeMax=time_max,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = results.get('items', [])
        formatted_events = [_format_event(e) for e in events]

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "query": query,
                    "count": len(formatted_events),
                    "events": formatted_events
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
                    "message": f"일정 검색 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "calendar_list_calendars",
    "접근 가능한 캘린더 목록을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 캘린더에 접근합니다.",
            },
        },
        "required": [],
    },
)
async def calendar_list_calendars(args: Dict[str, Any]) -> Dict[str, Any]:
    """캘린더 목록 조회"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_calendar_service(slack_user_id=slack_user_id)

        results = service.calendarList().list().execute()
        calendars = results.get('items', [])

        formatted_calendars = [
            {
                'id': cal.get('id'),
                'summary': cal.get('summary'),
                'description': cal.get('description'),
                'primary': cal.get('primary', False),
                'accessRole': cal.get('accessRole'),
                'backgroundColor': cal.get('backgroundColor'),
                'timeZone': cal.get('timeZone')
            }
            for cal in calendars
        ]

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "count": len(formatted_calendars),
                    "calendars": formatted_calendars
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
                    "message": f"캘린더 목록 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "calendar_get_free_busy",
    "특정 시간대의 바쁨/한가함 정보를 조회합니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 캘린더에 접근합니다.",
            },
            "time_min": {
                "type": "string",
                "description": "조회 시작 시간 (ISO 형식)",
            },
            "time_max": {
                "type": "string",
                "description": "조회 종료 시간 (ISO 형식)",
            },
            "calendar_ids": {
                "type": "string",
                "description": "캘린더 ID 목록 (쉼표로 구분, 기본값: 'primary')",
            },
        },
        "required": ["time_min", "time_max"],
    },
)
async def calendar_get_free_busy(args: Dict[str, Any]) -> Dict[str, Any]:
    """바쁨/한가함 조회"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_calendar_service(slack_user_id=slack_user_id)

        time_min = args["time_min"]
        time_max = args["time_max"]
        calendar_ids_str = args.get("calendar_ids", "primary")

        # 시간 형식 처리
        if not time_min.endswith('Z') and 'T' not in time_min:
            time_min = time_min + 'T00:00:00Z'
        if not time_max.endswith('Z') and 'T' not in time_max:
            time_max = time_max + 'T23:59:59Z'

        # 캘린더 ID 목록 파싱
        calendar_ids = [cid.strip() for cid in calendar_ids_str.split(',')]
        items = [{'id': cid} for cid in calendar_ids]

        body = {
            'timeMin': time_min,
            'timeMax': time_max,
            'items': items
        }

        results = service.freebusy().query(body=body).execute()
        calendars = results.get('calendars', {})

        formatted_result = {}
        for cal_id, data in calendars.items():
            busy_times = data.get('busy', [])
            formatted_result[cal_id] = {
                'busy': busy_times,
                'busy_count': len(busy_times)
            }

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "time_min": time_min,
                    "time_max": time_max,
                    "calendars": formatted_result
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
                    "message": f"바쁨/한가함 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "calendar_add_attendee",
    "기존 일정에 참석자를 추가합니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 캘린더에 접근합니다.",
            },
            "event_id": {
                "type": "string",
                "description": "일정 ID",
            },
            "attendees": {
                "type": "string",
                "description": "추가할 참석자 이메일 (쉼표로 구분)",
            },
            "calendar_id": {
                "type": "string",
                "description": "캘린더 ID (기본값: 'primary')",
            },
        },
        "required": ["event_id", "attendees"],
    },
)
async def calendar_add_attendee(args: Dict[str, Any]) -> Dict[str, Any]:
    """참석자 추가"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_calendar_service(slack_user_id=slack_user_id)

        event_id = args["event_id"]
        attendees_str = args["attendees"]
        calendar_id = args.get("calendar_id", "primary")

        # 기존 이벤트 조회
        event = service.events().get(
            calendarId=calendar_id,
            eventId=event_id
        ).execute()

        # 기존 참석자 유지 + 새 참석자 추가
        existing_attendees = event.get('attendees', [])
        new_attendees = [
            {'email': email.strip()}
            for email in attendees_str.split(',')
            if email.strip()
        ]

        # 중복 제거
        existing_emails = {a['email'] for a in existing_attendees}
        for new_att in new_attendees:
            if new_att['email'] not in existing_emails:
                existing_attendees.append(new_att)

        event['attendees'] = existing_attendees

        # 일정 업데이트
        updated_event = service.events().update(
            calendarId=calendar_id,
            eventId=event_id,
            body=event,
            sendUpdates='all'
        ).execute()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "event_id": updated_event.get('id'),
                    "added_attendees": [a['email'] for a in new_attendees],
                    "total_attendees": len(updated_event.get('attendees', [])),
                    "message": f"참석자 추가 완료: {attendees_str}"
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
                    "message": f"참석자 추가 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "calendar_respond_to_event",
    "일정 초대에 응답합니다. (수락/거절/미정)",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 캘린더에 접근합니다.",
            },
            "event_id": {
                "type": "string",
                "description": "일정 ID",
            },
            "response": {
                "type": "string",
                "description": "응답: 'accepted' (수락), 'declined' (거절), 'tentative' (미정)",
            },
            "calendar_id": {
                "type": "string",
                "description": "캘린더 ID (기본값: 'primary')",
            },
        },
        "required": ["event_id", "response"],
    },
)
async def calendar_respond_to_event(args: Dict[str, Any]) -> Dict[str, Any]:
    """일정 초대 응답"""
    try:
        slack_user_id = args.get("slack_user_id")
        service = get_calendar_service(slack_user_id=slack_user_id)

        event_id = args["event_id"]
        response = args["response"].lower()
        calendar_id = args.get("calendar_id", "primary")

        if response not in ['accepted', 'declined', 'tentative']:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "응답은 'accepted', 'declined', 'tentative' 중 하나여야 합니다"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

        # 기존 이벤트 조회
        event = service.events().get(
            calendarId=calendar_id,
            eventId=event_id
        ).execute()

        # 현재 사용자의 참석 상태 업데이트
        # Calendar API에서는 직접 attendee 상태를 수정할 수 없어서
        # events().update()를 사용해야 함

        # Slack 사용자 이메일 조회 (자동 매핑)
        from app.cc_tools.google_calendar.auth_helper import _get_slack_user_email
        from app.config.settings import get_settings
        settings = get_settings()

        user_email = None
        if slack_user_id:
            user_email = _get_slack_user_email(slack_user_id)
        if not user_email:
            user_email = settings.GOOGLE_CALENDAR_USER_EMAIL

        if not user_email:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "사용자 이메일을 확인할 수 없습니다. Slack 프로필에 이메일이 설정되어 있는지 확인해주세요."
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

        attendees = event.get('attendees', [])
        updated = False
        for attendee in attendees:
            if attendee.get('email') == user_email:
                attendee['responseStatus'] = response
                updated = True
                break

        if not updated:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "이 일정의 참석자가 아닙니다"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

        event['attendees'] = attendees

        updated_event = service.events().update(
            calendarId=calendar_id,
            eventId=event_id,
            body=event,
            sendUpdates='all'
        ).execute()

        response_text = {
            'accepted': '수락',
            'declined': '거절',
            'tentative': '미정'
        }

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "event_id": updated_event.get('id'),
                    "response": response,
                    "message": f"일정 초대 응답 완료: {response_text.get(response)}"
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
                    "message": f"일정 초대 응답 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "calendar_list_rooms",
    "설정에 등록된 회의실 목록을 조회합니다.",
    {
        "type": "object",
        "properties": {},
        "required": [],
    },
)
async def calendar_list_rooms(args: Dict[str, Any]) -> Dict[str, Any]:
    """등록된 회의실 목록 반환"""
    try:
        if not ROOMS:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "count": 0,
                        "rooms": [],
                        "message": "등록된 회의실이 없습니다. calendar_tools.py의 ROOMS 목록을 설정해주세요."
                    }, ensure_ascii=False, indent=2)
                }]
            }

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "count": len(ROOMS),
                    "rooms": ROOMS
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"회의실 목록 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "calendar_find_available_room",
    "특정 시간대에 비어있는 회의실을 자동으로 검색합니다. 회의실 예약 전에 이 도구로 빈 회의실을 확인하세요.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "요청한 Slack 사용자 ID (예: 'U12345678'). 반드시 전달해야 해당 사용자의 캘린더에 접근합니다.",
            },
            "start_time": {
                "type": "string",
                "description": "시작 시간 (ISO 형식, 예: '2026-01-29T15:00:00+09:00' 또는 '2026-01-29 15:00')",
            },
            "end_time": {
                "type": "string",
                "description": "종료 시간 (ISO 형식)",
            },
            "timezone": {
                "type": "string",
                "description": "타임존 (기본값: 'Asia/Seoul')",
            },
        },
        "required": ["start_time", "end_time"],
    },
)
async def calendar_find_available_room(args: Dict[str, Any]) -> Dict[str, Any]:
    """비어있는 회의실 검색"""
    slack_user_id = args.get("slack_user_id")
    try:
        if not ROOMS:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "등록된 회의실이 없습니다. calendar_tools.py의 ROOMS 목록을 설정해주세요."
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

        service = get_calendar_service(slack_user_id=slack_user_id)

        start_time = args["start_time"]
        end_time = args["end_time"]
        timezone = args.get("timezone", "Asia/Seoul")

        # 시간 형식을 freeBusy API에 맞게 변환
        start_dt = _parse_datetime(start_time, timezone)
        end_dt = _parse_datetime(end_time, timezone)

        # freeBusy API는 dateTime 형식 필요
        time_min = start_dt.get('dateTime', start_dt.get('date', ''))
        time_max = end_dt.get('dateTime', end_dt.get('date', ''))

        # freeBusy query로 모든 회의실 가용 여부 한번에 조회
        body = {
            'timeMin': time_min,
            'timeMax': time_max,
            'timeZone': timezone,
            'items': [{'id': room['email']} for room in ROOMS]
        }

        results = service.freebusy().query(body=body).execute()
        calendars = results.get('calendars', {})

        available_rooms = []
        unavailable_rooms = []

        for room in ROOMS:
            room_email = room['email']
            room_data = calendars.get(room_email, {})
            busy_times = room_data.get('busy', [])
            errors = room_data.get('errors', [])

            if errors:
                unavailable_rooms.append({
                    'name': room.get('name', room_email),
                    'email': room_email,
                    'reason': 'access_error'
                })
            elif busy_times:
                unavailable_rooms.append({
                    'name': room.get('name', room_email),
                    'email': room_email,
                    'reason': 'busy',
                    'busy_times': busy_times
                })
            else:
                available_rooms.append({
                    'name': room.get('name', room_email),
                    'email': room_email
                })

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "time_range": {
                        "start": time_min,
                        "end": time_max
                    },
                    "available_rooms": available_rooms,
                    "unavailable_rooms": unavailable_rooms,
                    "message": f"빈 회의실 {len(available_rooms)}개, 사용 중 {len(unavailable_rooms)}개"
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
                    "message": f"회의실 검색 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


# MCP Server
calendar_tools = [
    calendar_list_events,
    calendar_get_event,
    calendar_create_event,
    calendar_update_event,
    calendar_delete_event,
    calendar_search_events,
    calendar_list_calendars,
    calendar_get_free_busy,
    calendar_add_attendee,
    calendar_respond_to_event,
    calendar_list_rooms,
    calendar_find_available_room,
]


def create_google_calendar_mcp_server():
    """Claude Code SDK Google Calendar MCP server"""
    return create_sdk_mcp_server(
        name="google-calendar",
        version="1.0.0",
        tools=calendar_tools
    )
