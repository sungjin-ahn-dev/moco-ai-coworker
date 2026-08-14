"""
웹 챗 라우트.

- GET  /chat                              ChatGPT 스타일 UI (Google 로그인 필요)
- GET  /chat/api/conversations            현재 사용자의 대화 목록
- POST /chat/api/conversations            새 대화 생성
- DEL  /chat/api/conversations/{id}       대화 삭제
- PATCH /chat/api/conversations/{id}      대화 제목 변경
- GET  /chat/api/conversations/{id}/messages  메시지 히스토리
- POST /chat/api/conversations/{id}/stream    SSE 스트리밍 응답
"""

import asyncio
import json
import logging
import mimetypes
import os
import re
import time
import uuid
from pathlib import Path
from typing import List, Optional

from urllib.parse import quote

from fastapi import APIRouter, Request, HTTPException, Body
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse, FileResponse

from app.config.settings import get_settings

from app.cc_slack_handlers import is_authorized_user
from app.cc_web_interface.auth_handler import auth_handler
from app.cc_web_interface.utils import get_session_user, require_auth
from app.cc_web_interface.chat import history_store
from app.cc_web_interface.chat.context_builder import (
    resolve_slack_user_id,
    build_message_data,
    retrieve_memory,
    compose_query_with_history,
)
from app.cc_web_interface.chat.agent_adapter import stream_operator_for_web
from app.cc_agents.atticus import stream_atticus_for_web
from app.cc_agents.ra_regulatory_expert import stream_ra_expert_for_web
from app.cc_agents.agent_factory import registry as agent_registry
from app.cc_agents.generated.loader import load_all_generated_streamers

_init_logger = logging.getLogger(__name__)

# 웹 챗에서 선택 가능한 에이전트 매핑.
# UI 가 body 의 `agent` 필드에 키를 보내면 해당 어댑터로 라우팅.
# 키가 비어 있거나 알 수 없으면 기본 operator 사용.
_AGENT_STREAMERS = {
    "operator": stream_operator_for_web,
    "atticus": stream_atticus_for_web,
    "ra_expert": stream_ra_expert_for_web,
}

# 시작 시 generated/ 의 모든 자동 생성 에이전트 로드 (격리 — 깨진 거 있어도 나머지 살림)
try:
    _generated = load_all_generated_streamers()
    _AGENT_STREAMERS.update(_generated)
    if _generated:
        _init_logger.info(f"[WEB_CHAT] generated 에이전트 {len(_generated)}개 로드: {list(_generated.keys())}")
except Exception as _e:
    _init_logger.warning(f"[WEB_CHAT] generated 에이전트 로드 중 예외: {_e}")


# 정적 에이전트 카탈로그 — 동적 카탈로그가 빈 경우의 폴백
# (Atticus / RA Expert 처럼 사람이 .py 로 직접 만든 에이전트의 UI 메타데이터)
_BUILTIN_AGENT_CATALOG = {
    "atticus": {
        "agent_id": "atticus",
        "agent_name": "⚖️ Atticus",
        "description": "법령·계약·RA 1차 자문",
        "icon": "⚖️",
        "examples": [
            "디지털의료제품법 시행일과 기존 의료기기법 대비 분류 기준이 어떻게 달라졌는지 핵심만 정리해줘",
            "제품A(MCI DTx)의 보험등재 가이드라인 핵심 요구사항을 표로 보여줘",
            "AI 의료기기에 GDPR 적용 시 우리가 KR 인허가와 어떻게 분리해서 대응해야 하는지 권고 옵션을 제시해줘",
            "표준 NDA 계약서에서 분쟁관할·해지·IP 조항의 일반적 리스크 플래깅 포인트를 알려줘",
        ],
        "source": "builtin",
    },
    "ra_expert": {
        "agent_id": "ra_expert",
        "agent_name": "🏥 식약처 RA 전문가",
        "description": "디지털의료기기 인허가 심화",
        "icon": "🏥",
        "examples": [
            "음성 AI로 인지장애를 선별하는 SaMD는 몇 등급으로 분류될까? 매트릭스 적용 과정을 보여줘",
            "제품A 변경허가 시 필요한 서류와 예상 소요기간 범위를 알려줘",
            "AI 디지털의료기기 임상시험에서 참조표준(reference standard) 설정 방법을 가이드라인 근거와 함께 설명해줘",
            "디지털의료기기 GMP에서 SW 변경관리 요구사항이 기존 의료기기 GMP와 어떻게 다른지 비교해줘",
            "MCI DTx의 임상시험계획서 작성 시 가이드라인이 요구하는 1차 평가지표를 정리해줘",
        ],
        "source": "builtin",
    },
}

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Web Chat"])

