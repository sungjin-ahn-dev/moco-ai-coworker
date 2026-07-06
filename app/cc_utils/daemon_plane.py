"""
Daemon Plane — 운영 모니터링
- MCP 서버 상태 추적
- 활성 세션 모니터링
- 구조화된 이벤트 로그 (JSONL)
- 리소스 레지스트리
"""

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

EVENT_LOG = Path("/home/user/MOCO_DATA/logs/daemon_events.jsonl")


class DaemonEventStore:
    """구조화된 이벤트 로그 (JSONL)"""

    def __init__(self, log_file: Path = EVENT_LOG):
        self._log_file = log_file
        self._lock = threading.RLock()
        self._log_file.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event_type: str, data: dict = None):
        """이벤트 기록"""
        entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "event": event_type,
            "data": data or {},
        }
        with self._lock:
            try:
                with self._log_file.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.error(f"[DAEMON] Event write failed: {e}")

    def tail(self, limit: int = 100) -> list:
        """최근 N건 조회"""
        with self._lock:
            try:
                if not self._log_file.exists():
                    return []
                lines = self._log_file.read_text(encoding="utf-8").strip().split("\n")
                entries = []
                for line in lines[-limit:]:
                    if line.strip():
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
                return entries
            except Exception:
                return []


class DaemonResourceRegistry:
    """리소스 상태 추적"""

    def __init__(self):
        self._resources: dict[str, dict] = {}
        self._lock = threading.RLock()

    def register(self, resource_type: str, name: str, state: str = "active", meta: dict = None):
        """리소스 등록/업데이트"""
        key = f"{resource_type}:{name}"
        with self._lock:
            self._resources[key] = {
                "type": resource_type,
                "name": name,
                "state": state,
                "meta": meta or {},
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

    def unregister(self, resource_type: str, name: str):
        """리소스 해제"""
        key = f"{resource_type}:{name}"
        with self._lock:
            self._resources.pop(key, None)

    def get_all(self) -> list:
        """모든 리소스 조회"""
        with self._lock:
            return list(self._resources.values())

    def get_by_type(self, resource_type: str) -> list:
        """타입별 리소스 조회"""
        with self._lock:
            return [r for r in self._resources.values() if r["type"] == resource_type]

    def summary(self) -> dict:
        """리소스 요약"""
        with self._lock:
            by_type = {}
            for r in self._resources.values():
                t = r["type"]
                if t not in by_type:
                    by_type[t] = {"total": 0, "active": 0, "error": 0}
                by_type[t]["total"] += 1
                if r["state"] == "active":
                    by_type[t]["active"] += 1
                elif r["state"] == "error":
                    by_type[t]["error"] += 1
            return by_type


class DaemonPlane:
    """통합 운영 모니터링"""

    def __init__(self):
        self.events = DaemonEventStore()
        self.resources = DaemonResourceRegistry()
        self._start_time = time.time()

    def startup(self):
        """서버 시작 이벤트"""
        self.events.emit("daemon.started", {"pid": __import__("os").getpid()})

    def register_mcp(self, name: str, state: str = "active"):
        """MCP 서버 등록"""
        self.resources.register("mcp", name, state)
        self.events.emit("mcp.registered", {"name": name, "state": state})

    def mcp_error(self, name: str, error: str):
        """MCP 서버 에러"""
        self.resources.register("mcp", name, "error", {"error": error})
        self.events.emit("mcp.error", {"name": name, "error": error})

    def register_channel(self, name: str, state: str = "connected"):
        """채널 어댑터 등록"""
        self.resources.register("channel", name, state)
        self.events.emit("channel.registered", {"name": name, "state": state})

    def channel_error(self, name: str, error: str):
        """채널 에러"""
        self.resources.register("channel", name, "error", {"error": error})
        self.events.emit("channel.error", {"name": name, "error": error})

    def session_started(self, session_id: str):
        """세션 시작"""
        self.resources.register("session", session_id, "active")
        self.events.emit("session.started", {"session_id": session_id})

    def session_ended(self, session_id: str):
        """세션 종료"""
        self.resources.unregister("session", session_id)
        self.events.emit("session.ended", {"session_id": session_id})

    def scheduler_event(self, schedule_name: str, state: str):
        """스케줄러 이벤트"""
        self.events.emit("scheduler.run", {"name": schedule_name, "state": state})

    def get_status(self) -> dict:
        """전체 상태 조회"""
        from app.queueing_extended import session_manager

        uptime = int(time.time() - self._start_time)
        hours = uptime // 3600
        minutes = (uptime % 3600) // 60

        return {
            "state": "running",
            "uptime": f"{hours}h {minutes}m",
            "uptime_seconds": uptime,
            "active_sessions": session_manager.active_count,
            "total_lanes": session_manager.total_lanes,
            "resources": self.resources.summary(),
            "resource_details": self.resources.get_all(),
        }


# 싱글톤
daemon = DaemonPlane()
