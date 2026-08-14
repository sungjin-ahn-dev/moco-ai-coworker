import logging
import random
import re
from app.queueing_extended import debounced_enqueue_message, enqueue_orchestrator_job
from app.cc_utils.language_helper import detect_language
from app.cc_utils.slack_helper import get_slack_context_data
from app.cc_agents.bot_call_detector import call_bot_call_detector
from app.cc_agents.bot_thread_context_detector import call_bot_thread_context_detector
from app.cc_agents.answer_aggregator import call_answer_aggregator
from app.cc_agents.memory_retriever import call_memory_retriever
from app.cc_agents.simple_chat import call_simple_chat
from app.cc_agents.proactive_suggester import call_proactive_suggester
from app.cc_agents.proactive_confirm import call_proactive_confirm
from app.config.settings import get_settings
from slack_sdk import WebClient

# =============================================
# Global Bot User ID
# =============================================
_bot_user_id = None

def set_bot_user_id(user_id: str):
    """봇 사용자 ID 설정"""
    global _bot_user_id
    _bot_user_id = user_id

def get_bot_user_id() -> str:
    """봇 사용자 ID 반환"""
    return _bot_user_id

# =============================================
# 복잡 작업 키워드 (모듈 레벨에서 한 번만 정의)
# =============================================
_COMPLEX_KEYWORDS = [
    # 일정/캘린더
    "일정", "스케줄", "캘린더", "calendar", "schedule",
    # 이메일
    "메일", "이메일", "email", "mail",
    # 문서 생성
    "ppt", "pptx", "문서 만들", "문서 작성", "보고서", "제안서", "기획서",
    "발표자료", "슬라이드", "프레젠테이션",
    "docx", "워드", "pdf",
    "xlsx", "엑셀", "스프레드시트", "스프레드 시트", "시트",
    # 태스크/프로젝트
    "할 일", "할일", "태스크", "task", "clickup", "클릭업",
    "jira", "지라", "confluence", "컨플루언스", "위키",
    # 파일 작업
    "파일 만들", "파일 생성", "파일 보내", "다운로드", "업로드",
    "파일 찾", "파일 검색", "파일 목록", "문서 찾", "문서 검색",
    # 외부 서비스
    "구글 드라이브", "구글드라이브", "google drive", "드라이브",
    "gmail", "지메일",
    "google calendar", "구글 캘린더", "구글캘린더",
    "번역", "translate", "gitlab", "github",
    "웹사이트", "크롬", "브라우저", "playwright",
    "perplexity", "deepl",
    # 액션 동사
    "만들어", "작성해", "분석해", "요약해", "비교해",
    "조사해", "리서치", "확인해줘", "처리해",
    "수정해", "변경해", "등록해", "삭제해",
    "예약해", "설정해",
    # 검색/조회
    "찾아줘", "검색해줘", "조회해줘", "정리해줘",
    "뭐가 있", "뭐 있", "어떤 게 있", "목록",
    # 커뮤니케이션
    "전달해줘", "보내줘", "공유해줘", "알려줘",
    # 기억/메모리
    "기억해", "저장해", "메모해",
    # 회의/미팅
    "회의록", "미팅", "회의",
]

# =============================================
# Utils
# =============================================

def is_authorized_user(user_name: str) -> bool:
    """사용자 이름이 허용 목록에 포함되는지 확인 (목록이 비면 전체 허용)."""
    settings = get_settings()
    authorized_users = []

    if settings.BOT_AUTHORIZED_USERS_EN:
        authorized_users.extend(settings.BOT_AUTHORIZED_USERS_EN.split(", "))
    if settings.BOT_AUTHORIZED_USERS_KR:
        authorized_users.extend(settings.BOT_AUTHORIZED_USERS_KR.split(", "))

    # 허용 목록이 비어있으면 모든 사용자 허용
    if not authorized_users:
        return True

    # 사용자 이름에 허용된 이름이 포함되어 있는지 확인
    return any(auth_user in user_name for auth_user in authorized_users)


async def get_user_name(user_id: str, client: WebClient) -> str:
    """Slack user_id로 display_name(없으면 real_name)을 조회. 실패 시 user_id 반환."""
    try:
        response = await client.users_info(user=user_id)
        if response["ok"]:
            user = response["user"]
            profile = user.get("profile", {})
            # display_name 우선, 없으면 real_name 사용
            return profile.get("display_name") or user.get("real_name", user_id)
    except Exception as e:
        logging.warning(f"Failed to fetch user info for {user_id}: {e}")

    return user_id


