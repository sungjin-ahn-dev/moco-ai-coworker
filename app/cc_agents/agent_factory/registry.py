"""
생성된 에이전트의 레지스트리.

위치: $FILESYSTEM_BASE_DIR/agents_registry.json (없으면 /home/user/MOCO_DATA/)

상태 머신:
  pending  → 승인 대기 (Slack DM 발송됨)
  approved → publish 완료, generated/ 디렉토리에 활성
  disabled → 사용자 또는 관리자가 비활성화
  archived → 30일 미사용 자동 archive
  rejected → 승인자가 반려

스키마 (각 항목):
{
  "agent_id": "mfds_tracker",
  "agent_name": "🛰️ 식약처 동향 트래커",
  "description": "...",
  "system_prompt_preview": "첫 500자",
  "model_tier": "MODERATE",
  "allowed_tools": [...],
  "corpus_dir": "...",
  "created_by": "user2@example.com",
  "created_at": "2026-06-09T14:31:00",
  "approver_slack_id": "U...",
  "approved_at": "2026-06-09T14:35:00" | null,
  "status": "approved",
  "usage_count": 0,
  "last_used_at": null,
  "rejection_reason": null,
  "examples": [...]
}
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _registry_path() -> Path:
    from app.config.settings import get_settings
    base = get_settings().FILESYSTEM_BASE_DIR or "/home/user/MOCO_DATA"
    return Path(base) / "agents_registry.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _load_all() -> Dict[str, Dict[str, Any]]:
    p = _registry_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"[AGENT_REGISTRY] 파싱 실패, 빈 레지스트리로 시작: {e}")
        return {}


def _save_all(data: Dict[str, Dict[str, Any]]) -> None:
    p = _registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)  # atomic


def upsert(entry: Dict[str, Any]) -> None:
    agent_id = entry["agent_id"]
    data = _load_all()
    existing = data.get(agent_id, {})
    existing.update(entry)
    data[agent_id] = existing
    _save_all(data)


def get(agent_id: str) -> Optional[Dict[str, Any]]:
    return _load_all().get(agent_id)


def list_all(status: Optional[str] = None) -> List[Dict[str, Any]]:
    data = _load_all()
    items = list(data.values())
    if status:
        items = [i for i in items if i.get("status") == status]
    return items


def list_active() -> List[Dict[str, Any]]:
    """UI 카드에 노출할 에이전트만 (approved 상태)."""
    return list_all(status="approved")


def set_status(agent_id: str, status: str, **extra) -> None:
    data = _load_all()
    if agent_id not in data:
        raise KeyError(f"unknown agent_id: {agent_id}")
    data[agent_id]["status"] = status
    data[agent_id].update(extra)
    _save_all(data)


def record_usage(agent_id: str) -> None:
    """호출 1회 발생. last_used_at 갱신 + usage_count 증분."""
    data = _load_all()
    if agent_id not in data:
        return
    data[agent_id]["usage_count"] = data[agent_id].get("usage_count", 0) + 1
    data[agent_id]["last_used_at"] = _now_iso()
    _save_all(data)


def create_pending(
    *,
    agent_id: str,
    agent_name: str,
    description: str,
    system_prompt: str,
    model_tier: str,
    allowed_tools: List[str],
    corpus_dir: str,
    created_by: str,
    examples: Optional[List[str]] = None,
    approver_slack_id: str = "",
) -> Dict[str, Any]:
    """pending 상태 항목 신규 생성."""
    entry = {
        "agent_id": agent_id,
        "agent_name": agent_name,
        "description": description,
        "system_prompt_preview": system_prompt[:500],
        "system_prompt_full_hash": str(hash(system_prompt)),
        "model_tier": model_tier,
        "allowed_tools": allowed_tools,
        "corpus_dir": corpus_dir,
        "created_by": created_by,
        "created_at": _now_iso(),
        "approver_slack_id": approver_slack_id,
        "approved_at": None,
        "status": "pending",
        "usage_count": 0,
        "last_used_at": None,
        "rejection_reason": None,
        "examples": examples or [],
    }
    upsert(entry)
    return entry
