"""
generated/ 격리 로더.

시작 시 또는 hot-reload 후 호출. 깨진 에이전트가 있어도 try/except 로
나머지를 살린다. 결과는 {agent_id: stream_for_web_callable} 매핑.
"""

import importlib
import logging
from pathlib import Path
from typing import Callable, Dict

logger = logging.getLogger(__name__)

_GENERATED_DIR = Path(__file__).resolve().parent


def load_all_generated_streamers() -> Dict[str, Callable]:
    """
    generated/ 의 모든 서브 패키지를 try/except 로 import.

    Returns:
        dict {agent_id: stream_for_web_callable}
    """
    streamers: Dict[str, Callable] = {}

    for child in _GENERATED_DIR.iterdir():
        if not child.is_dir():
            continue
        if child.name.startswith("_") or child.name.startswith("."):
            continue
        if not (child / "agent.py").exists():
            continue

        agent_id = child.name
        mod_path = f"app.cc_agents.generated.{agent_id}"
        try:
            mod = importlib.import_module(mod_path)
            streamer = getattr(mod, "stream_for_web", None)
            if streamer is None:
                logger.warning(f"[GENERATED_LOADER] {agent_id}: stream_for_web 없음, 건너뜀")
                continue
            streamers[agent_id] = streamer
            logger.info(f"[GENERATED_LOADER] ✓ loaded: {agent_id}")
        except Exception as e:
            logger.warning(f"[GENERATED_LOADER] ✗ failed: {agent_id}: {e}")
            # 깨진 거 있어도 나머지는 계속

    return streamers


def reload_one(agent_id: str) -> Callable:
    """단일 에이전트만 reload."""
    import sys
    mod_path = f"app.cc_agents.generated.{agent_id}"
    sub_path = f"{mod_path}.agent"
    if sub_path in sys.modules:
        importlib.reload(sys.modules[sub_path])
    if mod_path in sys.modules:
        importlib.reload(sys.modules[mod_path])
    else:
        importlib.import_module(mod_path)
    return getattr(sys.modules[mod_path], "stream_for_web")
