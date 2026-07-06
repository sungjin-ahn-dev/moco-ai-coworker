"""
에이전트 라이프사이클 관리 (Phase 2/3).

호출 지점:
- 매 사용 시 registry.record_usage(agent_id) (routes.py 에서 이미 호출)
- 일일/주간 스케줄러에서 archive_unused_agents() 호출
- 일 1회 detect_agent_candidates() 로 새 에이전트 후보 자동 감지

Phase 2 구현:
- detect_agent_candidates(): 메모리 분석 → 도메인 후보 + 5-슬롯 spec 자동 작성

Phase 3 미구현:
- 품질 신호 (👎 버튼) — UI 와 함께 추가 예정
- 버전 히스토리 / 롤백 UI — registry 에 versions 배열 추가 예정
"""

import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

from app.cc_agents.agent_factory import installer, registry

logger = logging.getLogger(__name__)


def archive_unused_agents(idle_days: int = 30) -> List[str]:
    """
    지난 idle_days 동안 호출 0회인 generated 에이전트를 archive.

    archive = status='archived' + generated/<id>/ 디렉토리 유지(롤백 가능),
              routes 의 _AGENT_STREAMERS 에서 제거.

    Returns: archive된 agent_id 리스트.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=idle_days)
    archived: List[str] = []

    for entry in registry.list_active():
        last_iso = entry.get("last_used_at")
        usage = entry.get("usage_count", 0)
        if usage > 0 and last_iso:
            try:
                last = datetime.fromisoformat(last_iso)
                if last >= cutoff:
                    continue
            except Exception:
                continue
        # 호출 0회 + 30일 경과 / 또는 last_used 가 cutoff 이전
        aid = entry["agent_id"]
        registry.set_status(aid, "archived")

        # routes 에서 제거 (운영 프로세스 한정)
        try:
            from app.cc_web_interface.chat import routes as chat_routes
            chat_routes._AGENT_STREAMERS.pop(aid, None)
        except Exception:
            pass

        archived.append(aid)
        logger.info(f"[AGENT_LIFECYCLE] archived: {aid} (usage={usage}, last={last_iso})")

    return archived


def unarchive(agent_id: str) -> None:
    """archive 된 에이전트 다시 활성화."""
    entry = registry.get(agent_id)
    if entry is None:
        raise KeyError(agent_id)
    if entry["status"] != "archived":
        return
    # 디렉토리 살아있어야 함
    target = installer.GENERATED_PKG_DIR / agent_id
    if not target.exists():
        raise FileNotFoundError(f"generated/{agent_id}/ 디렉토리 없음, unarchive 불가")
    # 다시 로드 + routes 등록
    installer.hot_reload(agent_id)
    try:
        from app.cc_web_interface.chat import routes as chat_routes
        chat_routes._AGENT_STREAMERS[agent_id] = installer.get_streamer(agent_id)
    except Exception as e:
        logger.error(f"[AGENT_LIFECYCLE] unarchive routes 등록 실패: {e}")
    registry.set_status(agent_id, "approved")


def disable_agent(agent_id: str, reason: str = "manual_disable") -> None:
    """사용자/관리자가 명시적으로 에이전트 비활성화 (UI '이 에이전트 별로' 버튼 등)."""
    entry = registry.get(agent_id)
    if entry is None:
        raise KeyError(agent_id)
    registry.set_status(agent_id, "disabled", rejection_reason=reason)
    # routes 에서 제거
    try:
        from app.cc_web_interface.chat import routes as chat_routes
        chat_routes._AGENT_STREAMERS.pop(agent_id, None)
    except Exception:
        pass
    logger.info(f"[AGENT_LIFECYCLE] disabled: {agent_id} ({reason})")


# =============================================
# Phase 2 — 자동 감지
# =============================================

# 후보 검출에서 제외할 (이미 일반 MOCO 가 처리하는) 도메인 키워드
# - 단순 잡담, 일정관리 등은 기본 에이전트로 충분
_GENERIC_DOMAINS = {
    "잡담", "인사", "일정", "회의", "메모", "할일", "todo",
}

# 메모리 스캔 시 한 도메인을 "반복 등장" 으로 인정할 최소 횟수
_MIN_OCCURRENCE_DEFAULT = 5

# 분석에 사용할 메모리 파일 최대 개수 (LLM 입력 폭주 방지)
_MAX_FILES_PER_SCAN = 40


def _memories_root() -> Path:
    from app.config.settings import get_settings
    base = get_settings().FILESYSTEM_BASE_DIR or os.getcwd()
    return Path(base) / "memories"


def _collect_recent_memory_summary(window_days: int) -> Dict[str, Any]:
    """
    최근 window_days 일 내 수정된 메모리 파일을 모아 요약 입력을 만든다.

    Returns:
        {
            "files_scanned": int,
            "channels": [{"path": str, "preview": str}, ...],
            "projects": [...],
            "users": [...],
            "decisions": [...],
        }
    """
    root = _memories_root()
    if not root.exists():
        return {"files_scanned": 0, "channels": [], "projects": [], "users": [], "decisions": []}

    cutoff = datetime.now(timezone.utc).timestamp() - window_days * 86400

    summary: Dict[str, List[Dict[str, str]]] = {
        "channels": [], "projects": [], "users": [], "decisions": [],
    }

    files_scanned = 0
    for category in summary.keys():
        cat_dir = root / category
        if not cat_dir.exists():
            continue
        # 최근 수정 파일 우선
        candidates = sorted(
            cat_dir.rglob("*.md"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )
        for path in candidates:
            try:
                if path.stat().st_mtime < cutoff:
                    continue
                preview = path.read_text(encoding="utf-8", errors="ignore")[:800]
                summary[category].append({
                    "path": str(path.relative_to(root)),
                    "preview": preview,
                })
                files_scanned += 1
                if files_scanned >= _MAX_FILES_PER_SCAN:
                    break
            except Exception:
                continue
        if files_scanned >= _MAX_FILES_PER_SCAN:
            break

    return {"files_scanned": files_scanned, **summary}


def _existing_agent_ids() -> List[str]:
    return [e["agent_id"] for e in registry.list_all()]


_CANDIDATE_SCHEMA_HINT = """
[
  {
    "agent_id": "mfds_tracker",
    "agent_name": "🛰️ 식약처 동향 트래커",
    "description": "매주 식약처 가이드라인 변경 모니터링",
    "system_prompt": "당신은 ... (200자 이상, 페르소나/원칙/응답포맷 포함)",
    "model_tier": "MODERATE",
    "allowed_tools": ["Read", "Glob", "Grep", "WebFetch", "WebSearch", "mcp__time__*"],
    "corpus_dir": "",
    "examples": ["...", "...", "..."],
    "domain_evidence": "어떤 메모리 파일에서 어떤 신호로 이 도메인을 발견했는지 1~2줄",
    "target_user": "이 에이전트가 가장 도움될 사용자 이름 (메모리에서 추정)",
    "target_user_id": "U... (users 메모리에서 매칭한 user_id, 못 찾으면 빈 문자열)",
    "target_channel_id": "D... 또는 C... (channels 메모리에서 매칭, 못 찾으면 빈 문자열)"
  }
]
""".strip()


_DETECT_SYSTEM_PROMPT = """당신은 사용자의 업무 메모리를 분석하여, **새 전문 에이전트를 만들면 좋을 도메인** 을 찾아내는 분석가입니다.

