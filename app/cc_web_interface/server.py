"""
MOCO Web Interface Server
음성 입력 및 웹 인터페이스 서버
"""

import logging
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

# 라우터 임포트
from app.cc_web_interface.routes import (
    auth_router,
    bot_auth_router,
    meeting_router,
    voice_router,
    api_router
)
from app.cc_web_interface.auth_handler import auth_handler
from app.cc_web_interface.utils import get_session_user, require_auth
from app.cc_slack_handlers import is_authorized_user
from app.cc_utils.slack_helper import get_bot_profile_image

logger = logging.getLogger(__name__)

# FastAPI 앱 생성
web_app = FastAPI(title="MOCO Web Interface")

# 세션 미들웨어 추가
web_app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET_KEY") or secrets.token_hex(32),
)

# 정적 파일 서빙
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    web_app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# 라우터 등록
web_app.include_router(auth_router)
web_app.include_router(bot_auth_router)
web_app.include_router(meeting_router)
web_app.include_router(voice_router)
web_app.include_router(api_router)
# Voice Chat (ElevenLabs STT/TTS + Claude) — 삭제 시 이 줄만 제거
from app.cc_web_interface.voice_chat.routes import router as voice_chat_router
web_app.include_router(voice_chat_router)
# Voice Chat Outbound (아웃바운드 재처방 안내) — 삭제 시 이 줄만 제거
from app.cc_web_interface.voice_chat.routes_outbound import router as voice_chat_out_router
web_app.include_router(voice_chat_out_router)
# Voice Chat Gemini Live (Gemini 실시간 음성 대화) — 삭제 시 이 줄만 제거
from app.cc_web_interface.voice_chat.routes_gemini import router as voice_chat_gemini_router
web_app.include_router(voice_chat_gemini_router)
# Twilio AICC (전화 → Gemini Live) — 삭제 시 이 줄만 제거
from app.cc_web_interface.voice_chat.routes_twilio import router as twilio_router
web_app.include_router(twilio_router)
# CLAW OPS AICC (070 전화 → Gemini Live) — 삭제 시 이 줄만 제거
from app.cc_web_interface.voice_chat.routes_clawops import router as clawops_router
web_app.include_router(clawops_router)
# CS팀용 AICC 관리 대시보드 (운영시간 / 키워드 / 멘트 등 핫스왑 가능)
from app.cc_web_interface.admin_aicc.routes import router as admin_aicc_router
web_app.include_router(admin_aicc_router)
# Web Chat (ChatGPT 스타일 웹 채팅 — Slack MOCO와 완전 분리) — 삭제 시 이 줄만 제거
from app.cc_web_interface.chat.routes import router as web_chat_router
web_app.include_router(web_chat_router)
_chat_static_dir = Path(__file__).parent / "chat" / "static"
if _chat_static_dir.exists():
    web_app.mount("/chat/static", StaticFiles(directory=str(_chat_static_dir)), name="chat_static")
# Memorial Chat (음성 복제 추모 대화) — 비활성화
# from app.cc_web_interface.memorial_chat.routes import router as memorial_chat_router
# web_app.include_router(memorial_chat_router)
# Free AI Call (Whisper + Edge TTS, 무료) — 비활성화 (서버 리소스 영향)
# from app.cc_web_interface.memorial_chat.routes_free import router as free_call_router
# web_app.include_router(free_call_router)

# CRM 모듈 초기화 및 라우터 등록
async def _setup_crm():
    try:
        from app.cc_web_interface.crm import setup_crm_routes
        await setup_crm_routes(web_app)
        logger.info("[CRM] CRM module initialized successfully")
    except Exception as e:
        logger.warning(f"[CRM] CRM module initialization failed: {e}")

@web_app.on_event("startup")
async def startup_crm():
    await _setup_crm()


# MCP Server (외부 Claude Code/Desktop 노출) — settings.MCP_ENABLED=False면 noop
try:
    from app.cc_mcp.server import attach_mcp
    from app.config.settings import get_settings as _get_settings_for_mcp
    attach_mcp(web_app, _get_settings_for_mcp())
except Exception as _mcp_err:
    logger.warning(f"[MCP] attach_mcp 호출 실패 (기존 시스템엔 영향 없음): {_mcp_err}")


