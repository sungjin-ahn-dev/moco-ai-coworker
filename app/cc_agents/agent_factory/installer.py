"""
생성된 에이전트의 디스크 배치 + 운영 프로세스 hot-reload.

흐름:
1. /tmp/<agent_id> 에 임시 작성 (validator 가 사용)
2. 검증 통과 시 atomic move → app/cc_agents/generated/<agent_id>/
3. importlib.reload() 로 운영 프로세스에 즉시 노출
4. routes 의 _AGENT_STREAMERS 에 동적 등록

실패 시 자동 롤백 (rename 되돌리기 + registry 에서 항목 status='rejected').
"""

import importlib
import logging
import shutil
import sys
from pathlib import Path
from typing import Optional

from app.cc_agents.agent_factory import registry, template

logger = logging.getLogger(__name__)


GENERATED_PKG_DIR = Path(__file__).resolve().parent.parent / "generated"
TEMP_STAGING_DIR = Path("/tmp/moco_agent_factory")


def stage_to_temp(*, agent_id: str, agent_source: str) -> Path:
    """임시 디렉토리에 .py 파일 작성. validator 가 이 경로를 검증."""
    template.validate_agent_id(agent_id)
    stage_dir = TEMP_STAGING_DIR / agent_id
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    template.write_agent_files(stage_dir, agent_source)
    return stage_dir


def promote_to_generated(stage_dir: Path, agent_id: str) -> Path:
    """검증 통과한 stage_dir 를 app/cc_agents/generated/<agent_id>/ 로 원자적 이동."""
    template.validate_agent_id(agent_id)
    GENERATED_PKG_DIR.mkdir(parents=True, exist_ok=True)
    target = GENERATED_PKG_DIR / agent_id

    # 기존 같은 id 폴더 백업
    backup: Optional[Path] = None
    if target.exists():
        backup = target.with_suffix(".bak")
        if backup.exists():
            shutil.rmtree(backup)
        target.rename(backup)

    try:
        shutil.move(str(stage_dir), str(target))
    except Exception:
        # 실패하면 백업 복원
        if backup is not None:
            backup.rename(target)
        raise

    # 성공하면 백업 제거
    if backup is not None and backup.exists():
        shutil.rmtree(backup)

    # __init__.py 가 없다면 만들기 (패키지 인식)
    init_py = GENERATED_PKG_DIR / "__init__.py"
    if not init_py.exists():
        init_py.write_text(
            "# auto-generated agents directory\n"
            "# 이 폴더의 에이전트는 agent_factory 가 생성·관리합니다.\n",
            encoding="utf-8",
        )
    return target


def hot_reload(agent_id: str) -> None:
    """운영 프로세스에서 새 에이전트를 import (재시작 없이 노출)."""
    mod_path = f"app.cc_agents.generated.{agent_id}"
    if mod_path in sys.modules:
        importlib.reload(sys.modules[mod_path])
        # agent 서브모듈도 같이
        if f"{mod_path}.agent" in sys.modules:
            importlib.reload(sys.modules[f"{mod_path}.agent"])
    else:
        importlib.import_module(mod_path)


def get_streamer(agent_id: str):
    """레지스트리에서 approved 상태 에이전트의 stream_for_web 콜러블 반환."""
    mod = importlib.import_module(f"app.cc_agents.generated.{agent_id}")
    return getattr(mod, "stream_for_web")


def rollback(agent_id: str, reason: str = "") -> None:
    """generated/<agent_id> 제거 + registry 에 status='rejected'."""
    target = GENERATED_PKG_DIR / agent_id
    if target.exists():
        shutil.rmtree(target)
    sys.modules.pop(f"app.cc_agents.generated.{agent_id}", None)
    sys.modules.pop(f"app.cc_agents.generated.{agent_id}.agent", None)
    if registry.get(agent_id):
        registry.set_status(agent_id, "rejected", rejection_reason=reason)
    logger.info(f"[AGENT_FACTORY] rolled back {agent_id}: {reason}")
