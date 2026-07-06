"""
Observer Service
- 긴 작업 실행 중에 새 메시지가 오면 분류하여 적절히 응답
- 주기적으로 하트비트(진행 상황)를 Slack에 전송
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ActiveRun:
    """현재 실행 중인 작업 상태"""
    session_id: str          # channel:thread 조합
    channel_id: str
    thread_ts: Optional[str]
    user_id: str
    user_text: str           # 원래 요청
    start_time: float = field(default_factory=time.time)
    tool_events: list = field(default_factory=list)  # 사용된 도구 로그
    last_heartbeat: float = 0
    heartbeat_task: Optional[asyncio.Task] = None


class ObserverService:
    """인플라이트 메시지 관찰 + 하트비트 서비스"""

    def __init__(self):
        self._active_runs: dict[str, ActiveRun] = {}
        self._queued_messages: dict[str, list] = {}  # 작업 중 들어온 추가 메시지

    def get_session_id(self, channel_id: str, thread_ts: str = None) -> str:
        return f"{channel_id}:{thread_ts or 'main'}"

    def start_run(self, channel_id: str, thread_ts: str, user_id: str, user_text: str) -> ActiveRun:
        """새 작업 시작 등록"""
        sid = self.get_session_id(channel_id, thread_ts)
        run = ActiveRun(
            session_id=sid,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
            user_text=user_text,
        )
        self._active_runs[sid] = run
        logger.info(f"[OBSERVER] Run started: {sid}")
        return run

    def end_run(self, channel_id: str, thread_ts: str = None):
        """작업 종료"""
        sid = self.get_session_id(channel_id, thread_ts)
        run = self._active_runs.pop(sid, None)
        if run and run.heartbeat_task and not run.heartbeat_task.done():
            run.heartbeat_task.cancel()
        # 큐에 쌓인 메시지 반환
        queued = self._queued_messages.pop(sid, [])
        if queued:
            logger.info(f"[OBSERVER] Run ended: {sid}, {len(queued)} queued messages")
        else:
            logger.info(f"[OBSERVER] Run ended: {sid}")
        return queued

    def has_active_run(self, channel_id: str, thread_ts: str = None) -> bool:
        sid = self.get_session_id(channel_id, thread_ts)
        return sid in self._active_runs

    def get_active_run(self, channel_id: str, thread_ts: str = None) -> Optional[ActiveRun]:
        sid = self.get_session_id(channel_id, thread_ts)
        return self._active_runs.get(sid)

    def add_tool_event(self, channel_id: str, thread_ts: str, tool_name: str):
        """실행 중인 작업에 도구 이벤트 추가"""
        sid = self.get_session_id(channel_id, thread_ts)
        run = self._active_runs.get(sid)
        if run:
            run.tool_events.append({"tool": tool_name, "time": time.time()})

    def queue_message(self, channel_id: str, thread_ts: str, message: dict):
        """작업 중 들어온 추가 메시지를 큐에 저장"""
        sid = self.get_session_id(channel_id, thread_ts)
        self._queued_messages.setdefault(sid, []).append(message)
        logger.info(f"[OBSERVER] Message queued for {sid}: {message.get('text', '')[:50]}")

    def get_progress_summary(self, channel_id: str, thread_ts: str = None) -> str:
        """현재 작업 진행 상황 요약"""
        sid = self.get_session_id(channel_id, thread_ts)
        run = self._active_runs.get(sid)
        if not run:
            return ""

        elapsed = int(time.time() - run.start_time)
        tools_used = len(run.tool_events)

        if tools_used == 0:
            # 도구 이벤트 없어도 시간 기반으로 단계 추정
            if elapsed < 15:
                return f"요청을 분석하고 있습니다... ({elapsed}초 경과)"
            elif elapsed < 30:
                return f"관련 정보를 수집하고 있습니다... ({elapsed}초 경과)"
            elif elapsed < 60:
                return f"데이터를 처리하고 있습니다... ({elapsed}초 경과)"
            elif elapsed < 120:
                return f"응답을 작성하고 있습니다... ({elapsed}초 경과)"
            else:
                return f"복잡한 작업 처리 중입니다... ({elapsed}초 경과)"

        recent_tools = run.tool_events[-3:]
        tool_names = [t["tool"].replace("mcp__", "").replace("__", " ").split("_")[0] for t in recent_tools]

        # 도구명을 한국어로 변환
        tool_kr = {
            "slack": "Slack", "google": "Google", "clickup": "ClickUp",
            "crm": "CRM", "gmail": "Gmail", "calendar": "캘린더",
            "drive": "Drive", "sheets": "Sheets", "time": "시간 확인",
            "scheduler": "스케줄러", "deepl": "번역", "phone": "전화",
        }
        friendly_names = []
        for name in tool_names:
            for k, v in tool_kr.items():
                if k in name.lower():
                    friendly_names.append(v)
                    break
            else:
                friendly_names.append(name[:15])

        unique_names = list(dict.fromkeys(friendly_names))  # 중복 제거
        tool_text = ", ".join(unique_names)

        return f"{tool_text} 확인 중... ({tools_used}개 도구 사용, {elapsed}초 경과)"

    async def handle_inflight_message(self, channel_id: str, thread_ts: str,
                                       user_text: str, client) -> bool:
        """
        작업 중에 새 메시지가 들어왔을 때 처리.
        Returns: True = Observer가 처리함 (메인 파이프라인 스킵), False = 처리 안 함
        """
        sid = self.get_session_id(channel_id, thread_ts)
        run = self._active_runs.get(sid)
        if not run:
            return False

        text_lower = user_text.strip().lower()

        # 1. 상태 질문 감지
        status_keywords = ["됐어", "되고있어", "진행", "얼마나", "상태", "아직",
                          "doing", "status", "progress", "done", "still", "how long"]
        is_status = any(kw in text_lower for kw in status_keywords) or len(text_lower) <= 5

        if is_status:
            progress = self.get_progress_summary(channel_id, thread_ts)
            reply = f"네, 아직 작업 중이에요. {progress}"
            reply_params = {"channel": channel_id, "text": reply}
            if thread_ts:
                reply_params["thread_ts"] = thread_ts
            await client.chat_postMessage(**reply_params)
            logger.info(f"[OBSERVER] Status query answered: {sid}")
            return True

        # 2. 취소 요청 감지
        cancel_keywords = ["취소", "그만", "됐어 그만", "cancel", "stop", "nevermind"]
        is_cancel = any(kw in text_lower for kw in cancel_keywords)

        if is_cancel:
            reply = "현재 작업을 중단하기는 어렵지만, 완료되면 바로 알려드릴게요."
            reply_params = {"channel": channel_id, "text": reply}
            if thread_ts:
                reply_params["thread_ts"] = thread_ts
            await client.chat_postMessage(**reply_params)
            return True

        # 3. 추가 요청 → 큐에 저장
        self.queue_message(channel_id, thread_ts, {
            "text": user_text,
            "user_id": run.user_id,
            "ts": str(time.time()),
        })
        reply = f"네, 현재 이전 요청을 처리 중이에요. 완료되면 이 요청도 바로 이어서 처리할게요."
        reply_params = {"channel": channel_id, "text": reply}
        if thread_ts:
            reply_params["thread_ts"] = thread_ts
        await client.chat_postMessage(**reply_params)
        logger.info(f"[OBSERVER] Additional message queued: {sid}")
        return True

    async def start_heartbeat(self, channel_id: str, thread_ts: str, client,
                               initial_delay: float = 20, interval: float = 30):
        """하트비트 루프 시작 — 긴 작업 시 주기적으로 진행 상황 전송"""
        sid = self.get_session_id(channel_id, thread_ts)
        run = self._active_runs.get(sid)
        if not run:
            return

        async def _heartbeat_loop():
            max_beats = 3  # 최대 3번만
            beat_count = 0
            try:
                await asyncio.sleep(initial_delay)

                while sid in self._active_runs and beat_count < max_beats:
                    run = self._active_runs.get(sid)
                    if not run:
                        break

                    progress = self.get_progress_summary(channel_id, thread_ts)
                    if progress:
                        beat_count += 1
                        remaining = f" ({beat_count}/{max_beats})" if beat_count == max_beats else ""
                        beat_emojis = [":mag:", ":pencil2:", ":rocket:"]
                        emoji = beat_emojis[min(beat_count - 1, len(beat_emojis) - 1)]
                        msg = f"{emoji} {progress}{remaining}"

                        reply_params = {"channel": channel_id, "text": msg}
                        if thread_ts:
                            reply_params["thread_ts"] = thread_ts
                        try:
                            await client.chat_postMessage(**reply_params)
                            run.last_heartbeat = time.time()
                            logger.info(f"[HEARTBEAT] Sent ({beat_count}/{max_beats}): {msg}")
                        except Exception as e:
                            logger.warning(f"[HEARTBEAT] Send failed: {e}")

                    await asyncio.sleep(interval)

            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"[HEARTBEAT] Error: {e}")

        run.heartbeat_task = asyncio.create_task(_heartbeat_loop())


# 싱글톤 인스턴스
observer_service = ObserverService()