_STATIC_DIR = Path(__file__).parent / "static"

# 업로드 제한
_MAX_FILES_PER_MESSAGE = 10
_MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50MB (Slack 동일)
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._\-가-힣 ()\[\]]+")


def _safe_filename(name: str) -> str:
    """디렉토리 트래버설 차단 + 안전한 문자만 허용."""
    name = os.path.basename(name or "").strip() or "file"
    name = _SAFE_NAME_RE.sub("_", name)
    return name[:200]


def _attachments_dir(conv_id: str) -> Path:
    """첨부 파일 저장 디렉토리. Slack 의 FILESYSTEM_BASE_DIR/files/{channel_id}/ 와 동일한 컨셉."""
    settings = get_settings()
    base = settings.FILESYSTEM_BASE_DIR or os.getcwd()
    # conv_id 는 uuid hex 이므로 trust 가능하지만 한 번 더 sanitize
    safe_conv = re.sub(r"[^A-Za-z0-9_-]", "", conv_id)[:64] or "default"
    d = Path(base) / "files" / "web" / safe_conv
    d.mkdir(parents=True, exist_ok=True)
    return d


def _require_user(request: Request) -> dict:
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="login required")
    if not is_authorized_user(user.get("name", "")):
        raise HTTPException(status_code=403, detail="not authorized")
    return user


@router.get("", response_class=HTMLResponse)
async def chat_page(request: Request):
    """ChatGPT 스타일 UI. 세션이 없으면 닉네임 입력 페이지로."""
    user = get_session_user(request)
    if not user:
        return RedirectResponse(url="/chat/login", status_code=302)

    if not is_authorized_user(user.get("name", "")):
        return HTMLResponse(
            content=f"<h1>접근 권한이 없습니다</h1><p>{user.get('name')} ({user.get('email')})</p>",
            status_code=403,
        )

    html_path = _STATIC_DIR / "index.html"
    if not html_path.exists():
        return HTMLResponse("<h1>chat UI not deployed</h1>", status_code=500)

    html = html_path.read_text(encoding="utf-8")
    html = html.replace("{{USER_NAME}}", user.get("name", "사용자"))
    html = html.replace("{{USER_EMAIL}}", user.get("email", ""))
    html = html.replace("{{USER_AVATAR}}", user.get("avatar", ""))
    return HTMLResponse(content=html)