# 판단 기준

후보 도메인의 조건 (모두 충족해야 함):
1. 같은 주제·키워드가 여러 파일에 걸쳐 반복 등장 (최소 3~5회)
2. 특정 사용자/팀이 해당 도메인에 지속적으로 시간을 쓰고 있음
3. 도메인이 충분히 구체적 (예: "마케팅" 같은 광범위 도메인은 제외, "식약처 규제 모니터링" 같은 구체 도메인 OK)
4. 기존 일반 MOCO 응답으로 부족한 전문성·반복성이 있음
5. 이미 존재하는 에이전트와 중복되지 않음

# 출력

- 후보가 없으면: 빈 배열 `[]`
- 후보가 있으면: 아래 스키마의 JSON 배열만 출력 (설명 텍스트 금지)
- 최대 2개까지만 (스팸 방지)

스키마:
""" + _CANDIDATE_SCHEMA_HINT + """

# 주의사항

- agent_id: 영문 소문자/숫자/언더스코어, 2~40자
- allowed_tools: Read/Glob/Grep/WebFetch/WebSearch/mcp__time__* 또는 mcp__*__* 만
- model_tier: 빠른 분류면 SIMPLE, 일반 분석이면 MODERATE, 복잡 추론이면 COMPLEX
- system_prompt: 페르소나 + 원칙 + 응답 포맷 포함, 200자 이상
- examples: 3~5개의 자연스러운 사용자 질문
- target_user_id / target_channel_id: users/ channels/ 메모리에서 YAML frontmatter 의 user_id / channel_id 와 일치하는 것만. 못 찾으면 빈 문자열.

