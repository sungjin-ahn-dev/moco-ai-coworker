# MOCO MCP Server — Changelog

외부 Claude(Claude Code / Claude Desktop)에서 MOCO 봇 기능을 MCP 도구로 호출하기 위한 모듈.

---

## [Unreleased] — 2026-05-06

### Added — 1차 골격 (싸개)
- 신규 패키지 `app/cc_mcp/` 추가. 기존 코드 무영향(MCP_ENABLED=false 기본).
- `auth.py` — `~/.moco/mcp_tokens.json` 기반 Bearer 토큰 ↔ `user_slack_id` 매핑 (issue/verify/add/load).
- `context.py` — Slack 의존 agent들이 받는 `slack_data`/`message_data` 형태의 가짜 컨텍스트 생성 (channel_id=`MCP_<user>`, is_dm=true, is_mcp=true).
- `mcp_app.py` — FastMCP 인스턴스 빌더 + ContextVar 기반 현재 사용자 추적.
- `tools.py` — MCP 도구 9개 등록:
  - `moco_ask(message)` — Operator wrapper (모든 MCP 도구 사용 가능, 가장 중요)
  - `moco_chat(message)` — Simple Chat
  - `moco_search_memory(query)` — Memory Retriever
  - `moco_save_memory(content, category)` — Memory Manager 큐로 위임
  - `moco_list_email_tasks(status)` — email_tasks DB
  - `moco_list_jira_tasks(status)` — jira_tasks DB
  - `moco_list_pending_answers()` — waiting_answer DB
  - `moco_schedule_message(channel, message, when)` — scheduler
  - `moco_status()` — 시스템 상태 요약
- `server.py` — FastAPI 마운트 함수 + Bearer 인증 미들웨어. `MCP_ENABLED=true`일 때만 `/mcp` 엔드포인트 노출.
- `app/cc_web_interface/server.py` 수정 — 마지막에 `attach_mcp(web_app, settings)` 호출.
- `app/config/settings.py` — `MCP_ENABLED`, `MCP_PATH`, `MCP_TOKEN_FILE` 3개 환경변수 신설 (기본 OFF).
- `app/config/env/dev.env` — 같은 3개 default 값 추가.
- Electron UI 3곳 sync (CLAUDE.md 5곳 sync 규칙 준수):
  - `electron-app/renderer/index.html` — "MCP Server (외부 Claude 노출)" 섹션 추가
  - `electron-app/renderer/main.js` — `fields` 배열에 `MCP_ENABLED`, `MCP_PATH`, `MCP_TOKEN_FILE` 추가
  - `electron-app/main.js` — `sections` 객체에 동일 섹션 추가

### Excluded (사용자 요청에 따라)
- CRM 페이지(`cc_web_interface/crm/`) — 별도 웹 UI에서 관리
- AICC(제품A) — 별도 프로젝트(project-a), MOCO 범위 외

### Known Limitations / TODO
- `moco_ask` 응답 캡처: Operator system_prompt에 "MCP 호출이므로 mcp__slack__answer 호출 금지" 지침 추가 + `mcp__slack__answer`/`mcp__slack__send_message` 도구 disallowed로 두고 `ResultMessage.result`에서 텍스트 회수. 모델이 지침 무시하면 빈 응답 가능 — 추후 가짜 Slack mock MCP 서버로 응답 가로채기 방식으로 강화 필요.
- 보조 도구 일부(`moco_list_*`, `moco_schedule_message`)는 `cc_tools/*` 함수 시그니처를 가정해서 호출 — 실제 시그니처와 일부 차이 가능성 있음. 1차 동작 시 stub 메시지 반환 → 시그니처 맞춰 수정.
- `moco_chat`은 골격만 — `call_simple_chat`이 Slack으로 직접 응답 보내는 구조라 MCP 응답 캡처 wrapper 필요. 현재는 `_run_operator`로 폴백.
- 토큰 발급 GUI 미구현 — `~/.moco/mcp_tokens.json` 직접 편집 또는 Python 콘솔에서 `add_user_token` 호출.
- Cloudflare Tunnel 설정은 별도 작업 (외부 노출용).