_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>MOCO Chat</title>
  <style>
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#1a1a1a;color:#eee;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
    .card{{background:#222;padding:32px;border-radius:12px;min-width:320px;max-width:380px;width:90%;box-shadow:0 8px 24px rgba(0,0,0,.4)}}
    h1{{margin:0 0 16px;font-size:20px;color:#fff}}
    label{{display:block;font-size:13px;color:#aaa;margin-bottom:6px}}
    input{{width:100%;padding:12px;background:#111;border:1px solid #444;color:#eee;border-radius:6px;font-size:16px;box-sizing:border-box}}
    input:focus{{outline:none;border-color:#007AFF}}
    button{{width:100%;padding:12px;background:#007AFF;color:white;border:none;border-radius:6px;font-size:16px;cursor:pointer;margin-top:16px}}
    button:hover{{background:#0066cc}}
    .err{{background:#3a1a1a;color:#f88;padding:10px;border-radius:6px;margin-bottom:12px;font-size:14px}}
    .hint{{color:#777;font-size:12px;margin-top:6px}}
  </style>
</head>
<body>
  <form class="card" method="post" action="/chat/login">
    <h1>MOCO Chat</h1>
    {error_block}
    <label for="nickname">Slack 닉네임</label>
    <input id="nickname" name="nickname" autofocus required maxlength="50" placeholder="예: 관리자" autocomplete="username">
    <div class="hint">{{닉네임}}@example.com 으로 매핑되어 대화 컨텍스트에 사용됩니다.</div>
    <button type="submit">시작하기</button>
  </form>
</body>
</html>"""


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    if get_session_user(request):
        return RedirectResponse(url="/chat", status_code=302)
    error_block = f'<div class="err">{error}</div>' if error else ""
    return HTMLResponse(_LOGIN_PAGE.format(error_block=error_block))


@router.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    nickname = (form.get("nickname") or "").strip()
    if not nickname:
        return RedirectResponse(url=f"/chat/login?error={quote('닉네임을 입력하세요')}", status_code=302)
    if not is_authorized_user(nickname):
        return RedirectResponse(
            url=f"/chat/login?error={quote(f'허용되지 않은 사용자입니다: {nickname}')}",
            status_code=302,
        )

    email = f"{nickname.lower()}@example.com"
    try:
        slack_uid = await resolve_slack_user_id(email)
    except Exception:
        slack_uid = None

    request.session["user"] = {
        "name": nickname,
        "email": email,
        "id": slack_uid or f"web:{email}",
    }
    logger.info(f"[WEB_CHAT] nickname login: {nickname} ({email}, slack_id={slack_uid})")
    return RedirectResponse(url="/chat", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    request.session.pop("user", None)
    return RedirectResponse(url="/chat/login", status_code=302)


@router.get("/api/agents")
async def list_agents(request: Request):
    """
    UI 에 노출할 에이전트 카탈로그 반환.

    빌트인 (Atticus, RA Expert) + agent_factory 가 publish 한 generated 에이전트(status='approved').
    """
    _require_user(request)
    items = list(_BUILTIN_AGENT_CATALOG.values())

    # generated 에이전트 중 approved 만
    for entry in agent_registry.list_active():
        aid = entry["agent_id"]
        if aid in _AGENT_STREAMERS:
            items.append({
                "agent_id": aid,
                "agent_name": entry["agent_name"],
                "description": entry["description"],
                "icon": entry["agent_name"][:2] if entry["agent_name"] else "🤖",
                "examples": entry.get("examples") or [],
                "source": "generated",
                "created_by": entry.get("created_by"),
                "created_at": entry.get("created_at"),
            })
    return {"agents": items}


@router.get("/api/conversations")
async def list_conversations(request: Request):
    user = _require_user(request)
    return {"conversations": history_store.list_conversations(user["email"])}


@router.post("/api/conversations")
async def create_conversation(request: Request, body: dict = Body(default={})):
    user = _require_user(request)
    title = (body.get("title") or "새 대화")[:200]
    conv_id = history_store.create_conversation(user["email"], user.get("name", ""), title)
    return {"id": conv_id, "title": title}


@router.delete("/api/conversations/{conv_id}")
async def delete_conversation(request: Request, conv_id: str):
    user = _require_user(request)
    ok = history_store.delete_conversation(conv_id, user["email"])
    return {"deleted": ok}


@router.patch("/api/conversations/{conv_id}")
async def rename_conversation(request: Request, conv_id: str, body: dict = Body(...)):
    user = _require_user(request)
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title required")
    ok = history_store.rename_conversation(conv_id, user["email"], title)
    return {"renamed": ok}


@router.get("/api/conversations/{conv_id}/messages")
async def get_messages(request: Request, conv_id: str):
    user = _require_user(request)
    conv = history_store.get_conversation(conv_id, user["email"])
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"conversation": conv, "messages": history_store.get_messages(conv_id)}


@router.get("/api/conversations/{conv_id}/attachments/{stored_name}")
async def download_attachment(request: Request, conv_id: str, stored_name: str):
    """대화에 첨부한 파일 다운로드 (UI 에서 클릭 시 사용). stored_name 은 디스크에 저장된 실제 파일명."""
    user = _require_user(request)
    conv = history_store.get_conversation(conv_id, user["email"])
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")

    # 디렉토리 탈출 차단 — basename + 안전 문자만
    safe = _safe_filename(stored_name)
    base_dir = _attachments_dir(conv_id).resolve()
    path = (base_dir / safe).resolve()
    try:
        path.relative_to(base_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid path")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    # 사용자에게 보여줄 파일명은 batch_id 접두어 제거
    display_name = safe.split("__", 1)[-1] if "__" in safe else safe
    return FileResponse(path, filename=display_name)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def _parse_stream_request(
    request: Request, conv_id: str
) -> tuple[str, str, List[dict]]:
    """
    stream 요청을 multipart 또는 JSON 으로 파싱.

    Returns: (user_text, agent_key, attachments)
        attachments: [{"name", "mimetype", "size", "file_path"}, ...]
    """
    content_type = (request.headers.get("content-type") or "").lower()

    if content_type.startswith("multipart/"):
        form = await request.form()
        user_text = (form.get("text") or "").strip()
        agent_key = (form.get("agent") or "operator").strip().lower()

        upload_files = form.getlist("files") if hasattr(form, "getlist") else []
        # FastAPI 의 form 은 multidict — getlist 없을 수도 있어서 fallback
        if not upload_files:
            upload_files = [v for k, v in form.multi_items() if k == "files"]

        if len(upload_files) > _MAX_FILES_PER_MESSAGE:
            raise HTTPException(
                status_code=400,
                detail=f"한 메시지에 첨부 가능한 파일은 최대 {_MAX_FILES_PER_MESSAGE}개입니다.",
            )

        attachments: List[dict] = []
        if upload_files:
            target_dir = _attachments_dir(conv_id)
            # 같은 메시지에 첨부된 파일들을 묶을 prefix (충돌 방지 + 정렬용)
            batch_id = f"{int(time.time())}_{uuid.uuid4().hex[:6]}"
            for uf in upload_files:
                # UploadFile 만 처리. 빈 form field 무시.
                if not hasattr(uf, "filename") or not uf.filename:
                    continue
                safe = _safe_filename(uf.filename)
                stored_name = f"{batch_id}__{safe}"
                file_path = target_dir / stored_name
                size = 0
                with open(file_path, "wb") as out:
                    while True:
                        chunk = await uf.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > _MAX_FILE_SIZE_BYTES:
                            out.close()
                            try:
                                file_path.unlink()
                            except Exception:
                                pass
                            raise HTTPException(
                                status_code=413,
                                detail=f"'{safe}' 파일이 너무 큽니다 (최대 50MB).",
                            )
                        out.write(chunk)
                mime = uf.content_type or mimetypes.guess_type(safe)[0] or "application/octet-stream"
                attachments.append({
                    "name": safe,             # 표시용 (UI 칩, 다운로드 시 사용자에게 보이는 파일명)
                    "stored_name": stored_name,  # 디스크 상 실제 파일명 (다운로드 URL 용)
                    "mimetype": mime,
                    "size": size,
                    "file_path": str(file_path),
                })
        return user_text, agent_key, attachments
    else:
        body = await request.json()
        user_text = (body.get("text") or "").strip()
        agent_key = (body.get("agent") or "operator").strip().lower()
        return user_text, agent_key, []


@router.post("/api/conversations/{conv_id}/stream")
async def stream_reply(request: Request, conv_id: str):
    """사용자 메시지(+선택적 첨부 파일)를 받아 operator를 SSE로 스트리밍.

    multipart/form-data: fields = text, agent (optional), files (multiple)
    application/json   : { text, agent? }   — 첨부 없는 경우
    """
    user = _require_user(request)
    conv = history_store.get_conversation(conv_id, user["email"])
    if not conv:
        raise HTTPException(status_code=404, detail="conversation not found")

    user_text, agent_key, attachments = await _parse_stream_request(request, conv_id)
    if not user_text and not attachments:
        raise HTTPException(status_code=400, detail="text or files required")
    # 텍스트가 비어있고 파일만 있을 때 기본 프롬프트
    if not user_text:
        user_text = "첨부한 파일을 확인해줘."

    streamer = _AGENT_STREAMERS.get(agent_key, stream_operator_for_web)

    # 사용량 추적 — generated 에이전트만 (operator/atticus/ra_expert 는 빌트인이라 skip)
    if agent_key not in ("operator", "atticus", "ra_expert"):
        try:
            agent_registry.record_usage(agent_key)
        except Exception as e:
            logger.warning(f"[WEB_CHAT] record_usage 실패: {e}")

    history_store.add_message(conv_id, "user", user_text, attachments=attachments or None)

    # 첫 메시지면 제목을 사용자 입력 앞부분으로 자동 설정
    msgs_so_far = history_store.get_messages(conv_id)
    if len(msgs_so_far) == 1 and conv.get("title") == "새 대화":
        history_store.rename_conversation(conv_id, user["email"], user_text[:60])

    # 컨텍스트 준비
    slack_user_id = await resolve_slack_user_id(user["email"])
    message_data = build_message_data(user, slack_user_id, user_text, attachments=attachments or None)
    memory = await retrieve_memory(user_text, message_data)
    history = history_store.get_recent_messages_for_context(conv_id, max_turns=12)
    composed_query = compose_query_with_history(user_text, history[:-1])  # 방금 추가한 user 메시지 제외

    # SSE 하트비트 주기 — Cloudflare(~100s), nginx(기본 60s), 일반 LB 환경 모두 안전한 값
    HEARTBEAT_SEC = 15.0
    _STREAMER_DONE = object()  # 큐 종료 센티넬

    async def gen():
        """SSE 스트림 생성기.

        설계 의도:
        - streamer는 백그라운드 태스크로 분리하고 본 루프는 큐에서 이벤트를 끌어 씀.
        - HEARTBEAT_SEC마다 깨어나 ': keep-alive' SSE 코멘트를 송출 → 프록시/LB의 idle timeout 방지
          (Cloudflare Tunnel·nginx·직접 노출 어느 환경에서도 동일하게 작동).
        - 같은 주기로 request.is_disconnected() 확인 → 클라가 떠났으면 streamer 즉시 취소
          → 하위 ClaudeSDKClient.__aexit__ 정상 호출되어 좀비 서브프로세스 누적 방지.
        """
        accumulated_text = ""
        event_queue: asyncio.Queue = asyncio.Queue()

        async def run_streamer():
            try:
                async for ev in streamer(composed_query, message_data, memory):
                    await event_queue.put(ev)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[WEB_CHAT_STREAM] streamer error: {e}", exc_info=True)
                await event_queue.put({"type": "error", "message": str(e)[:200]})
            finally:
                await event_queue.put(_STREAMER_DONE)

        streamer_task = asyncio.create_task(run_streamer())

        try:
            yield _sse({"type": "start"})
            while True:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=HEARTBEAT_SEC)
                except asyncio.TimeoutError:
                    # 조용한 구간 — keep-alive 코멘트 + disconnect 점검
                    if await request.is_disconnected():
                        logger.info(f"[WEB_CHAT_STREAM] client disconnected (conv={conv_id})")
                        break
                    yield ": keep-alive\n\n"
                    continue

                if event is _STREAMER_DONE:
                    break

                if event["type"] == "text":
                    accumulated_text += event["delta"]
                yield _sse(event)

                if event["type"] == "done":
                    history_store.add_message(conv_id, "assistant", event.get("final", accumulated_text))
                elif event["type"] == "error":
                    history_store.add_message(conv_id, "assistant", f"⚠️ {event.get('message', '')}")
        except (asyncio.CancelledError, GeneratorExit):
            logger.info(f"[WEB_CHAT_STREAM] stream cancelled (conv={conv_id})")
            raise
        except Exception as e:
            logger.error(f"[WEB_CHAT_STREAM] {e}", exc_info=True)
            try:
                yield _sse({"type": "error", "message": str(e)[:200]})
            except Exception:
                pass
        finally:
            if not streamer_task.done():
                streamer_task.cancel()
                try:
                    await streamer_task
                except (asyncio.CancelledError, Exception):
                    pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
