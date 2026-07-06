"""
CRM Tools for Claude Code SDK
MOCO가 CRM 시스템과 상호작용할 수 있는 도구
"""

import json
import logging
from typing import Any, Dict

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

logger = logging.getLogger(__name__)

CRM_BASE_URL = "https://127.0.0.1:8000"

# 한국어 → 영어 enum 매핑 (MOCO가 한국어로 값을 넣는 경우 대비)
_LEAD_STATUS_MAP = {
    "신규": "new", "새로운": "new", "new": "new",
    "연락함": "contacted", "연락중": "contacted", "접촉": "contacted", "contacted": "contacted",
    "적격": "qualified", "자격있음": "qualified", "qualified": "qualified",
    "부적격": "unqualified", "자격없음": "unqualified", "unqualified": "unqualified",
}
_LIFECYCLE_MAP = {
    "구독자": "subscriber", "subscriber": "subscriber",
    "리드": "lead", "lead": "lead",
    "mql": "mql", "sql": "sql",
    "기회": "opportunity", "opportunity": "opportunity",
    "고객": "customer", "customer": "customer",
    "전도사": "evangelist", "에반젤리스트": "evangelist", "evangelist": "evangelist",
}
_PRIORITY_MAP = {
    "낮음": "low", "low": "low",
    "보통": "medium", "medium": "medium",
    "높음": "high", "high": "high",
    "긴급": "high", "urgent": "high",
}
_TASK_STATUS_MAP = {
    "할일": "todo", "할 일": "todo", "todo": "todo",
    "진행중": "in_progress", "in_progress": "in_progress",
    "완료": "done", "done": "done",
}

def _normalize_enum(value, mapping):
    """한국어/영어 값을 영어 enum 값으로 정규화"""
    if not value:
        return value
    return mapping.get(value.lower().strip(), mapping.get(value.strip(), value))


async def _resolve_stage_name(pipeline_id, stage_value):
    """
    stage 값이 ID든 name이든 항상 name(한국어)으로 반환.
    파이프라인의 stages에서 id→name 매핑을 조회한다.
    """
    if not stage_value or not pipeline_id:
        return stage_value
    try:
        async with _client() as client:
            resp = await client.get(f"/api/crm/pipelines/{pipeline_id}")
            if resp.status_code == 200:
                data = resp.json()
                pipeline = data.get("data", data)
                for s in (pipeline.get("stages") or []):
                    # ID로 들어온 경우 → name 반환
                    if s.get("id") == stage_value:
                        return s.get("name", stage_value)
                    # name으로 들어온 경우 → 그대로
                    if s.get("name") == stage_value:
                        return stage_value
    except Exception:
        pass
    return stage_value


def _get_public_base_url() -> str:
    """CRM 서버의 공개 URL을 반환"""
    try:
        from app.config.settings import get_settings
        settings = get_settings()
        if settings.WEB_INTERFACE_URL:
            return settings.WEB_INTERFACE_URL.rstrip("/")
        # WEB_INTERFACE_URL이 없으면 실제 서버 IP 감지
        import socket
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        if local_ip and not local_ip.startswith("127."):
            return f"https://{local_ip}:8000"
    except Exception:
        pass
    return "https://127.0.0.1:8000"


def _error_response(message: str) -> Dict[str, Any]:
    """공통 에러 응답 헬퍼"""
    return {
        "content": [{
            "type": "text",
            "text": json.dumps({
                "success": False,
                "error": True,
                "message": message
            }, ensure_ascii=False, indent=2)
        }],
        "error": True
    }


def _success_response(data: Any) -> Dict[str, Any]:
    """공통 성공 응답 헬퍼"""
    return {
        "content": [{
            "type": "text",
            "text": json.dumps(data, ensure_ascii=False, indent=2)
        }]
    }


def _client():
    """공통 httpx 클라이언트"""
    return httpx.AsyncClient(base_url=CRM_BASE_URL, timeout=30.0, verify=False)