async def convert_mentions_to_readable(text: str, client: WebClient) -> str:
    """
    Slack 멘션 형식 <@U12345>를 사람이 읽기 쉬운 형식으로 변환
    예: <@U12345> -> 사용자A(@U12345)
    """
    # 모든 멘션 찾기
    pattern = r'<@([A-Z0-9]+)>'
    matches = re.finditer(pattern, text)

    # user_id -> real_name 매핑 생성
    user_map = {}
    for match in matches:
        user_id = match.group(1)
        if user_id not in user_map:
            try:
                # Slack API로 사용자 정보 조회
                response = await client.users_info(user=user_id)
                if response["ok"]:
                    user = response["user"]
                    profile = user.get("profile", {})
                    # display_name 우선, 없으면 real_name 사용
                    display_name = profile.get("display_name") or user.get("real_name", f"User {user_id}")
                    user_map[user_id] = f"{display_name}(@{user_id})"
                else:
                    user_map[user_id] = f"<@{user_id}>"
            except Exception as e:
                logging.warning(f"Failed to fetch user info for {user_id}: {e}")
                user_map[user_id] = f"<@{user_id}>"

    # 텍스트 변환
    def replace_mention(match):
        user_id = match.group(1)
        return user_map.get(user_id, match.group(0))

    return re.sub(pattern, replace_mention, text)

# =============================================
# 메시지 처리
# =============================================

