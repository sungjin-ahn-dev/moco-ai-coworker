"""
ClickUp Tools for Claude Code SDK
Claude can manage tasks and projects in ClickUp
"""

import json
import logging
from typing import Any, Dict

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

CLICKUP_API_BASE = "https://api.clickup.com/api/v2"


async def _find_user_id(client: httpx.AsyncClient, team_id: str, username: str = "", email: str = "") -> tuple[str | None, str | None]:
    """
    팀 멤버에서 username 또는 email로 사용자를 찾습니다.

    Returns:
        tuple: (user_id, matched_username) 또는 (None, None)
    """
    if not username and not email:
        return None, None

    username = username.lower()
    email = email.lower()

    team_resp = await client.get(
        f"{CLICKUP_API_BASE}/team",
        headers=_get_headers(),
    )
    team_resp.raise_for_status()
    teams = team_resp.json().get("teams", [])

    for team in teams:
        if str(team.get("id")) == str(team_id):
            members = team.get("members", [])
            for member in members:
                m_user = member.get("user", {})
                m_username = (m_user.get("username") or "").lower()
                m_email = (m_user.get("email") or "").lower()

                # 이메일 정확히 일치
                if email and m_email == email:
                    return str(m_user.get("id")), m_user.get("username") or m_email

                # username 부분 일치
                if username and username in m_username:
                    return str(m_user.get("id")), m_user.get("username") or m_email

            break

    return None, None


# 닉네임별 ClickUp API 키 매핑 (settings에서 lazy 로드)
_USER_API_KEYS: dict[str, str] = {}
_USER_API_KEYS_LOADED: bool = False


def _load_user_api_keys():
    """settings에서 사용자별 API 키를 로드 (최초 1회)"""
    global _USER_API_KEYS_LOADED
    if _USER_API_KEYS_LOADED:
        return
    settings = get_settings()
    for name, attr in (("user1", "CLICKUP_API_KEY_USER1"), ("user2", "CLICKUP_API_KEY_USER2"),
                       ("user3", "CLICKUP_API_KEY_USER3"), ("user4", "CLICKUP_API_KEY_USER4")):
        key = getattr(settings, attr, "")
        if key:
            _USER_API_KEYS[name] = key
    _USER_API_KEYS_LOADED = True
    if _USER_API_KEYS:
        logger.info(f"[CLICKUP] 사용자별 API 키 로드: {list(_USER_API_KEYS.keys())}")

# 태스크별 요청자 매핑 (asyncio 동시성 안전)
import contextvars
_requester_var: contextvars.ContextVar[str] = contextvars.ContextVar("clickup_requester", default="")


def set_clickup_requester(nickname: str):
    """현재 asyncio 태스크의 ClickUp 요청자를 설정합니다"""
    _requester_var.set(nickname.lower().strip())
    logger.info(f"[CLICKUP] 요청자 설정: {nickname.lower().strip()}")


def _get_headers(requester: str = "") -> dict:
    """ClickUp API 인증 헤더 반환 — 요청자별 API 키 사용"""
    _load_user_api_keys()
    # 1. 직접 전달된 requester
    nick = requester.lower().strip() if requester else ""
    # 2. contextvars (asyncio 태스크별)
    if not nick:
        nick = _requester_var.get("")
    # 3. 닉네임 매핑에 있으면 해당 API 키 사용
    if nick and nick in _USER_API_KEYS:
        api_key = _USER_API_KEYS[nick]
        logger.info(f"[CLICKUP] {nick} 전용 API 키 사용")
    else:
        settings = get_settings()
        api_key = settings.CLICKUP_API_KEY
        if nick:
            logger.info(f"[CLICKUP] {nick}의 전용 키 없음, 기본 키 사용")
    if not api_key:
        raise ValueError("CLICKUP_API_KEY가 설정되지 않았습니다.")
    return {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }


def _auto_set_requester(args: Dict[str, Any]):
    """tool args에서 요청자를 자동 감지하여 설정"""
    _load_user_api_keys()
    # requester, assign_to_username, username 순으로 확인
    for key in ("requester", "assign_to_username", "username"):
        val = args.get(key, "")
        if val:
            nick = val.lower().strip()
            if nick in _USER_API_KEYS:
                _requester_var.set(nick)
                logger.info(f"[CLICKUP] args에서 요청자 감지: {nick}")
                return
    # assignees 필드에서도 확인
    assignees = args.get("assignees", "")
    if assignees:
        for name in assignees.split(","):
            nick = name.strip().lower()
            if nick in _USER_API_KEYS:
                _requester_var.set(nick)
                logger.info(f"[CLICKUP] assignees에서 요청자 감지: {nick}")
                return


