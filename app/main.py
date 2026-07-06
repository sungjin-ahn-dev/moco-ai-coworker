import asyncio
import logging
import os
import sys

# Ensure UTF-8 encoding on Windows (for Python itself and all child processes)
if sys.platform == 'win32':
    os.environ['PYTHONUTF8'] = '1'
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Setup logging first
logging.basicConfig(level=logging.INFO)

# Suppress rate_limit_event spam from claude_agent_sdk message parser
# (the CLI handles retries internally; these warnings are just noise)
logging.getLogger("claude_agent_sdk._internal.message_parser").setLevel(logging.ERROR)

# ── AICC ClawOps SDK verbose logging (control WS / media WS / session 모두 이 logger 공유) ──
# 통화가 reject/route 실패 시 SDK 측 사유를 보려면 DEBUG 필요
logging.getLogger("clawops").setLevel(logging.DEBUG)
logging.getLogger("clawops.agent").setLevel(logging.DEBUG)

# DEBUG: Check environment variables at startup
logging.info(f"[STARTUP DEBUG] CLAUDE_CODE_CLI_PATH={os.environ.get('CLAUDE_CODE_CLI_PATH', 'NOT SET')}")
logging.info(f"[STARTUP DEBUG] PATH={os.environ.get('PATH', 'NOT SET')[:200]}...")
logging.info(f"[STARTUP DEBUG] WEB_INTERFACE_AUTH_PROVIDER={os.environ.get('WEB_INTERFACE_AUTH_PROVIDER', 'NOT SET')}")

from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from app.config.settings import get_settings
from app.queueing_extended import start_channel_workers
from app.scheduler import scheduler, reload_schedules_from_file
from app.cc_slack_handlers import _process_message_logic
from app.cc_slack_handlers import register_handlers
from app.cc_utils.waiting_answer_db import init_db
from app.cc_utils.confirm_db import init_db as init_confirm_db
from app.cc_utils.email_tasks_db import init_db as init_email_tasks_db
from app.cc_utils.jira_tasks_db import init_db as init_jira_tasks_db
from app.cc_utils.skill_registry import init_db as init_skill_registry_db

settings = get_settings()