# ──────────────────────────────────────────────
# 1. crm_search_contacts
# ──────────────────────────────────────────────
@tool(
    "crm_search_contacts",
    "CRM에서 연락처를 검색합니다. 이름, 이메일, 회사명, 리드 상태로 검색할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "검색어 (이름, 이메일 등)"
            },
            "lead_status": {
                "type": "string",
                "description": "리드 상태 필터 (new, contacted, qualified, unqualified)"
            },
            "lifecycle_stage": {
                "type": "string",
                "description": "라이프사이클 단계 필터 (subscriber, lead, mql, sql, opportunity, customer)"
            },
            "limit": {
                "type": "number",
                "description": "최대 결과 수 (기본값: 20)"
            },
            "tag": {
                "type": "string",
                "description": "태그 필터 (예: '정형외과', 'VIP')"
            }
        },
        "required": ["query"]
    }
)
async def crm_search_contacts(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRM 연락처 검색"""
    try:
        async with _client() as client:
            params = {
                "search": args.get("query", ""),
                "page_size": args.get("limit", 20),
            }
            if args.get("lead_status"):
                params["lead_status"] = _normalize_enum(args["lead_status"], _LEAD_STATUS_MAP)
            if args.get("lifecycle_stage"):
                params["lifecycle_stage"] = _normalize_enum(args["lifecycle_stage"], _LIFECYCLE_MAP)
            if args.get("tag"):
                params["tag"] = args["tag"]

            resp = await client.get("/api/crm/contacts", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_search_contacts error: {e}")
        return _error_response(f"CRM 연락처 검색 실패: {str(e)}")


# ──────────────────────────────────────────────
# 2. crm_get_contact
# ──────────────────────────────────────────────
@tool(
    "crm_get_contact",
    "CRM 연락처의 상세 정보를 조회합니다. 최근 활동 내역, 연관된 딜 정보, 그리고 등록된 이메일 시퀀스(enrollments) 정보를 포함합니다.",
    {
        "type": "object",
        "properties": {
            "contact_id": {
                "type": "number",
                "description": "조회할 연락처 ID"
            }
        },
        "required": ["contact_id"]
    }
)
async def crm_get_contact(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRM 연락처 상세 조회"""
    try:
        contact_id = args["contact_id"]
        async with _client() as client:
            resp = await client.get(f"/api/crm/contacts/{contact_id}")
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_contact error: {e}")
        return _error_response(f"CRM 연락처 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 3. crm_create_contact
# ──────────────────────────────────────────────
@tool(
    "crm_create_contact",
    "CRM에 새 연락처를 생성합니다. company_id를 모르면 회사명으로 crm_search_contacts를 먼저 조회하세요.",
    {
        "type": "object",
        "properties": {
            "first_name": {
                "type": "string",
                "description": "이름 (성)"
            },
            "last_name": {
                "type": "string",
                "description": "이름 (이름)"
            },
            "email": {
                "type": "string",
                "description": "이메일 주소"
            },
            "phone": {
                "type": "string",
                "description": "전화번호"
            },
            "company_id": {
                "type": "number",
                "description": "연관 회사 ID"
            },
            "lead_status": {
                "type": "string",
                "description": "리드 상태 (new, contacted, qualified, unqualified)"
            },
            "lifecycle_stage": {
                "type": "string",
                "description": "라이프사이클 단계 (subscriber, lead, mql, sql, opportunity, customer)"
            },
            "source": {
                "type": "string",
                "description": "유입 경로 (예: web, referral, event, cold_call)"
            },
            "owner_slack_id": {
                "type": "string",
                "description": "담당자 Slack 사용자 ID"
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "태그 목록 (예: ['정형외과', '서울', 'VIP'])"
            },
            "custom_properties": {
                "type": "object",
                "description": "커스텀 속성 (예: {\"전문과목\": \"정형외과\", \"병원규모\": \"대형\"})"
            }
        },
        "required": ["first_name"]
    }
)
async def crm_create_contact(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRM 연락처 생성"""
    try:
        payload = {"first_name": args["first_name"]}
        for field in ["last_name", "email", "phone", "company_id", "source", "owner_slack_id", "tags", "custom_properties"]:
            if args.get(field) is not None:
                payload[field] = args[field]
        if args.get("lead_status"):
            payload["lead_status"] = _normalize_enum(args["lead_status"], _LEAD_STATUS_MAP)
        if args.get("lifecycle_stage"):
            payload["lifecycle_stage"] = _normalize_enum(args["lifecycle_stage"], _LIFECYCLE_MAP)

        async with _client() as client:
            resp = await client.post("/api/crm/contacts", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_create_contact error: {e}")
        return _error_response(f"CRM 연락처 생성 실패: {str(e)}")


# ──────────────────────────────────────────────
# 4. crm_update_contact
# ──────────────────────────────────────────────
@tool(
    "crm_update_contact",
    "CRM 연락처 정보를 업데이트합니다. 변경할 필드만 전달하면 됩니다.",
    {
        "type": "object",
        "properties": {
            "contact_id": {
                "type": "number",
                "description": "업데이트할 연락처 ID"
            },
            "first_name": {
                "type": "string",
                "description": "이름 (성)"
            },
            "last_name": {
                "type": "string",
                "description": "이름 (이름)"
            },
            "email": {
                "type": "string",
                "description": "이메일 주소"
            },
            "phone": {
                "type": "string",
                "description": "전화번호"
            },
            "company_id": {
                "type": "number",
                "description": "연관 회사 ID"
            },
            "lead_status": {
                "type": "string",
                "description": "리드 상태 (new, contacted, qualified, unqualified)"
            },
            "lifecycle_stage": {
                "type": "string",
                "description": "라이프사이클 단계 (subscriber, lead, mql, sql, opportunity, customer)"
            },
            "owner_slack_id": {
                "type": "string",
                "description": "담당자 Slack 사용자 ID"
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "태그 목록 (기존 태그를 완전히 대체합니다)"
            },
            "custom_properties": {
                "type": "object",
                "description": "커스텀 속성 (기존 속성을 완전히 대체합니다)"
            }
        },
        "required": ["contact_id"]
    }
)
async def crm_update_contact(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRM 연락처 업데이트"""
    try:
        contact_id = args["contact_id"]
        payload = {}
        for field in ["first_name", "last_name", "email", "phone", "company_id", "owner_slack_id", "tags", "custom_properties"]:
            if args.get(field) is not None:
                payload[field] = args[field]
        if args.get("lead_status"):
            payload["lead_status"] = _normalize_enum(args["lead_status"], _LEAD_STATUS_MAP)
        if args.get("lifecycle_stage"):
            payload["lifecycle_stage"] = _normalize_enum(args["lifecycle_stage"], _LIFECYCLE_MAP)

        if not payload:
            return _error_response("업데이트할 필드가 없습니다. 변경할 값을 하나 이상 전달해주세요.")

        async with _client() as client:
            resp = await client.put(f"/api/crm/contacts/{contact_id}", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_update_contact error: {e}")
        return _error_response(f"CRM 연락처 업데이트 실패: {str(e)}")


# ──────────────────────────────────────────────
# 5. crm_search_deals
# ──────────────────────────────────────────────
@tool(
    "crm_search_deals",
    "CRM에서 딜(거래)을 검색합니다. 이름, 단계, 파이프라인, 담당자로 필터링할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "검색어 (딜 이름 등)"
            },
            "stage": {
                "type": "string",
                "description": "딜 단계 필터"
            },
            "pipeline_id": {
                "type": "number",
                "description": "파이프라인 ID 필터"
            },
            "owner_slack_id": {
                "type": "string",
                "description": "담당자 Slack 사용자 ID 필터"
            }
        }
    }
)
async def crm_search_deals(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRM 딜 검색"""
    try:
        async with _client() as client:
            params = {}
            if args.get("query"):
                params["search"] = args["query"]
            if args.get("stage"):
                params["stage"] = args["stage"]
            if args.get("pipeline_id"):
                params["pipeline_id"] = args["pipeline_id"]
            if args.get("owner_slack_id"):
                params["owner"] = args["owner_slack_id"]

            resp = await client.get("/api/crm/deals", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_search_deals error: {e}")
        return _error_response(f"CRM 딜 검색 실패: {str(e)}")


# ──────────────────────────────────────────────
# 6. crm_create_deal
# ──────────────────────────────────────────────
@tool(
    "crm_create_deal",
    "CRM에 새 딜(거래)을 생성합니다. pipeline_id를 모르면 생략해도 됩니다(기본 파이프라인 자동 사용).",
    {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "딜 이름"
            },
            "amount": {
                "type": "number",
                "description": "딜 금액"
            },
            "contact_id": {
                "type": "number",
                "description": "연관 연락처 ID"
            },
            "company_id": {
                "type": "number",
                "description": "연관 회사 ID"
            },
            "pipeline_id": {
                "type": "number",
                "description": "파이프라인 ID (생략하면 기본 파이프라인)"
            },
            "stage": {
                "type": "string",
                "description": "딜 단계"
            },
            "owner_slack_id": {
                "type": "string",
                "description": "담당자 Slack 사용자 ID"
            },
            "close_date": {
                "type": "string",
                "description": "예상 종료일 (YYYY-MM-DD 형식)"
            }
        },
        "required": ["name", "amount"]
    }
)
async def crm_create_deal(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRM 딜 생성"""
    try:
        async with _client() as client:
            # pipeline_id가 없으면 기본 파이프라인 조회
            pipeline_id = args.get("pipeline_id")
            if not pipeline_id:
                pipe_resp = await client.get("/api/crm/pipelines")
                pipe_resp.raise_for_status()
                pipes = pipe_resp.json()
                pipe_data = pipes.get("data", pipes)
                if isinstance(pipe_data, list) and pipe_data:
                    # is_default가 True인 파이프라인 또는 첫 번째
                    default = next((p for p in pipe_data if p.get("is_default")), pipe_data[0])
                    pipeline_id = default["id"]
                elif isinstance(pipe_data, dict) and pipe_data.get("items"):
                    items = pipe_data["items"]
                    default = next((p for p in items if p.get("is_default")), items[0])
                    pipeline_id = default["id"]
                else:
                    return _error_response("기본 파이프라인을 찾을 수 없습니다. pipeline_id를 직접 전달해주세요.")

            payload = {
                "name": args["name"],
                "amount": args["amount"],
                "pipeline_id": pipeline_id,
            }
            # stage가 없으면 파이프라인 첫 번째 단계 사용 (ID→name 자동 변환)
            if args.get("stage"):
                payload["stage"] = await _resolve_stage_name(pipeline_id, args["stage"])
            else:
                # 기본 파이프라인의 첫 번째 스테이지 조회
                pipe_detail = await client.get(f"/api/crm/pipelines/{pipeline_id}")
                pipe_detail.raise_for_status()
                pd = pipe_detail.json()
                pd_data = pd.get("data", pd)
                stages = pd_data.get("stages", [])
                if stages:
                    first_stage = stages[0] if isinstance(stages[0], str) else stages[0].get("name", stages[0].get("id", ""))
                    payload["stage"] = first_stage

            for field in ["contact_id", "company_id", "owner_slack_id"]:
                if args.get(field) is not None:
                    payload[field] = args[field]
            if args.get("close_date"):
                payload["close_date"] = args["close_date"]

            resp = await client.post("/api/crm/deals", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_create_deal error: {e}")
        return _error_response(f"CRM 딜 생성 실패: {str(e)}")


# ──────────────────────────────────────────────
# 7. crm_update_deal_stage
# ──────────────────────────────────────────────
@tool(
    "crm_update_deal_stage",
    "CRM 딜의 단계를 변경합니다. 단계 변경 시 자동화가 트리거될 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "deal_id": {
                "type": "number",
                "description": "딜 ID"
            },
            "new_stage": {
                "type": "string",
                "description": "새로운 딜 단계"
            },
            "lost_reason": {
                "type": "string",
                "description": "실주 사유 (실주 단계로 변경 시)"
            }
        },
        "required": ["deal_id", "new_stage"]
    }
)
async def crm_update_deal_stage(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRM 딜 단계 변경"""
    try:
        deal_id = args["deal_id"]

        # 딜의 pipeline_id 조회 후 stage ID→name 변환
        resolved_stage = args["new_stage"]
        async with _client() as client:
            deal_resp = await client.get(f"/api/crm/deals/{deal_id}")
            if deal_resp.status_code == 200:
                deal_data = deal_resp.json()
                dd = deal_data.get("data", deal_data)
                pid = dd.get("pipeline_id")
                if pid:
                    resolved_stage = await _resolve_stage_name(pid, args["new_stage"])

            payload = {"stage": resolved_stage}
            if args.get("lost_reason"):
                payload["lost_reason"] = args["lost_reason"]

            resp = await client.put(f"/api/crm/deals/{deal_id}/stage", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_update_deal_stage error: {e}")
        return _error_response(f"CRM 딜 단계 변경 실패: {str(e)}")


# ──────────────────────────────────────────────
# 8. crm_log_activity
# ──────────────────────────────────────────────
@tool(
    "crm_log_activity",
    "CRM에 활동(통화, 이메일, 미팅, 메모)을 기록합니다. Sales Activity에 일정을 등록하려면 type='call', timestamp, user_slack_id, metadata를 포함하세요.",
    {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["call", "email", "meeting", "note"],
                "description": "활동 유형"
            },
            "subject": {
                "type": "string",
                "description": "활동 제목"
            },
            "body": {
                "type": "string",
                "description": "활동 상세 내용"
            },
            "contact_id": {
                "type": "number",
                "description": "연관 연락처 ID"
            },
            "deal_id": {
                "type": "number",
                "description": "연관 딜 ID"
            },
            "company_id": {
                "type": "number",
                "description": "연관 회사 ID"
            },
            "timestamp": {
                "type": "string",
                "description": "활동 일시 (예: 2026-04-14T09:00:00)"
            },
            "user_slack_id": {
                "type": "string",
                "description": "담당자 (예: Harry, Chloe)"
            },
            "metadata": {
                "type": "object",
                "description": "메타데이터 (call_objective, hospital, customer, product, done 등)"
            }
        },
        "required": ["type", "subject"]
    }
)
async def crm_log_activity(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRM 활동 기록"""
    try:
        payload = {
            "type": args["type"],
            "subject": args["subject"],
        }
        for f in ["body", "contact_id", "deal_id", "company_id", "timestamp", "user_slack_id", "metadata"]:
            if args.get(f) is not None:
                payload[f] = args[f]

        async with _client() as client:
            resp = await client.post("/api/crm/activities", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_log_activity error: {e}")
        return _error_response(f"CRM 활동 기록 실패: {str(e)}")


# ──────────────────────────────────────────────
# 9. crm_get_pipeline_summary
# ──────────────────────────────────────────────
@tool(
    "crm_get_pipeline_summary",
    "CRM 파이프라인의 전체 현황을 조회합니다. 각 단계별 딜 수와 총 금액을 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "pipeline_id": {
                "type": "number",
                "description": "파이프라인 ID (생략하면 기본 파이프라인)"
            }
        }
    }
)
async def crm_get_pipeline_summary(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRM 파이프라인 현황 조회"""
    try:
        async with _client() as client:
            params = {}
            if args.get("pipeline_id") is not None:
                params["pipeline_id"] = args["pipeline_id"]

            resp = await client.get("/api/crm/reports/pipeline", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_pipeline_summary error: {e}")
        return _error_response(f"CRM 파이프라인 현황 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 10. crm_create_task
# ──────────────────────────────────────────────
@tool(
    "crm_create_task",
    "CRM 태스크(할 일)를 생성합니다.",
    {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "태스크 제목"
            },
            "description": {
                "type": "string",
                "description": "태스크 상세 설명"
            },
            "due_date": {
                "type": "string",
                "description": "마감일 (YYYY-MM-DD 형식)"
            },
            "priority": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "우선순위 (기본값: medium)"
            },
            "contact_id": {
                "type": "number",
                "description": "연관 연락처 ID"
            },
            "deal_id": {
                "type": "number",
                "description": "연관 딜 ID"
            },
            "assigned_to_slack_id": {
                "type": "string",
                "description": "담당자 Slack 사용자 ID"
            }
        },
        "required": ["title"]
    }
)
async def crm_create_task(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRM 태스크 생성"""
    try:
        payload = {"title": args["title"]}
        if args.get("description"):
            payload["description"] = args["description"]
        if args.get("due_date"):
            payload["due_date"] = args["due_date"]
        if args.get("priority"):
            payload["priority"] = _normalize_enum(args["priority"], _PRIORITY_MAP)
        if args.get("contact_id") is not None:
            payload["contact_id"] = args["contact_id"]
        if args.get("deal_id") is not None:
            payload["deal_id"] = args["deal_id"]
        if args.get("assigned_to_slack_id"):
            payload["assigned_to_slack_id"] = args["assigned_to_slack_id"]

        async with _client() as client:
            resp = await client.post("/api/crm/tasks", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_create_task error: {e}")
        return _error_response(f"CRM 태스크 생성 실패: {str(e)}")


# ──────────────────────────────────────────────
# 11. crm_get_my_tasks
# ──────────────────────────────────────────────
@tool(
    "crm_get_my_tasks",
    "특정 사용자에게 할당된 CRM 태스크 목록을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "slack_user_id": {
                "type": "string",
                "description": "조회할 사용자의 Slack ID"
            },
            "status": {
                "type": "string",
                "enum": ["todo", "in_progress", "done"],
                "description": "태스크 상태 필터"
            }
        },
        "required": ["slack_user_id"]
    }
)
async def crm_get_my_tasks(args: Dict[str, Any]) -> Dict[str, Any]:
    """사용자의 CRM 태스크 조회"""
    try:
        async with _client() as client:
            params = {"slack_id": args["slack_user_id"]}
            if args.get("status"):
                params["status"] = _normalize_enum(args["status"], _TASK_STATUS_MAP)

            resp = await client.get("/api/crm/tasks/my", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_my_tasks error: {e}")
        return _error_response(f"CRM 태스크 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 12. crm_dashboard_summary
# ──────────────────────────────────────────────
@tool(
    "crm_dashboard_summary",
    "CRM 대시보드 요약 지표를 조회합니다. 총 연락처 수, 활성 딜 수, 파이프라인 총 금액, 전환율 등을 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {}
    }
)
async def crm_dashboard_summary(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRM 대시보드 요약"""
    try:
        async with _client() as client:
            resp = await client.get("/api/crm/reports/dashboard")
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_dashboard_summary error: {e}")
        return _error_response(f"CRM 대시보드 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 13. crm_enroll_sequence
# ──────────────────────────────────────────────
@tool(
    "crm_enroll_sequence",
    "연락처를 이메일 시퀀스에 등록합니다.",
    {
        "type": "object",
        "properties": {
            "sequence_id": {
                "type": "number",
                "description": "시퀀스 ID"
            },
            "contact_id": {
                "type": "number",
                "description": "등록할 연락처 ID"
            }
        },
        "required": ["sequence_id", "contact_id"]
    }
)
async def crm_enroll_sequence(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRM 시퀀스 등록"""
    try:
        sequence_id = args["sequence_id"]
        payload = {"contact_id": args["contact_id"]}

        async with _client() as client:
            resp = await client.post(
                f"/api/crm/emails/sequences/{sequence_id}/enroll",
                json=payload,
            )
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_enroll_sequence error: {e}")
        return _error_response(f"CRM 시퀀스 등록 실패: {str(e)}")


# ──────────────────────────────────────────────
# 14. crm_list_sequences
# ──────────────────────────────────────────────
@tool(
    "crm_list_sequences",
    "CRM 이메일 시퀀스 목록을 조회합니다. 시퀀스 이름, 상태, 단계 수를 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {}
    }
)
async def crm_list_sequences(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRM 이메일 시퀀스 목록 조회"""
    try:
        async with _client() as client:
            resp = await client.get("/api/crm/emails/sequences", params={"page_size": 50})
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_list_sequences error: {e}")
        return _error_response(f"CRM 시퀀스 목록 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 15. crm_get_sequence
# ──────────────────────────────────────────────
@tool(
    "crm_get_sequence",
    "CRM 이메일 시퀀스의 상세 정보와 등록 통계를 조회합니다.",
    {
        "type": "object",
        "properties": {
            "sequence_id": {
                "type": "number",
                "description": "시퀀스 ID"
            }
        },
        "required": ["sequence_id"]
    }
)
async def crm_get_sequence(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRM 이메일 시퀀스 상세 조회"""
    try:
        seq_id = args["sequence_id"]
        async with _client() as client:
            resp = await client.get(f"/api/crm/emails/sequences/{seq_id}")
            resp.raise_for_status()
            data = resp.json()
            # 통계도 같이 가져오기
            stats_resp = await client.get(f"/api/crm/emails/sequences/{seq_id}/stats")
            if stats_resp.status_code == 200:
                stats = stats_resp.json()
                data["stats"] = stats.get("data", stats)
            return _success_response(data)

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_sequence error: {e}")
        return _error_response(f"CRM 시퀀스 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 16. crm_list_automations
# ──────────────────────────────────────────────
@tool(
    "crm_list_automations",
    "CRM 자동화 워크플로우 목록을 조회합니다. 트리거 유형, 상태, 실행 횟수를 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {}
    }
)
async def crm_list_automations(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRM 자동화 목록 조회"""
    try:
        async with _client() as client:
            resp = await client.get("/api/crm/automations", params={"page_size": 50})
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_list_automations error: {e}")
        return _error_response(f"CRM 자동화 목록 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 17. crm_list_forms
# ──────────────────────────────────────────────
@tool(
    "crm_list_forms",
    "CRM 폼 목록을 조회합니다. 폼 이름, 필드 구성, 제출 건수를 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {}
    }
)
async def crm_list_forms(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRM 폼 목록 조회"""
    try:
        async with _client() as client:
            resp = await client.get("/api/crm/forms", params={"page_size": 50})
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_list_forms error: {e}")
        return _error_response(f"CRM 폼 목록 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 18. crm_list_segments
# ──────────────────────────────────────────────
@tool(
    "crm_list_segments",
    "CRM 세그먼트(스마트 리스트) 목록을 조회합니다. 필터 조건과 매칭 연락처 수를 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {}
    }
)
async def crm_list_segments(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRM 세그먼트 목록 조회"""
    try:
        async with _client() as client:
            resp = await client.get("/api/crm/segments", params={"page_size": 50})
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_list_segments error: {e}")
        return _error_response(f"CRM 세그먼트 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 19. crm_get_segment_contacts
# ──────────────────────────────────────────────
@tool(
    "crm_get_segment_contacts",
    "세그먼트에 해당하는 연락처 목록을 조회합니다. 세그먼트 필터 조건에 맞는 연락처를 반환합니다.",
    {
        "type": "object",
        "properties": {
            "segment_id": {
                "type": "number",
                "description": "세그먼트 ID"
            },
            "limit": {
                "type": "number",
                "description": "최대 결과 수 (기본값: 20)"
            }
        },
        "required": ["segment_id"]
    }
)
async def crm_get_segment_contacts(args: Dict[str, Any]) -> Dict[str, Any]:
    """세그먼트 연락처 조회"""
    try:
        seg_id = args["segment_id"]
        async with _client() as client:
            resp = await client.get(
                f"/api/crm/segments/{seg_id}/contacts",
                params={"page_size": args.get("limit", 20)}
            )
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_segment_contacts error: {e}")
        return _error_response(f"세그먼트 연락처 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 20. crm_list_activities
# ──────────────────────────────────────────────
@tool(
    "crm_list_activities",
    "CRM 활동 이력을 조회합니다. 연락처별, 딜별, 유형별 필터링이 가능합니다.",
    {
        "type": "object",
        "properties": {
            "contact_id": {
                "type": "number",
                "description": "연락처 ID로 필터"
            },
            "deal_id": {
                "type": "number",
                "description": "딜 ID로 필터"
            },
            "type": {
                "type": "string",
                "enum": ["call", "email", "meeting", "note"],
                "description": "활동 유형 필터"
            },
            "limit": {
                "type": "number",
                "description": "최대 결과 수 (기본값: 20)"
            }
        }
    }
)
async def crm_list_activities(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRM 활동 이력 조회"""
    try:
        async with _client() as client:
            params = {"page_size": args.get("limit", 20)}
            if args.get("contact_id"):
                params["contact_id"] = args["contact_id"]
            if args.get("deal_id"):
                params["deal_id"] = args["deal_id"]
            if args.get("type"):
                params["type"] = args["type"]

            resp = await client.get("/api/crm/activities", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_list_activities error: {e}")
        return _error_response(f"CRM 활동 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 21. crm_get_reports
# ──────────────────────────────────────────────
@tool(
    "crm_get_reports",
    "CRM 리포트를 조회합니다. 파이프라인 분석, 매출 예측, 영업 성과, 리드 소스, 활동 통계를 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "report_type": {
                "type": "string",
                "enum": ["pipeline", "revenue-forecast", "sales-performance", "lead-sources", "activities"],
                "description": "리포트 유형"
            }
        },
        "required": ["report_type"]
    }
)
async def crm_get_reports(args: Dict[str, Any]) -> Dict[str, Any]:
    """CRM 리포트 조회"""
    try:
        report_type = args["report_type"]
        async with _client() as client:
            resp = await client.get(f"/api/crm/reports/{report_type}")
            resp.raise_for_status()
            return _success_response(resp.json())

    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_reports error: {e}")
        return _error_response(f"CRM 리포트 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 22. crm_delete_contact
# ──────────────────────────────────────────────
@tool(
    "crm_delete_contact",
    "CRM 연락처를 삭제합니다.",
    {
        "type": "object",
        "properties": {
            "contact_id": {
                "type": "number",
                "description": "삭제할 연락처 ID"
            }
        },
        "required": ["contact_id"]
    }
)
async def crm_delete_contact(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.delete(f"/api/crm/contacts/{args['contact_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_delete_contact error: {e}")
        return _error_response(f"CRM 연락처 삭제 실패: {str(e)}")


# ──────────────────────────────────────────────
# 23. crm_contact_timeline
# ──────────────────────────────────────────────
@tool(
    "crm_contact_timeline",
    "연락처의 활동 타임라인을 조회합니다. 통화, 이메일, 미팅, 메모 등 모든 활동 이력을 시간순으로 확인합니다.",
    {
        "type": "object",
        "properties": {
            "contact_id": {
                "type": "number",
                "description": "연락처 ID"
            },
            "limit": {
                "type": "number",
                "description": "최대 결과 수 (기본값: 20)"
            }
        },
        "required": ["contact_id"]
    }
)
async def crm_contact_timeline(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get(
                f"/api/crm/contacts/{args['contact_id']}/timeline",
                params={"page_size": args.get("limit", 20)}
            )
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_contact_timeline error: {e}")
        return _error_response(f"타임라인 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 24. crm_recalculate_lead_score
# ──────────────────────────────────────────────
@tool(
    "crm_recalculate_lead_score",
    "연락처의 리드 점수를 재계산합니다. 활동 이력, 딜 상태 등을 기반으로 자동 산출됩니다.",
    {
        "type": "object",
        "properties": {
            "contact_id": {
                "type": "number",
                "description": "연락처 ID"
            }
        },
        "required": ["contact_id"]
    }
)
async def crm_recalculate_lead_score(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.post(f"/api/crm/contacts/{args['contact_id']}/score")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_recalculate_lead_score error: {e}")
        return _error_response(f"리드 점수 재계산 실패: {str(e)}")


# ──────────────────────────────────────────────
# 25. crm_list_companies
# ──────────────────────────────────────────────
@tool(
    "crm_list_companies",
    "CRM 회사 목록을 검색합니다. 이름, 산업군, 도시 등으로 필터링 가능합니다.",
    {
        "type": "object",
        "properties": {
            "search": {
                "type": "string",
                "description": "검색어 (회사명, 도메인 등)"
            },
            "industry": {
                "type": "string",
                "description": "산업군 필터"
            },
            "limit": {
                "type": "number",
                "description": "최대 결과 수 (기본값: 20)"
            }
        }
    }
)
async def crm_list_companies(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            params = {"page_size": args.get("limit", 20)}
            if args.get("search"):
                params["search"] = args["search"]
            if args.get("industry"):
                params["industry"] = args["industry"]
            resp = await client.get("/api/crm/companies", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_list_companies error: {e}")
        return _error_response(f"회사 목록 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 26. crm_get_company
# ──────────────────────────────────────────────
@tool(
    "crm_get_company",
    "CRM 회사의 상세 정보를 조회합니다. 연관 연락처와 딜 정보를 포함합니다.",
    {
        "type": "object",
        "properties": {
            "company_id": {
                "type": "number",
                "description": "회사 ID"
            }
        },
        "required": ["company_id"]
    }
)
async def crm_get_company(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get(f"/api/crm/companies/{args['company_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_company error: {e}")
        return _error_response(f"회사 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 27. crm_create_company
# ──────────────────────────────────────────────
@tool(
    "crm_create_company",
    "CRM에 새 회사를 등록합니다.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "회사명 (필수)"},
            "domain": {"type": "string", "description": "회사 도메인 (예: acme.com)"},
            "industry": {"type": "string", "description": "산업군"},
            "employee_count": {"type": "number", "description": "직원 수"},
            "annual_revenue": {"type": "number", "description": "연 매출"},
            "phone": {"type": "string", "description": "전화번호"},
            "address": {"type": "string", "description": "주소"},
            "city": {"type": "string", "description": "도시"},
            "country": {"type": "string", "description": "국가"},
            "custom_properties": {"type": "object", "description": "커스텀 속성"}
        },
        "required": ["name"]
    }
)
async def crm_create_company(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = {}
        for f in ["name", "domain", "industry", "employee_count", "annual_revenue",
                   "phone", "address", "city", "country", "custom_properties"]:
            if args.get(f) is not None:
                payload[f] = args[f]
        async with _client() as client:
            resp = await client.post("/api/crm/companies", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_create_company error: {e}")
        return _error_response(f"회사 생성 실패: {str(e)}")


# ──────────────────────────────────────────────
# 28. crm_update_company
# ──────────────────────────────────────────────
@tool(
    "crm_update_company",
    "CRM 회사 정보를 수정합니다. 변경할 필드만 전달하면 됩니다.",
    {
        "type": "object",
        "properties": {
            "company_id": {"type": "number", "description": "회사 ID"},
            "name": {"type": "string", "description": "회사명"},
            "domain": {"type": "string", "description": "회사 도메인"},
            "industry": {"type": "string", "description": "산업군"},
            "employee_count": {"type": "number", "description": "직원 수"},
            "annual_revenue": {"type": "number", "description": "연 매출"},
            "phone": {"type": "string", "description": "전화번호"},
            "address": {"type": "string", "description": "주소"},
            "city": {"type": "string", "description": "도시"},
            "country": {"type": "string", "description": "국가"},
            "custom_properties": {"type": "object", "description": "커스텀 속성"}
        },
        "required": ["company_id"]
    }
)
async def crm_update_company(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        cid = args["company_id"]
        payload = {}
        for f in ["name", "domain", "industry", "employee_count", "annual_revenue",
                   "phone", "address", "city", "country", "custom_properties"]:
            if args.get(f) is not None:
                payload[f] = args[f]
        if not payload:
            return _error_response("업데이트할 필드가 없습니다.")
        async with _client() as client:
            resp = await client.put(f"/api/crm/companies/{cid}", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_update_company error: {e}")
        return _error_response(f"회사 수정 실패: {str(e)}")


# ──────────────────────────────────────────────
# 29. crm_delete_company
# ──────────────────────────────────────────────
@tool(
    "crm_delete_company",
    "CRM 회사를 삭제합니다. 연관된 연락처와 딜도 함께 삭제될 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "company_id": {"type": "number", "description": "삭제할 회사 ID"}
        },
        "required": ["company_id"]
    }
)
async def crm_delete_company(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.delete(f"/api/crm/companies/{args['company_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_delete_company error: {e}")
        return _error_response(f"회사 삭제 실패: {str(e)}")


# ──────────────────────────────────────────────
# 30. crm_get_company_contacts
# ──────────────────────────────────────────────
@tool(
    "crm_get_company_contacts",
    "특정 회사에 소속된 연락처 목록을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "company_id": {"type": "number", "description": "회사 ID"}
        },
        "required": ["company_id"]
    }
)
async def crm_get_company_contacts(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get(f"/api/crm/companies/{args['company_id']}/contacts")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_company_contacts error: {e}")
        return _error_response(f"회사 연락처 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 31. crm_get_company_deals
# ──────────────────────────────────────────────
@tool(
    "crm_get_company_deals",
    "특정 회사에 연관된 딜(거래) 목록을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "company_id": {"type": "number", "description": "회사 ID"}
        },
        "required": ["company_id"]
    }
)
async def crm_get_company_deals(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get(f"/api/crm/companies/{args['company_id']}/deals")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_company_deals error: {e}")
        return _error_response(f"회사 딜 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 32. crm_get_deal
# ──────────────────────────────────────────────
@tool(
    "crm_get_deal",
    "CRM 딜(거래) 상세 정보를 조회합니다. 연관 연락처, 회사, 활동 포함.",
    {
        "type": "object",
        "properties": {
            "deal_id": {"type": "number", "description": "딜 ID"}
        },
        "required": ["deal_id"]
    }
)
async def crm_get_deal(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get(f"/api/crm/deals/{args['deal_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_deal error: {e}")
        return _error_response(f"딜 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 33. crm_update_deal
# ──────────────────────────────────────────────
@tool(
    "crm_update_deal",
    "CRM 딜(거래) 정보를 수정합니다. 이름, 금액, 담당자, 종료일 등 변경할 필드만 전달하면 됩니다.",
    {
        "type": "object",
        "properties": {
            "deal_id": {"type": "number", "description": "딜 ID"},
            "name": {"type": "string", "description": "딜 이름"},
            "amount": {"type": "number", "description": "금액"},
            "stage": {"type": "string", "description": "딜 단계"},
            "pipeline_id": {"type": "number", "description": "파이프라인 ID"},
            "contact_id": {"type": "number", "description": "연락처 ID"},
            "company_id": {"type": "number", "description": "회사 ID"},
            "owner_slack_id": {"type": "string", "description": "담당자 Slack ID"},
            "close_date": {"type": "string", "description": "예상 종료일 (YYYY-MM-DD)"},
            "probability": {"type": "number", "description": "성공 확률 (0~100)"},
            "lost_reason": {"type": "string", "description": "실주 사유"},
            "custom_properties": {"type": "object", "description": "커스텀 속성"}
        },
        "required": ["deal_id"]
    }
)
async def crm_update_deal(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        did = args["deal_id"]
        payload = {}
        for f in ["name", "amount", "stage", "pipeline_id", "contact_id", "company_id",
                   "owner_slack_id", "close_date", "probability", "lost_reason", "custom_properties"]:
            if args.get(f) is not None:
                payload[f] = args[f]
        if not payload:
            return _error_response("업데이트할 필드가 없습니다.")
        async with _client() as client:
            resp = await client.put(f"/api/crm/deals/{did}", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_update_deal error: {e}")
        return _error_response(f"딜 수정 실패: {str(e)}")


# ──────────────────────────────────────────────
# 34. crm_delete_deal
# ──────────────────────────────────────────────
@tool(
    "crm_delete_deal",
    "CRM 딜(거래)을 삭제합니다.",
    {
        "type": "object",
        "properties": {
            "deal_id": {"type": "number", "description": "삭제할 딜 ID"}
        },
        "required": ["deal_id"]
    }
)
async def crm_delete_deal(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.delete(f"/api/crm/deals/{args['deal_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_delete_deal error: {e}")
        return _error_response(f"딜 삭제 실패: {str(e)}")


# ──────────────────────────────────────────────
# 35. crm_list_pipelines
# ──────────────────────────────────────────────
@tool(
    "crm_list_pipelines",
    "CRM 파이프라인 목록을 조회합니다.",
    {
        "type": "object",
        "properties": {}
    }
)
async def crm_list_pipelines(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get("/api/crm/pipelines")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_list_pipelines error: {e}")
        return _error_response(f"파이프라인 목록 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 36. crm_create_pipeline
# ──────────────────────────────────────────────
@tool(
    "crm_create_pipeline",
    "CRM 파이프라인을 새로 생성합니다. 단계(stages)를 정의해야 합니다.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "파이프라인 이름"},
            "stages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "단계 ID (영문)"},
                        "name": {"type": "string", "description": "단계 이름"},
                        "probability": {"type": "number", "description": "성공 확률 (0~100)"},
                        "order": {"type": "number", "description": "순서"}
                    },
                    "required": ["id", "name"]
                },
                "description": "파이프라인 단계 목록"
            },
            "is_default": {"type": "boolean", "description": "기본 파이프라인 여부"}
        },
        "required": ["name", "stages"]
    }
)
async def crm_create_pipeline(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = {
            "name": args["name"],
            "stages": args["stages"],
        }
        if args.get("is_default") is not None:
            payload["is_default"] = args["is_default"]
        async with _client() as client:
            resp = await client.post("/api/crm/pipelines", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_create_pipeline error: {e}")
        return _error_response(f"파이프라인 생성 실패: {str(e)}")


# ──────────────────────────────────────────────
# 37. crm_update_pipeline
# ──────────────────────────────────────────────
@tool(
    "crm_update_pipeline",
    "CRM 파이프라인을 수정합니다. 이름이나 단계 구성을 변경할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "pipeline_id": {"type": "number", "description": "파이프라인 ID"},
            "name": {"type": "string", "description": "파이프라인 이름"},
            "stages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "probability": {"type": "number"},
                        "order": {"type": "number"}
                    },
                    "required": ["id", "name"]
                },
                "description": "파이프라인 단계 목록"
            },
            "is_default": {"type": "boolean", "description": "기본 파이프라인 여부"}
        },
        "required": ["pipeline_id"]
    }
)
async def crm_update_pipeline(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        pid = args["pipeline_id"]
        payload = {}
        for f in ["name", "stages", "is_default"]:
            if args.get(f) is not None:
                payload[f] = args[f]
        if not payload:
            return _error_response("업데이트할 필드가 없습니다.")
        async with _client() as client:
            resp = await client.put(f"/api/crm/pipelines/{pid}", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_update_pipeline error: {e}")
        return _error_response(f"파이프라인 수정 실패: {str(e)}")


# ──────────────────────────────────────────────
# 38. crm_delete_pipeline
# ──────────────────────────────────────────────
@tool(
    "crm_delete_pipeline",
    "CRM 파이프라인을 삭제합니다. 기본 파이프라인은 삭제할 수 없습니다.",
    {
        "type": "object",
        "properties": {
            "pipeline_id": {"type": "number", "description": "삭제할 파이프라인 ID"}
        },
        "required": ["pipeline_id"]
    }
)
async def crm_delete_pipeline(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.delete(f"/api/crm/pipelines/{args['pipeline_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_delete_pipeline error: {e}")
        return _error_response(f"파이프라인 삭제 실패: {str(e)}")


# ──────────────────────────────────────────────
# 39. crm_create_sequence
# ──────────────────────────────────────────────
@tool(
    "crm_create_sequence",
    "CRM 이메일 시퀀스를 생성합니다. 단계별 이메일 템플릿과 발송 간격을 설정합니다.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "시퀀스 이름"},
            "description": {"type": "string", "description": "시퀀스 설명"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step_number": {"type": "number", "description": "단계 번호 (1부터)"},
                        "delay_days": {"type": "number", "description": "이전 단계로부터 대기 일수"},
                        "subject_template": {"type": "string", "description": "이메일 제목 템플릿"},
                        "body_template": {"type": "string", "description": "이메일 본문 템플릿"}
                    },
                    "required": ["step_number", "delay_days", "subject_template", "body_template"]
                },
                "description": "시퀀스 단계 목록"
            },
            "status": {"type": "string", "enum": ["active", "paused"], "description": "상태 (기본: active)"}
        },
        "required": ["name"]
    }
)
async def crm_create_sequence(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = {"name": args["name"]}
        for f in ["description", "steps", "status"]:
            if args.get(f) is not None:
                payload[f] = args[f]
        async with _client() as client:
            resp = await client.post("/api/crm/emails/sequences", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_create_sequence error: {e}")
        return _error_response(f"시퀀스 생성 실패: {str(e)}")


# ──────────────────────────────────────────────
# 40. crm_update_sequence
# ──────────────────────────────────────────────
@tool(
    "crm_update_sequence",
    "CRM 이메일 시퀀스를 수정합니다. 이름, 단계, 상태 등을 변경할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "sequence_id": {"type": "number", "description": "시퀀스 ID"},
            "name": {"type": "string", "description": "시퀀스 이름"},
            "description": {"type": "string", "description": "시퀀스 설명"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step_number": {"type": "number"},
                        "delay_days": {"type": "number"},
                        "subject_template": {"type": "string"},
                        "body_template": {"type": "string"}
                    },
                    "required": ["step_number", "delay_days", "subject_template", "body_template"]
                },
                "description": "시퀀스 단계 목록 (전체 교체)"
            },
            "status": {"type": "string", "enum": ["active", "paused"], "description": "상태"}
        },
        "required": ["sequence_id"]
    }
)
async def crm_update_sequence(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        sid = args["sequence_id"]
        payload = {}
        for f in ["name", "description", "steps", "status"]:
            if args.get(f) is not None:
                payload[f] = args[f]
        if not payload:
            return _error_response("업데이트할 필드가 없습니다.")
        async with _client() as client:
            resp = await client.put(f"/api/crm/emails/sequences/{sid}", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_update_sequence error: {e}")
        return _error_response(f"시퀀스 수정 실패: {str(e)}")


# ──────────────────────────────────────────────
# 41. crm_delete_sequence
# ──────────────────────────────────────────────
@tool(
    "crm_delete_sequence",
    "CRM 이메일 시퀀스를 삭제합니다.",
    {
        "type": "object",
        "properties": {
            "sequence_id": {"type": "number", "description": "삭제할 시퀀스 ID"}
        },
        "required": ["sequence_id"]
    }
)
async def crm_delete_sequence(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.delete(f"/api/crm/emails/sequences/{args['sequence_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_delete_sequence error: {e}")
        return _error_response(f"시퀀스 삭제 실패: {str(e)}")


# ──────────────────────────────────────────────
# 42. crm_pause_sequence
# ──────────────────────────────────────────────
@tool(
    "crm_pause_sequence",
    "CRM 이메일 시퀀스의 등록(enrollment)을 일시정지합니다.",
    {
        "type": "object",
        "properties": {
            "sequence_id": {"type": "number", "description": "시퀀스 ID"},
            "contact_id": {"type": "number", "description": "연락처 ID (특정 연락처만 정지)"}
        },
        "required": ["sequence_id"]
    }
)
async def crm_pause_sequence(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        sid = args["sequence_id"]
        payload = {}
        if args.get("contact_id"):
            payload["contact_id"] = args["contact_id"]
        async with _client() as client:
            resp = await client.post(f"/api/crm/emails/sequences/{sid}/pause", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_pause_sequence error: {e}")
        return _error_response(f"시퀀스 일시정지 실패: {str(e)}")


# ──────────────────────────────────────────────
# 42-1. crm_sequence_enrollments
# ──────────────────────────────────────────────
@tool(
    "crm_sequence_enrollments",
    "이메일 시퀀스에 등록된 연락처 목록을 조회합니다. 이름, 이메일, 진행 단계, 상태를 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "sequence_id": {"type": "number", "description": "시퀀스 ID"},
            "status": {"type": "string", "enum": ["active", "completed", "paused", "bounced"], "description": "등록 상태 필터"},
            "limit": {"type": "number", "description": "최대 결과 수 (기본값: 50)"}
        },
        "required": ["sequence_id"]
    }
)
async def crm_sequence_enrollments(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        sid = args["sequence_id"]
        async with _client() as client:
            params = {"page_size": args.get("limit", 50)}
            if args.get("status"):
                params["status"] = args["status"]
            resp = await client.get(f"/api/crm/emails/sequences/{sid}/enrollments", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_sequence_enrollments error: {e}")
        return _error_response(f"시퀀스 등록자 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 42-2. crm_sequence_dashboard
# ──────────────────────────────────────────────
@tool(
    "crm_sequence_dashboard",
    "모든 이메일 시퀀스의 현황을 한 번에 조회합니다. 시퀀스별 등록자 수, 활성/완료/일시정지 현황을 대시보드 형태로 보여줍니다.",
    {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["active", "paused", "archived"],
                "description": "시퀀스 상태 필터 (선택사항)"
            }
        }
    }
)
async def crm_sequence_dashboard(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        params = {}
        if args.get("status"):
            params["status"] = args["status"]
        async with _client() as client:
            resp = await client.get("/api/crm/emails/sequences/dashboard/overview", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_sequence_dashboard error: {e}")
        return _error_response(f"시퀀스 대시보드 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 42-3. crm_bulk_enroll_sequence
# ──────────────────────────────────────────────
@tool(
    "crm_bulk_enroll_sequence",
    "여러 연락처를 이메일 시퀀스에 일괄 등록합니다. contact_ids를 직접 지정하거나, segment_id로 세그먼트에 해당하는 연락처를 한 번에 등록할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "sequence_id": {
                "type": "number",
                "description": "등록할 시퀀스 ID"
            },
            "contact_ids": {
                "type": "array",
                "items": {"type": "number"},
                "description": "등록할 연락처 ID 목록 (contact_ids 또는 segment_id 중 하나 필수)"
            },
            "segment_id": {
                "type": "number",
                "description": "세그먼트 ID - 해당 세그먼트의 모든 연락처를 등록 (contact_ids 또는 segment_id 중 하나 필수)"
            }
        },
        "required": ["sequence_id"]
    }
)
async def crm_bulk_enroll_sequence(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        seq_id = args["sequence_id"]
        payload = {}
        if args.get("contact_ids"):
            payload["contact_ids"] = args["contact_ids"]
        if args.get("segment_id"):
            payload["segment_id"] = args["segment_id"]

        async with _client() as client:
            resp = await client.post(
                f"/api/crm/emails/sequences/{seq_id}/enroll-bulk",
                json=payload,
            )
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_bulk_enroll_sequence error: {e}")
        return _error_response(f"시퀀스 벌크 등록 실패: {str(e)}")


# ──────────────────────────────────────────────
# 43. crm_create_automation
# ──────────────────────────────────────────────
@tool(
    "crm_create_automation",
    "CRM 자동화 워크플로우를 생성합니다. 트리거 조건과 실행할 액션을 설정합니다.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "자동화 이름"},
            "description": {"type": "string", "description": "자동화 설명"},
            "trigger_type": {
                "type": "string",
                "enum": ["deal_stage_change", "contact_created", "lead_score_threshold",
                         "form_submission", "email_opened", "tag_added", "manual"],
                "description": "트리거 유형"
            },
            "trigger_config": {
                "type": "object",
                "description": "트리거 설정 (예: {\"stage\": \"won\"}, {\"threshold\": 80}, {\"form_id\": 1})"
            },
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["send_email", "create_task", "update_property",
                                     "notify_slack", "enroll_sequence", "change_stage",
                                     "update_lead_score", "add_tag"],
                            "description": "액션 유형"
                        },
                        "config": {"type": "object", "description": "액션 설정"}
                    },
                    "required": ["type"]
                },
                "description": "실행할 액션 목록"
            },
            "status": {"type": "string", "enum": ["active", "paused"], "description": "상태 (기본: active)"}
        },
        "required": ["name", "trigger_type"]
    }
)
async def crm_create_automation(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = {"name": args["name"], "trigger_type": args["trigger_type"]}
        for f in ["description", "trigger_config", "actions", "status"]:
            if args.get(f) is not None:
                payload[f] = args[f]
        async with _client() as client:
            resp = await client.post("/api/crm/automations", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_create_automation error: {e}")
        return _error_response(f"자동화 생성 실패: {str(e)}")


# ──────────────────────────────────────────────
# 44. crm_get_automation
# ──────────────────────────────────────────────
@tool(
    "crm_get_automation",
    "CRM 자동화 워크플로우의 상세 정보를 조회합니다.",
    {
        "type": "object",
        "properties": {
            "automation_id": {"type": "number", "description": "자동화 ID"}
        },
        "required": ["automation_id"]
    }
)
async def crm_get_automation(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get(f"/api/crm/automations/{args['automation_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_automation error: {e}")
        return _error_response(f"자동화 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 45. crm_update_automation
# ──────────────────────────────────────────────
@tool(
    "crm_update_automation",
    "CRM 자동화 워크플로우를 수정합니다. 이름, 트리거, 액션, 상태 등을 변경할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "automation_id": {"type": "number", "description": "자동화 ID"},
            "name": {"type": "string", "description": "자동화 이름"},
            "description": {"type": "string", "description": "설명"},
            "trigger_type": {"type": "string", "description": "트리거 유형"},
            "trigger_config": {"type": "object", "description": "트리거 설정"},
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "config": {"type": "object"}
                    },
                    "required": ["type"]
                },
                "description": "액션 목록 (전체 교체)"
            },
            "status": {"type": "string", "enum": ["active", "paused"], "description": "상태"}
        },
        "required": ["automation_id"]
    }
)
async def crm_update_automation(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        aid = args["automation_id"]
        payload = {}
        for f in ["name", "description", "trigger_type", "trigger_config", "actions", "status"]:
            if args.get(f) is not None:
                payload[f] = args[f]
        if not payload:
            return _error_response("업데이트할 필드가 없습니다.")
        async with _client() as client:
            resp = await client.put(f"/api/crm/automations/{aid}", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_update_automation error: {e}")
        return _error_response(f"자동화 수정 실패: {str(e)}")


# ──────────────────────────────────────────────
# 46. crm_delete_automation
# ──────────────────────────────────────────────
@tool(
    "crm_delete_automation",
    "CRM 자동화 워크플로우를 삭제합니다.",
    {
        "type": "object",
        "properties": {
            "automation_id": {"type": "number", "description": "삭제할 자동화 ID"}
        },
        "required": ["automation_id"]
    }
)
async def crm_delete_automation(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.delete(f"/api/crm/automations/{args['automation_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_delete_automation error: {e}")
        return _error_response(f"자동화 삭제 실패: {str(e)}")


# ──────────────────────────────────────────────
# 47. crm_execute_automation
# ──────────────────────────────────────────────
@tool(
    "crm_execute_automation",
    "CRM 자동화를 수동으로 실행합니다. 특정 연락처나 딜에 대해 자동화 액션을 즉시 실행합니다.",
    {
        "type": "object",
        "properties": {
            "automation_id": {"type": "number", "description": "자동화 ID"},
            "contact_id": {"type": "number", "description": "대상 연락처 ID"},
            "deal_id": {"type": "number", "description": "대상 딜 ID"}
        },
        "required": ["automation_id"]
    }
)
async def crm_execute_automation(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        aid = args["automation_id"]
        payload = {}
        if args.get("contact_id"):
            payload["contact_id"] = args["contact_id"]
        if args.get("deal_id"):
            payload["deal_id"] = args["deal_id"]
        async with _client() as client:
            resp = await client.post(f"/api/crm/automations/{aid}/execute", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_execute_automation error: {e}")
        return _error_response(f"자동화 실행 실패: {str(e)}")


# ──────────────────────────────────────────────
# 48. crm_automation_history
# ──────────────────────────────────────────────
@tool(
    "crm_automation_history",
    "CRM 자동화의 실행 이력을 조회합니다. 성공/실패 여부, 실행 시간, 결과를 확인합니다.",
    {
        "type": "object",
        "properties": {
            "automation_id": {"type": "number", "description": "자동화 ID"},
            "limit": {"type": "number", "description": "최대 결과 수 (기본값: 20)"}
        },
        "required": ["automation_id"]
    }
)
async def crm_automation_history(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        aid = args["automation_id"]
        async with _client() as client:
            resp = await client.get(
                f"/api/crm/automations/{aid}/history",
                params={"page_size": args.get("limit", 20)}
            )
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_automation_history error: {e}")
        return _error_response(f"자동화 이력 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 49. crm_list_tasks
# ──────────────────────────────────────────────
@tool(
    "crm_list_tasks",
    "CRM 태스크 목록을 조회합니다. 상태, 우선순위, 담당자로 필터링할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["todo", "in_progress", "done"], "description": "상태 필터"},
            "priority": {"type": "string", "enum": ["low", "medium", "high"], "description": "우선순위 필터"},
            "contact_id": {"type": "number", "description": "연락처 ID 필터"},
            "deal_id": {"type": "number", "description": "딜 ID 필터"},
            "limit": {"type": "number", "description": "최대 결과 수 (기본값: 20)"}
        }
    }
)
async def crm_list_tasks(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            params = {"page_size": args.get("limit", 20)}
            if args.get("status"):
                params["status"] = _normalize_enum(args["status"], _TASK_STATUS_MAP)
            if args.get("priority"):
                params["priority"] = _normalize_enum(args["priority"], _PRIORITY_MAP)
            if args.get("contact_id"):
                params["contact_id"] = args["contact_id"]
            if args.get("deal_id"):
                params["deal_id"] = args["deal_id"]
            resp = await client.get("/api/crm/tasks", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_list_tasks error: {e}")
        return _error_response(f"태스크 목록 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 50. crm_get_task
# ──────────────────────────────────────────────
@tool(
    "crm_get_task",
    "CRM 태스크의 상세 정보를 조회합니다.",
    {
        "type": "object",
        "properties": {
            "task_id": {"type": "number", "description": "태스크 ID"}
        },
        "required": ["task_id"]
    }
)
async def crm_get_task(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get(f"/api/crm/tasks/{args['task_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_task error: {e}")
        return _error_response(f"태스크 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 51. crm_update_task
# ──────────────────────────────────────────────
@tool(
    "crm_update_task",
    "CRM 태스크를 수정합니다. 제목, 설명, 상태, 우선순위, 마감일 등을 변경할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "task_id": {"type": "number", "description": "태스크 ID"},
            "title": {"type": "string", "description": "제목"},
            "description": {"type": "string", "description": "설명"},
            "due_date": {"type": "string", "description": "마감일 (YYYY-MM-DD)"},
            "status": {"type": "string", "enum": ["todo", "in_progress", "done"], "description": "상태"},
            "priority": {"type": "string", "enum": ["low", "medium", "high"], "description": "우선순위"},
            "contact_id": {"type": "number", "description": "연락처 ID"},
            "deal_id": {"type": "number", "description": "딜 ID"},
            "assigned_to_slack_id": {"type": "string", "description": "담당자 Slack ID"}
        },
        "required": ["task_id"]
    }
)
async def crm_update_task(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        tid = args["task_id"]
        payload = {}
        for f in ["title", "description", "due_date", "contact_id", "deal_id", "assigned_to_slack_id"]:
            if args.get(f) is not None:
                payload[f] = args[f]
        if args.get("status"):
            payload["status"] = _normalize_enum(args["status"], _TASK_STATUS_MAP)
        if args.get("priority"):
            payload["priority"] = _normalize_enum(args["priority"], _PRIORITY_MAP)
        if not payload:
            return _error_response("업데이트할 필드가 없습니다.")
        async with _client() as client:
            resp = await client.put(f"/api/crm/tasks/{tid}", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_update_task error: {e}")
        return _error_response(f"태스크 수정 실패: {str(e)}")


# ──────────────────────────────────────────────
# 52. crm_complete_task
# ──────────────────────────────────────────────
@tool(
    "crm_complete_task",
    "CRM 태스크를 완료 처리합니다.",
    {
        "type": "object",
        "properties": {
            "task_id": {"type": "number", "description": "완료할 태스크 ID"}
        },
        "required": ["task_id"]
    }
)
async def crm_complete_task(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.put(f"/api/crm/tasks/{args['task_id']}/complete")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_complete_task error: {e}")
        return _error_response(f"태스크 완료 실패: {str(e)}")


# ──────────────────────────────────────────────
# 53. crm_delete_task
# ──────────────────────────────────────────────
@tool(
    "crm_delete_task",
    "CRM 태스크를 삭제합니다.",
    {
        "type": "object",
        "properties": {
            "task_id": {"type": "number", "description": "삭제할 태스크 ID"}
        },
        "required": ["task_id"]
    }
)
async def crm_delete_task(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.delete(f"/api/crm/tasks/{args['task_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_delete_task error: {e}")
        return _error_response(f"태스크 삭제 실패: {str(e)}")


# ──────────────────────────────────────────────
# 54. crm_create_form
# ──────────────────────────────────────────────
@tool(
    "crm_create_form",
    "CRM 폼(양식)을 생성합니다. 웹에서 연락처 정보를 수집하는 폼을 만듭니다.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "폼 이름"},
            "fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "필드 이름 (영문)"},
                        "label": {"type": "string", "description": "필드 라벨 (표시용)"},
                        "type": {"type": "string", "enum": ["text", "email", "phone", "select", "textarea", "checkbox"], "description": "입력 유형"},
                        "required": {"type": "boolean", "description": "필수 입력 여부"},
                        "options": {"type": "array", "items": {"type": "string"}, "description": "select 타입일 때 선택지"}
                    },
                    "required": ["name", "label"]
                },
                "description": "폼 필드 목록"
            },
            "redirect_url": {"type": "string", "description": "제출 후 리다이렉트 URL"},
            "notification_emails": {"type": "string", "description": "알림 받을 이메일 (콤마 구분)"}
        },
        "required": ["name"]
    }
)
async def crm_create_form(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = {"name": args["name"]}
        for f in ["fields", "redirect_url", "notification_emails"]:
            if args.get(f) is not None:
                payload[f] = args[f]
        async with _client() as client:
            resp = await client.post("/api/crm/forms", json=payload)
            resp.raise_for_status()
            data = resp.json()
            # 공개 URL 추가
            form_data = data.get("data", data)
            if isinstance(form_data, dict) and form_data.get("id"):
                base = _get_public_base_url()
                form_data["public_url"] = f"{base}/api/crm/forms/{form_data['id']}/submit"
            return _success_response(data)
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_create_form error: {e}")
        return _error_response(f"폼 생성 실패: {str(e)}")


# ──────────────────────────────────────────────
# 55. crm_get_form
# ──────────────────────────────────────────────
@tool(
    "crm_get_form",
    "CRM 폼의 상세 정보를 조회합니다.",
    {
        "type": "object",
        "properties": {
            "form_id": {"type": "number", "description": "폼 ID"}
        },
        "required": ["form_id"]
    }
)
async def crm_get_form(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get(f"/api/crm/forms/{args['form_id']}")
            resp.raise_for_status()
            data = resp.json()
            form_data = data.get("data", data)
            if isinstance(form_data, dict) and form_data.get("id"):
                base = _get_public_base_url()
                form_data["public_url"] = f"{base}/api/crm/forms/{form_data['id']}/submit"
            return _success_response(data)
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_form error: {e}")
        return _error_response(f"폼 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 56. crm_update_form
# ──────────────────────────────────────────────
@tool(
    "crm_update_form",
    "CRM 폼을 수정합니다. 이름, 필드 구성, 리다이렉트 URL 등을 변경할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "form_id": {"type": "number", "description": "폼 ID"},
            "name": {"type": "string", "description": "폼 이름"},
            "fields": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "label": {"type": "string"},
                        "type": {"type": "string"},
                        "required": {"type": "boolean"},
                        "options": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["name", "label"]
                },
                "description": "폼 필드 목록 (전체 교체)"
            },
            "redirect_url": {"type": "string", "description": "제출 후 리다이렉트 URL"},
            "notification_emails": {"type": "string", "description": "알림 이메일"}
        },
        "required": ["form_id"]
    }
)
async def crm_update_form(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        fid = args["form_id"]
        payload = {}
        for f in ["name", "fields", "redirect_url", "notification_emails"]:
            if args.get(f) is not None:
                payload[f] = args[f]
        if not payload:
            return _error_response("업데이트할 필드가 없습니다.")
        async with _client() as client:
            resp = await client.put(f"/api/crm/forms/{fid}", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_update_form error: {e}")
        return _error_response(f"폼 수정 실패: {str(e)}")


# ──────────────────────────────────────────────
# 57. crm_delete_form
# ──────────────────────────────────────────────
@tool(
    "crm_delete_form",
    "CRM 폼을 삭제합니다.",
    {
        "type": "object",
        "properties": {
            "form_id": {"type": "number", "description": "삭제할 폼 ID"}
        },
        "required": ["form_id"]
    }
)
async def crm_delete_form(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.delete(f"/api/crm/forms/{args['form_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_delete_form error: {e}")
        return _error_response(f"폼 삭제 실패: {str(e)}")


# ──────────────────────────────────────────────
# 58. crm_create_segment
# ──────────────────────────────────────────────
@tool(
    "crm_create_segment",
    "CRM 세그먼트(스마트 리스트)를 생성합니다. 필터 조건으로 연락처를 자동 분류합니다.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "세그먼트 이름"},
            "description": {"type": "string", "description": "설명"},
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string", "description": "필터 필드 (lead_status, lifecycle_stage, lead_score, source, tag, owner_slack_id, custom_*)"},
                        "operator": {"type": "string", "enum": ["eq", "neq", "gt", "gte", "lt", "lte", "contains", "in"], "description": "연산자"},
                        "value": {"description": "비교 값"}
                    },
                    "required": ["field", "operator", "value"]
                },
                "description": "필터 조건 목록 (AND 조합)"
            }
        },
        "required": ["name"]
    }
)
async def crm_create_segment(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = {"name": args["name"]}
        for f in ["description", "filters"]:
            if args.get(f) is not None:
                payload[f] = args[f]
        async with _client() as client:
            resp = await client.post("/api/crm/segments", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_create_segment error: {e}")
        return _error_response(f"세그먼트 생성 실패: {str(e)}")


# ──────────────────────────────────────────────
# 59. crm_update_segment
# ──────────────────────────────────────────────
@tool(
    "crm_update_segment",
    "CRM 세그먼트를 수정합니다. 이름, 설명, 필터 조건을 변경할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "segment_id": {"type": "number", "description": "세그먼트 ID"},
            "name": {"type": "string", "description": "세그먼트 이름"},
            "description": {"type": "string", "description": "설명"},
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string"},
                        "operator": {"type": "string"},
                        "value": {}
                    },
                    "required": ["field", "operator", "value"]
                },
                "description": "필터 조건 목록 (전체 교체)"
            }
        },
        "required": ["segment_id"]
    }
)
async def crm_update_segment(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        sid = args["segment_id"]
        payload = {}
        for f in ["name", "description", "filters"]:
            if args.get(f) is not None:
                payload[f] = args[f]
        if not payload:
            return _error_response("업데이트할 필드가 없습니다.")
        async with _client() as client:
            resp = await client.put(f"/api/crm/segments/{sid}", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_update_segment error: {e}")
        return _error_response(f"세그먼트 수정 실패: {str(e)}")


# ──────────────────────────────────────────────
# 60. crm_delete_segment
# ──────────────────────────────────────────────
@tool(
    "crm_delete_segment",
    "CRM 세그먼트를 삭제합니다.",
    {
        "type": "object",
        "properties": {
            "segment_id": {"type": "number", "description": "삭제할 세그먼트 ID"}
        },
        "required": ["segment_id"]
    }
)
async def crm_delete_segment(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.delete(f"/api/crm/segments/{args['segment_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_delete_segment error: {e}")
        return _error_response(f"세그먼트 삭제 실패: {str(e)}")


# ──────────────────────────────────────────────
# 61. crm_refresh_segment
# ──────────────────────────────────────────────
@tool(
    "crm_refresh_segment",
    "CRM 세그먼트의 연락처 수를 새로고침합니다. 필터 조건에 맞는 최신 연락처 수를 다시 계산합니다.",
    {
        "type": "object",
        "properties": {
            "segment_id": {"type": "number", "description": "세그먼트 ID"}
        },
        "required": ["segment_id"]
    }
)
async def crm_refresh_segment(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.post(f"/api/crm/segments/{args['segment_id']}/refresh")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_refresh_segment error: {e}")
        return _error_response(f"세그먼트 새로고침 실패: {str(e)}")


# ──────────────────────────────────────────────
# 62. crm_update_activity
# ──────────────────────────────────────────────
@tool(
    "crm_update_activity",
    "CRM 활동 기록을 수정합니다. done=true로 설정하면 완료 체크, metadata로 call_objective, result 등 모든 메타데이터를 업데이트할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "activity_id": {"type": "number", "description": "활동 ID"},
            "type": {"type": "string", "enum": ["call", "email", "meeting", "note"], "description": "활동 유형"},
            "subject": {"type": "string", "description": "활동 제목"},
            "body": {"type": "string", "description": "활동 내용"},
            "contact_id": {"type": "number", "description": "연락처 ID"},
            "deal_id": {"type": "number", "description": "딜 ID"},
            "company_id": {"type": "number", "description": "회사 ID"},
            "timestamp": {"type": "string", "description": "활동 일시 (ISO 형식)"},
            "done": {"type": "boolean", "description": "완료 여부 (true=완료 체크, false=미완료)"},
            "metadata": {"type": "object", "description": "메타데이터 (call_objective, result, hospital, customer, product, done 등)"}
        },
        "required": ["activity_id"]
    }
)
async def crm_update_activity(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        aid = args["activity_id"]
        payload = {}
        for f in ["type", "subject", "body", "contact_id", "deal_id", "company_id", "timestamp"]:
            if args.get(f) is not None:
                payload[f] = args[f]
        # metadata 처리 (done 포함)
        metadata = args.get("metadata")
        if metadata is None:
            metadata = {}
        if args.get("done") is not None:
            metadata["done"] = args["done"]
        if metadata:
            payload["metadata"] = metadata
        if not payload:
            return _error_response("업데이트할 필드가 없습니다.")
        async with _client() as client:
            resp = await client.put(f"/api/crm/activities/{aid}", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_update_activity error: {e}")
        return _error_response(f"활동 수정 실패: {str(e)}")


# ──────────────────────────────────────────────
# 63. crm_delete_activity
# ──────────────────────────────────────────────
@tool(
    "crm_delete_activity",
    "CRM 활동 기록을 삭제합니다.",
    {
        "type": "object",
        "properties": {
            "activity_id": {"type": "number", "description": "삭제할 활동 ID"}
        },
        "required": ["activity_id"]
    }
)
async def crm_delete_activity(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.delete(f"/api/crm/activities/{args['activity_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_delete_activity error: {e}")
        return _error_response(f"활동 삭제 실패: {str(e)}")


# ──────────────────────────────────────────────
# 64. crm_get_deals_by_pipeline
# ──────────────────────────────────────────────
@tool(
    "crm_get_deals_by_pipeline",
    "파이프라인별 딜 목록을 단계(stage)별로 그룹화하여 조회합니다. 칸반 보드 형태의 데이터를 제공합니다.",
    {
        "type": "object",
        "properties": {
            "pipeline_id": {"type": "number", "description": "파이프라인 ID"}
        },
        "required": ["pipeline_id"]
    }
)
async def crm_get_deals_by_pipeline(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get(f"/api/crm/deals/pipeline/{args['pipeline_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_deals_by_pipeline error: {e}")
        return _error_response(f"파이프라인별 딜 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 65. crm_deal_forecast
# ──────────────────────────────────────────────
@tool(
    "crm_deal_forecast",
    "딜 기반 매출 예측을 조회합니다. 월별 예상 매출과 가중 매출을 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "months": {"type": "number", "description": "예측 개월 수 (기본값: 6)"}
        }
    }
)
async def crm_deal_forecast(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        params = {}
        if args.get("months"):
            params["months"] = args["months"]
        async with _client() as client:
            resp = await client.get("/api/crm/deals/forecast", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_deal_forecast error: {e}")
        return _error_response(f"매출 예측 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 66. crm_get_pipeline
# ──────────────────────────────────────────────
@tool(
    "crm_get_pipeline",
    "CRM 파이프라인의 상세 정보를 조회합니다. 단계 구성과 설정을 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "pipeline_id": {"type": "number", "description": "파이프라인 ID"}
        },
        "required": ["pipeline_id"]
    }
)
async def crm_get_pipeline(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get(f"/api/crm/pipelines/{args['pipeline_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_pipeline error: {e}")
        return _error_response(f"파이프라인 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 67. crm_get_activity
# ──────────────────────────────────────────────
@tool(
    "crm_get_activity",
    "CRM 활동 기록의 상세 정보를 조회합니다. 연락처명, 딜명, 회사명을 포함합니다.",
    {
        "type": "object",
        "properties": {
            "activity_id": {"type": "number", "description": "활동 ID"}
        },
        "required": ["activity_id"]
    }
)
async def crm_get_activity(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get(f"/api/crm/activities/{args['activity_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_activity error: {e}")
        return _error_response(f"활동 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 68. crm_recent_activities
# ──────────────────────────────────────────────
@tool(
    "crm_recent_activities",
    "CRM 최근 활동 기록을 조회합니다. 연락처명, 딜명, 회사명을 포함한 최신 활동 목록을 반환합니다.",
    {
        "type": "object",
        "properties": {
            "limit": {"type": "number", "description": "최대 결과 수 (기본값: 20, 최대 100)"}
        }
    }
)
async def crm_recent_activities(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        params = {}
        if args.get("limit"):
            params["limit"] = args["limit"]
        async with _client() as client:
            resp = await client.get("/api/crm/activities/recent", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_recent_activities error: {e}")
        return _error_response(f"최근 활동 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 69. crm_submit_form
# ──────────────────────────────────────────────
@tool(
    "crm_submit_form",
    "CRM 폼에 데이터를 제출합니다. 폼 필드에 맞는 데이터를 전송하여 연락처를 생성하거나 정보를 수집합니다.",
    {
        "type": "object",
        "properties": {
            "form_id": {"type": "number", "description": "폼 ID"},
            "data": {"type": "object", "description": "폼 필드 데이터 (예: {\"name\": \"홍길동\", \"email\": \"hong@example.com\"})"}
        },
        "required": ["form_id", "data"]
    }
)
async def crm_submit_form(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.post(
                f"/api/crm/forms/{args['form_id']}/submit",
                json={"data": args["data"]},
            )
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_submit_form error: {e}")
        return _error_response(f"폼 제출 실패: {str(e)}")


# ──────────────────────────────────────────────
# 70. crm_list_form_submissions
# ──────────────────────────────────────────────
@tool(
    "crm_list_form_submissions",
    "CRM 폼에 제출된 데이터 목록을 조회합니다. 제출자 정보와 제출 내용을 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "form_id": {"type": "number", "description": "폼 ID"},
            "page": {"type": "number", "description": "페이지 번호 (기본값: 1)"},
            "page_size": {"type": "number", "description": "페이지 크기 (기본값: 20)"}
        },
        "required": ["form_id"]
    }
)
async def crm_list_form_submissions(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        params = {
            "page": args.get("page", 1),
            "page_size": args.get("page_size", 20),
        }
        async with _client() as client:
            resp = await client.get(
                f"/api/crm/forms/{args['form_id']}/submissions",
                params=params,
            )
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_list_form_submissions error: {e}")
        return _error_response(f"폼 제출 목록 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 71. crm_get_segment
# ──────────────────────────────────────────────
@tool(
    "crm_get_segment",
    "CRM 세그먼트의 상세 정보를 조회합니다. 필터 조건과 매칭되는 연락처 수를 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "segment_id": {"type": "number", "description": "세그먼트 ID"}
        },
        "required": ["segment_id"]
    }
)
async def crm_get_segment(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get(f"/api/crm/segments/{args['segment_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_segment error: {e}")
        return _error_response(f"세그먼트 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 72. crm_sequence_stats
# ──────────────────────────────────────────────
@tool(
    "crm_sequence_stats",
    "개별 이메일 시퀀스의 등록 통계를 조회합니다. 활성/완료/일시정지/바운스 수를 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "sequence_id": {"type": "number", "description": "시퀀스 ID"}
        },
        "required": ["sequence_id"]
    }
)
async def crm_sequence_stats(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get(f"/api/crm/emails/sequences/{args['sequence_id']}/stats")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_sequence_stats error: {e}")
        return _error_response(f"시퀀스 통계 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 73. crm_report_activities
# ──────────────────────────────────────────────
@tool(
    "crm_report_activities",
    "활동 유형별 통계를 조회합니다. 이메일, 전화, 미팅 등 유형별 활동 건수를 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "days": {"type": "number", "description": "조회 기간 일수 (기본값: 30)"}
        }
    }
)
async def crm_report_activities(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        params = {}
        if args.get("days"):
            params["days"] = args["days"]
        async with _client() as client:
            resp = await client.get("/api/crm/reports/activities", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_report_activities error: {e}")
        return _error_response(f"활동 통계 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 74. crm_report_lead_sources
# ──────────────────────────────────────────────
@tool(
    "crm_report_lead_sources",
    "리드 소스별 분석을 조회합니다. 유입 경로별 연락처 수와 전환 수를 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {}
    }
)
async def crm_report_lead_sources(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get("/api/crm/reports/lead-sources")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_report_lead_sources error: {e}")
        return _error_response(f"리드 소스 분석 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 75. crm_report_revenue_forecast
# ──────────────────────────────────────────────
@tool(
    "crm_report_revenue_forecast",
    "매출 예측 리포트를 조회합니다. 월별 예상 매출, 가중 매출, 딜 수를 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "months": {"type": "number", "description": "예측 개월 수 (기본값: 6)"}
        }
    }
)
async def crm_report_revenue_forecast(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        params = {}
        if args.get("months"):
            params["months"] = args["months"]
        async with _client() as client:
            resp = await client.get("/api/crm/reports/revenue-forecast", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_report_revenue_forecast error: {e}")
        return _error_response(f"매출 예측 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 76. crm_report_sales_performance
# ──────────────────────────────────────────────
@tool(
    "crm_report_sales_performance",
    "영업 담당자별 성과를 조회합니다. 담당자별 성사/실패 딜 수, 총 매출, 평균 딜 크기, 성사율을 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {}
    }
)
async def crm_report_sales_performance(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get("/api/crm/reports/sales-performance")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_report_sales_performance error: {e}")
        return _error_response(f"영업 성과 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 77. crm_list_templates
# ──────────────────────────────────────────────
@tool(
    "crm_list_templates",
    "이메일/뉴스레터/팜플렛 템플릿 목록을 조회합니다. 유형(email, newsletter, pamphlet)별로 필터링할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["email", "newsletter", "pamphlet"], "description": "템플릿 유형 필터"},
            "status": {"type": "string", "enum": ["active", "archived"], "description": "상태 필터"},
            "tag": {"type": "string", "description": "태그 필터"}
        }
    }
)
async def crm_list_templates(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        params = {"page_size": 50}
        if args.get("type"):
            params["type"] = args["type"]
        if args.get("status"):
            params["status"] = args["status"]
        if args.get("tag"):
            params["tag"] = args["tag"]
        async with _client() as client:
            resp = await client.get("/api/crm/emails/templates", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_list_templates error: {e}")
        return _error_response(f"템플릿 목록 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 78. crm_get_template
# ──────────────────────────────────────────────
@tool(
    "crm_get_template",
    "이메일/뉴스레터/팜플렛 템플릿의 상세 정보를 조회합니다. HTML 본문, 치환 변수, 태그 등을 포함합니다.",
    {
        "type": "object",
        "properties": {
            "template_id": {"type": "number", "description": "템플릿 ID"}
        },
        "required": ["template_id"]
    }
)
async def crm_get_template(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get(f"/api/crm/emails/templates/{args['template_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_template error: {e}")
        return _error_response(f"템플릿 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 79. crm_create_template
# ──────────────────────────────────────────────
@tool(
    "crm_create_template",
    "이메일, 뉴스레터, 또는 팜플렛 템플릿을 생성합니다. HTML 본문에 {{first_name}}, {{company_name}} 등의 치환 변수를 사용할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "템플릿 이름"},
            "type": {"type": "string", "enum": ["email", "newsletter", "pamphlet"], "description": "템플릿 유형 (기본값: email)"},
            "subject": {"type": "string", "description": "이메일 제목 (치환 변수 사용 가능)"},
            "body_html": {"type": "string", "description": "HTML 본문. {{variable}} 형식으로 치환 변수 사용 가능"},
            "body_text": {"type": "string", "description": "플레인 텍스트 대체 본문 (선택)"},
            "description": {"type": "string", "description": "템플릿 설명 (선택)"},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "태그 목록 (선택)"}
        },
        "required": ["name", "body_html"]
    }
)
async def crm_create_template(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = {
            "name": args["name"],
            "body_html": args["body_html"],
            "type": args.get("type", "email"),
        }
        if args.get("subject"):
            payload["subject"] = args["subject"]
        if args.get("body_text"):
            payload["body_text"] = args["body_text"]
        if args.get("description"):
            payload["description"] = args["description"]
        if args.get("tags"):
            payload["tags"] = args["tags"]

        async with _client() as client:
            resp = await client.post("/api/crm/emails/templates", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_create_template error: {e}")
        return _error_response(f"템플릿 생성 실패: {str(e)}")


# ──────────────────────────────────────────────
# 80. crm_update_template
# ──────────────────────────────────────────────
@tool(
    "crm_update_template",
    "이메일/뉴스레터/팜플렛 템플릿을 수정합니다.",
    {
        "type": "object",
        "properties": {
            "template_id": {"type": "number", "description": "수정할 템플릿 ID"},
            "name": {"type": "string", "description": "템플릿 이름"},
            "type": {"type": "string", "enum": ["email", "newsletter", "pamphlet"], "description": "템플릿 유형"},
            "subject": {"type": "string", "description": "이메일 제목"},
            "body_html": {"type": "string", "description": "HTML 본문"},
            "body_text": {"type": "string", "description": "플레인 텍스트 본문"},
            "description": {"type": "string", "description": "템플릿 설명"},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "태그 목록"},
            "status": {"type": "string", "enum": ["active", "archived"], "description": "상태"}
        },
        "required": ["template_id"]
    }
)
async def crm_update_template(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        template_id = args.pop("template_id")
        payload = {k: v for k, v in args.items() if v is not None}
        async with _client() as client:
            resp = await client.put(f"/api/crm/emails/templates/{template_id}", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_update_template error: {e}")
        return _error_response(f"템플릿 수정 실패: {str(e)}")


# ──────────────────────────────────────────────
# 81. crm_delete_template
# ──────────────────────────────────────────────
@tool(
    "crm_delete_template",
    "이메일/뉴스레터/팜플렛 템플릿을 삭제합니다.",
    {
        "type": "object",
        "properties": {
            "template_id": {"type": "number", "description": "삭제할 템플릿 ID"}
        },
        "required": ["template_id"]
    }
)
async def crm_delete_template(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.delete(f"/api/crm/emails/templates/{args['template_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_delete_template error: {e}")
        return _error_response(f"템플릿 삭제 실패: {str(e)}")


# ──────────────────────────────────────────────
# 82. crm_render_template
# ──────────────────────────────────────────────
@tool(
    "crm_render_template",
    "템플릿에 변수를 적용하여 완성된 이메일을 렌더링합니다. 예: {{first_name}}을 '홍길동'으로 치환합니다.",
    {
        "type": "object",
        "properties": {
            "template_id": {"type": "number", "description": "렌더링할 템플릿 ID"},
            "variables": {"type": "object", "description": "치환 변수 (예: {\"first_name\": \"홍길동\", \"company_name\": \"우리 회사\"})"}
        },
        "required": ["template_id", "variables"]
    }
)
async def crm_render_template(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.post(
                f"/api/crm/emails/templates/{args['template_id']}/render",
                json=args.get("variables", {}),
            )
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_render_template error: {e}")
        return _error_response(f"템플릿 렌더링 실패: {str(e)}")


# ──────────────────────────────────────────────
# 83. crm_duplicate_template
# ──────────────────────────────────────────────
@tool(
    "crm_duplicate_template",
    "기존 템플릿을 복제하여 새 템플릿을 만듭니다. 기존 템플릿을 기반으로 변형할 때 유용합니다.",
    {
        "type": "object",
        "properties": {
            "template_id": {"type": "number", "description": "복제할 템플릿 ID"}
        },
        "required": ["template_id"]
    }
)
async def crm_duplicate_template(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.post(f"/api/crm/emails/templates/{args['template_id']}/duplicate")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_duplicate_template error: {e}")
        return _error_response(f"템플릿 복제 실패: {str(e)}")


# ──────────────────────────────────────────────
# 84-R. crm_create_relationship
# ──────────────────────────────────────────────
@tool(
    "crm_create_relationship",
    "두 엔티티(연락처, 회사, 딜) 간의 관계를 생성합니다. 예: 의사가 여러 병원에 소속, 병원과 유통사 연결, 딜과 기관 연결 등 네트워크형 관계를 표현합니다.",
    {
        "type": "object",
        "properties": {
            "from_type": {"type": "string", "enum": ["contact", "company", "deal"], "description": "출발 엔티티 유형"},
            "from_id": {"type": "number", "description": "출발 엔티티 ID"},
            "to_type": {"type": "string", "enum": ["contact", "company", "deal"], "description": "도착 엔티티 유형"},
            "to_id": {"type": "number", "description": "도착 엔티티 ID"},
            "relationship_type": {"type": "string", "description": "관계 유형 (소속, 겸임, 유통, 보험, 협력, 의뢰, 납품, 추천 등)"},
            "role": {"type": "string", "description": "역할 (예: 신경과 과장, 외래 진료, 총판 등)"},
            "is_primary": {"type": "boolean", "description": "주 관계 여부 (기본값: false)"}
        },
        "required": ["from_type", "from_id", "to_type", "to_id", "relationship_type"]
    }
)
async def crm_create_relationship(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = {k: v for k, v in args.items() if v is not None}
        async with _client() as client:
            resp = await client.post("/api/crm/relationships", json=payload)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_create_relationship error: {e}")
        return _error_response(f"관계 생성 실패: {str(e)}")


# ──────────────────────────────────────────────
# 84-R2. crm_get_relationships
# ──────────────────────────────────────────────
@tool(
    "crm_get_relationships",
    "특정 연락처, 회사, 딜의 모든 관계를 조회합니다. 해당 엔티티와 연결된 모든 기관, 인물, 딜을 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "entity_type": {"type": "string", "enum": ["contact", "company", "deal"], "description": "엔티티 유형"},
            "entity_id": {"type": "number", "description": "엔티티 ID"},
            "relationship_type": {"type": "string", "description": "관계 유형 필터 (선택)"}
        },
        "required": ["entity_type", "entity_id"]
    }
)
async def crm_get_relationships(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        params = {}
        if args.get("relationship_type"):
            params["relationship_type"] = args["relationship_type"]
        async with _client() as client:
            resp = await client.get(f"/api/crm/relationships/entity/{args['entity_type']}/{args['entity_id']}", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_relationships error: {e}")
        return _error_response(f"관계 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 84-R3. crm_get_network
# ──────────────────────────────────────────────
@tool(
    "crm_get_network",
    "특정 엔티티의 관계 네트워크를 깊이 탐색합니다. 연결된 모든 기관, 인물, 딜의 관계망을 파악할 수 있습니다. 예: 김과장 → 소속 병원들 → 병원의 유통사들",
    {
        "type": "object",
        "properties": {
            "entity_type": {"type": "string", "enum": ["contact", "company", "deal"], "description": "엔티티 유형"},
            "entity_id": {"type": "number", "description": "엔티티 ID"},
            "depth": {"type": "number", "description": "탐색 깊이 (1~3, 기본값: 2)"}
        },
        "required": ["entity_type", "entity_id"]
    }
)
async def crm_get_network(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        params = {}
        if args.get("depth"):
            params["depth"] = args["depth"]
        async with _client() as client:
            resp = await client.get(f"/api/crm/relationships/network/{args['entity_type']}/{args['entity_id']}", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_network error: {e}")
        return _error_response(f"네트워크 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 84-R4. crm_delete_relationship
# ──────────────────────────────────────────────
@tool(
    "crm_delete_relationship",
    "관계를 삭제합니다.",
    {
        "type": "object",
        "properties": {
            "relationship_id": {"type": "number", "description": "관계 ID"}
        },
        "required": ["relationship_id"]
    }
)
async def crm_delete_relationship(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.delete(f"/api/crm/relationships/{args['relationship_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_delete_relationship error: {e}")
        return _error_response(f"관계 삭제 실패: {str(e)}")


# ──────────────────────────────────────────────
# 84. crm_get_email_tracking
# ──────────────────────────────────────────────
@tool(
    "crm_get_email_tracking",
    "연락처에게 보낸 이메일의 추적 데이터를 조회합니다. 열람 횟수, 클릭 횟수, 답장 여부를 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "contact_id": {"type": "number", "description": "연락처 ID"}
        },
        "required": ["contact_id"]
    }
)
async def crm_get_email_tracking(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get(f"/api/crm/track/contact/{args['contact_id']}")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_email_tracking error: {e}")
        return _error_response(f"이메일 추적 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 85. crm_get_email_tracking_summary
# ──────────────────────────────────────────────
@tool(
    "crm_get_email_tracking_summary",
    "연락처별 이메일 추적 요약을 조회합니다. 열람률, 클릭률, 답장률을 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "contact_id": {"type": "number", "description": "연락처 ID"}
        },
        "required": ["contact_id"]
    }
)
async def crm_get_email_tracking_summary(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get(f"/api/crm/track/contact/{args['contact_id']}/summary")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_email_tracking_summary error: {e}")
        return _error_response(f"이메일 추적 요약 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 86. crm_get_sequence_tracking
# ──────────────────────────────────────────────
@tool(
    "crm_get_sequence_tracking",
    "이메일 시퀀스의 추적 통계를 조회합니다. 열람률, 클릭률, 답장률을 확인할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "sequence_id": {"type": "number", "description": "시퀀스 ID"}
        },
        "required": ["sequence_id"]
    }
)
async def crm_get_sequence_tracking(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        async with _client() as client:
            resp = await client.get(f"/api/crm/track/sequence/{args['sequence_id']}/stats")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_get_sequence_tracking error: {e}")
        return _error_response(f"시퀀스 추적 통계 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 87. crm_create_meeting_booking
# ──────────────────────────────────────────────
@tool(
    "crm_create_meeting_booking",
    "고객에게 미팅 시간 선택 링크를 생성합니다. 담당자의 Google Calendar에서 빈 시간을 슬롯으로 제공하고, 고객이 클릭하면 자동 확정됩니다.",
    {
        "type": "object",
        "properties": {
            "contact_id": {"type": "number", "description": "고객 연락처 ID"},
            "host_slack_id": {"type": "string", "description": "담당자 Slack ID"},
            "title": {"type": "string", "description": "미팅 제목 (기본값: 미팅)"},
            "duration_minutes": {"type": "number", "description": "미팅 시간 (분, 기본값: 30)"},
            "slots": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "string", "description": "시작 시간 (ISO 8601)"},
                        "end": {"type": "string", "description": "종료 시간 (ISO 8601)"},
                        "label": {"type": "string", "description": "표시 레이블 (예: '3월 18일 (화) 오후 2:00')"}
                    },
                    "required": ["start", "end"]
                },
                "description": "제안할 시간 슬롯 목록"
            },
            "message": {"type": "string", "description": "고객에게 보여줄 메시지 (선택)"}
        },
        "required": ["contact_id", "host_slack_id", "slots"]
    }
)
async def crm_create_meeting_booking(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = {
            "contact_id": args["contact_id"],
            "title": args.get("title", "미팅"),
            "duration_minutes": args.get("duration_minutes", 30),
            "slots": args["slots"],
            "message": args.get("message"),
        }
        async with _client() as client:
            resp = await client.post(
                f"/api/crm/booking?host_slack_id={args['host_slack_id']}",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            booking_data = data.get("data", data)
            if isinstance(booking_data, dict) and booking_data.get("token"):
                base = _get_public_base_url()
                booking_data["booking_url"] = f"{base}/api/crm/booking/{booking_data['token']}"
            return _success_response(data)
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_create_meeting_booking error: {e}")
        return _error_response(f"미팅 예약 생성 실패: {str(e)}")


# ──────────────────────────────────────────────
# 88. crm_list_meeting_bookings
# ──────────────────────────────────────────────
@tool(
    "crm_list_meeting_bookings",
    "미팅 예약 목록을 조회합니다. 상태(pending, confirmed, expired, cancelled)로 필터링할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["pending", "confirmed", "expired", "cancelled"], "description": "상태 필터"}
        }
    }
)
async def crm_list_meeting_bookings(args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        params = {"page_size": 50}
        if args.get("status"):
            params["status"] = args["status"]
        async with _client() as client:
            resp = await client.get("/api/crm/booking/list/all", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_list_meeting_bookings error: {e}")
        return _error_response(f"미팅 예약 목록 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 89. crm_search_prescriptions (의료/제약)
# ──────────────────────────────────────────────
@tool(
    "crm_search_prescriptions",
    "처방전을 검색합니다. 병원, 의사, 처방유형(NP/NR), 기간 등으로 필터링할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "hospital_id": {"type": "number", "description": "병원(회사) ID"},
            "doctor_id": {"type": "number", "description": "의사(연락처) ID"},
            "prescription_type": {"type": "string", "description": "처방 유형 (NP=신규처방, NR=재처방)"},
            "year": {"type": "number", "description": "연도 (예: 2026)"},
            "month": {"type": "number", "description": "월 (1-12)"},
            "page": {"type": "number", "description": "페이지 번호 (기본값: 1)"},
            "page_size": {"type": "number", "description": "페이지 크기 (기본값: 50)"}
        }
    }
)
async def crm_search_prescriptions(args: Dict[str, Any]) -> Dict[str, Any]:
    """처방전 검색"""
    try:
        params = {}
        for key in ("hospital_id", "doctor_id", "prescription_type", "year", "month", "page", "page_size"):
            if args.get(key) is not None:
                params[key] = args[key]
        if "page_size" not in params:
            params["page_size"] = 50
        async with _client() as client:
            resp = await client.get("/api/crm/prescriptions", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_search_prescriptions error: {e}")
        return _error_response(f"처방전 검색 실패: {str(e)}")


# ──────────────────────────────────────────────
# 90. crm_prescription_stats (의료/제약)
# ──────────────────────────────────────────────
@tool(
    "crm_prescription_stats",
    "처방 통계를 조회합니다. 총 처방수, NP/NR 비율, 유니크 병원/의사/환자 수, 월별 추이, 상위 병원/의사 등을 제공합니다.",
    {
        "type": "object",
        "properties": {
            "year": {"type": "number", "description": "연도 필터 (예: 2026)"},
            "month": {"type": "number", "description": "월 필터 (1-12)"},
            "hospital_id": {"type": "number", "description": "특정 병원 ID로 제한"}
        }
    }
)
async def crm_prescription_stats(args: Dict[str, Any]) -> Dict[str, Any]:
    """처방 통계 조회"""
    try:
        params = {}
        for key in ("year", "month", "hospital_id"):
            if args.get(key) is not None:
                params[key] = args[key]
        async with _client() as client:
            resp = await client.get("/api/crm/prescriptions/stats", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_prescription_stats error: {e}")
        return _error_response(f"처방 통계 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 91. crm_search_sales (의료/제약)
# ──────────────────────────────────────────────
@tool(
    "crm_search_sales",
    "매출(매입/납품) 거래를 검색합니다. 병원, 제품, 기간으로 필터링할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "company_id": {"type": "number", "description": "병원(회사) ID"},
            "product": {"type": "string", "description": "제품명 필터"},
            "year": {"type": "number", "description": "연도 (예: 2026)"},
            "month": {"type": "number", "description": "월 (1-12)"}
        }
    }
)
async def crm_search_sales(args: Dict[str, Any]) -> Dict[str, Any]:
    """매출 거래 검색"""
    try:
        params = {}
        for key in ("company_id", "product", "year", "month"):
            if args.get(key) is not None:
                params[key] = args[key]
        async with _client() as client:
            resp = await client.get("/api/crm/sales", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_search_sales error: {e}")
        return _error_response(f"매출 검색 실패: {str(e)}")


# ──────────────────────────────────────────────
# 92. crm_sales_summary (의료/제약)
# ──────────────────────────────────────────────
@tool(
    "crm_sales_summary",
    "매출 요약을 조회합니다. 총 매출, 수량, 수금액, 월별 추이, 제품별 매출을 제공합니다.",
    {
        "type": "object",
        "properties": {
            "year": {"type": "number", "description": "연도 필터 (예: 2026)"},
            "company_id": {"type": "number", "description": "특정 병원 ID로 제한"},
            "product": {"type": "string", "description": "제품명 필터"}
        }
    }
)
async def crm_sales_summary(args: Dict[str, Any]) -> Dict[str, Any]:
    """매출 요약 조회"""
    try:
        params = {}
        for key in ("year", "company_id", "product"):
            if args.get(key) is not None:
                params[key] = args[key]
        async with _client() as client:
            resp = await client.get("/api/crm/sales/summary", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_sales_summary error: {e}")
        return _error_response(f"매출 요약 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 93. crm_search_product_listings (의료/제약)
# ──────────────────────────────────────────────
@tool(
    "crm_search_product_listings",
    "제품 리스팅(등재) 현황을 검색합니다. 병원별, 제품별, 상태별로 필터링할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "company_id": {"type": "number", "description": "병원(회사) ID"},
            "product": {"type": "string", "description": "제품명 필터"},
            "status": {"type": "string", "description": "리스팅 상태 필터 (예: active, pending, expired)"}
        }
    }
)
async def crm_search_product_listings(args: Dict[str, Any]) -> Dict[str, Any]:
    """제품 리스팅 검색"""
    try:
        params = {}
        for key in ("company_id", "product", "status"):
            if args.get(key) is not None:
                params[key] = args[key]
        async with _client() as client:
            resp = await client.get("/api/crm/product-listings", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_search_product_listings error: {e}")
        return _error_response(f"제품 리스팅 검색 실패: {str(e)}")


# ──────────────────────────────────────────────
# 94. crm_search_kol_plans (의료/제약)
# ──────────────────────────────────────────────
@tool(
    "crm_search_kol_plans",
    "KOL(Key Opinion Leader) 관리 계획을 검색합니다. 의사별 외래스케줄, 학술활동, 역할 등을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "company_id": {"type": "number", "description": "병원(회사) ID"},
            "doctor_id": {"type": "number", "description": "의사(연락처) ID"},
            "plan_type": {"type": "string", "description": "계획 유형 필터"},
            "search": {"type": "string", "description": "검색어"}
        }
    }
)
async def crm_search_kol_plans(args: Dict[str, Any]) -> Dict[str, Any]:
    """KOL 관리 계획 검색"""
    try:
        params = {}
        for key in ("company_id", "doctor_id", "plan_type", "search"):
            if args.get(key) is not None:
                params[key] = args[key]
        async with _client() as client:
            resp = await client.get("/api/crm/kol-plans", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_search_kol_plans error: {e}")
        return _error_response(f"KOL 계획 검색 실패: {str(e)}")


# ──────────────────────────────────────────────
# 95. crm_search_hospital_contracts (의료/제약)
# ──────────────────────────────────────────────
@tool(
    "crm_search_hospital_contracts",
    "병원 계약 현황을 검색합니다. 병원별, 제품별, 계약 상태별로 필터링할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "company_id": {"type": "number", "description": "병원(회사) ID"},
            "product": {"type": "string", "description": "제품명 필터"},
            "contract_status": {"type": "string", "description": "계약 상태 필터 (예: active, expired, negotiating)"}
        }
    }
)
async def crm_search_hospital_contracts(args: Dict[str, Any]) -> Dict[str, Any]:
    """병원 계약 검색"""
    try:
        params = {}
        for key in ("company_id", "product", "contract_status"):
            if args.get(key) is not None:
                params[key] = args[key]
        async with _client() as client:
            resp = await client.get("/api/crm/hospital-contracts", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_search_hospital_contracts error: {e}")
        return _error_response(f"병원 계약 검색 실패: {str(e)}")


# ──────────────────────────────────────────────
# 96. crm_hospital_360 (의료/제약)
# ──────────────────────────────────────────────
@tool(
    "crm_hospital_360",
    "병원 종합 정보(360도 뷰)를 조회합니다. 의사, 처방, 매출, 리스팅, KOL, 계약 정보를 한번에 제공합니다.",
    {
        "type": "object",
        "properties": {
            "company_id": {"type": "number", "description": "병원(회사) ID"}
        },
        "required": ["company_id"]
    }
)
async def crm_hospital_360(args: Dict[str, Any]) -> Dict[str, Any]:
    """병원 360도 뷰 조회"""
    try:
        async with _client() as client:
            resp = await client.get(f"/api/crm/companies/{args['company_id']}/360")
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_hospital_360 error: {e}")
        return _error_response(f"병원 종합 정보 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 97. crm_hospital_prescriptions (의료/제약)
# ──────────────────────────────────────────────
@tool(
    "crm_hospital_prescriptions",
    "특정 병원의 처방 내역을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "company_id": {"type": "number", "description": "병원(회사) ID"},
            "year": {"type": "number", "description": "연도 필터"},
            "month": {"type": "number", "description": "월 필터"}
        },
        "required": ["company_id"]
    }
)
async def crm_hospital_prescriptions(args: Dict[str, Any]) -> Dict[str, Any]:
    """병원별 처방 내역 조회"""
    try:
        params = {}
        for key in ("year", "month"):
            if args.get(key) is not None:
                params[key] = args[key]
        async with _client() as client:
            resp = await client.get(f"/api/crm/companies/{args['company_id']}/prescriptions", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_hospital_prescriptions error: {e}")
        return _error_response(f"병원 처방 내역 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 98. crm_hospital_sales (의료/제약)
# ──────────────────────────────────────────────
@tool(
    "crm_hospital_sales",
    "특정 병원의 매출 내역을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "company_id": {"type": "number", "description": "병원(회사) ID"},
            "year": {"type": "number", "description": "연도 필터"},
            "month": {"type": "number", "description": "월 필터"}
        },
        "required": ["company_id"]
    }
)
async def crm_hospital_sales(args: Dict[str, Any]) -> Dict[str, Any]:
    """병원별 매출 내역 조회"""
    try:
        params = {}
        for key in ("year", "month"):
            if args.get(key) is not None:
                params[key] = args[key]
        async with _client() as client:
            resp = await client.get(f"/api/crm/companies/{args['company_id']}/sales", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_hospital_sales error: {e}")
        return _error_response(f"병원 매출 내역 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 99. crm_doctor_prescriptions (의료/제약)
# ──────────────────────────────────────────────
@tool(
    "crm_doctor_prescriptions",
    "특정 의사의 처방 내역을 조회합니다.",
    {
        "type": "object",
        "properties": {
            "contact_id": {"type": "number", "description": "의사(연락처) ID"},
            "year": {"type": "number", "description": "연도 필터"},
            "month": {"type": "number", "description": "월 필터"}
        },
        "required": ["contact_id"]
    }
)
async def crm_doctor_prescriptions(args: Dict[str, Any]) -> Dict[str, Any]:
    """의사별 처방 내역 조회"""
    try:
        params = {}
        for key in ("year", "month"):
            if args.get(key) is not None:
                params[key] = args[key]
        async with _client() as client:
            resp = await client.get(f"/api/crm/contacts/{args['contact_id']}/prescriptions", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_doctor_prescriptions error: {e}")
        return _error_response(f"의사 처방 내역 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 100. crm_prescription_dashboard (의료/제약)
# ──────────────────────────────────────────────
@tool(
    "crm_prescription_dashboard",
    "처방 대시보드를 조회합니다. 월별 병원유형별 처방 현황, Naive/Repeat 분석, Active Users 등 E_Sum 엑셀과 동일한 데이터를 제공합니다.",
    {
        "type": "object",
        "properties": {
            "year": {"type": "number", "description": "연도 필터 (예: 2026)"},
            "product": {"type": "string", "description": "제품명 필터"}
        }
    }
)
async def crm_prescription_dashboard(args: Dict[str, Any]) -> Dict[str, Any]:
    """처방 대시보드 조회"""
    try:
        params = {}
        for key in ("year", "product"):
            if args.get(key) is not None:
                params[key] = args[key]
        async with _client() as client:
            resp = await client.get("/api/crm/dashboards/prescription-dashboard", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_prescription_dashboard error: {e}")
        return _error_response(f"처방 대시보드 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 101. crm_listing_dashboard (의료/제약)
# ──────────────────────────────────────────────
@tool(
    "crm_listing_dashboard",
    "리스팅 대시보드를 조회합니다. 제품별, 병원유형별, 담당자별 리스팅 현황 및 리스팅→처방 연결 분석을 제공합니다.",
    {
        "type": "object",
        "properties": {
            "year": {"type": "number", "description": "연도 필터 (예: 2026)"},
            "product": {"type": "string", "description": "제품명 필터"}
        }
    }
)
async def crm_listing_dashboard(args: Dict[str, Any]) -> Dict[str, Any]:
    """리스팅 대시보드 조회"""
    try:
        params = {}
        for key in ("year", "product"):
            if args.get(key) is not None:
                params[key] = args[key]
        async with _client() as client:
            resp = await client.get("/api/crm/dashboards/listing-dashboard", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_listing_dashboard error: {e}")
        return _error_response(f"리스팅 대시보드 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 102. crm_territory_dashboard (의료/제약)
# ──────────────────────────────────────────────
@tool(
    "crm_territory_dashboard",
    "Territory 성과 대시보드를 조회합니다. 담당자별 병원 수, 의사 수, 처방 수, 리스팅 수, 매출을 제공합니다.",
    {
        "type": "object",
        "properties": {
            "year": {"type": "number", "description": "연도 필터 (예: 2026)"},
            "month": {"type": "number", "description": "월 필터 (1-12)"}
        }
    }
)
async def crm_territory_dashboard(args: Dict[str, Any]) -> Dict[str, Any]:
    """Territory 성과 대시보드 조회"""
    try:
        params = {}
        for key in ("year", "month"):
            if args.get(key) is not None:
                params[key] = args[key]
        async with _client() as client:
            resp = await client.get("/api/crm/dashboards/territory-dashboard", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_territory_dashboard error: {e}")
        return _error_response(f"Territory 대시보드 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 103. crm_compliance_report (의료/제약)
# ──────────────────────────────────────────────
@tool(
    "crm_compliance_report",
    "환자 복약순응도(Compliance/Adherence) 데이터를 조회합니다.",
    {
        "type": "object",
        "properties": {
            "year": {"type": "number", "description": "연도 필터 (예: 2026)"},
            "month": {"type": "number", "description": "월 필터 (1-12)"},
            "hospital_id": {"type": "number", "description": "병원 ID 필터"},
            "product": {"type": "string", "description": "제품명 필터"}
        }
    }
)
async def crm_compliance_report(args: Dict[str, Any]) -> Dict[str, Any]:
    """복약순응도 리포트 조회"""
    try:
        params = {}
        for key in ("year", "month", "hospital_id", "product"):
            if args.get(key) is not None:
                params[key] = args[key]
        async with _client() as client:
            resp = await client.get("/api/crm/prescriptions/compliance", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_compliance_report error: {e}")
        return _error_response(f"복약순응도 리포트 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# 104. crm_sfe_dashboard (의료/제약)
# ──────────────────────────────────────────────
@tool(
    "crm_sfe_dashboard",
    "SFE(Sales Force Effectiveness) E_Sum 대시보드를 조회합니다. 리스팅 Target vs Actual, 처방 Target vs Actual 데이터를 제공합니다.",
    {
        "type": "object",
        "properties": {
            "year": {"type": "number", "description": "연도 필터 (예: 2026)"},
            "month": {"type": "number", "description": "월 필터 (1-12)"}
        }
    }
)
async def crm_sfe_dashboard(args: Dict[str, Any]) -> Dict[str, Any]:
    """SFE E_Sum 대시보드 조회"""
    try:
        params = {}
        for key in ("year", "month"):
            if args.get(key) is not None:
                params[key] = args[key]
        async with _client() as client:
            resp = await client.get("/api/crm/dashboards/e-sum", params=params)
            resp.raise_for_status()
            return _success_response(resp.json())
    except Exception as e:
        logger.error(f"[CRM_TOOLS] crm_sfe_dashboard error: {e}")
        return _error_response(f"SFE 대시보드 조회 실패: {str(e)}")


# ──────────────────────────────────────────────
# MCP Server 생성
# ──────────────────────────────────────────────
crm_tools = [
    # 연락처 (Contacts)
    crm_search_contacts,
    crm_get_contact,
    crm_create_contact,
    crm_update_contact,
    crm_delete_contact,
    crm_contact_timeline,
    crm_recalculate_lead_score,
    # 회사 (Companies)
    crm_list_companies,
    crm_get_company,
    crm_create_company,
    crm_update_company,
    crm_delete_company,
    crm_get_company_contacts,
    crm_get_company_deals,
    # 딜 (Deals)
    crm_search_deals,
    crm_get_deal,
    crm_create_deal,
    crm_update_deal,
    crm_update_deal_stage,
    crm_delete_deal,
    crm_get_deals_by_pipeline,
    crm_deal_forecast,
    # 파이프라인 (Pipelines)
    crm_list_pipelines,
    crm_get_pipeline,
    crm_get_pipeline_summary,
    crm_create_pipeline,
    crm_update_pipeline,
    crm_delete_pipeline,
    # 활동 (Activities)
    crm_log_activity,
    crm_list_activities,
    crm_get_activity,
    crm_recent_activities,
    crm_update_activity,
    crm_delete_activity,
    # 태스크 (Tasks)
    crm_create_task,
    crm_list_tasks,
    crm_get_task,
    crm_get_my_tasks,
    crm_update_task,
    crm_complete_task,
    crm_delete_task,
    # 이메일 시퀀스 (Email Sequences)
    crm_list_sequences,
    crm_get_sequence,
    crm_create_sequence,
    crm_update_sequence,
    crm_delete_sequence,
    crm_enroll_sequence,
    crm_pause_sequence,
    crm_sequence_enrollments,
    crm_sequence_dashboard,
    crm_bulk_enroll_sequence,
    crm_sequence_stats,
    # 자동화 (Automations)
    crm_list_automations,
    crm_get_automation,
    crm_create_automation,
    crm_update_automation,
    crm_delete_automation,
    crm_execute_automation,
    crm_automation_history,
    # 폼 (Forms)
    crm_list_forms,
    crm_get_form,
    crm_create_form,
    crm_update_form,
    crm_delete_form,
    crm_submit_form,
    crm_list_form_submissions,
    # 세그먼트 (Segments)
    crm_list_segments,
    crm_get_segment,
    crm_get_segment_contacts,
    crm_create_segment,
    crm_update_segment,
    crm_delete_segment,
    crm_refresh_segment,
    # 대시보드/리포트 (Reports)
    crm_dashboard_summary,
    crm_get_reports,
    crm_report_activities,
    crm_report_lead_sources,
    crm_report_revenue_forecast,
    crm_report_sales_performance,
    # 관계 네트워크 (Relationships)
    crm_create_relationship,
    crm_get_relationships,
    crm_get_network,
    crm_delete_relationship,
    # 이메일 추적 (Email Tracking)
    crm_get_email_tracking,
    crm_get_email_tracking_summary,
    crm_get_sequence_tracking,
    # 미팅 예약 (Meeting Booking)
    crm_create_meeting_booking,
    crm_list_meeting_bookings,
    # 이메일 템플릿 (Email Templates)
    crm_list_templates,
    crm_get_template,
    crm_create_template,
    crm_update_template,
    crm_delete_template,
    crm_render_template,
    crm_duplicate_template,
    # 의료/제약 (Medical/Pharma)
    crm_search_prescriptions,
    crm_prescription_stats,
    crm_search_sales,
    crm_sales_summary,
    crm_search_product_listings,
    crm_search_kol_plans,
    crm_search_hospital_contracts,
    crm_hospital_360,
    crm_hospital_prescriptions,
    crm_hospital_sales,
    crm_doctor_prescriptions,
    crm_prescription_dashboard,
    crm_listing_dashboard,
    crm_territory_dashboard,
    crm_compliance_report,
    crm_sfe_dashboard,
]


def create_crm_mcp_server():
    """Claude Code SDK용 CRM MCP 서버"""
    return create_sdk_mcp_server(
        name="crm",
        version="1.0.0",
        tools=crm_tools
    )
