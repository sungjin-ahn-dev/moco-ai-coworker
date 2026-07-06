# MOCO를 Claude.ai 웹에서 사용하기 (멤버 안내)

acme Claude Team 워크스페이스 멤버 누구나 본인 브라우저에서 MOCO 도구를 호출할 수 있습니다. 설치·터미널·코드 편집 없습니다.

## 사전 준비물 (1분)

1. Claude.ai 로그인 (Team 워크스페이스)
2. 관리자님이 발급한 **본인 전용 토큰** (`tok_xxxxxxx`) — 외부 공유 금지

## 등록 절차 (3분)

### 1. Connector 추가 페이지 열기

Claude.ai → 좌측 사이드바 또는 우측 메뉴에서 **Connectors** 또는 **Settings → Connectors**

### 2. **+ Add Custom Connector**

다음 정보 입력:

| 필드 | 값 |
|---|---|
| Name | `MOCO` |
| Server URL | `https://your-tunnel.trycloudflare.com/mcp/mcp` |

(URL은 운영팀이 안내한 최신값으로 사용 — 임시 도메인은 변경될 수 있음)

### 3. **Connect** 클릭

새 창이 뜨고 MOCO 인증 페이지로 이동합니다.

### 4. MOCO 인증 페이지

```
🔐 MOCO MCP 인증
Claude가 MOCO 봇 도구에 접근하려고 합니다.
본인 토큰을 입력하고 승인해주세요.

[MOCO MCP 토큰: tok_____________]
[승인하고 연결]
```

받은 토큰(`tok_xxx`) 붙여넣고 **승인하고 연결**.

### 5. 자동으로 Claude.ai로 돌아옴

Connector 목록에 `MOCO · ✓ Connected`. 끝.

## 사용 (일상)

새 채팅에서 자연어로:

```
moco_status 도구로 시스템 상태 보여줘
moco_ask("어제 회의록 정리해줘") 호출
moco_ask("미답신 24시간 넘은 메일 알려줘") 호출
moco_save_memory("오늘 결정: X 방향으로 가기로", "decisions") 호출
```

또는 그냥 자연어로:
```
moco에 우리 부서 이번주 일정 정리해 달라고 해줘
moco로 #engineering 채널에 다음 월요일 9시에 알림 보내달라 해줘
```

## 사용 가능한 도구 9개

| 도구 | 설명 |
|---|---|
| `moco_ask(message)` | **메인**. Operator(Opus)가 모든 MCP 도구로 자연어 요청 처리 |
| `moco_chat(message)` | 가벼운 답변 (Haiku) |
| `moco_search_memory(query)` | 메모리 검색 |
| `moco_save_memory(content, category)` | 메모리 저장 |
| `moco_list_email_tasks(status)` | 이메일 태스크 |
| `moco_list_jira_tasks(status)` | Jira 태스크 |
| `moco_list_pending_answers()` | 대기 중 답변 |
| `moco_schedule_message(channel, message, when)` | Slack 메시지 예약 |
| `moco_status()` | 시스템 상태 |

## 주의

- **토큰 외부 공유 금지** (개인 권한)
- **관리자님 PC가 켜져 있어야** 동작 (현재 본인 PC 호스팅, 클라우드 이전 예정)
- **Slack 봇과 동일한 메모리/태스크** 공유 — Slack에서 했던 작업이 Claude에도 반영됨
- 도구 실행은 Claude가 자동 — `moco_ask`만 부르면 Operator가 알아서 적절한 도구 선택

## 안 되면

- Connector가 `✗ Failed to connect` → 관리자님께 URL 갱신 확인 (트라이클라우드 임시 URL은 가끔 바뀜)
- 인증 페이지에서 토큰 거부 → 관리자님께 토큰 재발급 요청
- `moco_ask` 응답 없음 → 관리자님 PC가 꺼졌을 수도. #moco-help 채널