async def main():
    """Main function to setup and run the Slack bot."""

    # 1. Load settings
    settings = get_settings()

    # 1-1. Chrome profile setup (first-time login or always if enabled)
    if settings.CHROME_ENABLED:
        from pathlib import Path

        profile_dir = Path(settings.FILESYSTEM_BASE_DIR) / "chrome_profile"

        # 프로필이 없거나, CHROME_ALWAYS_PROFILE_SETUP=True면 브라우저 열기
        should_open_browser = (
            not profile_dir.exists()
            or not any(profile_dir.iterdir())
            or settings.CHROME_ALWAYS_PROFILE_SETUP
        )

        if should_open_browser:
            if settings.CHROME_ALWAYS_PROFILE_SETUP:
                logging.info("[CHROME_SETUP] 🌐 Opening browser for profile setup (CHROME_ALWAYS_PROFILE_SETUP=True)...")
            else:
                logging.info("[CHROME_SETUP] 🌐 Opening browser for initial login...")
            logging.info(
                "[CHROME_SETUP] Please login to any sites you need and press ENTER when done."
            )

            try:
                from playwright.async_api import async_playwright

                async with async_playwright() as p:
                    # Chrome 브라우저 실행 (persistent context 사용)
                    context = await p.chromium.launch_persistent_context(
                        user_data_dir=str(profile_dir),
                        channel="chrome",
                        headless=False,
                        # 봇 탐지 우회 설정
                        args=[
                            "--disable-blink-features=AutomationControlled",
                            "--disable-dev-shm-usage",
                            "--no-sandbox",
                        ],
                        ignore_default_args=["--enable-automation"],
                        bypass_csp=True,
                    )
                    page = await context.new_page()

                    # Google 열기
                    await page.goto("https://www.google.com")

                    print("\n" + "=" * 70)
                    print("🌐 Chrome browser opened for profile setup")
                    print("=" * 70)
                    print("\n👉 Log in to any sites you need (Google, etc.)")
                    print("👉 When done, press ENTER to continue...")
                    print("=" * 70 + "\n")

                    # 사용자 입력 대기 (별도 스레드에서)
                    await asyncio.to_thread(lambda: input("Press ENTER to continue: "))

                    # 브라우저 닫기
                    await context.close()

                logging.info("[CHROME_SETUP] ✅ Browser closed, login saved!")
            except Exception as e:
                logging.error(f"[CHROME_SETUP] ❌ Failed to setup Chrome: {e}")
        else:
            logging.info("[CHROME_SETUP] Chrome profile already exists, skipping setup")

    # 2. Initialize waiting_answer database
    init_db()
    logging.info("Waiting answer database initialized")

    # 2-1. Initialize confirm database
    init_confirm_db()
    logging.info("Confirm database initialized")

    # 2-2. Initialize email tasks database
    init_email_tasks_db()
    logging.info("Email tasks database initialized")

    # 2-3. Initialize jira tasks database
    init_jira_tasks_db()
    logging.info("Jira tasks database initialized")

    # 2-4. Initialize skill registry database
    init_skill_registry_db()
    logging.info("Skill registry database initialized")

    # 2-5. Load local skills from .claude/skills/
    from app.cc_utils.skill_registry import load_local_skills
    load_local_skills()

    # 3. Validate signing secret
    if not settings.SLACK_SIGNING_SECRET or settings.SLACK_SIGNING_SECRET == "...":
        logging.error(
            "Error: SLACK_SIGNING_SECRET is not set. Please set it in your config/settings.py file."
        )
        sys.exit(1)

    # 3. Initialize Slack AsyncApp
    app = AsyncApp(
        token=settings.SLACK_BOT_TOKEN, signing_secret=settings.SLACK_SIGNING_SECRET
    )

    # 4. Get bot user ID
    try:
        auth_test = await app.client.auth_test()
        bot_user_id = auth_test["user_id"]

        # Set global bot user ID for handlers
        from app.cc_slack_handlers import set_bot_user_id

        set_bot_user_id(bot_user_id)
        logging.info(f"App is running as user: {bot_user_id}")
    except Exception as e:
        logging.error(f"Error checking auth: {e}")
        sys.exit(1)

    # 6. Register handlers
    register_handlers(app)

    # 7-1. Wrap the message process (⏳ 리액션으로 처리 중 표시)
    async def process_wrapper(message, client):
        channel_id = message.get("channel")
        message_ts = message.get("ts")
        reaction_added = False

        # 즉시 ⏳ 리액션 추가 (사용자 대기 경험 개선)
        try:
            await client.reactions_add(
                channel=channel_id,
                timestamp=message_ts,
                name="hourglass_flowing_sand"
            )
            reaction_added = True
        except Exception:
            pass

        try:
            await _process_message_logic(message, client)
        finally:
            # 처리 완료 후 ⏳ 리액션 제거
            if reaction_added:
                try:
                    await client.reactions_remove(
                        channel=channel_id,
                        timestamp=message_ts,
                        name="hourglass_flowing_sand"
                    )
                except Exception:
                    pass

    # 7-2. Wrap the orchestrator process
    async def orchestrator_wrapper(job, client):
        from app.cc_utils.slack_helper import get_slack_context_data

        channel_id = job["message_data"]["channel_id"]
        thread_ts = job["message_data"].get("thread_ts")
        conv_key = f"{channel_id}:{thread_ts}" if thread_ts else channel_id

        # Session Lane이 자동으로 직렬화하므로 conversation lock 불필요
        logging.info(f"[ORCHESTRATOR_WRAPPER] Processing: {conv_key}")

        try:
            # Observer: 작업 시작 등록 + 하트비트 시작
            from app.cc_agents.observer.service import observer_service
            user_id = job["message_data"].get("user_id", "")
            user_text = job.get("query", "")
            observer_service.start_run(channel_id, thread_ts, user_id, user_text)
            await observer_service.start_heartbeat(channel_id, thread_ts, client,
                                                    initial_delay=20, interval=30)

            # Lock 획득 후 최신 Slack context로 갱신 (이전 응답 포함)
            fresh_slack_data = await asyncio.to_thread(get_slack_context_data, channel_id, message_limit=10)
            logging.info(f"[ORCHESTRATOR_WRAPPER] Refreshed slack_data for {conv_key}")

            # 메모리 가져오기 (이미 검색되었으면 재사용, 아니면 새로 검색)
            retrieved_memory = job.get("retrieved_memory")
            if not retrieved_memory:
                from app.cc_agents.memory_retriever import call_memory_retriever

                logging.info(f"[ORCHESTRATOR_WRAPPER] Retrieving relevant memories...")
                retrieved_memory = await call_memory_retriever(
                    query=job["query"],
                    slack_data=fresh_slack_data,
                    message_data=job["message_data"],
                )
                logging.info(
                    f"[ORCHESTRATOR_WRAPPER] Memory retrieved: {retrieved_memory[:100] if retrieved_memory else 'None'}..."
                )
            else:
                logging.info(
                    f"[ORCHESTRATOR_WRAPPER] Using pre-retrieved memory: {retrieved_memory[:100] if retrieved_memory else 'None'}..."
                )

            # Orchestrator 실행 (활성화된 MCP 직접 사용)
            from app.cc_agents.orchestrator.agent import call_orchestrator_agent
            import time as _time_mod
            _orch_start = _time_mod.time()

            response = await call_orchestrator_agent(
                user_query=job["query"],
                slack_data=fresh_slack_data,
                message_data=job["message_data"],
                retrieved_memory=retrieved_memory,
            )
            logging.info(
                f"[ORCHESTRATOR_WRAPPER] Response: {response[:100] if response else 'None'}..."
            )

            # Run Log 기록
            import time as _time_mod
            from app.cc_utils.run_log_store import run_log
            run_log.log_run(
                run_type="operator",
                user_id=job["message_data"].get("user_id", ""),
                user_name=job["message_data"].get("user_name", ""),
                channel_id=channel_id, thread_ts=thread_ts or "",
                prompt=job.get("query", ""),
                response=response[:500] if response else "",
                state="completed",
                elapsed_seconds=_time_mod.time() - _orch_start,
            )

            # 처리 완료: 👀 → ✅ 리액션 전환
            message_ts = job["message_data"].get("message_ts")
            if message_ts:
                try:
                    from slack_sdk import WebClient as _WC
                    from app.config.settings import get_settings as _gs
                    _sc = _WC(token=_gs().SLACK_BOT_TOKEN)
                    _sc.reactions_remove(channel=channel_id, name="eyes", timestamp=message_ts)
                    _sc.reactions_add(channel=channel_id, name="white_check_mark", timestamp=message_ts)
                except Exception:
                    pass  # 리액션 전환 실패해도 무시
        finally:
            # Observer: 작업 종료 + 큐에 쌓인 추가 메시지 처리
            queued_messages = observer_service.end_run(channel_id, thread_ts)
            if queued_messages:
                logging.info(f"[ORCHESTRATOR_WRAPPER] Processing {len(queued_messages)} queued messages")
                for qm in queued_messages:
                    from app.queueing_extended import enqueue_orchestrator_job
                    await enqueue_orchestrator_job({
                        "query": qm.get("text", ""),
                        "slack_data": job.get("slack_data", {}),
                        "message_data": {**job["message_data"], "user_text": qm.get("text", "")},
                        "retrieved_memory": "",
                    })
            logging.info(f"[ORCHESTRATOR_WRAPPER] Completed: {conv_key}")

    # 7-3. Wrap the memory process
    async def memory_worker_wrapper(job):
        """메모리 저장 작업을 처리하는 워커"""
        from app.cc_agents.memory_manager import call_memory_manager

        memory_query = job.get("memory_query")
        user_id = job.get("user_id")

        if not memory_query:
            logging.warning(f"[MEMORY_WRAPPER] No memory query in job")
            return

        logging.info(f"[MEMORY_WRAPPER] Saving memory for user_id={user_id}: {memory_query[:100]}...")
        result = await call_memory_manager(memory_query, user_id=user_id)
        if result.startswith("메모리 작업 중 오류"):
            logging.warning(f"[MEMORY_WRAPPER] Memory save failed: {result[:200]}")
        else:
            logging.info(f"[MEMORY_WRAPPER] Memory saved successfully")

    # 7-4. Start the workers
    from app.queueing_extended import start_orchestrator_worker, start_memory_worker

    start_channel_workers(app, process_wrapper, workers_per_channel=8)
    start_orchestrator_worker(app, orchestrator_wrapper, num_workers=50)
    start_memory_worker(memory_worker_wrapper, num_workers=10)

    # Daemon Plane 초기화
    from app.cc_utils.daemon_plane import daemon
    from app.cc_agents.operator.agent import build_mcp_servers_dict
    daemon.startup()
    daemon.register_channel("slack", "connected")
    # MCP 서버 등록
    mcp_names = list(build_mcp_servers_dict(settings).keys())
    for mcp_name in mcp_names:
        daemon.register_mcp(mcp_name, "active")
    logging.info(f"[DAEMON] Registered {len(mcp_names)} MCP servers")

    # 8. Start the scheduler
    await reload_schedules_from_file()

    # 8-1. Add MS365 (Outlook) checker job
    if settings.OUTLOOK_CHECK_ENABLED and settings.MS365_ENABLED:
        from app.cc_checkers.ms365.outlook_checker import check_email_updates

        # 스케줄러에 등록
        scheduler.add_job(
            check_email_updates,
            trigger="interval",
            seconds=settings.OUTLOOK_CHECK_INTERVAL * 60,  # 분을 초로 변환
            id="outlook_checker",
            name="MS365 Outlook Checker",
        )
        logging.info(
            f"[SCHEDULER] MS365 Outlook checker registered (interval: {settings.OUTLOOK_CHECK_INTERVAL} minutes)"
        )

    # 8-3. Add Atlassian checkers (Confluence & Jira)
    if settings.ATLASSIAN_ENABLED:
        # Confluence checker
        if settings.CONFLUENCE_CHECK_ENABLED:
            from app.cc_checkers.atlassian.confluence_checker import check_confluence_updates

            logging.info("[CONFLUENCE_CHECKER] Initializing Confluence checker...")
            scheduler.add_job(
                check_confluence_updates,
                trigger="interval",
                seconds=settings.CONFLUENCE_CHECK_INTERVAL * 60,
                id="confluence_checker",
                name="Confluence Checker",
            )
            logging.info(
                f"[SCHEDULER] Confluence checker registered (interval: {settings.CONFLUENCE_CHECK_INTERVAL} minutes)"
            )

        # Jira checker
        if settings.JIRA_CHECK_ENABLED:
            from app.cc_checkers.atlassian.jira_checker import check_jira_updates

            logging.info("[JIRA_CHECKER] Initializing Jira checker...")
            scheduler.add_job(
                check_jira_updates,
                trigger="interval",
                seconds=settings.JIRA_CHECK_INTERVAL * 60,
                id="jira_checker",
                name="Jira Checker",
            )
            logging.info(
                f"[SCHEDULER] Jira checker registered (interval: {settings.JIRA_CHECK_INTERVAL} minutes)"
            )

    # 8-4. Add dynamic suggester job
    if settings.DYNAMIC_SUGGESTER_ENABLED:
        from app.cc_agents.proactive_dynamic_suggester import call_dynamic_suggester

        logging.info("[DYNAMIC_SUGGESTER] Initializing dynamic suggester...")

        # 스케줄러에 등록
        scheduler.add_job(
            call_dynamic_suggester,
            trigger="interval",
            minutes=settings.DYNAMIC_SUGGESTER_INTERVAL,
            id="dynamic_suggester",
            name="Dynamic Suggester",
        )
        logging.info(
            f"[SCHEDULER] Dynamic suggester registered (interval: {settings.DYNAMIC_SUGGESTER_INTERVAL} minutes)"
        )

    # 8-4-a. Agent Factory — 자동 후보 감지 + 라이프사이클 (일 1회)
    if settings.AGENT_FACTORY_ENABLED:
        from apscheduler.triggers.cron import CronTrigger

        # 후보 감지 → confirm 제안
        if settings.AGENT_CANDIDATE_SUGGESTER_ENABLED:
            from app.cc_agents.agent_factory.candidate_suggester import (
                call_agent_candidate_suggester,
            )

            scheduler.add_job(
                call_agent_candidate_suggester,
                trigger=CronTrigger(
                    hour=settings.AGENT_LIFECYCLE_DAILY_HOUR,
                    minute=0,
                ),
                id="agent_candidate_suggester",
                name="Agent Candidate Suggester",
                replace_existing=True,
            )
            logging.info(
                f"[SCHEDULER] Agent candidate suggester registered "
                f"(daily {settings.AGENT_LIFECYCLE_DAILY_HOUR:02d}:00)"
            )

        # 사용 0회 에이전트 자동 archive (라이프사이클 정리)
        async def _agent_lifecycle_tick():
            try:
                from app.cc_agents.agent_factory.lifecycle import archive_unused_agents
                archived = archive_unused_agents(
                    idle_days=settings.AGENT_AUTO_ARCHIVE_DAYS,
                )
                if archived:
                    logging.info(
                        f"[AGENT_LIFECYCLE] archived {len(archived)} unused agents: {archived}"
                    )
            except Exception as e:
                logging.error(f"[AGENT_LIFECYCLE] tick 실패: {e}")

        scheduler.add_job(
            _agent_lifecycle_tick,
            trigger=CronTrigger(
                hour=settings.AGENT_LIFECYCLE_DAILY_HOUR,
                minute=30,  # suggester 보다 30분 뒤
            ),
            id="agent_lifecycle_archive",
            name="Agent Lifecycle Archive",
            replace_existing=True,
        )
        logging.info(
            f"[SCHEDULER] Agent lifecycle archive registered "
            f"(daily {settings.AGENT_LIFECYCLE_DAILY_HOUR:02d}:30, "
            f"idle_days={settings.AGENT_AUTO_ARCHIVE_DAYS})"
        )

    # 8-5. Add Skill Marketplace sync job
    if settings.SKILL_MARKETPLACE_ENABLED and settings.GOOGLE_DRIVE_ENABLED:
        from app.cc_checkers.skill_sync.skill_syncer import sync_community_skills

        scheduler.add_job(
            sync_community_skills,
            trigger="interval",
            minutes=60,
            id="skill_marketplace_sync",
            name="Skill Marketplace Sync",
        )
        logging.info("[SCHEDULER] Skill Marketplace sync registered (interval: 60 minutes)")

    # 8-6. Add CRM email sequence processor
    if settings.WEB_INTERFACE_ENABLED and settings.GMAIL_ENABLED:
        async def _process_crm_sequences():
            """CRM 이메일 시퀀스 예정 발송 처리"""
            try:
                from app.cc_web_interface.crm.database import async_session
                from app.cc_web_interface.crm.services.sequences import process_pending_emails
                async with async_session() as db:
                    processed = await process_pending_emails(db)
                    await db.commit()
                    if processed > 0:
                        logging.info(f"[CRM Sequence] {processed}건 시퀀스 이메일 처리 완료")
            except Exception as e:
                logging.error(f"[CRM Sequence] 처리 오류: {e}")

        from apscheduler.triggers.interval import IntervalTrigger

        scheduler.add_job(
            _process_crm_sequences,
            trigger=IntervalTrigger(hours=1),
            id="crm_sequence_processor",
            name="CRM Email Sequence Processor",
            replace_existing=True,
        )
        logging.info("[SCHEDULER] CRM email sequence processor registered (every 1 hour)")

    # 8-7. Working Day Google Calendar 양방향 동기화 (pull)
    if settings.WEB_INTERFACE_ENABLED and settings.WORKING_DAY_GCAL_SYNC_ENABLED:
        async def _sync_working_day_gcal():
            try:
                from app.cc_web_interface.crm.services.google_calendar_sync import run_full_sync
                results = await run_full_sync(months_ahead=1)
                done = sum(1 for r in results if "error" not in r)
                fail = len(results) - done
                logging.info(f"[WD_SYNC] gcal pull 완료 ok={done} fail={fail}")
            except Exception as e:
                logging.error(f"[WD_SYNC] 주기 동기화 오류: {e}")

        from apscheduler.triggers.interval import IntervalTrigger

        scheduler.add_job(
            _sync_working_day_gcal,
            trigger=IntervalTrigger(minutes=settings.WORKING_DAY_GCAL_SYNC_INTERVAL_MIN),
            id="working_day_gcal_sync",
            name="Working Day Google Calendar Sync",
            replace_existing=True,
        )
        logging.info(
            f"[SCHEDULER] Working Day gcal sync registered "
            f"(every {settings.WORKING_DAY_GCAL_SYNC_INTERVAL_MIN} minutes)"
        )

    scheduler.start()

    # 9. Start FastAPI Web Server (음성 인터페이스)
    web_server = None
    web_server_task = None
    cloudflare_tunnel = None

    if settings.WEB_INTERFACE_ENABLED:
        logging.info("[WEB_SERVER] Starting FastAPI web server on port 8000...")
        import uvicorn
        from pathlib import Path
        from app.cc_web_interface.server import web_app

        # SSL 인증서 경로 (없으면 HTTP로 폴백)
        cert_dir = Path(__file__).parent / "config" / "certs"
        ssl_keyfile = str(cert_dir / "key.pem")
        ssl_certfile = str(cert_dir / "cert.pem")

        ssl_kwargs = {}
        if (cert_dir / "key.pem").exists() and (cert_dir / "cert.pem").exists():
            ssl_kwargs["ssl_keyfile"] = ssl_keyfile
            ssl_kwargs["ssl_certfile"] = ssl_certfile
            protocol = "https"
        else:
            logging.info("[WEB_SERVER] SSL certificates not found, starting in HTTP mode")
            protocol = "http"

        # FastAPI를 별도 태스크로 실행
        config = uvicorn.Config(
            web_app,
            host="0.0.0.0",
            port=8000,
            log_level="info",
            **ssl_kwargs,
        )
        web_server = uvicorn.Server(config)
        web_server_task = asyncio.create_task(web_server.serve())
        logging.info(f"[WEB_SERVER] Web server started at {protocol}://localhost:8000")
        logging.info(f"[WEB_SERVER] CRM Dashboard: {protocol}://localhost:8000/crm")
        logging.info(f"[WEB_SERVER] Access from other devices: {protocol}://YOUR_IP:8000")

        if getattr(settings, "CLOUDFLARE_TUNNEL_ENABLED", False):
            from app.cc_web_interface.cloudflare_tunnel import CloudflareQuickTunnel
            cloudflare_tunnel = CloudflareQuickTunnel(
                local_port=8000,
                use_https=(protocol == "https"),
            )
            public_url = await cloudflare_tunnel.start()
            if public_url:
                settings.WEB_INTERFACE_URL = public_url
    else:
        logging.info("[WEB_SERVER] Web interface disabled")

    # 9-0. CLAW OPS AICC Agent (070 전화 → Gemini Live)
    clawops_task = None
    try:
        import os
        os.environ.setdefault("CLAWOPS_API_KEY", "")
        os.environ.setdefault("CLAWOPS_ACCOUNT_ID", "")
        os.environ.setdefault("GOOGLE_API_KEY", "")
        from clawops.agent import ClawOpsAgent, GeminiRealtime, BuiltinTool
        from app.cc_web_interface.admin_aicc import (
            config as aicc_cfg,
            agent_control as aicc_ctrl,
            scenario_loader as aicc_scenario,
            call_log_db as aicc_db,
            call_classifier as aicc_classifier,
        )

        # CS팀 어드민이 편집하는 설정 파일 (없으면 기본값으로 시드)
        aicc_cfg.ensure_seed()
        # 콜 로그 DB 초기화 (테이블 + 인덱스 생성)
        aicc_db.init_db()
        # AICC_세팅_시나리오_v1.2.xlsx에서 G-code 멘트 + 46 FAQ 로드
        faq_text = aicc_scenario.get_faq_text()
        _initial_config = aicc_cfg.load_config()
        system_prompt = aicc_cfg.build_system_prompt(_initial_config, faq_text=faq_text)

        aicc_conversations = {}
        aicc_call_state: dict[str, dict] = {}  # call_id → {failure_count, transferred}
        CALLER_SLACK_MAP = {
            "01000000001": "U0000000A1",   # member1
            "01000000002": "U0000000B2",   # member2
            "01000000003": "U0000000C3",   # admin
            "01000000004": "U0000000D4",   # member3
            "01000000005": "U0000000E5",   # member4
        }

        aicc_agent = ClawOpsAgent(
            from_="07000000000",
            session=GeminiRealtime(
                system_prompt=system_prompt,
                language="ko",
            ),
            recording=True,
            recording_path="/home/user/MOCO_DATA/aicc_recordings",
        )

        # 어드민 [저장 + 즉시 적용] 버튼이 호출하는 핫스왑 콜백
        def _apply_to_aicc_agent(new_config: dict) -> None:
            try:
                new_prompt = aicc_cfg.build_system_prompt(new_config, faq_text=faq_text)
                aicc_agent._session._system_prompt = new_prompt
                logging.info("[AICC] 🔄 system_prompt 핫스왑 완료 — 다음 통화부터 적용")
            except Exception as swap_err:
                logging.error(f"[AICC] 핫스왑 실패: {swap_err}")
        aicc_ctrl.register_apply_handler(_apply_to_aicc_agent)

        # 콜백 디스패처에 agent 등록 — /admin/aicc/callbacks/{id}/call-now 라우트에서 사용
        try:
            from app.cc_web_interface.admin_aicc import callback_dispatcher as aicc_callback_dispatcher
            aicc_callback_dispatcher.set_agent(aicc_agent)
        except Exception as cb_err:
            logging.warning(f"[AICC] callback_dispatcher 등록 실패: {cb_err}")

        # ──────── Silence Watchdog (G02·G03 자동 발화) ────────
        # Gemini SDK는 TTS audio를 빠른 batch로 stream → application의 마지막 chunk 시각은
        # phone에서 실제 발화 끝 시각보다 한참 빠르다.
        # → audio data 누적 길이로 phone 발화 끝 시각을 estimate.
        SILENCE_RETRY_SEC = 3.0          # phone 발화 끝 후 사용자 침묵 3초 → G02
        SILENCE_CLOSE_SEC = 5.0          # G02 phone 발화 끝 후 추가 5초 침묵 → G03
        AUDIO_END_DWELL_SEC = 0.5        # estimated 끝 + 이만큼 phone latency 버퍼
        AUDIO_NEW_TURN_GAP_SEC = 0.8     # chunk 간 gap이 이보다 크면 새 turn으로 간주
        AUDIO_BYTES_PER_SEC = 48000.0    # PCM16 24kHz raw = 24000 samples × 2 bytes
        SILENCE_HANGUP_GRACE_SEC = 12.0  # G03 발화 시간 확보 후 hangup

        # call_id → state dict
        aicc_silence_state: dict[str, dict] = {}

        # ── _handle_audio_data monkey-patch: audio chunk 길이 누적으로 phone 발화 끝 추정 ──
        _orig_handle_audio = aicc_agent._session._handle_audio_data
        async def _hooked_handle_audio_data(audio_data: bytes):
            await _orig_handle_audio(audio_data)
            try:
                call_obj = aicc_agent._session._call
                if call_obj is None:
                    return
                state = aicc_silence_state.get(call_obj.call_id)
                if state is None or state.get("ended"):
                    return
                now = asyncio.get_event_loop().time()
                # audio_data는 PCM16 24kHz raw → 길이 / 48000 = 초 단위 발화 길이
                chunk_sec = len(audio_data) / AUDIO_BYTES_PER_SEC

                # 새 turn 시작 판정 (이전 chunk와의 gap이 크면 새 turn)
                prev = state.get("last_assistant_audio_ts") or 0.0
                if prev == 0.0 or (now - prev) > AUDIO_NEW_TURN_GAP_SEC:
                    # 새 turn — 누적 length 리셋, 시작 시각 = now
                    state["audio_turn_start_ts"] = now
                    state["audio_turn_total_sec"] = chunk_sec
                else:
                    state["audio_turn_total_sec"] = state.get("audio_turn_total_sec", 0.0) + chunk_sec

                state["last_assistant_audio_ts"] = now
                # phone 발화 끝 예상 = turn 시작 시각 + 누적 audio 길이
                state["audio_estimated_end_ts"] = (
                    state.get("audio_turn_start_ts", now) + state.get("audio_turn_total_sec", 0.0)
                )

                # orchestrator 미실행이면 시작
                t = state.get("task")
                if not t or t.done():
                    state["task"] = asyncio.create_task(_silence_orchestrator(call_obj.call_id))
            except Exception:
                pass
        aicc_agent._session._handle_audio_data = _hooked_handle_audio_data

        async def _inject_text(text: str) -> bool:
            """Gemini Live session에 텍스트 주입 (model이 응답 발화)."""
            try:
                await aicc_agent._session._session.send_realtime_input(text=text)
                return True
            except Exception as e:
                logging.warning(f"[AICC] 텍스트 주입 실패: {e}")
                return False

        async def _silence_orchestrator(call_id: str):
            """발화 활동 시각을 보고 적절한 시점에 G02·G03 발화 + hangup.

            동작:
            1. 모델이 발화 중 (마지막 assistant chunk가 너무 최근) → 대기
            2. 모델 발화 끝 후 사용자가 SILENCE_RETRY_SEC 동안 침묵 → G02 발화
            3. G02 발화 끝 후 사용자가 SILENCE_CLOSE_SEC 동안 침묵 → G03 발화 → hangup
            중간에 사용자가 말하면 _silence_reset이 이 task를 cancel하고 새로 시작.
            """
            state = aicc_silence_state.get(call_id)
            if not state:
                return
            loop = asyncio.get_event_loop()
            try:
                cfg = aicc_cfg.load_config()
                prompts = cfg.get("prompts", {})
                retry_msg = (prompts.get("silence_retry_message") or "").strip()
                close_msg = (prompts.get("silence_close_message") or "").strip()

                # ── Phase 1: phone 발화 끝 + 사용자 침묵 3초 대기 ──
                while True:
                    if state.get("ended") or state.get("transferred"):
                        return
                    now = loop.time()
                    audio_end = state.get("audio_estimated_end_ts") or 0.0
                    last_user = state.get("last_user_ts") or 0.0
                    # phone 발화 끝 시각 + 작은 buffer = 발화 끝 확정 시각
                    phone_end_confirmed = audio_end + AUDIO_END_DWELL_SEC

                    # phone에서 아직 발화 중 → 끝날 때까지 대기
                    if now < phone_end_confirmed:
                        await asyncio.sleep(phone_end_confirmed - now)
                        continue

                    # 마지막 활동 = max(phone 발화 끝, 사용자 발화 시각)
                    last_activity = max(phone_end_confirmed, last_user)
                    silence_age = now - last_activity if last_activity else 1e9
                    if silence_age >= SILENCE_RETRY_SEC:
                        break
                    await asyncio.sleep(SILENCE_RETRY_SEC - silence_age + 0.05)

                if state.get("ended") or state.get("transferred"):
                    return

                # ── G02 발화 트리거 ──
                if retry_msg:
                    inj = (
                        f"[시스템 알림: 고객이 약 {SILENCE_RETRY_SEC:.0f}초간 말이 없습니다.] "
                        f"다음 멘트를 정확히 그대로 한국어로 말해 주세요:\n\"{retry_msg}\""
                    )
                    if await _inject_text(inj):
                        logging.info(f"[AICC] 🔇 silence G02 트리거 — call_id={call_id}")
                        state["g02_emitted_at"] = loop.time()
                        state["phase"] = "post_g02"
                        # G02는 새 turn — audio 누적 reset (다음 hook 호출이 새 turn으로 인식)
                        state["audio_turn_start_ts"] = 0.0
                        state["audio_turn_total_sec"] = 0.0
                        state["last_assistant_audio_ts"] = 0.0
                        state["audio_estimated_end_ts"] = 0.0

                # ── Phase 2: G02 phone 발화 끝 + 사용자 추가 침묵 5초 ──
                while True:
                    if state.get("ended") or state.get("transferred"):
                        return
                    now = loop.time()
                    audio_end = state.get("audio_estimated_end_ts") or 0.0
                    g02_at = state.get("g02_emitted_at") or 0.0
                    phone_end_confirmed = audio_end + AUDIO_END_DWELL_SEC

                    # phone에서 G02 아직 발화 중 → 끝날 때까지 대기
                    if now < phone_end_confirmed:
                        await asyncio.sleep(phone_end_confirmed - now)
                        continue

                    # G02 phone 발화 끝부터 경과
                    ref = max(phone_end_confirmed, g02_at)
                    elapsed = now - ref
                    if elapsed >= SILENCE_CLOSE_SEC:
                        break
                    await asyncio.sleep(SILENCE_CLOSE_SEC - elapsed + 0.05)

                if state.get("ended") or state.get("transferred"):
                    return

                # ── G03 발화 + hangup ──
                if close_msg:
                    inj = (
                        "[시스템 알림: 고객이 계속 말이 없어 통화를 종료합니다.] "
                        f"다음 멘트를 정확히 그대로 한국어로 말해 주세요:\n\"{close_msg}\""
                    )
                    if await _inject_text(inj):
                        logging.info(f"[AICC] 🔇 silence G03 트리거 — call_id={call_id}")
                        state["phase"] = "post_g03"

                # G03 발화 시간 확보
                await asyncio.sleep(SILENCE_HANGUP_GRACE_SEC)
                if state.get("ended") or state.get("transferred"):
                    return
                call_ref = state.get("call")
                if call_ref:
                    try:
                        await call_ref.hangup()
                        logging.info(f"[AICC] 🔇 silence 자동 hangup — call_id={call_id}")
                    except Exception as e:
                        logging.warning(f"[AICC] silence hangup 실패: {e}")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logging.error(f"[AICC] silence orchestrator 오류 (call_id={call_id}): {e}")

        def _silence_reset(call_id: str, role: str):
            """발화 시각 갱신 (transcript 기반 fallback. 정확한 발화 끝은 audio hook이 추적).

            - role="user" → 사용자 활동. G02 phase였더라도 escape.
            - role="assistant"/"model" → transcript 시각 fallback 기록. orchestrator는 audio hook이 시작.
            """
            state = aicc_silence_state.get(call_id)
            if not state or state.get("ended"):
                return
            now = asyncio.get_event_loop().time()
            if role == "user":
                state["last_user_ts"] = now
                t = state.get("task")
                if t and not t.done():
                    t.cancel()
                state["phase"] = "watching"
                state["g02_emitted_at"] = None
                state["task"] = asyncio.create_task(_silence_orchestrator(call_id))
            else:
                # assistant·model — transcript 시각 (audio chunk 시각이 주, 이건 fallback)
                state["last_assistant_ts"] = now

        def _silence_stop(call_id: str):
            """call_end·transfer·hangup 시 정리."""
            state = aicc_silence_state.pop(call_id, None)
            if not state:
                return
            state["ended"] = True
            t = state.get("task")
            if t and not t.done():
                t.cancel()

        @aicc_agent.on("call_start")
        async def on_aicc_call_start(call):
            aicc_conversations[call.call_id] = {"from": call.from_number, "to": call.to_number, "log": []}
            aicc_call_state[call.call_id] = {"failure_count": 0, "transferred": False}
            # silence watchdog state 등록 (orchestrator는 첫 audio chunk 시 hook이 시작)
            aicc_silence_state[call.call_id] = {
                "task": None,
                "last_user_ts": 0.0,
                "last_assistant_ts": 0.0,        # transcript 시각 (fallback)
                "last_assistant_audio_ts": 0.0,  # 마지막 audio chunk 들어온 시각
                "audio_turn_start_ts": 0.0,      # 현재 turn audio 시작 시각
                "audio_turn_total_sec": 0.0,    # 현재 turn 누적 audio 길이
                "audio_estimated_end_ts": 0.0,  # phone 발화 끝 예상 시각 (start + total_sec)
                "g02_emitted_at": None,
                "phase": "idle",
                "ended": False,
                "transferred": False,
                "call": call,
            }
            logging.info(f"[AICC] 📞 전화 수신: from={call.from_number}, to={call.to_number}, id={call.call_id}")

            # DB에 통화 시작 기록
            try:
                aicc_db.insert_call_start(
                    call_id=call.call_id,
                    from_number=call.from_number or "",
                    to_number=call.to_number or "",
                )
            except Exception as db_err:
                logging.error(f"[AICC] DB insert_call_start 실패: {db_err}")

            # 운영시간 / 휴무일 / 야간 휴식 / 점심시간 게이트
            cfg = aicc_cfg.load_config()
            status, msg = aicc_cfg.check_call_status(cfg)
            if status != "ok":
                logging.info(f"[AICC] ⏰ 통화 차단 ({status}) — 안내 멘트 발화 후 종료: {msg!r}")
                try:
                    aicc_db.mark_blocked(call.call_id, status)
                except Exception as db_err:
                    logging.error(f"[AICC] DB mark_blocked 실패: {db_err}")

                # 차단 통화 자동 콜백 큐잉 (운영시간에 자동 다시 연락)
                try:
                    from app.cc_web_interface.admin_aicc import callback_db as _aicc_cb_db
                    from_n = (call.from_number or "").replace("-", "").replace(" ", "").strip()
                    # 휴대폰만 큐잉 (070·1588 등 사업자는 skip)
                    if from_n and from_n.startswith("01"):
                        _aicc_cb_db.enqueue(
                            from_number=from_n,
                            source=_aicc_cb_db.SOURCE_AUTO_BLOCKED,
                            reason=f"통화 차단({status})",
                            original_call_id=call.call_id,
                            priority=_aicc_cb_db.PRIORITY_NORMAL,
                        )
                except Exception as cb_err:
                    logging.warning(f"[AICC] 자동 콜백 큐잉 실패: {cb_err}")

                # 차단 안내 멘트를 Gemini Live에 강제 주입 → model이 발화
                spoken = await _inject_text(
                    f"다음 문장을 정확히 그대로 한 번만 말한 후 어떤 추가 말도 하지 마세요. "
                    f"고객의 응답이 와도 더 이상 말하지 마세요.\n\n"
                    f"{msg}"
                )
                if spoken:
                    # 한국어 발화 속도 ≈ 7자/초. 멘트 길이에 따라 동적 대기 (6~20초).
                    wait_sec = min(20.0, max(6.0, len(msg) / 7.0))
                    logging.info(f"[AICC] 차단 멘트 발화 중 ({wait_sec:.1f}초 대기)")
                    await asyncio.sleep(wait_sec)
                else:
                    logging.warning("[AICC] 차단 멘트 발화 실패 — 짧게 대기 후 hangup")
                    await asyncio.sleep(0.5)

                _silence_stop(call.call_id)
                try:
                    await call.hangup()
                except Exception as hangup_err:
                    logging.warning(f"[AICC] hangup 실패: {hangup_err}")
                return  # 차단 통화는 이후 흐름 진입 X

        @aicc_agent.on("call_end")
        async def on_aicc_call_end(call):
            logging.info(f"[AICC] 📞 통화 종료: id={call.call_id}")
            aicc_call_state.pop(call.call_id, None)
            _silence_stop(call.call_id)
            conv = aicc_conversations.pop(call.call_id, None)

            # 1. 트랜스크립트 + 녹음 경로 + duration 정리
            transcript_text = ""
            if conv and conv["log"]:
                transcript_text = "\n".join(
                    f"{'👤 고객' if r == 'user' else '🤖 AI'}: {t}" for r, t in conv["log"]
                )
            from_num_raw = conv.get('from', '') if conv else ''
            duration_sec = int(getattr(call, 'duration', 0) or 0)

            import glob as _glob
            rec_dir = "/home/user/MOCO_DATA/aicc_recordings"
            mix_files = _glob.glob(f"{rec_dir}/{call.call_id}/mix.wav")
            if not mix_files:
                mix_files = _glob.glob(f"{rec_dir}/{call.call_id}/*.wav")
            recording_relative = None
            if mix_files:
                # aicc_recordings/ 기준 상대경로로 저장 (S3 이전 시 base만 갈아끼우면 됨)
                recording_relative = os.path.relpath(mix_files[0], rec_dir)

            # 2. DB에 통화 종료 기록 (transferred/blocked는 보존)
            try:
                aicc_db.finalize_call(
                    call_id=call.call_id,
                    transcript=transcript_text,
                    duration_sec=duration_sec,
                    recording_relative_path=recording_relative,
                )
            except Exception as db_err:
                logging.error(f"[AICC] DB finalize_call 실패: {db_err}")

            # 3. Gemini 정제 + 분류 + SMS 요약 (1회 호출)
            refined_text = transcript_text
            cls_result: dict = {}
            if transcript_text:
                try:
                    cls_result = await aicc_classifier.classify_and_save(
                        call_id=call.call_id,
                        transcript=transcript_text,
                        from_number=from_num_raw,
                    )
                    if cls_result.get("refined"):
                        refined_text = cls_result["refined"]
                        logging.info("[AICC] ✅ 분류 + 트랜스크립트 정제 완료")
                except Exception as cls_err:
                    logging.warning(f"[AICC] 분류 실패 (원본 트랜스크립트로 진행): {cls_err}")

            # 4. Slack 전송 (정제된 트랜스크립트 사용)
            try:
                from slack_sdk import WebClient
                settings = get_settings()
                if settings.SLACK_BOT_TOKEN:
                    slack_client = WebClient(token=settings.SLACK_BOT_TOKEN)
                    if conv and conv["log"]:
                        conv_text = refined_text
                        if len(conv_text) > 3000:
                            conv_text = conv_text[:3000] + "\n..."
                        msg = f"📞 *070 AI 전화 상담 종료*\n\n*발신자:* {conv['from']}\n*대화 ({len(conv['log'])}턴):*\n{conv_text}\n\n---\n_CLAW OPS + Gemini 3.1 Flash Live_"
                    else:
                        from_disp = from_num_raw or '알 수 없음'
                        msg = f"📞 *070 AI 전화 상담 종료*\n\n*발신자:* {from_disp}\n*대화 내용:* 트랜스크립트 없음\n\n---\n_CLAW OPS + Gemini 3.1 Flash Live_"
                    clean_num = from_num_raw.replace("-", "").replace("+82", "0").lstrip("82")
                    slack_channel = CALLER_SLACK_MAP.get(clean_num, "U0000000C3")
                    slack_client.chat_postMessage(channel=slack_channel, text=msg)
                    logging.info(f"[AICC] ✅ Slack 전송 완료 → {slack_channel} (from: {clean_num})")

                    # 녹음 파일 Slack 전송
                    for wav_path in mix_files:
                        try:
                            slack_client.files_upload_v2(
                                channel=slack_channel,
                                file=wav_path,
                                title=f"통화 녹음 ({call.call_id})",
                                initial_comment="🎙️ 통화 녹음 파일",
                            )
                            logging.info(f"[AICC] ✅ 녹음 파일 전송 완료: {wav_path}")
                        except Exception as wav_err:
                            logging.warning(f"[AICC] 녹음 파일 전송 실패: {wav_err}")
            except Exception as e:
                logging.error(f"[AICC] Slack 전송 실패: {e}")

            # 5. 사후 안내 SMS 발송 (헤더 + 상담 요약 + 풋터)
            try:
                from app.cc_web_interface.admin_aicc import sms_sender
                db_row = aicc_db.get_call(call.call_id) or {}
                final_status = db_row.get("status") or aicc_db.STATUS_COMPLETED
                block_reason = db_row.get("block_reason")
                turns = len(conv["log"]) if (conv and conv.get("log")) else 0
                sms_summary = (cls_result or {}).get("summary", "") or ""
                asyncio.create_task(sms_sender.send_call_summary_sms(
                    call_id=call.call_id,
                    to_number=from_num_raw,
                    summary=sms_summary,
                    turns=turns,
                    call_status=final_status,
                    block_reason=block_reason,
                ))
                logging.info(f"[AICC] 📨 SMS 발송 작업 큐잉 완료 (status={final_status}, summary={'O' if sms_summary else 'X'})")
            except Exception as sms_err:
                logging.error(f"[AICC] SMS 발송 트리거 실패: {sms_err}")

        @aicc_agent.on("call_failed")
        async def on_aicc_call_failed(call, reason):
            # call_failed의 reason은 str 또는 Exception일 수 있음 — 둘 다 풀어서 출력
            try:
                reason_repr = repr(reason)
            except Exception:
                reason_repr = str(reason)
            call_attrs = {
                k: getattr(call, k, None)
                for k in ("call_id", "from_number", "to_number", "duration", "status")
            }
            logging.error(
                f"[AICC] ❌ 통화 실패 — reason={reason_repr} "
                f"type={type(reason).__name__} call={call_attrs}"
            )
            _silence_stop(call.call_id)
            # Exception이면 traceback도
            if isinstance(reason, BaseException):
                logging.exception("[AICC] call_failed traceback", exc_info=reason)
            try:
                # call_start 없이 바로 fail될 수도 있어서 row 없으면 insert부터
                if not aicc_db.get_call(call.call_id):
                    aicc_db.insert_call_start(
                        call_id=call.call_id,
                        from_number=getattr(call, 'from_number', '') or '',
                        to_number=getattr(call, 'to_number', '') or '',
                    )
                aicc_db.mark_failed(call.call_id, str(reason)[:500])
            except Exception as db_err:
                logging.error(f"[AICC] DB mark_failed 실패: {db_err}")

        @aicc_agent.on("dtmf")
        async def on_aicc_dtmf(call, digit):
            # 디버그용: 키패드 입력은 추적에 도움
            logging.info(f"[AICC] 🔢 DTMF: call_id={call.call_id} digit={digit}")

        @aicc_agent.on("transcript")
        async def on_aicc_transcript(call, role, text):
            logging.info(f"[AICC] 💬 [{role}] {text}")
            if call.call_id in aicc_conversations:
                log = aicc_conversations[call.call_id]["log"]
                if log and log[-1][0] == role:
                    log[-1] = (role, log[-1][1] + text)
                else:
                    log.append((role, text))
            # silence watchdog: role별 분기 (모델 발화 중에는 timer 안 흐름)
            _silence_reset(call.call_id, role)

            # 자동 상담사 전환 로직 (어드민 설정에 따라)
            cs = aicc_call_state.get(call.call_id)
            if not cs or cs.get("transferred"):
                return
            cfg = aicc_cfg.load_config()
            routing = cfg.get("routing", {})
            if not routing.get("enabled"):
                return
            transfer_to = (routing.get("transfer_to") or "").strip()
            if not transfer_to:
                return
            threshold = int(routing.get("failure_threshold") or 3)

            try:
                if role == "user":
                    kw = aicc_cfg.match_transfer_keyword(text, cfg)
                    if kw:
                        cs["transferred"] = True
                        logging.info(f"[AICC] 📲 자동 전환 (전환 키워드='{kw}') → {transfer_to}")
                        try:
                            aicc_db.mark_transferred(call.call_id, transfer_to, aicc_db.TRANSFER_KEYWORD)
                        except Exception:
                            pass
                        _silence_stop(call.call_id)
                        await call.transfer(to=transfer_to, mode="blind")
                        return
                    kw = aicc_cfg.match_complaint_keyword(text, cfg)
                    if kw:
                        cs["transferred"] = True
                        logging.info(f"[AICC] 📲 자동 전환 (불만 키워드='{kw}') → {transfer_to}")
                        try:
                            aicc_db.mark_transferred(call.call_id, transfer_to, aicc_db.TRANSFER_COMPLAINT)
                        except Exception:
                            pass
                        _silence_stop(call.call_id)
                        await call.transfer(to=transfer_to, mode="blind")
                        return
                elif role == "model":
                    if aicc_cfg.is_failure_response(text, cfg):
                        cs["failure_count"] = int(cs.get("failure_count", 0)) + 1
                        logging.info(f"[AICC] ⚠️ NLU 실패 카운트 {cs['failure_count']}/{threshold}")
                        try:
                            aicc_db.update_failure_count(call.call_id, cs["failure_count"])
                        except Exception:
                            pass
                        if cs["failure_count"] >= threshold:
                            cs["transferred"] = True
                            logging.info(f"[AICC] 📲 자동 전환 (실패 임계값 도달) → {transfer_to}")
                            try:
                                aicc_db.mark_transferred(call.call_id, transfer_to, aicc_db.TRANSFER_FAILURE_THRESHOLD)
                            except Exception:
                                pass
                            _silence_stop(call.call_id)
                        await call.transfer(to=transfer_to, mode="blind")
                    else:
                        if cs.get("failure_count", 0) > 0:
                            logging.info("[AICC] ✓ NLU 성공 — 실패 카운터 리셋")
                            cs["failure_count"] = 0
                            try:
                                aicc_db.update_failure_count(call.call_id, 0)
                            except Exception:
                                pass
            except Exception as transfer_err:
                logging.error(f"[AICC] 자동 전환 실패: {transfer_err}")

        clawops_task = asyncio.create_task(aicc_agent.serve())
        logging.info("[AICC] 🚀 CLAW OPS Agent started — 070-0000-0000 대기 중")
    except Exception as e:
        logging.warning(f"[AICC] CLAW OPS Agent 시작 실패 (무시): {e}")

    # 9-1. X 인증 체크 및 처리
    if settings.X_ENABLED:
        from app.cc_utils.x_helper import load_token
        from app.cc_tools.x import initialize_x_client
        import webbrowser

        # OAuth 1.0a 클라이언트 초기화 (트윗 작성, 미디어 업로드, 타임라인용)
        if all(
            [
                settings.X_API_KEY,
                settings.X_API_SECRET,
                settings.X_ACCESS_TOKEN,
                settings.X_ACCESS_TOKEN_SECRET,
            ]
        ):
            try:
                client = initialize_x_client()
                me = client.get_me()
                username = me.data.username
                name = me.data.name
                logging.info(
                    f"[X_CLIENT] ✅ OAuth 1.0a authenticated as @{username} ({name})"
                )
            except Exception as e:
                logging.error(f"[X_CLIENT] ❌ OAuth 1.0a authentication failed: {e}")
        else:
            logging.warning("[X_CLIENT] OAuth 1.0a credentials not configured")

        # OAuth 2.0 토큰 체크 (트윗 조회, 검색용)
        token = load_token()
        if not token:
            logging.warning("[X_OAUTH] No OAuth 2.0 token found")
            print("\n" + "=" * 70)
            print("🔐 X (Twitter) OAuth 2.0 Authentication Required")
            print("=" * 70)
            print("\nTo enable follow/unfollow/following features:")
            print("  https://localhost:8000/bot/auth/x/start")
            print("\nOpening browser for authentication...")
            print("=" * 70 + "\n")

            # 웹 서버 시작 대기 (1초)
            await asyncio.sleep(1)

            # 브라우저 열기
            webbrowser.open("https://localhost:8000/bot/auth/x/start")

            # 토큰 생성 대기 (최대 3분)
            timeout = 180
            start_time = asyncio.get_event_loop().time()

            while True:
                token = load_token()
                if token:
                    logging.info("[X_OAUTH] ✅ OAuth 2.0 authentication completed!")
                    break

                if asyncio.get_event_loop().time() - start_time > timeout:
                    logging.error("[X_OAUTH] ❌ Authentication timeout (3 minutes)")
                    logging.warning(
                        "[X_OAUTH] Follow/unfollow features will not work until authenticated"
                    )
                    break

                await asyncio.sleep(2)
        else:
            logging.info("[X_OAUTH] ✅ OAuth 2.0 token found, X features ready")

    # 10. Start Slack Socket Mode handler
    logging.info("Starting Socket Mode handler...")
    handler = AsyncSocketModeHandler(app, settings.SLACK_APP_TOKEN)

    # SIGTERM 핸들러 등록 (Electron/dev.py에서 terminate() 시 graceful shutdown 실행)
    shutdown_event = asyncio.Event()

    def _handle_sigterm():
        logging.info("\n[SHUTDOWN] SIGTERM received, initiating graceful shutdown...")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    import signal
    try:
        loop.add_signal_handler(signal.SIGTERM, _handle_sigterm)
    except NotImplementedError:
        # Windows에서는 add_signal_handler 미지원 — signal 모듈로 대체
        signal.signal(signal.SIGTERM, lambda s, f: _handle_sigterm())

    async def _graceful_shutdown():
        """Web server, scheduler, handler를 순서대로 종료"""
        logging.info("[SHUTDOWN] Graceful shutdown initiated...")

        # 0. Cloudflare tunnel 종료
        if cloudflare_tunnel:
            await cloudflare_tunnel.stop()

        # 1. Web server 종료
        if web_server and web_server_task:
            logging.info("[SHUTDOWN] Stopping web server...")
            web_server.should_exit = True
            web_server_task.cancel()
            try:
                await web_server_task
            except asyncio.CancelledError:
                pass

        # 2. Scheduler 종료
        logging.info("[SHUTDOWN] Stopping scheduler...")
        scheduler.shutdown()

        # 3. Handler 종료
        logging.info("[SHUTDOWN] Stopping Slack handler...")
        await handler.close_async()

        logging.info("[SHUTDOWN] ✅ Shutdown complete")

    # SIGTERM 감시 태스크
    async def _watch_sigterm():
        await shutdown_event.wait()
        await _graceful_shutdown()  # 이 안에서 handler.close_async() 이미 호출됨

    sigterm_task = asyncio.create_task(_watch_sigterm())

    try:
        await handler.start_async()
    except KeyboardInterrupt:
        await _graceful_shutdown()
    finally:
        sigterm_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
