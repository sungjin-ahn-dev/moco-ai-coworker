"""
자동 감지된 에이전트 후보 spec 저장소.

흐름:
  lifecycle.detect_agent_candidates() → save_candidate(spec) → candidate_id 반환
  → candidate_suggester 가 confirm 발송 시 candidate_id 만 노출
  → 사용자 승인 → operator 가 propose_candidate_agent(candidate_id) 도구 호출
  → store 에서 spec 조회 → propose_agent() 실행 → 결과 status='accepted'/'rejected'

위치: $FILESYSTEM_BASE_DIR/agent_candidates.json (없으면 /home/user/MOCO_DATA/).

스키마 (각 항목):
{
  "candidate_id": "cand_20260609_a1b2",
  "spec": { propose_agent() args },
  "target": { "user_id": "U...", "channel_id": "D...", "user_name": "..." },
  "domain_evidence": "...",
  "status": "pending" | "accepted" | "rejected" | "expired",
  "created_at": "...",
  "consumed_at": null,
  "confirm_id": null,    # request_confirmation 결과
}

TTL: 7일. 그 이상은 expired 처리.
"""

import json
import logging
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CANDIDATE_TTL_DAYS = 7


def _store_path() -> Path:
    from app.config.settings import get_settings
    base = get_settings().FILESYSTEM_BASE_DIR or "/home/user/MOCO_DATA"
    return Path(base) / "agent_candidates.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _load() -> Dict[str, Dict[str, Any]]:
    p = _store_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"[CANDIDATES] 파싱 실패, 빈 저장소로 시작: {e}")
        return {}


def _save(data: Dict[str, Dict[str, Any]]) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _gen_candidate_id() -> str:
    return f"cand_{datetime.now(timezone.utc).strftime('%Y%m%d')}_{secrets.token_hex(3)}"


def save_candidate(
    spec: Dict[str, Any],
    target: Dict[str, str],
    domain_evidence: str = "",
) -> str:
    """후보를 저장하고 candidate_id 반환."""
    candidate_id = _gen_candidate_id()
    data = _load()
    data[candidate_id] = {
        "candidate_id": candidate_id,
        "spec": spec,
        "target": target,
        "domain_evidence": domain_evidence,
        "status": "pending",
        "created_at": _now_iso(),
        "consumed_at": None,
        "confirm_id": None,
    }
    _save(data)
    return candidate_id


def get(candidate_id: str) -> Optional[Dict[str, Any]]:
    return _load().get(candidate_id)


def list_pending() -> List[Dict[str, Any]]:
    return [c for c in _load().values() if c.get("status") == "pending"]


def mark_status(candidate_id: str, status: str, **extra) -> None:
    data = _load()
    if candidate_id not in data:
        return
    data[candidate_id]["status"] = status
    data[candidate_id]["consumed_at"] = _now_iso()
    data[candidate_id].update(extra)
    _save(data)


def expire_old() -> int:
    """TTL 지난 pending 후보를 expired 로 정리. 정리 개수 반환."""
    data = _load()
    cutoff = datetime.now(timezone.utc) - timedelta(days=_CANDIDATE_TTL_DAYS)
    changed = 0
    for c in data.values():
        if c.get("status") != "pending":
            continue
        try:
            created = datetime.fromisoformat(c["created_at"])
            if created < cutoff:
                c["status"] = "expired"
                c["consumed_at"] = _now_iso()
                changed += 1
        except Exception:
            continue
    if changed:
        _save(data)
    return changed


def already_suggested_for(domain_key: str, window_hours: int = 48) -> bool:
    """같은 도메인 키워드로 최근 N시간 내 제안한 적 있으면 True (중복 방지)."""
    if not domain_key:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    needle = domain_key.lower()
    for c in _load().values():
        spec = c.get("spec") or {}
        hay = " ".join([
            spec.get("agent_id", ""),
            spec.get("agent_name", ""),
            spec.get("description", ""),
        ]).lower()
        if needle in hay:
            try:
                created = datetime.fromisoformat(c["created_at"])
                if created >= cutoff:
                    return True
            except Exception:
                continue
    return False
