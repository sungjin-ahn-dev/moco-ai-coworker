"""
Run Log Store — 모든 에이전트 실행을 JSONL 파일에 기록
서버 재시작해도 보존됨. 디버깅/QA/모니터링용.
"""

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)

LOG_DIR = Path("/home/user/MOCO_DATA/logs")
LOG_FILE = LOG_DIR / "runs.jsonl"


class RunLogStore:
    def __init__(self, log_file: Path = LOG_FILE):
        self._log_file = log_file
        self._lock = threading.RLock()
        self._log_file.parent.mkdir(parents=True, exist_ok=True)

    def log_run(
        self,
        run_type: str,           # "simple_chat", "operator", "memory", "schedule", etc.
        user_id: str = "",
        user_name: str = "",
        channel_id: str = "",
        thread_ts: str = "",
        prompt: str = "",
        response: str = "",
        tools_used: list = None,
        state: str = "completed", # "completed", "error", "timeout"
        error: str = "",
        elapsed_seconds: float = 0,
        metadata: dict = None,
    ) -> str:
        """실행 기록을 JSONL에 추가. run_id 반환."""
        run_id = str(uuid4())[:8]
        entry = {
            "run_id": run_id,
            "type": run_type,
            "user_id": user_id,
            "user_name": user_name,
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "prompt": prompt[:500],        # 프롬프트 500자 제한
            "response": response[:500],    # 응답 500자 제한
            "tools_used": tools_used or [],
            "tool_count": len(tools_used) if tools_used else 0,
            "state": state,
            "error": error[:200] if error else "",
            "elapsed_seconds": round(elapsed_seconds, 2),
            "metadata": metadata or {},
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        with self._lock:
            try:
                with self._log_file.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.error(f"[RUN_LOG] Write failed: {e}")

        return run_id

    def tail(self, limit: int = 50) -> list:
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
            except Exception as e:
                logger.error(f"[RUN_LOG] Read failed: {e}")
                return []

    def search(self, user_id: str = None, run_type: str = None,
               state: str = None, limit: int = 20) -> list:
        """조건 검색"""
        all_entries = self.tail(500)
        results = []
        for entry in reversed(all_entries):
            if user_id and entry.get("user_id") != user_id:
                continue
            if run_type and entry.get("type") != run_type:
                continue
            if state and entry.get("state") != state:
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        return results

    def stats(self, hours: int = 24) -> dict:
        """최근 N시간 통계"""
        cutoff = datetime.now().timestamp() - (hours * 3600)
        entries = self.tail(1000)

        recent = []
        for e in entries:
            try:
                ts = datetime.strptime(e["created_at"], "%Y-%m-%d %H:%M:%S").timestamp()
                if ts >= cutoff:
                    recent.append(e)
            except:
                pass

        total = len(recent)
        by_type = {}
        by_user = {}
        by_state = {}
        total_time = 0

        for e in recent:
            t = e.get("type", "unknown")
            u = e.get("user_name", "unknown")
            s = e.get("state", "unknown")
            by_type[t] = by_type.get(t, 0) + 1
            by_user[u] = by_user.get(u, 0) + 1
            by_state[s] = by_state.get(s, 0) + 1
            total_time += e.get("elapsed_seconds", 0)

        # 일별 분포
        daily = {}
        for e in recent:
            day = e.get("created_at", "")[:10]
            if day:
                if day not in daily:
                    daily[day] = {"total": 0, "completed": 0, "error": 0}
                daily[day]["total"] += 1
                s = e.get("state", "")
                if s == "completed":
                    daily[day]["completed"] += 1
                elif s == "error":
                    daily[day]["error"] += 1

        return {
            "total_runs": total,
            "by_type": by_type,
            "by_user": by_user,
            "by_state": by_state,
            "avg_elapsed": round(total_time / total, 2) if total else 0,
            "total_elapsed": round(total_time, 2),
            "period_hours": hours,
            "daily": dict(sorted(daily.items())),
        }

    def stats_by_date(self, date_from: str = "", date_to: str = "") -> dict:
        """날짜 범위 통계"""
        entries = self.tail(5000)
        filtered = []
        for e in entries:
            day = e.get("created_at", "")[:10]
            if date_from and day < date_from:
                continue
            if date_to and day > date_to:
                continue
            filtered.append(e)

        total = len(filtered)
        by_type = {}
        by_user = {}
        by_state = {}
        total_time = 0
        daily = {}

        for e in filtered:
            t = e.get("type", "unknown")
            u = e.get("user_name", "unknown")
            s = e.get("state", "unknown")
            by_type[t] = by_type.get(t, 0) + 1
            by_user[u] = by_user.get(u, 0) + 1
            by_state[s] = by_state.get(s, 0) + 1
            total_time += e.get("elapsed_seconds", 0)
            day = e.get("created_at", "")[:10]
            if day:
                if day not in daily:
                    daily[day] = {"total": 0, "completed": 0, "error": 0}
                daily[day]["total"] += 1
                if s == "completed":
                    daily[day]["completed"] += 1
                elif s == "error":
                    daily[day]["error"] += 1

        return {
            "total_runs": total,
            "by_type": by_type,
            "by_user": by_user,
            "by_state": by_state,
            "avg_elapsed": round(total_time / total, 2) if total else 0,
            "total_elapsed": round(total_time, 2),
            "period": f"{date_from} ~ {date_to}",
            "daily": dict(sorted(daily.items())),
        }


# 싱글톤
run_log = RunLogStore()