JSON 외 다른 텍스트를 출력하면 안 됩니다."""


def _extract_json_array(text: str) -> List[Dict[str, Any]]:
    """LLM 응답에서 첫 JSON 배열을 추출."""
    if not text:
        return []
    # ```json ... ``` 또는 첫 [ 부터 마지막 ] 까지
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        raw = text[start:end + 1]
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"[AGENT_DETECT] JSON 파싱 실패: {e}; raw[:200]={raw[:200]!r}")
        return []


def _filter_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """기존 agent_id 중복 / 도메인 일반성 / 필수 필드 누락 필터."""
    existing = set(_existing_agent_ids())
    out: List[Dict[str, Any]] = []
    for c in candidates:
        aid = (c.get("agent_id") or "").strip()
        if not aid or aid in existing:
            continue
        desc = (c.get("description") or "").lower()
        if any(g in desc for g in _GENERIC_DOMAINS):
            continue
        if not c.get("agent_name") or not c.get("system_prompt"):
            continue
        if len(c["system_prompt"]) < 100:
            continue
        out.append(c)
    return out


async def detect_agent_candidates(
    window_days: int = 30,
    max_candidates: int = 2,
) -> List[Dict[str, Any]]:
    """
    메모리 분석으로 새 에이전트 후보 도메인 감지.

    Returns:
        propose_agent() 의 args 로 그대로 넘길 수 있는 dict 의 리스트.
        각 항목은 target_user / target_user_id / target_channel_id 등의
        추가 필드를 포함 (suggester 가 confirm 발송 시 사용).
    """
    from app.config.settings import get_settings
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage
    from app.cc_utils.sdk_retry import RetryableSDKClient
    from app.cc_utils.prompt_helper import prepare_options

    settings = get_settings()
    if not settings.AGENT_FACTORY_ENABLED:
        return []

    memory_summary = _collect_recent_memory_summary(window_days)
    if memory_summary["files_scanned"] == 0:
        logger.info("[AGENT_DETECT] 최근 메모리 변경 없음 → skip")
        return []

    existing_ids = _existing_agent_ids()

    # LLM 입력 페이로드 — 큰 파일 본문은 잘라서 전달
    payload = {
        "window_days": window_days,
        "files_scanned": memory_summary["files_scanned"],
        "existing_agent_ids": existing_ids,
        "channels": memory_summary["channels"],
        "projects": memory_summary["projects"],
        "users": memory_summary["users"],
        "decisions": memory_summary["decisions"],
    }

    user_query = (
        f"다음 메모리 스냅샷을 분석해서 후보 도메인 최대 {max_candidates}개를 JSON 배열로 반환하세요. "
        f"JSON 외 텍스트는 절대 출력하지 마세요.\n\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False)[:30000]}\n```"
    )

    options = ClaudeAgentOptions(
        system_prompt=_DETECT_SYSTEM_PROMPT,
        model=settings.MODEL_FOR_MODERATE,
        permission_mode="bypassPermissions",
        allowed_tools=[],
        disallowed_tools=["Bash", "Write", "Edit", "WebFetch"],
        setting_sources=['project'],
        cwd=os.getcwd(),
    )
    options = prepare_options(options)

    try:
        async with RetryableSDKClient(options, max_retries=2, agent_name="AGENT_DETECT") as client:
            await client.query(user_query)
            result_text = ""
            async for message in client.receive_response():
                if isinstance(message, ResultMessage):
                    result_text = message.result or ""
                    break
    except Exception as e:
        logger.error(f"[AGENT_DETECT] SDK 호출 실패: {e}")
        return []

    candidates = _extract_json_array(result_text)
    filtered = _filter_candidates(candidates)
    logger.info(
        f"[AGENT_DETECT] files_scanned={memory_summary['files_scanned']} "
        f"raw={len(candidates)} filtered={len(filtered)}"
    )
    return filtered[:max_candidates]
