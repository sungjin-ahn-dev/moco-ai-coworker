# MOCO MCP Server

acme 멤버가 **Claude Code / Claude Desktop**에서 MOCO 봇 기능을 MCP 도구로 호출하기 위한 모듈.

기존 Slack 봇 진입점은 그대로. FastAPI(8000)에 `/mcp` 엔드포인트만 추가 마운트.

## 활성화 (개발자 1회)

1. Electron 앱 → 설정 → **MCP Server** 섹션에서 `MCP_ENABLED=True` 토글
   - 또는 `~/.moco/config.env`에 직접 `MCP_ENABLED=True`
2. Electron Stop → Start (서버 재시작 + WSL sync)
3. `~/.moco/mcp_tokens.json` 생성:
   ```bash
   # WSL 또는 Cloud Shell 등에서
   python -c "from app.cc_mcp.auth import add_user_token; print(add_user_token('~/.moco/mcp_tokens.json', 'U01ABC'))"
   ```
   출력된 `tok_xxx`를 멤버에게 전달.

## 멤버 등록 (각자 1회)

### Claude Code (CLI)

```bash
claude mcp add moco http://localhost:8000/mcp \
  -t streamable-http \
  -H "Authorization: Bearer tok_xxx"
```

확인:
```bash
claude mcp list
```

### Claude Desktop (앱)

`%APPDATA%\Claude\claude_desktop_config.json` (Win) 또는 `~/Library/Application Support/Claude/claude_desktop_config.json` (Mac):

```json
{
  "mcpServers": {
    "moco": {
      "type": "http",
      "url": "http://localhost:8000/mcp",
      "headers": {
        "Authorization": "Bearer tok_xxx"
      }
    }
  }
}
```

저장 후 Claude Desktop 재시작.

## 노출된 도구

| 도구 | 설명 |
|---|---|
| `moco_ask(message)` | **메인**. Operator(Opus)로 자연어 처리. 모든 MCP 도구 사용 |
| `moco_chat(message)` | (1차에선 ask로 폴백) |
| `moco_search_memory(query)` | 메모리 검색 |
| `moco_save_memory(content, category)` | 메모리 저장 |
| `moco_list_email_tasks(status)` | 이메일 태스크 |
| `moco_list_jira_tasks(status)` | Jira 태스크 |
| `moco_list_pending_answers()` | 대기 중 답변 |
| `moco_schedule_message(channel, message, when)` | Slack 메시지 예약 |
| `moco_status()` | 시스템 상태 |

## 외부 노출 (Cloudflare Tunnel)

별도 작업. 로컬 PC에서 호스팅 중이면 `cloudflared tunnel` 깔고 `localhost:8000`을 도메인에 매핑.

## 제외 항목

- **CRM 페이지** (`cc_web_interface/crm/*`): 별도 웹 UI에서 관리
- **AICC** (제품A): 별도 프로젝트(project-a), MOCO 범위 외

## 알려진 한계

- `moco_ask` 응답은 Operator system_prompt 지침에 의존 — 모델이 무시하면 빈 응답 가능
- 보조 도구 시그니처 일부 추측 — 실제 동작 안 하면 `tools.py`의 `_call_cc_tool` candidates 조정 필요

자세한 작업 내역은 `CHANGELOG.md` 참고.
