"""
outbox — at-least-once 부작용 아웃박스 (crash-safe transactional outbox).

현재 부작용(메모리 저장·태스크 생성 등)은 인메모리 큐(enqueue_*)에 실려 처리된다.
프로세스가 큐잉 직후·핸들러 완료 전에 크래시하면 그 작업은 흔적 없이 유실된다.
이 모듈은 부작용을 실행하기 "직전"에 append-only WAL(jsonl) 에 먼저 기록하고,
성공 후 done 을 append 한다. 다음 부팅에서 replay() 로 미완료분을 다시 흘려보내므로
최소 1회(at-least-once) 실행이 보장된다(→ 핸들러는 멱등하게 짜는 것이 이상적).

설계 원칙:
- 순수 로컬 파일 I/O 만 사용. MCP/LLM/네트워크 의존 없음 — 실제 부작용은 주입된
  handler(op) 콜백이 수행하므로 런타임 없이도 테스트가 통과한다.
- append-only + fsync 로 crash-safe. 마지막 줄이 부분 기록(크래시)이어도 재오픈 시
  해당 줄만 건너뛰고 나머지 상태를 온전히 재구성한다.
- opt-in·무회귀: 기존 경로는 그대로 두고, getattr(settings,'OUTBOX_ENABLED',False)
  로 감싸 켠 곳에서만 put/mark_done/replay 를 호출한다. 끄면 동작이 0.

파일 포맷(jsonl, 한 줄 = 한 이벤트):
    {"op_id": "...", "op": {...}|null, "status": "pending"|"done", "ts": "1699999999.5"}
재오픈 시 같은 op_id 의 "마지막" status 가 최종 상태(done 이면 완료 처리),
op payload 는 최초 put 기록의 것을 원래 순서대로 보존한다.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Callable, Optional


class Outbox:
    """크래시-세이프 append-only 부작용 아웃박스.

    Args:
        path: WAL(jsonl) 파일 경로. 부모 디렉터리는 필요 시 생성한다.
    """

    def __init__(self, path: str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        # op_id -> op payload. dict 삽입 순서 = 최초 put 순서(원래 순서 보존).
        self._ops: "dict[str, Optional[dict]]" = {}
        # op_id -> 최종 status ("pending" | "done").
        self._status: "dict[str, str]" = {}

        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._load()

    # ------------------------------------------------------------------ #
    # 내부: 상태 재구성 / append
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        """기존 WAL 을 재생해 인메모리 상태를 재구성한다."""
        if not os.path.exists(self.path):
            return
        with self._lock, open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    # 크래시로 인한 부분 기록(주로 마지막 줄) — 건너뛴다.
                    continue
                if not isinstance(rec, dict):
                    continue
                op_id = rec.get("op_id")
                if not op_id:
                    continue
                if op_id not in self._ops:
                    # 최초 등장(=put 기록)이 순서와 payload 를 확정한다.
                    self._ops[op_id] = rec.get("op")
                elif rec.get("op") is not None:
                    # 이후 기록이 payload 를 갱신한 경우만 반영(done 은 op=null).
                    self._ops[op_id] = rec.get("op")
                # status 는 "마지막 기록"이 우선한다.
                self._status[op_id] = rec.get("status", "pending")

    def _append(self, rec: dict) -> None:
        """한 이벤트를 파일 끝에 원자적으로 append 하고 flush+fsync 한다."""
        line = json.dumps(rec, ensure_ascii=False)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    # ------------------------------------------------------------------ #
    # 공개 API
    # ------------------------------------------------------------------ #
    def put(self, op: dict) -> str:
        """부작용 op 를 pending 으로 WAL 에 기록하고 op_id 를 돌려준다.

        실제 부작용을 수행하기 "직전"에 호출한다.
        """
        op_id = uuid.uuid4().hex
        with self._lock:
            self._append({
                "op_id": op_id,
                "op": op,
                "status": "pending",
                "ts": str(time.time()),
            })
            self._ops[op_id] = op
            self._status[op_id] = "pending"
        return op_id

    def mark_done(self, op_id: str) -> None:
        """op 를 done 으로 확정한다. 부작용이 성공한 "직후"에 호출한다."""
        with self._lock:
            self._append({
                "op_id": op_id,
                "op": None,
                "status": "done",
                "ts": str(time.time()),
            })
            self._status[op_id] = "done"

    def pending(self) -> "list[dict]":
        """아직 done 이 아닌 op payload 들을 원래(put) 순서로 반환한다."""
        with self._lock:
            return [
                self._ops[op_id]
                for op_id in self._ops
                if self._status.get(op_id) != "done"
            ]

    def replay(self, handler: Callable[[dict], Any]) -> "tuple[int, int]":
        """미완료 op 각각에 handler(op) 를 호출한다.

        예외 없이 반환하면 mark_done, 예외가 나면 pending 으로 남긴다
        (다음 replay 에서 재시도 → at-least-once).

        Returns:
            (성공 수, 실패 수)
        """
        # 스냅샷만 잠금 안에서. 핸들러(느린 I/O 가능)는 잠금 밖에서 실행.
        with self._lock:
            pending_ids = [
                op_id
                for op_id in self._ops
                if self._status.get(op_id) != "done"
            ]
        ok = 0
        fail = 0
        for op_id in pending_ids:
            op = self._ops.get(op_id)
            try:
                handler(op)
            except Exception:
                fail += 1
                continue
            self.mark_done(op_id)
            ok += 1
        return ok, fail