@tool(
    "clickup_get_me",
    "현재 인증된 사용자(나) 정보를 조회합니다. 내 user_id를 확인할 때 사용합니다.",
    {
        "type": "object",
        "properties": {},
        "required": [],
    },
)
async def clickup_get_me(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    _auto_set_requester(args)현재 사용자 정보 조회"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{CLICKUP_API_BASE}/user",
                headers=_get_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        user = data.get("user", {})
        result = {
            "id": user.get("id"),
            "username": user.get("username"),
            "email": user.get("email"),
            "color": user.get("color"),
            "profilePicture": user.get("profilePicture"),
        }

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "user": result,
                    "message": f"현재 사용자: {result.get('username')} (ID: {result.get('id')})"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        logger.error(f"[CLICKUP] 사용자 정보 조회 실패: {e}")
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"사용자 정보 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "clickup_get_my_tasks",
    "특정 사용자에게 할당된 태스크 목록을 조회합니다. username 또는 email로 사용자를 찾아 해당 사용자의 태스크를 조회합니다. username/email을 생략하면 API 토큰 소유자의 태스크를 조회합니다.",
    {
        "type": "object",
        "properties": {
            "team_id": {
                "type": "string",
                "description": "워크스페이스(팀) ID",
            },
            "username": {
                "type": "string",
                "description": "조회할 사용자 이름 (Slack 사용자명 등). 부분 일치로 검색합니다.",
            },
            "email": {
                "type": "string",
                "description": "조회할 사용자 이메일. 정확히 일치해야 합니다.",
            },
            "include_closed": {
                "type": "boolean",
                "description": "완료된 태스크 포함 여부 (기본값: false)",
            },
        },
        "required": ["team_id"],
    },
)
async def clickup_get_my_tasks(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    _auto_set_requester(args)사용자 태스크 목록 조회 (Slack 사용자 매핑 지원)"""
    try:
        team_id = args["team_id"]
        username = args.get("username", "").strip().lower()
        email = args.get("email", "").strip().lower()
        include_closed = args.get("include_closed", False)

        async with httpx.AsyncClient() as client:
            user_id = None
            matched_user = None

            # username 또는 email이 제공된 경우 팀 멤버에서 검색
            if username or email:
                # 워크스페이스 정보에서 멤버 목록 가져오기
                team_resp = await client.get(
                    f"{CLICKUP_API_BASE}/team",
                    headers=_get_headers(),
                )
                team_resp.raise_for_status()
                teams = team_resp.json().get("teams", [])

                # 해당 team_id의 멤버 찾기
                for team in teams:
                    if str(team.get("id")) == str(team_id):
                        members = team.get("members", [])
                        for member in members:
                            m_user = member.get("user", {})
                            m_username = (m_user.get("username") or "").lower()
                            m_email = (m_user.get("email") or "").lower()

                            # 이메일 정확히 일치
                            if email and m_email == email:
                                user_id = m_user.get("id")
                                matched_user = m_user.get("username") or m_email
                                break

                            # username 부분 일치
                            if username and username in m_username:
                                user_id = m_user.get("id")
                                matched_user = m_user.get("username") or m_email
                                break

                        break

                if not user_id:
                    search_term = email or username
                    return {
                        "content": [{
                            "type": "text",
                            "text": json.dumps({
                                "success": False,
                                "error": True,
                                "message": f"ClickUp에서 '{search_term}' 사용자를 찾을 수 없습니다. 워크스페이스 멤버인지 확인해주세요."
                            }, ensure_ascii=False, indent=2)
                        }],
                        "error": True
                    }
            else:
                # username/email이 없으면 API 토큰 소유자 사용
                user_resp = await client.get(
                    f"{CLICKUP_API_BASE}/user",
                    headers=_get_headers(),
                )
                user_resp.raise_for_status()
                user_data = user_resp.json()
                user_id = user_data.get("user", {}).get("id")
                matched_user = user_data.get("user", {}).get("username")

            if not user_id:
                raise ValueError("사용자 ID를 가져올 수 없습니다.")

            # 해당 사용자의 태스크 조회
            params = {
                "assignees[]": [str(user_id)],
                "include_closed": str(include_closed).lower(),
            }

            tasks_resp = await client.get(
                f"{CLICKUP_API_BASE}/team/{team_id}/task",
                headers=_get_headers(),
                params=params,
            )
            tasks_resp.raise_for_status()
            tasks_data = tasks_resp.json()

        tasks = tasks_data.get("tasks", [])
        result = [
            {
                "id": t.get("id"),
                "name": t.get("name"),
                "status": t.get("status", {}).get("status"),
                "priority": t.get("priority", {}).get("priority") if t.get("priority") else None,
                "due_date": t.get("due_date"),
                "url": t.get("url"),
                "list": {"id": t.get("list", {}).get("id"), "name": t.get("list", {}).get("name")},
                "folder": {"id": t.get("folder", {}).get("id"), "name": t.get("folder", {}).get("name")},
            }
            for t in tasks
        ]

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "user": matched_user,
                    "user_id": user_id,
                    "count": len(result),
                    "tasks": result,
                    "message": f"{matched_user}의 태스크 {len(result)}개"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        logger.error(f"[CLICKUP] 태스크 조회 실패: {e}")
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"태스크 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "clickup_list_workspaces",
    "ClickUp 워크스페이스(팀) 목록을 조회합니다.",
    {
        "type": "object",
        "properties": {},
        "required": [],
    },
)
async def clickup_list_workspaces(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    _auto_set_requester(args)워크스페이스 목록 조회"""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{CLICKUP_API_BASE}/team",
                headers=_get_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        teams = data.get("teams", [])
        result = [
            {
                "id": t.get("id"),
                "name": t.get("name"),
                "members_count": len(t.get("members", [])),
            }
            for t in teams
        ]

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "count": len(result),
                    "workspaces": result,
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        logger.error(f"[CLICKUP] 워크스페이스 조회 실패: {e}")
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"워크스페이스 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "clickup_list_spaces",
    "워크스페이스의 스페이스 목록을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "team_id": {
                "type": "string",
                "description": "워크스페이스(팀) ID",
            },
        },
        "required": ["team_id"],
    },
)
async def clickup_list_spaces(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    _auto_set_requester(args)스페이스 목록 조회"""
    try:
        team_id = args["team_id"]

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{CLICKUP_API_BASE}/team/{team_id}/space",
                headers=_get_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        spaces = data.get("spaces", [])
        result = [
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "private": s.get("private"),
                "status": s.get("statuses", []),
            }
            for s in spaces
        ]

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "count": len(result),
                    "spaces": result,
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        logger.error(f"[CLICKUP] 스페이스 조회 실패: {e}")
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"스페이스 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "clickup_list_folders",
    "스페이스의 폴더 목록을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "space_id": {
                "type": "string",
                "description": "스페이스 ID",
            },
        },
        "required": ["space_id"],
    },
)
async def clickup_list_folders(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    _auto_set_requester(args)폴더 목록 조회"""
    try:
        space_id = args["space_id"]

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{CLICKUP_API_BASE}/space/{space_id}/folder",
                headers=_get_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        folders = data.get("folders", [])
        result = [
            {
                "id": f.get("id"),
                "name": f.get("name"),
                "lists": [
                    {"id": l.get("id"), "name": l.get("name")}
                    for l in f.get("lists", [])
                ],
            }
            for f in folders
        ]

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "count": len(result),
                    "folders": result,
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        logger.error(f"[CLICKUP] 폴더 조회 실패: {e}")
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"폴더 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "clickup_list_lists",
    "폴더 또는 스페이스의 리스트 목록을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "folder_id": {
                "type": "string",
                "description": "폴더 ID (폴더 내 리스트 조회 시)",
            },
            "space_id": {
                "type": "string",
                "description": "스페이스 ID (폴더 없는 리스트 조회 시)",
            },
        },
        "required": [],
    },
)
async def clickup_list_lists(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    _auto_set_requester(args)리스트 목록 조회"""
    try:
        folder_id = args.get("folder_id")
        space_id = args.get("space_id")

        if not folder_id and not space_id:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "folder_id 또는 space_id 중 하나를 지정해주세요."
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

        if folder_id:
            url = f"{CLICKUP_API_BASE}/folder/{folder_id}/list"
        else:
            url = f"{CLICKUP_API_BASE}/space/{space_id}/list"

        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=_get_headers())
            resp.raise_for_status()
            data = resp.json()

        lists = data.get("lists", [])
        result = [
            {
                "id": l.get("id"),
                "name": l.get("name"),
                "task_count": l.get("task_count"),
                "status": l.get("status", {}).get("status"),
            }
            for l in lists
        ]

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "count": len(result),
                    "lists": result,
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        logger.error(f"[CLICKUP] 리스트 조회 실패: {e}")
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"리스트 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "clickup_list_tasks",
    "리스트의 태스크 목록을 조회합니다. username으로 특정 사용자의 태스크만 필터링할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "list_id": {
                "type": "string",
                "description": "리스트 ID",
            },
            "team_id": {
                "type": "string",
                "description": "워크스페이스(팀) ID (username/email 필터 사용 시 필요)",
            },
            "username": {
                "type": "string",
                "description": "담당자 이름으로 필터 (Slack 사용자명 등, 부분 일치)",
            },
            "email": {
                "type": "string",
                "description": "담당자 이메일로 필터 (정확히 일치)",
            },
            "assignees": {
                "type": "string",
                "description": "담당자 ID로 필터 (쉼표로 구분, 선택)",
            },
            "statuses": {
                "type": "string",
                "description": "상태로 필터 (쉼표로 구분, 예: 'open,in progress')",
            },
            "include_closed": {
                "type": "boolean",
                "description": "완료된 태스크 포함 여부 (기본값: false)",
            },
        },
        "required": ["list_id"],
    },
)
async def clickup_list_tasks(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    _auto_set_requester(args)태스크 목록 조회"""
    try:
        list_id = args["list_id"]
        team_id = args.get("team_id", "")
        username = args.get("username", "")
        email = args.get("email", "")
        params = {}

        async with httpx.AsyncClient() as client:
            # username/email로 사용자 ID 찾기
            if (username or email) and team_id:
                user_id, matched_user = await _find_user_id(client, team_id, username, email)
                if user_id:
                    params.setdefault("assignees[]", []).append(user_id)
                else:
                    search_term = email or username
                    return {
                        "content": [{
                            "type": "text",
                            "text": json.dumps({
                                "success": False,
                                "error": True,
                                "message": f"ClickUp에서 '{search_term}' 사용자를 찾을 수 없습니다."
                            }, ensure_ascii=False, indent=2)
                        }],
                        "error": True
                    }

            if args.get("assignees"):
                for assignee in args["assignees"].split(","):
                    params.setdefault("assignees[]", []).append(assignee.strip())

            if args.get("statuses"):
                for status in args["statuses"].split(","):
                    params.setdefault("statuses[]", []).append(status.strip())

            if args.get("include_closed"):
                params["include_closed"] = "true"

            resp = await client.get(
                f"{CLICKUP_API_BASE}/list/{list_id}/task",
                headers=_get_headers(),
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

        tasks = data.get("tasks", [])
        result = [
            {
                "id": t.get("id"),
                "name": t.get("name"),
                "status": t.get("status", {}).get("status"),
                "priority": t.get("priority", {}).get("priority") if t.get("priority") else None,
                "assignees": [a.get("username") or a.get("email") for a in t.get("assignees", [])],
                "due_date": t.get("due_date"),
                "url": t.get("url"),
            }
            for t in tasks
        ]

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "count": len(result),
                    "tasks": result,
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        logger.error(f"[CLICKUP] 태스크 조회 실패: {e}")
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"태스크 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "clickup_get_task",
    "태스크 상세 정보를 조회합니다.",
    {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "태스크 ID",
            },
        },
        "required": ["task_id"],
    },
)
async def clickup_get_task(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    _auto_set_requester(args)태스크 상세 조회"""
    try:
        task_id = args["task_id"]

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{CLICKUP_API_BASE}/task/{task_id}",
                headers=_get_headers(),
            )
            resp.raise_for_status()
            task = resp.json()

        result = {
            "id": task.get("id"),
            "name": task.get("name"),
            "description": task.get("description"),
            "status": task.get("status", {}).get("status"),
            "priority": task.get("priority", {}).get("priority") if task.get("priority") else None,
            "assignees": [
                {"id": a.get("id"), "username": a.get("username"), "email": a.get("email")}
                for a in task.get("assignees", [])
            ],
            "due_date": task.get("due_date"),
            "start_date": task.get("start_date"),
            "time_estimate": task.get("time_estimate"),
            "tags": [tag.get("name") for tag in task.get("tags", [])],
            "parent": task.get("parent"),
            "url": task.get("url"),
            "date_created": task.get("date_created"),
            "date_updated": task.get("date_updated"),
            "creator": task.get("creator", {}).get("username"),
            "list": {"id": task.get("list", {}).get("id"), "name": task.get("list", {}).get("name")},
            "folder": {"id": task.get("folder", {}).get("id"), "name": task.get("folder", {}).get("name")},
            "space": {"id": task.get("space", {}).get("id")},
        }

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "task": result,
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        logger.error(f"[CLICKUP] 태스크 상세 조회 실패: {e}")
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"태스크 상세 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "clickup_create_task",
    "새 태스크를 생성합니다. assign_to_username을 지정하면 해당 사용자에게 자동 할당됩니다.",
    {
        "type": "object",
        "properties": {
            "list_id": {
                "type": "string",
                "description": "태스크를 생성할 리스트 ID",
            },
            "team_id": {
                "type": "string",
                "description": "워크스페이스(팀) ID (assign_to_username/email 사용 시 필요)",
            },
            "name": {
                "type": "string",
                "description": "태스크 이름",
            },
            "description": {
                "type": "string",
                "description": "태스크 설명 (선택)",
            },
            "assign_to_username": {
                "type": "string",
                "description": "담당자 이름으로 자동 할당 (Slack 사용자명 등, 부분 일치)",
            },
            "assign_to_email": {
                "type": "string",
                "description": "담당자 이메일로 자동 할당 (정확히 일치)",
            },
            "assignees": {
                "type": "string",
                "description": "담당자 ID (쉼표로 구분, 선택)",
            },
            "priority": {
                "type": "integer",
                "description": "우선순위: 1=긴급, 2=높음, 3=보통, 4=낮음 (선택)",
            },
            "due_date": {
                "type": "string",
                "description": "마감일 Unix timestamp (밀리초, 선택)",
            },
            "status": {
                "type": "string",
                "description": "태스크 상태 (선택, 예: 'open', 'in progress')",
            },
            "tags": {
                "type": "string",
                "description": "태그 (쉼표로 구분, 선택)",
            },
        },
        "required": ["list_id", "name"],
    },
)
async def clickup_create_task(args: Dict[str, Any]) -> Dict[str, Any]:
    """태스크 생성"""
    _auto_set_requester(args)
    try:
        list_id = args["list_id"]
        team_id = args.get("team_id", "")
        assign_to_username = args.get("assign_to_username", "")
        assign_to_email = args.get("assign_to_email", "")

        body = {"name": args["name"]}
        assigned_user = None

        async with httpx.AsyncClient() as client:
            # username/email로 사용자 ID 찾아서 할당
            if (assign_to_username or assign_to_email) and team_id:
                user_id, matched_user = await _find_user_id(client, team_id, assign_to_username, assign_to_email)
                if user_id:
                    body["assignees"] = [int(user_id)]
                    assigned_user = matched_user
                else:
                    search_term = assign_to_email or assign_to_username
                    return {
                        "content": [{
                            "type": "text",
                            "text": json.dumps({
                                "success": False,
                                "error": True,
                                "message": f"ClickUp에서 '{search_term}' 사용자를 찾을 수 없습니다."
                            }, ensure_ascii=False, indent=2)
                        }],
                        "error": True
                    }

            if args.get("description"):
                body["description"] = args["description"]
            if args.get("assignees"):
                existing = body.get("assignees", [])
                existing.extend([int(a.strip()) for a in args["assignees"].split(",")])
                body["assignees"] = existing
            if args.get("priority"):
                body["priority"] = args["priority"]
            if args.get("due_date"):
                body["due_date"] = int(args["due_date"])
            if args.get("status"):
                body["status"] = args["status"]
            if args.get("tags"):
                body["tags"] = [t.strip() for t in args["tags"].split(",")]

            resp = await client.post(
                f"{CLICKUP_API_BASE}/list/{list_id}/task",
                headers=_get_headers(),
                json=body,
            )
            resp.raise_for_status()
            task = resp.json()

        result = {
            "success": True,
            "task_id": task.get("id"),
            "name": task.get("name"),
            "url": task.get("url"),
            "message": f"태스크 생성 완료: {task.get('name')}"
        }

        if assigned_user:
            result["assigned_to"] = assigned_user
            result["message"] = f"태스크 생성 완료: {task.get('name')} (담당자: {assigned_user})"

        return {
            "content": [{
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        logger.error(f"[CLICKUP] 태스크 생성 실패: {e}")
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"태스크 생성 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "clickup_update_task",
    "기존 태스크를 수정합니다. 상태 변경, 담당자 변경, 우선순위 변경 등에 사용합니다.",
    {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "태스크 ID",
            },
            "name": {
                "type": "string",
                "description": "태스크 이름 (선택)",
            },
            "description": {
                "type": "string",
                "description": "태스크 설명 (선택)",
            },
            "status": {
                "type": "string",
                "description": "상태 (예: 'open', 'in progress', 'complete')",
            },
            "priority": {
                "type": "integer",
                "description": "우선순위: 1=긴급, 2=높음, 3=보통, 4=낮음",
            },
            "due_date": {
                "type": "string",
                "description": "마감일 Unix timestamp (밀리초)",
            },
            "assignees_add": {
                "type": "string",
                "description": "추가할 담당자 ID (쉼표로 구분)",
            },
            "assignees_remove": {
                "type": "string",
                "description": "제거할 담당자 ID (쉼표로 구분)",
            },
        },
        "required": ["task_id"],
    },
)
async def clickup_update_task(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    _auto_set_requester(args)태스크 수정"""
    try:
        task_id = args["task_id"]
        body = {}

        if "name" in args:
            body["name"] = args["name"]
        if "description" in args:
            body["description"] = args["description"]
        if "status" in args:
            body["status"] = args["status"]
        if "priority" in args:
            body["priority"] = args["priority"]
        if "due_date" in args:
            body["due_date"] = int(args["due_date"])

        if args.get("assignees_add") or args.get("assignees_remove"):
            assignees = {}
            if args.get("assignees_add"):
                assignees["add"] = [int(a.strip()) for a in args["assignees_add"].split(",")]
            if args.get("assignees_remove"):
                assignees["rem"] = [int(a.strip()) for a in args["assignees_remove"].split(",")]
            body["assignees"] = assignees

        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{CLICKUP_API_BASE}/task/{task_id}",
                headers=_get_headers(),
                json=body,
            )
            resp.raise_for_status()
            task = resp.json()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "task_id": task.get("id"),
                    "name": task.get("name"),
                    "status": task.get("status", {}).get("status"),
                    "url": task.get("url"),
                    "message": f"태스크 수정 완료: {task.get('name')}"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        logger.error(f"[CLICKUP] 태스크 수정 실패: {e}")
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"태스크 수정 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "clickup_delete_task",
    "태스크를 삭제합니다.",
    {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "삭제할 태스크 ID",
            },
        },
        "required": ["task_id"],
    },
)
async def clickup_delete_task(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    _auto_set_requester(args)태스크 삭제"""
    try:
        task_id = args["task_id"]

        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{CLICKUP_API_BASE}/task/{task_id}",
                headers=_get_headers(),
            )
            resp.raise_for_status()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "task_id": task_id,
                    "message": f"태스크 삭제 완료: {task_id}"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        logger.error(f"[CLICKUP] 태스크 삭제 실패: {e}")
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"태스크 삭제 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "clickup_add_comment",
    "태스크에 코멘트를 추가합니다.",
    {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "태스크 ID",
            },
            "comment_text": {
                "type": "string",
                "description": "코멘트 내용",
            },
        },
        "required": ["task_id", "comment_text"],
    },
)
async def clickup_add_comment(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    _auto_set_requester(args)코멘트 추가"""
    try:
        task_id = args["task_id"]
        comment_text = args["comment_text"]

        body = {"comment_text": comment_text}

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{CLICKUP_API_BASE}/task/{task_id}/comment",
                headers=_get_headers(),
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "comment_id": data.get("id"),
                    "message": "코멘트 추가 완료"
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        logger.error(f"[CLICKUP] 코멘트 추가 실패: {e}")
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"코멘트 추가 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "clickup_get_comments",
    "태스크의 코멘트 목록을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "태스크 ID",
            },
        },
        "required": ["task_id"],
    },
)
async def clickup_get_comments(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    _auto_set_requester(args)코멘트 목록 조회"""
    try:
        task_id = args["task_id"]

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{CLICKUP_API_BASE}/task/{task_id}/comment",
                headers=_get_headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        comments = data.get("comments", [])
        result = [
            {
                "id": c.get("id"),
                "comment_text": c.get("comment_text"),
                "user": c.get("user", {}).get("username"),
                "date": c.get("date"),
            }
            for c in comments
        ]

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "count": len(result),
                    "comments": result,
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        logger.error(f"[CLICKUP] 코멘트 조회 실패: {e}")
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"코멘트 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "clickup_search_tasks",
    "워크스페이스에서 태스크를 검색합니다. username으로 특정 사용자의 태스크만 필터링할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "team_id": {
                "type": "string",
                "description": "워크스페이스(팀) ID",
            },
            "query": {
                "type": "string",
                "description": "검색어 (태스크 이름에서 검색)",
            },
            "username": {
                "type": "string",
                "description": "담당자 이름으로 필터 (Slack 사용자명 등, 부분 일치)",
            },
            "email": {
                "type": "string",
                "description": "담당자 이메일로 필터 (정확히 일치)",
            },
            "include_closed": {
                "type": "boolean",
                "description": "완료된 태스크 포함 여부 (기본값: false)",
            },
        },
        "required": ["team_id", "query"],
    },
)
async def clickup_search_tasks(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    _auto_set_requester(args)태스크 검색"""
    try:
        team_id = args["team_id"]
        query = args["query"]
        username = args.get("username", "")
        email = args.get("email", "")
        include_closed = args.get("include_closed", False)

        params = {
            "name": query,
            "include_closed": str(include_closed).lower(),
        }

        async with httpx.AsyncClient() as client:
            # username/email로 사용자 ID 찾기
            if username or email:
                user_id, matched_user = await _find_user_id(client, team_id, username, email)
                if user_id:
                    params["assignees[]"] = [user_id]
                else:
                    search_term = email or username
                    return {
                        "content": [{
                            "type": "text",
                            "text": json.dumps({
                                "success": False,
                                "error": True,
                                "message": f"ClickUp에서 '{search_term}' 사용자를 찾을 수 없습니다."
                            }, ensure_ascii=False, indent=2)
                        }],
                        "error": True
                    }

            resp = await client.get(
                f"{CLICKUP_API_BASE}/team/{team_id}/task",
                headers=_get_headers(),
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

        tasks = data.get("tasks", [])
        result = [
            {
                "id": t.get("id"),
                "name": t.get("name"),
                "status": t.get("status", {}).get("status"),
                "priority": t.get("priority", {}).get("priority") if t.get("priority") else None,
                "assignees": [a.get("username") or a.get("email") for a in t.get("assignees", [])],
                "due_date": t.get("due_date"),
                "url": t.get("url"),
                "list": {"id": t.get("list", {}).get("id"), "name": t.get("list", {}).get("name")},
            }
            for t in tasks
        ]

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "query": query,
                    "count": len(result),
                    "tasks": result,
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        logger.error(f"[CLICKUP] 태스크 검색 실패: {e}")
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"태스크 검색 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "clickup_add_tag",
    "ClickUp 태스크에 태그를 추가합니다.",
    {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "태스크 ID"},
            "tag_name": {"type": "string", "description": "추가할 태그 이름"}
        },
        "required": ["task_id", "tag_name"]
    }
)
async def clickup_add_tag(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        task_id = args["task_id"]
        tag_name = args["tag_name"]
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{CLICKUP_API_BASE}/task/{task_id}/tag/{tag_name}",
                headers=_get_headers(),
            )
            resp.raise_for_status()
        return {"content": [{"type": "text", "text": json.dumps({"success": True, "message": f"태그 '{tag_name}' 추가 완료"}, ensure_ascii=False)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": True, "message": f"태그 추가 실패: {str(e)}"}, ensure_ascii=False)}], "error": True}


@tool(
    "clickup_remove_tag",
    "ClickUp 태스크에서 태그를 제거합니다.",
    {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "태스크 ID"},
            "tag_name": {"type": "string", "description": "제거할 태그 이름"}
        },
        "required": ["task_id", "tag_name"]
    }
)
async def clickup_remove_tag(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        task_id = args["task_id"]
        tag_name = args["tag_name"]
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.delete(
                f"{CLICKUP_API_BASE}/task/{task_id}/tag/{tag_name}",
                headers=_get_headers(),
            )
            resp.raise_for_status()
        return {"content": [{"type": "text", "text": json.dumps({"success": True, "message": f"태그 '{tag_name}' 제거 완료"}, ensure_ascii=False)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": True, "message": f"태그 제거 실패: {str(e)}"}, ensure_ascii=False)}], "error": True}


@tool(
    "clickup_set_custom_field",
    "ClickUp 태스크의 커스텀 필드 값을 설정합니다. 스쿼드명, 우선순위 등 드롭다운/라벨 필드의 값을 변경할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "태스크 ID"},
            "field_id": {"type": "string", "description": "커스텀 필드 ID (get_task로 확인 가능)"},
            "value": {"description": "설정할 값. 드롭다운은 옵션 ID(숫자), 텍스트는 문자열, 체크박스는 true/false"},
            "field_name": {"type": "string", "description": "필드 이름 (field_id를 모를 때 이름으로 검색용)"}
        },
        "required": ["task_id"]
    }
)
async def clickup_set_custom_field(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        task_id = args["task_id"]
        field_id = args.get("field_id")
        field_name = args.get("field_name", "").lower()
        value = args.get("value")

        async with httpx.AsyncClient(timeout=30) as client:
            # field_id가 없으면 태스크에서 필드 목록 조회해서 이름으로 찾기
            if not field_id and field_name:
                task_resp = await client.get(
                    f"{CLICKUP_API_BASE}/task/{task_id}",
                    headers=_get_headers(),
                    params={"custom_task_ids": "false", "include_subtasks": "false"},
                )
                task_resp.raise_for_status()
                task_data = task_resp.json()
                for cf in task_data.get("custom_fields", []):
                    if field_name in (cf.get("name", "").lower()):
                        field_id = cf.get("id")
                        # 드롭다운(labels) 타입이면 value를 옵션 index로 변환
                        if cf.get("type") == "labels" and isinstance(value, str):
                            for opt in cf.get("type_config", {}).get("options", []):
                                if opt.get("label", "").lower() == value.lower():
                                    value = opt.get("id")
                                    break
                        # drop_down 타입
                        elif cf.get("type") == "drop_down" and isinstance(value, str):
                            for opt in cf.get("type_config", {}).get("options", []):
                                if opt.get("name", "").lower() == value.lower():
                                    value = opt.get("orderindex")
                                    break
                        break

            if not field_id:
                return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": True, "message": f"필드를 찾을 수 없습니다: {args.get('field_name', args.get('field_id', ''))}"}, ensure_ascii=False)}], "error": True}

            resp = await client.post(
                f"{CLICKUP_API_BASE}/task/{task_id}/field/{field_id}",
                headers=_get_headers(),
                json={"value": value},
            )
            resp.raise_for_status()

        return {"content": [{"type": "text", "text": json.dumps({"success": True, "message": f"커스텀 필드 업데이트 완료"}, ensure_ascii=False)}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps({"success": False, "error": True, "message": f"커스텀 필드 설정 실패: {str(e)}"}, ensure_ascii=False)}], "error": True}


# MCP Server
clickup_tools = [
    clickup_get_me,
    clickup_get_my_tasks,
    clickup_list_workspaces,
    clickup_list_spaces,
    clickup_list_folders,
    clickup_list_lists,
    clickup_list_tasks,
    clickup_get_task,
    clickup_create_task,
    clickup_update_task,
    clickup_delete_task,
    clickup_add_comment,
    clickup_get_comments,
    clickup_search_tasks,
    clickup_add_tag,
    clickup_remove_tag,
    clickup_set_custom_field,
]


def create_clickup_mcp_server():
    """Claude Code SDK ClickUp MCP server"""
    return create_sdk_mcp_server(
        name="clickup",
        version="1.0.0",
        tools=clickup_tools,
    )