async def _process_message_logic(message, client):
    channel_id = message.get("channel")
    user_id = message.get("user")
    user_text = message.get("text", "")
    message_ts = message.get("ts")
    thread_ts = message.get("thread_ts")

    # 특정 채널 무시 (특정 채널 답글/반응 금지)
    IGNORED_CHANNELS = ["C0000000001", "C0000000002", "C0000000003"]
    if channel_id in IGNORED_CHANNELS:
        return

    # Observer: 현재 이 세션에서 작업 중인지 확인
    from app.cc_agents.observer.service import observer_service
    if observer_service.has_active_run(channel_id, thread_ts):
        handled = await observer_service.handle_inflight_message(
            channel_id, thread_ts, user_text, client
        )
        if handled:
            return

    # 메시지 수신 로그
    import time as _time
    _t_start = _time.time()
    logging.info(f"[MESSAGE_RECEIVED] channel={channel_id}, user={user_id}, text='{user_text[:50]}...', ts={message_ts}")

    # 즉각 이모지 리액션 + 대기 메시지: 메시지를 받았음을 바로 표시
    async def _add_eyes_reaction():
        try:
            await client.reactions_add(channel=channel_id, name="eyes", timestamp=message_ts)
        except Exception:
            pass

    async def _send_instant_ack():
        """모든 메시지에 즉시 대기 메시지 전송"""
        try:
            lang = detect_language(user_text)
            if lang == "Korean":
                _ack_msgs = ["네, 확인해볼게요.", "넵, 잠시만요.", "네네, 바로 확인해볼게요.", "알겠습니다, 확인 중이에요."]
            else:
                _ack_msgs = ["Sure, let me check.", "On it.", "Got it, checking now.", "Sure, one moment."]
            # channel_type은 아직 조회 전이므로 message 이벤트의 channel_type으로 판단
            _evt_channel_type = message.get("channel_type", "")
            if _evt_channel_type in ["channel", "group", "mpim"]:
                # 그룹 채널/단체 DM: 스레드로 답변
                _ack_thread = thread_ts or message_ts
            else:
                # DM: thread_ts가 있으면 스레드, 없으면 메인
                _ack_thread = thread_ts if thread_ts else None
            _ack_params = {"channel": channel_id, "text": random.choice(_ack_msgs)}
            if _ack_thread:
                _ack_params["thread_ts"] = _ack_thread
            await client.chat_postMessage(**_ack_params)
        except Exception:
            pass

    import asyncio as _asyncio
    from app.cc_utils.slack_helper import get_slack_context_data as _get_slack_context_data

    # 키워드 기반 복잡 작업 판단 (gather 전에 미리 체크)
    _text_lower_pre = user_text.strip().lower()
    _is_complex_pre = any(kw in _text_lower_pre for kw in _COMPLEX_KEYWORDS) or message.get("files")

    # 이모지 리액션 + (복잡 시 대기 메시지) + 사용자 이름 + 멘션 변환 + 채널 정보 병렬 조회
    # DM에서 짧은 메시지는 컨텍스트 수집량 축소 (5개)
    # return_exceptions=True로 하나가 실패해도 나머지는 정상 처리
    _context_limit = 5 if len(user_text.strip()) <= 20 else 10
    _gather_tasks = [
        _add_eyes_reaction(),
        get_user_name(user_id, client),
        convert_mentions_to_readable(user_text, client),
        _asyncio.to_thread(_get_slack_context_data, channel_id, message_limit=_context_limit),
    ]
    if _is_complex_pre:
        _gather_tasks.insert(1, _send_instant_ack())  # 복잡한 작업일 때만 대기 메시지

    _results = await _asyncio.gather(*_gather_tasks, return_exceptions=True)
    logging.info(f"[TIMING] Gather (eyes+username+mentions+context): {_time.time() - _t_start:.2f}s")

    if _is_complex_pre:
        # [0]=eyes, [1]=ack, [2]=user_name, [3]=user_text, [4]=slack_data
        user_name = _results[2] if not isinstance(_results[2], Exception) else user_id
        user_text = _results[3] if not isinstance(_results[3], Exception) else user_text
        slack_data = _results[4] if not isinstance(_results[4], Exception) else {"channel": {"channel_type": "dm"}}
    else:
        # [0]=eyes, [1]=user_name, [2]=user_text, [3]=slack_data
        user_name = _results[1] if not isinstance(_results[1], Exception) else user_id
        user_text = _results[2] if not isinstance(_results[2], Exception) else user_text
        slack_data = _results[3] if not isinstance(_results[3], Exception) else {"channel": {"channel_type": "dm"}}

    # 현재 메시지 정보 추가
    message_data = {
        "user_id": user_id,
        "user_name": user_name,
        "user_text": user_text,
        "channel_id": channel_id,
        "thread_ts": thread_ts,
        "message_ts": message_ts,
    }

    # 파일이 첨부된 경우 파일 정보 추가
    if message.get("files"):
        message_data["files"] = message.get("files")

    # Proactive Confirm + Answer Aggregator 병렬 실행
    _t_parallel = _time.time()
    logging.info(f"[PARALLEL_CHECK] Running proactive_confirm + answer_aggregator in parallel (user={user_id}, channel={channel_id})")
    import asyncio as _asyncio_parallel
    (approved, original_message), is_answer_completed = await _asyncio_parallel.gather(
        call_proactive_confirm(user_text, channel_id, user_id, thread_ts),
        call_answer_aggregator(user_text, message_data),
    )
    logging.info(f"[TIMING] Proactive check: {_time.time() - _t_parallel:.2f}s")
    logging.info(f"[PARALLEL_CHECK] proactive_confirm={approved}, answer_aggregator={is_answer_completed}")

    if approved and original_message:
        logging.info(f"[PROACTIVE_CONFIRM] User approved! Processing original message: '{original_message['user_text'][:50]}...'")

        # 채널 타입 확인 (DM vs 그룹 채널)
        channel_type = slack_data.get("channel", {}).get("channel_type", "")

        # DM이면 메인으로, 그룹 채널이면 스레드로
        if channel_type == "dm":
            approval_thread_ts = None  # DM: 메인으로
            operator_thread_ts = None
            logging.info(f"[PROACTIVE_CONFIRM] DM detected, responding in main channel")
        else:
            approval_thread_ts = thread_ts or message_ts  # 그룹 채널: 스레드로
            operator_thread_ts = thread_ts
            logging.info(f"[PROACTIVE_CONFIRM] Group channel detected, responding in thread")

        # 승인 응답 메시지 샘플 (언어에 따라 선택)
        original_text = original_message.get("user_text", "")
        lang = detect_language(original_text)

        if lang == "Korean":
            approval_messages = [
                "알겠습니다! 처리해드릴게요.",
                "넵, 바로 확인해드릴게요.",
                "네, 진행하겠습니다.",
                "알겠어요. 처리하겠습니다.",
                "확인했습니다. 바로 진행할게요."
            ]
        else:
            approval_messages = [
                "Got it! I'll take care of it.",
                "Sure, I'll check on that right away.",
                "Okay, I'll proceed.",
                "Understood. I'll handle it.",
                "Confirmed. I'll get started right away."
            ]

        await client.chat_postMessage(
            channel=channel_id,
            text=random.choice(approval_messages),
            thread_ts=approval_thread_ts
        )

        # original_message를 현재 컨텍스트로 업데이트 (operator가 올바른 스레드로 답변하도록)
        original_message["message_ts"] = message_ts
        original_message["thread_ts"] = operator_thread_ts
        original_message["channel_id"] = channel_id  # 채널도 업데이트

        original_user_text = original_message["user_text"]
        original_user_id = original_message["user_id"]
        original_user_name = original_message["user_name"]

        memory_query = f"""채널 {channel_id}에서 사용자 {original_user_name}({original_user_id})의 '{original_user_text}'한 요청을 수행하기 위한 메모리를 취합해 알려주세요. 
반드시 해당 채널과 요청 유저에 대한 **지침**과 정보(channel_id, user_id, user_name)를 포함하세요."""
        
        retrieved_memory = await call_memory_retriever(
            memory_query,
            slack_data,
            original_message
        )
        logging.info(f"[PROACTIVE_CONFIRM] Memory retrieved for original message: {retrieved_memory[:100] if retrieved_memory else 'None'}...")

        # orchestrator job enqueue
        orchestrator_job = {
            "query": original_user_text,
            "slack_data": slack_data,
            "message_data": original_message,
            "retrieved_memory": retrieved_memory
        }
        await enqueue_orchestrator_job(orchestrator_job)
        logging.info(f"[PROACTIVE_CONFIRM] Original message enqueued to orchestrator successfully")
        return

    # 그룹 채널/단체 DM일 때는 봇 호출 여부 확인 로직
    channel_type = slack_data.get("channel", {}).get("channel_type", "")
    if channel_type in ["public_channel", "private_channel", "group_dm"]:
        logging.info(f"[BOT_CALL_CHECK] Checking if bot is called in group context (channel={channel_id}, type={channel_type})")
        is_bot_called = await call_bot_call_detector(user_text)
        logging.info(f"[BOT_CALL_RESULT] is_bot_called={is_bot_called}, user_text='{user_text[:50]}...'")

        # 명시적 호출이 아니지만 스레드에서 봇이 대답해야 하는 지 확인
        if not is_bot_called and thread_ts:
            logging.info(f"[THREAD_CONTEXT_CHECK] Checking if bot is participating in thread (thread_ts={thread_ts})")
            is_bot_in_thread = await call_bot_thread_context_detector(thread_ts, channel_id, user_text, client)
            logging.info(f"[THREAD_CONTEXT_RESULT] is_bot_in_thread={is_bot_in_thread}")
            if is_bot_in_thread:
                is_bot_called = True
                logging.info(f"[THREAD_CONTEXT] Bot participating in thread, treating as bot call")

        if not is_bot_called:
            # Proactive 시스템: 과거 비슷한 작업을 했는지 확인
            logging.info(f"[PROACTIVE] Checking if bot can proactively suggest help (user={user_id}, channel={channel_id})")

            # 1. 메모리 검색 (proactive용)
            proactive_memory_query = f"""'{user_text}'한 요청을 했는데 내가 이전에 이런 비슷한 작업을 했는지 메모리를 취합해 알려주세요.
반드시 해당 채널과 요청 유저에 대한 **지침**과 정보(channel_id, user_id, user_name)를 포함하세요."""
            
            retrieved_memory = await call_memory_retriever(
                proactive_memory_query,
                slack_data,
                message_data
            )
            logging.info(f"[PROACTIVE] Memory retrieved: {retrieved_memory[:100] if retrieved_memory else 'None'}...")

            # 2. proactive_suggester 호출
            suggested = await call_proactive_suggester(
                user_text=user_text,
                retrieved_memory=retrieved_memory,
                slack_data=slack_data,
                message_data=message_data
            )

            if suggested:
                logging.info(f"[PROACTIVE] Suggestion sent to user, stopping message processing")
                return

            # 메모리 없거나 제안 안 했으면 그냥 종료
            logging.info(f"[BOT_CALL_SKIPPED] Bot not called in group channel, skipping message processing")
            return
        logging.info(f"[BOT_CALLED] Bot was called, proceeding with message processing")

    # 응답 취합 로직 (이미 병렬로 실행된 결과 사용)
    logging.info(f"[ANSWER_AGGREGATOR_RESULT] is_answer_completed={is_answer_completed}")
    if is_answer_completed:
        logging.info(f"[RESPONSE_COMPLETED] Answer aggregator processed the message, skipping simple_chat and orchestrator (user={user_id})")
        return

    # 권한 체크: 허용된 사용자만 처리 (새로운 요청만 체크)
    if not is_authorized_user(user_name):
        logging.info(f"[UNAUTHORIZED] User '{user_name}'({user_id}) is not authorized, skipping message")

        # channel_type에 따라 thread_ts 계산
        channel_type = slack_data.get("channel", {}).get("channel_type", "")
        if channel_type in ["public_channel", "private_channel", "group_dm"]:
            # 그룹 채널: 무조건 스레드로 답변
            final_thread_ts = thread_ts or message_ts
        elif channel_type in ["dm"]:
            # DM: thread_ts가 있으면 스레드로, 없으면 일반 메시지 (flat)
            final_thread_ts = thread_ts if thread_ts else None
        else:
            final_thread_ts = None

        # 바쁨 응답 메시지 샘플 (언어에 따라 선택)
        lang = detect_language(user_text)

        if lang == "Korean":
            busy_messages = [
                "지금 급한 업무가 있어서 조금 있다가 답변드릴게요.",
                "팀 업무로 바빠서 답변이 어렵습니다. 죄송합니다.",
                "급한 일 처리 중이라 시간이 좀 걸릴 것 같아요.",
                "지금은 다른 작업 중이라 나중에 확인하고 답변드릴게요.",
                "업무 중이라 바로 답변이 어려울 것 같습니다. 조금만 기다려주세요."
            ]
        else:
            busy_messages = [
                "I'm busy with urgent work right now. I'll get back to you soon.",
                "I'm tied up with team work at the moment. Sorry about that.",
                "I'm handling something urgent, so it might take a while.",
                "I'm working on something else right now. I'll check and respond later.",
                "I'm in the middle of work, so I can't respond immediately. Please wait a moment."
            ]

        # 메시지 전송
        post_params = {
            "channel": channel_id,
            "text": random.choice(busy_messages)
        }

        if final_thread_ts:
            post_params["thread_ts"] = final_thread_ts

        await client.chat_postMessage(**post_params)
        return

    if _is_complex_pre:
        # 복잡한 작업: Simple Chat 건너뛰고 바로 Operator 라우팅
        # (대기 메시지는 이미 _send_instant_ack()에서 전송 완료)
        logging.info(f"[FAST_ROUTE] Complex task detected by keyword, skipping simple_chat (user={user_id})")

        # 메모리 검색 후 Operator로 전달
        logging.info(f"[MEMORY_RETRIEVER] Retrieving memory for fast-routed task (user={user_id})")
        memory_query = f"""채널 {channel_id}에서 사용자 {user_name}({user_id})의 '{user_text}'한 요청을 수행하기 위한 메모리를 취합해 알려주세요.
반드시 해당 채널과 요청 유저에 대한 **지침**과 정보(channel_id, user_id, user_name)를 포함하세요."""
        retrieved_memory = await call_memory_retriever(memory_query, slack_data, message_data)

        orchestrator_job = {
            "query": user_text,
            "slack_data": slack_data,
            "message_data": message_data,
            "retrieved_memory": retrieved_memory
        }
        await enqueue_orchestrator_job(orchestrator_job)
        logging.info(f"[FAST_ROUTE] Orchestrator job enqueued (user={user_id})")
        return

    # 간단한 메시지: Simple Chat으로 처리
    _short_message = len(user_text.strip()) <= 20 and not message.get("files")
    if _short_message:
        logging.info(f"[FAST_PATH] Short message detected, skipping memory retrieval (user={user_id})")
        retrieved_memory = ""
    else:
        logging.info(f"[MEMORY_RETRIEVER] Retrieving memory (user={user_id}, channel={channel_id})")
        memory_query = f"""채널 {channel_id}에서 사용자 {user_name}({user_id})의 '{user_text}'한 요청을 수행하기 위한 메모리를 취합해 알려주세요.
반드시 해당 채널과 요청 유저에 대한 **지침**과 정보(channel_id, user_id, user_name)를 포함하세요."""
        retrieved_memory = await call_memory_retriever(memory_query, slack_data, message_data)
        logging.info(f"[MEMORY_RETRIEVER] Memory retrieved: {retrieved_memory[:100] if retrieved_memory else 'None'}...")

    _t_simple = _time.time()
    logging.info(f"[SIMPLE_CHAT] Calling simple_chat agent (user={user_id}, channel={channel_id})")
    is_simple_completed = await call_simple_chat(user_text, slack_data, message_data, retrieved_memory)
    _simple_elapsed = _time.time() - _t_simple
    logging.info(f"[TIMING] Simple chat: {_simple_elapsed:.2f}s")
    logging.info(f"[SIMPLE_CHAT_RESULT] is_simple_completed={is_simple_completed}")

    if is_simple_completed:
        logging.info(f"[TIMING] Total: {_time.time() - _t_start:.2f}s")
        logging.info(f"[RESPONSE_COMPLETED] Simple chat processed the message, skipping orchestrator (user={user_id})")
        # Run Log 기록
        from app.cc_utils.run_log_store import run_log
        run_log.log_run(
            run_type="simple_chat", user_id=user_id, user_name=user_name,
            channel_id=channel_id, thread_ts=thread_ts or "",
            prompt=user_text, state="completed",
            elapsed_seconds=_time.time() - _t_start,
        )
        # 처리 완료: 👀 리액션 제거
        try:
            await client.reactions_remove(channel=channel_id, name="eyes", timestamp=message_ts)
        except Exception:
            pass
        return

    # Simple Chat이 복잡하다고 판단: 메모리 없으면 검색 후 Operator로
    if _short_message and not retrieved_memory:
        logging.info(f"[MEMORY_RETRIEVER] Complex task detected after fast path, retrieving memory now (user={user_id})")
        memory_query = f"""채널 {channel_id}에서 사용자 {user_name}({user_id})의 '{user_text}'한 요청을 수행하기 위한 메모리를 취합해 알려주세요.
반드시 해당 채널과 요청 유저에 대한 **지침**과 정보(channel_id, user_id, user_name)를 포함하세요."""
        retrieved_memory = await call_memory_retriever(memory_query, slack_data, message_data)

    logging.info(f"[ORCHESTRATOR_ENQUEUE] Enqueuing orchestrator job (user={user_id}, channel={channel_id})")
    orchestrator_job = {
        "query": user_text,
        "slack_data": slack_data,
        "message_data": message_data,
        "retrieved_memory": retrieved_memory
    }
    await enqueue_orchestrator_job(orchestrator_job)
    logging.info(f"[ORCHESTRATOR_ENQUEUED] Orchestrator job enqueued successfully (user={user_id})")
    
