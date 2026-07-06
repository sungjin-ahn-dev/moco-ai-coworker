"""
Session Lane Queue System
- 대화(세션)별 독립 레인으로 메시지 순차 처리
- 서로 다른 세션은 동시 처리 (제한 없음)
- 같은 세션 내에서는 순서 보장
- 메모리 큐는 별도 유지 (순차 처리 필요)
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Callable, Awaitable

# ── Session Lane ──

class SessionLane:
    """세션(대화)별 독립 처리 레인"""

    def __init__(self, session_id: str, idle_timeout: float = 900):
        self.session_id = session_id
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: asyncio.Task = None
        self._idle_timeout = idle_timeout
        self._last_activity = datetime.now()
        self._active = False

    def ensure_worker(self):
        """워커가 없으면 생성"""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())
            logging.info(f"[SESSION_LANE] Worker started: {self.session_id}")

    async def enqueue(self, job_with_func: tuple):
        """작업 큐에 추가 — (job, process_func) 튜플"""
        self._last_activity = datetime.now()
        self.ensure_worker()
        await self._queue.put(job_with_func)
        logging.info(f"[SESSION_LANE] Job enqueued: {self.session_id} (queue: {self._queue.qsize()})")

    @property
    def is_busy(self) -> bool:
        return self._active

    @property
    def is_idle(self) -> bool:
        elapsed = (datetime.now() - self._last_activity).total_seconds()
        return not self._active and self._queue.empty() and elapsed > self._idle_timeout

    async def _worker(self):
        """세션 전용 워커 — 큐에서 작업을 순차적으로 처리"""
        try:
            while True:
                try:
                    job, process_func = await asyncio.wait_for(self._queue.get(), timeout=self._idle_timeout)
                except asyncio.TimeoutError:
                    logging.info(f"[SESSION_LANE] Idle timeout, worker stopping: {self.session_id}")
                    break

                self._active = True
                self._last_activity = datetime.now()
                try:
                    await process_func(job)
                    logging.info(f"[SESSION_LANE] Job completed: {self.session_id}")
                except Exception as e:
                    logging.error(f"[SESSION_LANE] Job error in {self.session_id}: {e}", exc_info=True)
                finally:
                    self._active = False
                    self._last_activity = datetime.now()
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logging.error(f"[SESSION_LANE] Worker error: {self.session_id}: {e}")


class SessionManager:
    """세션 레인 관리자"""

    def __init__(self):
        self._lanes: Dict[str, SessionLane] = {}
        self._cleanup_task: asyncio.Task = None

    def get_session_id(self, channel_id: str, thread_ts: str = None, user_id: str = None) -> str:
        """세션 ID 생성 — DM은 channel, 스레드는 channel:thread"""
        if thread_ts:
            return f"{channel_id}:{thread_ts}"
        return channel_id

    def get_lane(self, session_id: str) -> SessionLane:
        """세션 레인 가져오기 (없으면 자동 생성)"""
        if session_id not in self._lanes:
            self._lanes[session_id] = SessionLane(session_id)
            logging.info(f"[SESSION_MGR] New lane created: {session_id} (total: {len(self._lanes)})")
            try:
                from app.cc_utils.daemon_plane import daemon
                daemon.session_started(session_id)
            except Exception:
                pass
        return self._lanes[session_id]

    async def enqueue(self, session_id: str, job: dict, process_func: Callable):
        """세션 레인에 작업 추가 — job과 처리 함수를 함께 전달"""
        lane = self.get_lane(session_id)
        await lane.enqueue((job, process_func))

    def is_busy(self, session_id: str) -> bool:
        """해당 세션이 작업 중인지"""
        lane = self._lanes.get(session_id)
        return lane.is_busy if lane else False

    def start_cleanup(self):
        """유휴 레인 정리 태스크 시작"""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self):
        """5분마다 유휴 레인 정리"""
        while True:
            await asyncio.sleep(300)
            idle_keys = [k for k, lane in self._lanes.items() if lane.is_idle]
            for k in idle_keys:
                lane = self._lanes.pop(k, None)
                if lane and lane._worker_task and not lane._worker_task.done():
                    lane._worker_task.cancel()
                try:
                    from app.cc_utils.daemon_plane import daemon
                    daemon.session_ended(k)
                except Exception:
                    pass
            if idle_keys:
                logging.info(f"[SESSION_MGR] Cleaned {len(idle_keys)} idle lanes. Active: {len(self._lanes)}")

    @property
    def active_count(self) -> int:
        return sum(1 for lane in self._lanes.values() if lane.is_busy)

    @property
    def total_lanes(self) -> int:
        return len(self._lanes)


# ── 싱글톤 인스턴스 ──
session_manager = SessionManager()

# ── 메모리 큐 (별도 유지 — 순차 처리 필요) ──
memory_queue = asyncio.Queue(maxsize=300)


# ── Debounce ──
_debounce_timers: Dict[str, asyncio.Task] = {}
_accumulated_messages: Dict[str, list] = {}


async def debounced_enqueue_message(message, delay_seconds: float = 2.0):
    """Debounced message enqueue — DM 2초, 그룹 5초"""
    user_id = message.get("user")
    channel_id = message.get("channel")
    debounce_key = f"{channel_id}:{user_id}"

    if delay_seconds == 0:
        await enqueue_message(message)
        return

    if debounce_key not in _accumulated_messages:
        _accumulated_messages[debounce_key] = []
        logging.info(f"[DEBOUNCE] First message from {user_id} in {channel_id}, starting {delay_seconds}s timer")
    else:
        logging.info(f"[DEBOUNCE] Additional message from {user_id} in {channel_id}, resetting timer")

    _accumulated_messages[debounce_key].append({
        "message": message,
        "timestamp": datetime.now()
    })

    if debounce_key in _debounce_timers:
        _debounce_timers[debounce_key].cancel()

    async def delayed_process():
        try:
            await asyncio.sleep(delay_seconds)
            if debounce_key in _accumulated_messages:
                accumulated = _accumulated_messages[debounce_key]
                message_count = len(accumulated)
                logging.info(f"[DEBOUNCE] Timer expired, merging {message_count} messages from {user_id}")

                merged_text_parts = []
                base_message = accumulated[0]["message"].copy()
                for msg_data in accumulated:
                    text = msg_data["message"].get("text", "").strip()
                    if text:
                        merged_text_parts.append(text)

                if merged_text_parts:
                    base_message["text"] = "\n".join(merged_text_parts)
                    await enqueue_message(base_message)

                del _accumulated_messages[debounce_key]
                if debounce_key in _debounce_timers:
                    del _debounce_timers[debounce_key]
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.error(f"[DEBOUNCE] Error: {e}")
            _accumulated_messages.pop(debounce_key, None)
            _debounce_timers.pop(debounce_key, None)

    _debounce_timers[debounce_key] = asyncio.create_task(delayed_process())


# ── 메시지 / Orchestrator 큐 함수 (Session Lane으로 통합) ──

_process_message_func = None
_orchestrator_func = None
_app_client = None


async def enqueue_message(message):
    """메시지를 세션 레인에 추가"""
    channel_id = message.get("channel")
    thread_ts = message.get("thread_ts")
    session_id = session_manager.get_session_id(channel_id, thread_ts)

    async def _process_wrapper(job):
        if _process_message_func:
            await _process_message_func(job["message"], _app_client)

    await session_manager.enqueue(session_id, {"message": message}, _process_wrapper)


async def enqueue_orchestrator_job(orchestrator_job: dict):
    """Orchestrator 작업을 세션 레인에 추가"""
    channel_id = orchestrator_job["message_data"]["channel_id"]
    thread_ts = orchestrator_job["message_data"].get("thread_ts")
    session_id = session_manager.get_session_id(channel_id, thread_ts)

    async def _process_wrapper(job):
        if _orchestrator_func and _app_client:
            await _orchestrator_func(job, _app_client)

    await session_manager.enqueue(session_id, orchestrator_job, _process_wrapper)


async def enqueue_memory_job(memory_job: dict):
    """메모리 저장 큐에 작업 추가 (순차 처리)"""
    try:
        memory_queue.put_nowait(memory_job)
        logging.info(f"[MEMORY_QUEUE] Job enqueued, queue size: {memory_queue.qsize()}")
    except asyncio.QueueFull:
        logging.error(f"[MEMORY_QUEUE] Queue FULL, dropping memory job")


# ── 워커 시작 함수 (하위 호환) ──

def start_channel_workers(app, process_func, workers_per_channel=5):
    """Session Lane 방식으로 전환 — 채널 워커 대신 세션 매니저 사용"""
    global _process_message_func, _app_client
    _process_message_func = process_func
    _app_client = app.client
    session_manager.start_cleanup()
    logging.info(f"[SESSION_MGR] Session Lane system started (replaces channel workers)")


def start_orchestrator_worker(app, orchestrator_func, num_workers=2):
    """Session Lane 방식으로 전환 — Orchestrator 워커 대신 세션 매니저 사용"""
    global _orchestrator_func, _app_client
    _orchestrator_func = orchestrator_func
    _app_client = app.client
    logging.info(f"[SESSION_MGR] Orchestrator routing via Session Lanes (replaces {num_workers} fixed workers)")


def start_memory_worker(memory_func, num_workers=3):
    """메모리 워커는 기존 방식 유지 (순차 처리)"""
    async def memory_worker(worker_id: int):
        logging.info(f"[MEMORY_WORKER-{worker_id}] Started")
        while True:
            logging.info(f"[MEMORY_WORKER-{worker_id}] Waiting for next job...")
            job = await memory_queue.get()
            try:
                await memory_func(job)
                logging.info(f"[MEMORY_WORKER-{worker_id}] Job completed")
            except Exception as e:
                logging.error(f"[MEMORY_WORKER-{worker_id}] Error: {e}")
            finally:
                memory_queue.task_done()

    loop = asyncio.get_running_loop()
    for worker_id in range(num_workers):
        loop.create_task(memory_worker(worker_id))
    logging.info(f"[MEMORY_WORKER] Created {num_workers} workers")