### 관련 환경변수
| 이름 | 기본값 | 설명 |
|---|---|---|
| `MCP_ENABLED` | `false` | MCP 서버 켜기/끄기 |
| `MCP_PATH` | `/mcp` | 마운트 경로 |
| `MCP_TOKEN_FILE` | `~/.moco/mcp_tokens.json` | 토큰 매핑 파일 |

---

## 2026-05-06 (오후) — 동작 검증 + OAuth 2.1 추가

### Fixed
- `tools.py` `_run_operator`: `from app.cc_utils.mcp_servers import build_mcp_servers_dict` → `from app.cc_agents.operator.agent import build_mcp_servers_dict` (실제 모듈 위치)
- `server.py` `attach_mcp`: FastAPI mount 시 sub-app lifespan이 자동 호출되지 않아 FastMCP의 task group이 초기화되지 않는 문제 → parent의 `on_event("startup")`/`("shutdown")`에서 `http_app.router.lifespan_context(http_app)`를 명시적 enter/exit하여 task group 활성화
- `server.py` 인증 미들웨어: 디버그용 `[MCP_AUTH_DEBUG]` print 제거 + `WWW-Authenticate` 헤더에 `resource_metadata` URL 명시 (RFC 9728 / MCP authorization 스펙 준수, Claude.ai가 OAuth flow 자동 시작 가능)
- 실제 MCP URL은 `/<MCP_PATH>/mcp` (FastMCP의 streamable_http_app가 sub-app 안에 자체 `/mcp` path를 가지므로) — README/안내 갱신 필요

### Added — OAuth 2.1 (Claude.ai 웹 Custom Connector 호환)
- `oauth.py` — Authorization code (in-memory, 5분 단명, PKCE 검증) + Access token 영구 저장(`~/.moco/mcp_oauth_tokens.json`) + Dynamic Client Registration 저장(`~/.moco/mcp_oauth_clients.json`)
- `oauth_routes.py` — FastAPI 라우터:
  - `GET /.well-known/oauth-protected-resource` (RFC 9728)
  - `GET /.well-known/oauth-authorization-server` (RFC 8414)
  - `GET /.well-known/openid-configuration` (alias)
  - `POST /oauth/register` (RFC 7591 Dynamic Client Registration)
  - `GET /oauth/authorize` (HTML 토큰 입력 페이지) + `POST /oauth/authorize` (승인 → code 발급 → redirect)
  - `POST /oauth/token` (code + code_verifier → access_token, PKCE 검증)
- 미들웨어 `verify_any_token`로 확장: `mcp_at_*` (OAuth) + `tok_*` (정적) 둘 다 인식 — backward 호환
- 토큰 prefix 구분:
  - `tok_*`     — 직접 발급한 정적 Bearer (Claude Code CLI 등)
  - `mcp_at_*`  — OAuth access token (Claude.ai 웹 Custom Connector flow)
  - `mcp_code_*`— OAuth authorization code (5분 단명, in-memory)
  - `mcp_cli_*` — OAuth client_id (DCR로 등록)

### 흐름 (Claude.ai 웹 멤버)
1. Settings → Connectors → Add Custom Connector → URL `https://moco.../mcp/mcp`
2. Authorize 클릭 → MOCO `/oauth/authorize` 페이지 열림
3. 본인 토큰(`tok_xxx`, 관리자님이 발급) 붙여넣기 → 승인
4. Claude.ai로 redirect → 자동 access_token 교환
5. 이후 호출은 `Authorization: Bearer mcp_at_xxx` 로 자동 인증
6. 도구 9개 활성화

### Known Limitations
- access_token에 만료(expires_in)/refresh 미구현 — 영구 토큰
- DCR `client_secret` 발급 안 함 (public client only, PKCE 강제)
- `/oauth/authorize` 페이지의 토큰 검증은 정적 토큰(`tok_*`)만 지원 — 멤버는 관리자님이 발급한 정적 토큰을 가지고 있어야 함