# =============================================
# Agent Factory 승인 응답 처리
# =============================================

import re as _re

_AGENT_APPROVE_RE = _re.compile(r"^\s*(approve|승인)\s+([a-z][a-z0-9_]{1,39})\s*$", _re.IGNORECASE)
_AGENT_REJECT_RE = _re.compile(r"^\s*(reject|반려)\s+([a-z][a-z0-9_]{1,39})\s*(.*)$", _re.IGNORECASE)


async def _try_handle_agent_approval_reply(event, client) -> bool:
    """
    AGENT_APPROVER_SLACK_ID 와 일치하는 사용자가 DM 으로
    'approve <agent_id>' 또는 'reject <agent_id> <사유>' 를 보내면 처리하고 True 반환.

    매칭 안 되면 False (일반 메시지로 흘러감).
    """
    try:
        from app.config.settings import get_settings
        approver = (get_settings().AGENT_APPROVER_SLACK_ID or "").strip()
        if not approver:
            return False
        sender = event.get("user", "")
        if sender != approver:
            return False
        text = (event.get("text") or "").strip()

        m = _AGENT_APPROVE_RE.match(text)
        if m:
            agent_id = m.group(2)
            from app.cc_agents.agent_factory import approval, registry
            entry = registry.get(agent_id)
            if entry is None:
                await client.chat_postMessage(
                    channel=event["channel"],
                    text=f"⚠️ `{agent_id}` 를 registry 에서 찾을 수 없어요.",
                )
                return True
            try:
                approval.publish(agent_id, approver_id=sender)
                await client.chat_postMessage(
                    channel=event["channel"],
                    text=(
                        f"✅ `{agent_id}` (`{entry['agent_name']}`) publish 완료. "
                        f"웹 챗에 카드는 1분 내 자동 등장합니다 (열어둔 페이지면 새로고침 시 즉시)."
                    ),
                )
            except Exception as e:
                logging.exception("[AGENT_APPROVAL_REPLY] publish 실패")
                await client.chat_postMessage(
                    channel=event["channel"],
                    text=f"⚠️ publish 중 오류: `{e}`",
                )
            return True

        m = _AGENT_REJECT_RE.match(text)
        if m:
            agent_id = m.group(2)
            reason = m.group(3).strip() or "관리자 반려"
            from app.cc_agents.agent_factory import approval, registry
            entry = registry.get(agent_id)
            if entry is None:
                await client.chat_postMessage(
                    channel=event["channel"],
                    text=f"⚠️ `{agent_id}` 를 registry 에서 찾을 수 없어요.",
                )
                return True
            try:
                approval.reject(agent_id, reason=reason)
                await client.chat_postMessage(
                    channel=event["channel"],
                    text=f"❌ `{agent_id}` 반려 완료. 사유: {reason}",
                )
            except Exception as e:
                logging.exception("[AGENT_APPROVAL_REPLY] reject 실패")
                await client.chat_postMessage(
                    channel=event["channel"],
                    text=f"⚠️ reject 중 오류: `{e}`",
                )
            return True

        return False
    except Exception as e:
        logging.warning(f"[AGENT_APPROVAL_REPLY] 처리 실패 (일반 메시지로 통과): {e}")
        return False