@web_app.get("/thank-you")
async def thank_you():
    """폼 제출 완료 페이지"""
    from fastapi.responses import HTMLResponse
    return HTMLResponse("""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>제출 완료 - MOCO CRM</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f5f8fa;min-height:100vh;display:flex;align-items:center;justify-content:center}
.card{background:#fff;border-radius:16px;box-shadow:0 4px 24px rgba(0,0,0,0.08);max-width:480px;width:90%;padding:3rem;text-align:center}
.icon{font-size:4rem;margin-bottom:1.5rem}
h1{color:#33475B;font-size:1.5rem;margin-bottom:0.75rem}
p{color:#8899A6;font-size:1rem;line-height:1.6}
.back{display:inline-block;margin-top:1.5rem;color:#FF7A59;text-decoration:none;font-weight:600;font-size:0.9rem}
.back:hover{text-decoration:underline}
</style></head>
<body><div class="card">
<div class="icon">✅</div>
<h1>제출이 완료되었습니다</h1>
<p>감사합니다! 빠른 시일 내에 담당자가 연락드리겠습니다.</p>
<a href="/" class="back">&larr; 돌아가기</a>
</div></body></html>""")


@web_app.get("/")
async def home(request: Request):
    """메인 페이지 (음성 입력 UI)"""
    # 로그인 체크
    user = get_session_user(request)

    if not user:
        # 로그인 필요하면 로그인 페이지로
        if require_auth(request):
            return await auth_handler.handle_login(request)
        else:
            # 개발 모드 - 가상 사용자 설정
            user = {
                'email': 'dev@localhost',
                'name': 'Developer',
                'id': 'dev_user'
            }
            request.session['user'] = user

    # 인가된 사용자인지 재확인
    if not is_authorized_user(user.get('name', '')):
        logger.warning(f"[AUTH] Unauthorized access attempt: {user.get('name')} ({user.get('email')})")
        request.session.clear()
        return HTMLResponse(
            content=f"<h1>접근 권한이 없습니다</h1><p>사용자: {user.get('name')} ({user.get('email')})</p><p>관리자에게 문의하세요.</p>",
            status_code=403
        )

    # 로그인된 사용자: 음성 UI 표시
    html_path = Path(__file__).parent / "static" / "index.html"
    if html_path.exists():
        with open(html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()

        # 템플릿 변수 치환
        from app.config.settings import get_settings
        settings = get_settings()
        bot_profile_image = get_bot_profile_image()

        html_content = html_content.replace('{{BOT_NAME}}', settings.BOT_NAME)
        html_content = html_content.replace('{{BOT_ORGANIZATION}}', settings.BOT_ORGANIZATION)
        html_content = html_content.replace('{{USER_NAME}}', user.get('name', '사용자'))
        html_content = html_content.replace('{{BOT_PROFILE_IMAGE}}', bot_profile_image)
        html_content = html_content.replace('{{CLOVA_ENABLED}}', str(settings.CLOVA_ENABLED).lower())

        return HTMLResponse(content=html_content)
    else:
        return HTMLResponse(content="<h1>음성 인터페이스</h1><p>index.html 파일이 없습니다.</p>")


# CRM 비밀번호 (간단한 접근 제어) — 환경변수로 주입
CRM_PASSWORD = os.environ.get("CRM_PASSWORD", "")

import json as _json
from datetime import datetime as _dt

_CRM_LOG_PATH = Path("/home/user/MOCO_DATA/crm_access.log")

def _log_crm_access(request: Request, page: str = "dashboard"):
    """CRM 접속 로그 기록"""
    try:
        ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown")
        ua = request.headers.get("user-agent", "")[:100]
        entry = _json.dumps({
            "time": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ip": ip,
            "page": page,
            "ua": ua,
        }, ensure_ascii=False)
        with open(_CRM_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


@web_app.get("/crm")
async def crm_dashboard(request: Request):
    """CRM 대시보드 페이지 (비밀번호 보호)"""
    if request.session.get("crm_authenticated"):
        _log_crm_access(request, "dashboard")
        crm_html_path = Path(__file__).parent / "crm" / "static" / "index.html"
        if crm_html_path.exists():
            with open(crm_html_path, 'r', encoding='utf-8') as f:
                return HTMLResponse(content=f.read())
        return HTMLResponse(content="<h1>CRM</h1><p>CRM 모듈이 설치되지 않았습니다.</p>", status_code=404)

    # 로그인 폼
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MOCO CRM - 로그인</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0F1117;color:#fff;display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{background:#1E2028;border-radius:16px;padding:40px;width:360px;box-shadow:0 20px 60px rgba(0,0,0,0.3);text-align:center}
.logo{width:48px;height:48px;background:linear-gradient(135deg,#5B5FC7,#8B5CF6);border-radius:12px;display:flex;align-items:center;justify-content:center;margin:0 auto 16px;font-size:20px;font-weight:bold;color:#fff}
h1{font-size:20px;margin-bottom:4px}
p{font-size:13px;color:#9CA3AF;margin-bottom:24px}
input{width:100%;padding:12px 16px;border-radius:10px;border:1px solid #374151;background:#111318;color:#fff;font-size:14px;outline:none;margin-bottom:12px;transition:border-color 0.2s}
input:focus{border-color:#5B5FC7}
button{width:100%;padding:12px;border-radius:10px;border:none;background:linear-gradient(135deg,#5B5FC7,#8B5CF6);color:#fff;font-size:14px;font-weight:600;cursor:pointer;transition:opacity 0.2s}
button:hover{opacity:0.9}
.error{color:#EF4444;font-size:12px;margin-bottom:12px;display:none}
</style></head><body>
<div class="card">
<div class="logo">M</div>
<h1>MOCO CRM</h1>
<p>의료/제약 영업관리 시스템</p>
<form method="POST" action="/crm/login">
<div class="error" id="err">비밀번호가 틀렸습니다.</div>
<input type="password" name="password" placeholder="비밀번호 입력" autofocus required>
<button type="submit">로그인</button>
</form>
</div>
<script>if(location.search.includes('error'))document.getElementById('err').style.display='block'</script>
</body></html>""")

@web_app.post("/crm/login")
async def crm_login(request: Request):
    """CRM 로그인 처리"""
    from starlette.responses import RedirectResponse
    form = await request.form()
    password = form.get("password", "")
    if password == CRM_PASSWORD:
        request.session["crm_authenticated"] = True
        _log_crm_access(request, "login")
        return RedirectResponse(url="/crm", status_code=303)
    _log_crm_access(request, "login_failed")
    return RedirectResponse(url="/crm?error=1", status_code=303)

@web_app.get("/crm/logout")
async def crm_logout(request: Request):
    """CRM 로그아웃"""
    from starlette.responses import RedirectResponse
    request.session.pop("crm_authenticated", None)
    return RedirectResponse(url="/crm", status_code=303)


@web_app.get("/crm/intro")
async def crm_intro(request: Request):
    """CRM 소개 페이지"""
    intro_path = Path(__file__).parent / "crm" / "static" / "intro.html"
    if intro_path.exists():
        with open(intro_path, 'r', encoding='utf-8') as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>CRM</h1><p>소개 페이지를 찾을 수 없습니다.</p>", status_code=404)


@web_app.get("/crm/access-log")
async def crm_access_log(request: Request):
    """CRM 접속 로그 조회 (관리자용)"""
    if not request.session.get("crm_authenticated"):
        return HTMLResponse("Unauthorized", status_code=401)
    try:
        if _CRM_LOG_PATH.exists():
            lines = _CRM_LOG_PATH.read_text(encoding="utf-8").strip().split("\n")
            logs = [_json.loads(l) for l in lines[-100:]]  # 최근 100건
            return {"logs": logs, "total": len(lines)}
        return {"logs": [], "total": 0}
    except Exception as e:
        return {"error": str(e)}


@web_app.get("/daemon/settings")
async def daemon_settings():
    """현재 서버 설정 조회 (민감정보 마스킹)"""
    from app.config.settings import get_settings
    s = get_settings()
    # 민감하지 않은 설정만 노출
    return {
        "bot_name": s.BOT_NAME,
        "bot_organization": s.BOT_ORGANIZATION,
        "model_simple": getattr(s, "MODEL_FOR_SIMPLE", ""),
        "model_moderate": getattr(s, "MODEL_FOR_MODERATE", ""),
        "model_complex": getattr(s, "MODEL_FOR_COMPLEX", ""),
        "web_interface_enabled": getattr(s, "WEB_INTERFACE_ENABLED", False),
        "web_auth_provider": getattr(s, "WEB_INTERFACE_AUTH_PROVIDER", ""),
        "clova_enabled": getattr(s, "CLOVA_ENABLED", False),
        "deepl_enabled": getattr(s, "DEEPL_ENABLED", False),
        "github_enabled": getattr(s, "GITHUB_ENABLED", False),
        "gitlab_enabled": getattr(s, "GITLAB_ENABLED", False),
        "ms365_enabled": getattr(s, "MS365_ENABLED", False),
        "atlassian_enabled": getattr(s, "ATLASSIAN_ENABLED", False),
        "google_drive_enabled": getattr(s, "GOOGLE_DRIVE_ENABLED", False),
        "gmail_enabled": getattr(s, "GMAIL_ENABLED", False),
        "google_calendar_enabled": getattr(s, "GOOGLE_CALENDAR_ENABLED", False),
        "clickup_enabled": getattr(s, "CLICKUP_ENABLED", False),
        "x_enabled": getattr(s, "X_ENABLED", False),
        "outlook_check_enabled": getattr(s, "OUTLOOK_CHECK_ENABLED", False),
        "confluence_check_enabled": getattr(s, "CONFLUENCE_CHECK_ENABLED", False),
        "jira_check_enabled": getattr(s, "JIRA_CHECK_ENABLED", False),
        "proactive_enabled": getattr(s, "PROACTIVE_ENABLED", False),
    }

@web_app.get("/daemon/memory-stats")
async def daemon_memory_stats():
    """메모리 통계 (사용자별 파일 수)"""
    import os
    mem_dir = Path("/home/user/MOCO_DATA/memories")
    if not mem_dir.exists():
        return {"users": [], "total_files": 0}
    users = []
    total = 0
    for d in sorted(mem_dir.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            count = sum(1 for f in d.rglob("*.md") if f.name != "index.md")
            total += count
            categories = {}
            for sub in d.iterdir():
                if sub.is_dir():
                    cat_count = sum(1 for f in sub.rglob("*.md"))
                    if cat_count > 0:
                        categories[sub.name] = cat_count
            users.append({"user_id": d.name, "files": count, "categories": categories})
    users.sort(key=lambda x: -x["files"])
    return {"users": users, "total_files": total, "total_users": len(users)}

@web_app.get("/daemon/crm-stats")
async def daemon_crm_stats():
    """CRM DB 요약"""
    import sqlite3
    try:
        conn = sqlite3.connect("/home/user/.eco/crm.db")
        c = conn.cursor()
        stats = {}
        for t in ["companies","contacts","prescriptions","product_listings","sales_transactions","activities"]:
            stats[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        # 최근 처방
        latest_rx = c.execute("SELECT MAX(prescribed_date) FROM prescriptions").fetchone()[0]
        # 최근 활동
        latest_act = c.execute("SELECT MAX(timestamp) FROM activities").fetchone()[0]
        conn.close()
        return {"tables": stats, "latest_prescription": latest_rx, "latest_activity": latest_act}
    except Exception as e:
        return {"error": str(e)}

@web_app.get("/daemon/aicc-status")
async def daemon_aicc_status():
    """AICC (CLAW OPS) 상태"""
    try:
        from app.cc_agents.observer.service import observer_service
        active_calls = len(observer_service._active_runs)
    except:
        active_calls = 0
    return {
        "phone_number": "070-1234-5678",
        "provider": "CLAW OPS",
        "model": "Gemini 3.1 Flash Live",
        "active_calls": active_calls,
    }

@web_app.get("/daemon/console-log")
async def daemon_console_log():
    """서버 콘솔 로그 (최근 200줄)"""
    import subprocess
    try:
        # journalctl 또는 nohup.out에서 읽기
        log_sources = [
            "/home/user/MOCO_DATA/logs/daemon_events.jsonl",
        ]
        # 실행 로그에서 최근 줄 가져오기
        from app.cc_utils.run_log_store import run_log
        recent = run_log.tail(50)
        lines = []
        for r in recent:
            level = "ERROR" if r.get("state") == "error" else "INFO"
            lines.append(f"{r.get('created_at','')} {level} [{r.get('type','')}] {r.get('user_name','')} | {r.get('prompt','')[:80]} → {r.get('state','')} ({r.get('elapsed_seconds',0)}s)")
        return {"lines": lines[-200:]}
    except Exception as e:
        return {"lines": [f"Error reading logs: {str(e)}"]}

@web_app.get("/daemon/status")
async def daemon_status():
    """Daemon 전체 상태 JSON"""
    from app.cc_utils.daemon_plane import daemon
    return daemon.get_status()

@web_app.get("/daemon/events")
async def daemon_events(limit: int = 100):
    """Daemon 이벤트 로그 JSON"""
    from app.cc_utils.daemon_plane import daemon
    return {"events": daemon.events.tail(limit)}

@web_app.get("/daemon/resources")
async def daemon_resources():
    """Daemon 리소스 목록 JSON"""
    from app.cc_utils.daemon_plane import daemon
    return {"resources": daemon.resources.get_all(), "summary": daemon.resources.summary()}

@web_app.get("/daemon")
async def daemon_dashboard():
    """Daemon 모니터링 대시보드 UI"""
    daemon_html = Path(__file__).parent / "static" / "daemon.html"
    if daemon_html.exists():
        return HTMLResponse(daemon_html.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Daemon dashboard file not found</h1>")


@web_app.get("/daemon/dashboard-data")
async def daemon_dashboard_data(period: str = "today"):
    """Electron Dashboard와 동일한 메모리 기반 통계 + active users"""
    import os, re
    from datetime import datetime, timedelta

    mem_dir = Path("/home/user/MOCO_DATA/memories")
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    if "~" in period:
        start_str, end_str = period.split("~")
    elif period == "week":
        start_str = (now - timedelta(days=6)).strftime("%Y-%m-%d")
        end_str = today_str
    elif period == "month":
        start_str = (now - timedelta(days=29)).strftime("%Y-%m-%d")
        end_str = today_str
    else:
        start_str = today_str
        end_str = today_str

    users = []
    if mem_dir.exists():
        for user_dir in sorted(mem_dir.iterdir()):
            if not user_dir.is_dir() or user_dir.name.startswith(".") or user_dir.name == "users":
                continue
            user_id = user_dir.name
            user_name = user_id
            users_sub = user_dir / "users"
            if users_sub.exists():
                profiles = [f.stem.replace("_", " ") for f in users_sub.glob("*.md") if not f.name.startswith(".")]
                if profiles:
                    user_name = profiles[0].replace(" 프로필", "").replace(" 사용자", "")

            tasks = []
            for md_file in user_dir.rglob("*.md"):
                if md_file.name == "index.md":
                    continue
                try:
                    stat = md_file.stat()
                    mod_date = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d")
                    if mod_date < start_str or mod_date > end_str:
                        continue
                    content = md_file.read_text(encoding="utf-8", errors="ignore")[:2000]
                    rel = str(md_file.relative_to(user_dir))
                    category = rel.split("/")[0] if "/" in rel else "misc"
                    status = "success"
                    fm_match = re.match(r"^---\n([\s\S]*?)\n---", content)
                    if fm_match:
                        fm = fm_match.group(1)
                        if re.search(r"outcome:\s*failure|status:\s*failed|result:\s*fail", fm, re.I):
                            status = "fail"
                        elif re.search(r"status:\s*pending|status:\s*in.progress", fm, re.I):
                            status = "pending"
                    title_match = re.search(r"^#\s+(.+)$", content, re.M)
                    task_name = title_match.group(1) if title_match else md_file.stem.replace("_", " ")
                    if "Memory Index" in task_name:
                        continue
                    mod_time = datetime.fromtimestamp(stat.st_mtime).strftime("%H:%M")
                    tasks.append({"name": task_name, "status": status, "category": category, "date": mod_date, "time": mod_time})
                except Exception:
                    pass

            if tasks:
                users.append({
                    "userId": user_id, "userName": user_name, "tasks": tasks,
                    "total": len(tasks),
                    "success": sum(1 for t in tasks if t["status"] == "success"),
                    "fail": sum(1 for t in tasks if t["status"] == "fail"),
                    "pending": sum(1 for t in tasks if t["status"] == "pending"),
                })

    users.sort(key=lambda u: -u["total"])
    total = sum(u["total"] for u in users)
    total_s = sum(u["success"] for u in users)
    total_f = sum(u["fail"] for u in users)
    total_p = sum(u["pending"] for u in users)

    daily_map = {}
    for u in users:
        for t in u["tasks"]:
            d = t["date"]
            if d not in daily_map:
                daily_map[d] = {"success": 0, "fail": 0, "pending": 0}
            daily_map[d][t["status"]] = daily_map[d].get(t["status"], 0) + 1
    daily = [{"date": k, **v} for k, v in sorted(daily_map.items())]

    # Active sessions
    from app.queueing_extended import session_manager
    active_sessions = session_manager.active_count
    total_lanes = session_manager.total_lanes

    return {
        "period": period, "startDate": start_str, "endDate": end_str,
        "totalTasks": total, "totalSuccess": total_s, "totalFail": total_f, "totalPending": total_p,
        "successRate": round(total_s / total * 100) if total else 0,
        "users": users, "dailyBreakdown": daily,
        "activeSessions": active_sessions, "totalLanes": total_lanes,
    }


@web_app.get("/run-logs")
async def get_run_logs(request: Request, limit: int = 50):
    """실행 로그 조회"""
    from app.cc_utils.run_log_store import run_log
    return {"logs": run_log.tail(limit)}


@web_app.get("/run-stats")
async def get_run_stats(hours: int = 24, date_from: str = "", date_to: str = ""):
    """실행 통계 (시간 또는 날짜 범위)"""
    from app.cc_utils.run_log_store import run_log
    if date_from or date_to:
        return run_log.stats_by_date(date_from, date_to)
    return run_log.stats(hours)


@web_app.get("/health")
async def health_check():
    """헬스 체크"""
    return {"status": "healthy", "service": "MOCO Web Interface"}