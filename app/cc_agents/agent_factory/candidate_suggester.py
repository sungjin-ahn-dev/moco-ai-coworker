"""
자동 에이전트 후보 제안 (Phase 2 — 스케줄러 진입점).

흐름:
  스케줄러 → call_agent_candidate_suggester()
   ├─ candidates_store.expire_old()                  # 만료 정리
   ├─ lifecycle.detect_agent_candidates()            # 메모리 분석 → 후보 spec 리스트
   └─ 각 후보별:
        ├─ already_suggested_for() 중복 차단
        ├─ candidates_store.save_candidate()         # spec 저장 → candidate_id
        ├─ add_confirm_request()                     # 사용자에게 confirm DB 등록
        └─ Slack chat_postMessage()                  # 실제 제안 메시지 발송

사용자가 "응" 하면 proactive_confirm 이 original_request_text 를 봇에게 보내고,
operator 가 [AGENT_CANDIDATE:<id>] 토큰을 인식해서 propose_candidate_agent 도구 호출.
"""

import logging
import uuid
from typing import Any, Dict, List

from app.cc_agents.agent_factory import candidates_store, lifecycle

logger = logging.getLogger(__name__)


def _build_confirm_message(spec: Dict[str, Any], evidence: str) -> str:
    """사용자에게 보낼 짧은 제안 메시지."""
    name = spec.get("agent_name") or spec.get("agent_id", "새 에이전트")
    desc = spec.get("description") or ""
    base = f"요즘 관련 업무를 자주 다루시는 것 같아서요. **{name}** 만들어드릴까요?"
    if desc:
        base += f"\n• 역할: {desc}"
    if evidence:
        # 너무 길지 않게 cut
        base += f"\n• 감지: {evidence[:140]}"
    return base


def _build_original_request_text(bot_name: str, candidate_id: str, agent_name: str) -> str:
    """승인 시 봇에게 보낼 명령. operator 가 [AGENT_CANDIDATE:<id>] 를 인식."""
    return (
        f"{bot_name}님, 자동 감지된 후보 에이전트 [AGENT_CANDIDATE:{candidate_id}] "
        f"({agent_name}) 를 propose_candidate_agent 도구로 만들어주세요."
    )


async def _send_confirm_to_slack(
    *,
    channel_id: str,
    confirm_message: str,
) -> str:
    """Slack 으로 confirm 메시지 직접 발송. 발송된 ts 반환 (없으면 빈 문자열)."""
    from app.cc_tools.confirm.confirm_tools import get_slack_client
    try:
        client = get_slack_client()
        resp = await client.chat_postMessage(channel=channel_id, text=confirm_message)
        return resp.data.get("ts", "") if hasattr(resp, "data") else ""
    except Exception as e:
        logger.error(f"[AGENT_CANDIDATE_SUGGESTER] Slack 발송 실패: {e}")
        return ""


async def call_agent_candidate_suggester() -> str:
    """
    스케줄러가 호출. 메모리 분석으로 새 에이전트 후보 감지 → 사용자에게 confirm.

    Returns:
        실행 결과 요약 (로그용).
    """
    from app.config.settings import get_settings
    from app.cc_utils.confirm_db import add_confirm_request

    settings = get_settings()
    if not getattr(settings, "AGENT_CANDIDATE_SUGGESTER_ENABLED", False):
        return "disabled"
    if not settings.AGENT_FACTORY_ENABLED:
        return "agent_factory_disabled"

    # 만료 정리 (TTL 7일)
    expired = candidates_store.expire_old()
    if expired:
        logger.info(f"[AGENT_CANDIDATE_SUGGESTER] expired {expired} stale candidates")

    window_days = getattr(settings, "AGENT_CANDIDATE_DETECT_WINDOW_DAYS", 30)
    candidates: List[Dict[str, Any]] = await lifecycle.detect_agent_candidates(
        window_days=window_days,
        max_candidates=2,
    )
    if not candidates:
        return "no_candidates"

    bot_name = settings.BOT_NAME or "MOCO"
    sent = 0
    skipped = 0
    for c in candidates:
        agent_id = c.get("agent_id", "")
        agent_name = c.get("agent_name", agent_id)
        target_user_id = (c.get("target_user_id") or "").strip()
        target_channel_id = (c.get("target_channel_id") or "").strip()
        target_user_name = (c.get("target_user") or "").strip()
        evidence = (c.get("domain_evidence") or "").strip()

        # target 정보 없으면 confirm 발송 불가
        if not target_user_id or not target_channel_id or not target_user_name:
            logger.info(
                f"[AGENT_CANDIDATE_SUGGESTER] skip '{agent_id}': target 정보 부족 "
                f"(user_id={target_user_id!r}, channel_id={target_channel_id!r})"
            )
            skipped += 1
            continue

        # 중복 차단 (같은 도메인 키워드로 48시간 내 제안한 적 있으면)
        domain_key = agent_id or c.get("description", "")[:20]
        if candidates_store.already_suggested_for(domain_key, window_hours=48):
            logger.info(f"[AGENT_CANDIDATE_SUGGESTER] skip '{agent_id}': 48h 내 중복 제안")
            skipped += 1
            continue

        # spec 만 따로 추출 (target_* 등 메타필드 제외)
        spec_keys = {
            "agent_id", "agent_name", "description", "system_prompt",
            "model_tier", "allowed_tools", "corpus_dir", "examples",
        }
        spec = {k: v for k, v in c.items() if k in spec_keys}
        spec.setdefault("created_by", "auto_detect")

        candidate_id = candidates_store.save_candidate(
            spec=spec,
            target={
                "user_id": target_user_id,
                "channel_id": target_channel_id,
                "user_name": target_user_name,
            },
            domain_evidence=evidence,
        )

        confirm_id = str(uuid.uuid4())
        confirm_message = _build_confirm_message(spec, evidence)
        original_request_text = _build_original_request_text(
            bot_name=bot_name,
            candidate_id=candidate_id,
            agent_name=agent_name,
        )

        ok = add_confirm_request(
            confirm_id=confirm_id,
            channel_id=target_channel_id,
            user_id=target_user_id,
            user_name=target_user_name,
            confirm_message=confirm_message,
            original_request_text=original_request_text,
            thread_ts=None,
        )
        if not ok:
            logger.warning(f"[AGENT_CANDIDATE_SUGGESTER] confirm DB 저장 실패: {confirm_id}")
            skipped += 1
            continue

        ts = await _send_confirm_to_slack(
            channel_id=target_channel_id,
            confirm_message=confirm_message,
        )
        if not ts:
            logger.warning(f"[AGENT_CANDIDATE_SUGGESTER] Slack 발송 실패: {candidate_id}")
            skipped += 1
            continue

        candidates_store.mark_status(
            candidate_id,
            "pending",  # 이미 pending 이지만 confirm_id 기록 용도
            confirm_id=confirm_id,
        )
        logger.info(
            f"[AGENT_CANDIDATE_SUGGESTER] proposed candidate={candidate_id} "
            f"agent_id={agent_id} → user={target_user_name} ({target_user_id})"
        )
        sent += 1

    return f"sent={sent} skipped={skipped} total={len(candidates)}"