# =============================================
# 슬랙 수신 이벤트 등록
# =============================================

def register_handlers(app):
    """Register all Slack event handlers."""

    async def is_dm(body):
        """1:1 DM 또는 단체 DM (mpim)"""
        channel_type = body.get("event", {}).get("channel_type")
        return channel_type in ["im", "mpim"]

    async def is_channel(body):
        """공개/비공개 채널만"""
        channel_type = body.get("event", {}).get("channel_type")
        return channel_type in ["channel", "group"]

    async def is_bot_in_channel(body, client):
        """봇이 해당 채널에 참여했는지 확인 (User Token용 필터)"""
        channel_id = body.get("event", {}).get("channel")
        if not channel_id:
            return False

        try:
            # 채널 정보 조회하여 봇이 멤버인지 확인
            result = await client.conversations_info(channel=channel_id)
            return result.get("channel", {}).get("is_member", False)
        except Exception as e:
            # 채널 정보 조회 실패 시 (권한 없음 등) False 반환
            logging.debug(f"Channel membership check failed for {channel_id}: {e}")
            return False

    async def has_files(body):
        """파일이 첨부된 메시지인지 확인"""
        event = body.get("event", {})
        return event.get("files") or event.get("subtype") == "file_share"

    async def has_links(body):
        """링크가 포함된 메시지인지 확인"""
        event = body.get("event", {})
        text = event.get("text", "")
        return "http://" in text or "https://" in text


    @app.event("message", matchers=[is_dm])
    async def handle_dm_message(event, body, client):
        """DM 메시지 처리 (1:1 DM + 단체 DM, 항상 처리, 채널 멤버십 체크 불필요)"""
        # 봇 메시지 제외
        if event.get("bot_id") is not None:
            return

        # 봇 자신의 메시지 제외 (유저 토큰 사용 시)
        bot_user_id = get_bot_user_id()
        if bot_user_id and event.get("user") == bot_user_id:
            return

        # 다른 앱(Claude 등)을 통해 보낸 메시지 제외
        text = event.get("text", "")
        if "다음을 사용하여 보냄" in text or "Sent using" in text:
            logging.info(f"[DM] Ignoring message sent via another app: {text[:50]}")
            return

        # Slackbot 시스템 알림 제외
        if event.get("user") == "USLACKBOT":
            return

        # Agent Factory 승인 응답 처리 — 승인자가 "approve <id>" / "reject <id> ..." 회신 시
        if await _try_handle_agent_approval_reply(event, client):
            return

        # 파일 메시지 체크 (subtype 체크보다 먼저!)
        if await has_files(body):
            await debounced_enqueue_message(event, delay_seconds=5.0)
            return

        # 링크 메시지 체크
        if await has_links(body):
            await debounced_enqueue_message(event, delay_seconds=5.0)
            return

        # subtype이 있는 경우 (편집, 삭제 등) 무시
        subtype = event.get("subtype")
        if subtype is not None:
            return

        # 순수 텍스트 메시지 처리 (DM은 디바운싱 최소화)
        await debounced_enqueue_message(event, delay_seconds=0.5)
        return


    @app.event("message", matchers=[is_channel, is_bot_in_channel])
    async def handle_channel_message(event, body, client):
        """채널 메시지 처리 (공개/비공개 채널, 봇이 참여한 채널만)"""
        # 봇 메시지 제외
        if event.get("bot_id") is not None:
            return

        # 봇 자신의 메시지 제외 (유저 토큰 사용 시)
        bot_user_id = get_bot_user_id()
        if bot_user_id and event.get("user") == bot_user_id:
            return

        # 다른 앱(Claude 등)을 통해 보낸 메시지 제외
        text = event.get("text", "")
        if "다음을 사용하여 보냄" in text or "Sent using" in text:
            logging.info(f"[CHANNEL] Ignoring message sent via another app: {text[:50]}")
            return

        # Slackbot 시스템 알림 제외
        if event.get("user") == "USLACKBOT":
            return

        # 파일 메시지 체크 (subtype 체크보다 먼저!)
        if await has_files(body):
            await debounced_enqueue_message(event, delay_seconds=5.0)
            return

        # 링크 메시지 체크
        if await has_links(body):
            await debounced_enqueue_message(event, delay_seconds=5.0)
            return

        # subtype이 있는 경우 (편집, 삭제 등) 무시
        subtype = event.get("subtype")
        if subtype is not None:
            return

        # 순수 텍스트 메시지 처리 (일반 메시지 및 스레드 메시지 모두)
        await debounced_enqueue_message(event, delay_seconds=1.5)
        return


    # === 명시적으로 제외되는 이벤트들 ===
    # (Slack 앱에서 이벤트 구독하지 않음)

    # reaction_added - 이모지 추가 (구독 안함)
    # reaction_removed - 이모지 제거 (구독 안함)

    # === 무시되는 이벤트들 (로그만) ===

    # app_mention - 봇 멘션 알림 (message 이벤트로 이미 처리됨)
    @app.event("app_mention")
    async def ignore_app_mention(body, logger):
        logger.debug("app_mention event ignored (already handled via message event)")

    # file_shared - 파일 공유 알림 (message 이벤트로 이미 처리됨)
    @app.event("file_shared")
    async def ignore_file_shared(body, logger):
        logger.debug("file_shared event ignored (already handled via message event)")

    # link_shared - 링크 공유 알림 (message 이벤트로 이미 처리됨)
    @app.event("link_shared")
    async def ignore_link_shared(body, logger):
        logger.debug("link_shared event ignored (already handled via message event)")

    # member_joined_channel - 멤버 채널 참여
    @app.event("member_joined_channel")
    async def ignore_member_joined(body, logger):
        logger.debug("member_joined_channel event ignored")

    # member_left_channel - 멤버 채널 떠남
    @app.event("member_left_channel")
    async def ignore_member_left(body, logger):
        logger.debug("member_left_channel event ignored")

    # channel_left - 봇이 채널에서 나감/쫓겨남
    @app.event("channel_left")
    async def ignore_channel_left(body, logger):
        logger.debug("channel_left event ignored")

    # group_left - 봇이 그룹에서 나감
    @app.event("group_left")
    async def ignore_group_left(body, logger):
        logger.debug("group_left event ignored")

    # 기타 모든 subtype 메시지들 (편집, 삭제, 참여/떠남 등)
    @app.event("message")
    async def handle_other_message_subtypes(body, logger):
        event = body.get("event", {})
        subtype = event.get("subtype")
        channel_type = event.get("channel_type")
        user = event.get("user")
        logging.info(f"[DEBUG_CATCH_ALL] message event received: channel_type={channel_type}, subtype={subtype}, user={user}")
        if subtype is not None:
            logger.debug(
                f"Message subtype ignored: {subtype} in {event.get('channel')}"
            )

    # =============================================
    # Agent Factory — 승인/반려 버튼 (Block Kit actions)
    # =============================================

    async def _replace_approval_message(client, body, header_text: str, body_text: str):
        """승인 메시지의 블록을 결과 블록으로 교체 → 버튼 사라짐."""
        try:
            await client.chat_update(
                channel=body["channel"]["id"],
                ts=body["message"]["ts"],
                text=header_text,
                blocks=[
                    {"type": "header", "text": {"type": "plain_text", "text": header_text, "emoji": True}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": body_text}},
                ],
            )
        except Exception as e:
            logging.warning(f"[AGENT_APPROVAL_BTN] chat_update 실패: {e}")

    async def _check_approver_or_ephemeral(body, client) -> bool:
        approver = (get_settings().AGENT_APPROVER_SLACK_ID or "").strip()
        clicker = body.get("user", {}).get("id", "")
        if not approver or clicker != approver:
            try:
                await client.chat_postEphemeral(
                    channel=body["channel"]["id"],
                    user=clicker,
                    text="이 작업은 지정된 승인자만 수행할 수 있어요.",
                )
            except Exception:
                pass
            return False
        return True

    @app.action("agent_factory_approve")
    async def handle_agent_factory_approve(ack, body, client):
        await ack()
        if not await _check_approver_or_ephemeral(body, client):
            return
        try:
            agent_id = body["actions"][0]["value"]
        except Exception as e:
            logging.error(f"[AGENT_APPROVAL_BTN] agent_id 파싱 실패: {e}")
            return

        from app.cc_agents.agent_factory import approval, registry
        entry = registry.get(agent_id)
        if entry is None:
            await _replace_approval_message(client, body,
                "⚠️ 처리 실패", f"`{agent_id}` 를 registry 에서 찾을 수 없어요.")
            return

        try:
            approval.publish(agent_id, approver_id=body["user"]["id"])
            await _replace_approval_message(client, body,
                "✅ 에이전트 publish 완료",
                f"*{entry['agent_name']}* (`{agent_id}`) 가 활성화됐어요. "
                "웹 챗에 카드는 1분 내 자동 등장합니다 (열어둔 페이지면 새로고침 시 즉시).")
        except Exception as e:
            logging.exception("[AGENT_APPROVAL_BTN] publish 실패")
            await _replace_approval_message(client, body,
                "⚠️ publish 중 오류", f"`{e}`")

    @app.action("agent_factory_reject")
    async def handle_agent_factory_reject(ack, body, client):
        await ack()
        if not await _check_approver_or_ephemeral(body, client):
            return
        try:
            agent_id = body["actions"][0]["value"]
        except Exception as e:
            logging.error(f"[AGENT_APPROVAL_BTN] agent_id 파싱 실패: {e}")
            return

        from app.cc_agents.agent_factory import approval, registry
        entry = registry.get(agent_id)
        if entry is None:
            await _replace_approval_message(client, body,
                "⚠️ 처리 실패", f"`{agent_id}` 를 registry 에서 찾을 수 없어요.")
            return

        try:
            approval.reject(agent_id, reason="관리자 버튼 반려")
            await _replace_approval_message(client, body,
                "❌ 에이전트 반려 완료",
                f"*{entry['agent_name']}* (`{agent_id}`) 가 폐기됐어요.")
        except Exception as e:
            logging.exception("[AGENT_APPROVAL_BTN] reject 실패")
            await _replace_approval_message(client, body,
                "⚠️ reject 중 오류", f"`{e}`")
