# Agent Factory — MOCO 자동 에이전트 생성 시스템

> MOCO 가 사용자 요청을 받아 새 에이전트를 코드 생성 → 검증 → 승인 → publish 까지 자동으로 처리.
> Phase 1 (사용자 명시 요청) / Phase 2 (메모리 분석으로 후보 자동 감지) / Phase 3 자동 archive 까지 운영. Phase 3 잔여(품질 피드백 버튼·버전 히스토리·관리자 페이지)는 미구현.

## 구조

```
app/cc_agents/agent_factory/
├── __init__.py           # facade: propose_agent()
├── template.py           # 5-슬롯 템플릿 + 검증 (agent_id/도구/모델 화이트리스트)
├── validator.py          # 3단계 검증 (py_compile, 격리 import, dry_run)
├── installer.py          # /tmp → generated/ atomic move + importlib.reload
├── registry.py           # agents_registry.json (status/usage/provenance)
├── approval.py           # Slack DM 승인 요청 + publish/reject
├── mcp_tools.py          # MCP 도구 노출 (propose_new_agent, list_my_agents)
├── lifecycle.py          # 사용량 추적 + archive + disable
└── README.md (본 문서)

app/cc_agents/generated/   # 자동 생성된 에이전트 격리 디렉토리
├── __init__.py
├── loader.py             # try/except 격리 import
└── <agent_id>/agent.py   # propose_agent() 가 생성한 파일들
```

## 사용 방법

### 1. 웹 챗에서 (자연어)
사용자가 챗창에서 입력:
> "식약처 동향 모니터링 에이전트 하나 만들어줘. 매주 mfds.go.kr 새 가이드라인 체크하고 우리 제품 영향 분석해주는 거"

operator 가 `mcp__agent_factory__propose_new_agent` 도구 호출 → 검증 → 승인 요청.

### 2. Slack 에서 (자연어)
DM 또는 멘션:
> "@MOCO 에이전트 만들어줘 — 계약서 검토 보조"

operator 가 동일하게 처리.

### 3. 직접 호출 (테스트/스크립트)
```python
from app.cc_agents.agent_factory import propose_agent

result = await propose_agent(
    agent_id="mfds_tracker",
    agent_name="🛰️ 식약처 동향 트래커",
    description="...",
    system_prompt="당신은 ...",
    model_tier="MODERATE",
    allowed_tools=["Read", "WebFetch", "WebSearch", "mcp__time__*"],
    corpus_dir="/home/user/MOCO_DATA/RA_규제자료",
    examples=["이번 주 변경된 가이드라인 알려줘"],
    created_by="user2@example.com",
)
```

## 승인 흐름

```
사용자 OK → 템플릿 채우기 → /tmp staging → 검증 3단계
   ↓ (통과)
generated/<id>/ atomic move → registry pending
   ↓
AGENT_APPROVER_SLACK_ID 설정?
   ├─ Yes → 관리자 에게 Slack DM 발송 (블록 키트, 시스템 프롬프트 미리보기)
   │         ↓
   │       admin DM 회신: "approve <id>" 또는 "reject <id> <사유>"
   │         ↓
   │       publish() → routes._AGENT_STREAMERS 등록 → 즉시 사용 가능
   │
   └─ No  → auto_approve() → 즉시 publish (개발 모드)
```

## 안전 장치

| 장치 | 설명 |
|---|---|
| 템플릿 5-슬롯 | 자유 코드 생성 금지, 슬롯 채우기만 → 구문 오류 불가능 |
| 도구 화이트리스트 | Bash, Write, Edit 등 메타시스템 변조 도구 금지 |
| /tmp staging | 운영 디렉토리 진입 전 별도 위치에서 검증 |
| 격리 import | subprocess 에서 import 시도 — 사이드이펙트로부터 운영 프로세스 보호 |
| dry_run | 실제 stream_for_web 5초 호출 — 응답 안 오면 거부 |
| atomic move | shutil.move + 백업 — 실패 시 자동 롤백 |
| try/except 로드 | generated/ 한 개 깨져도 다른 에이전트 정상 동작 |
| status='archived' | 30일 미사용 자동 archive (디렉토리 유지, routes 제거) |

## 설정

`app/config/settings.py` 또는 `~/.moco/config.env`:

```env
# 자동 승인 (개발/MVP 모드)
AGENT_APPROVER_SLACK_ID=

# admin 의 Slack user_id 채우면 승인 게이트 활성화
AGENT_APPROVER_SLACK_ID=U01ABCDEF12

# 시스템 비활성화
AGENT_FACTORY_ENABLED=false

# archive 기준 일수 (기본 30)
AGENT_AUTO_ARCHIVE_DAYS=30
```

## 상태 머신

```
pending  → 검증 통과 후 승인 대기 (Slack DM 발송됨)
   ↓ 관리자 "approve"
approved → 활성, routes._AGENT_STREAMERS 등록, UI 모달에 카드 등장
   ↓ 30일 미사용
archived → 디렉토리 유지, routes 제거, unarchive 로 복귀 가능
   ↓ 또는
   ↓ 관리자 "reject" / 사용자 "disable" 
disabled / rejected → routes 제거, registry 에 사유 기록
   ↓ rollback()
삭제 → generated/<id>/ 디렉토리 제거
```

## Phase 2 (자동 감지) — 운영 중

- 별도 스케줄러 `candidate_suggester` 가 매일 `AGENT_LIFECYCLE_DAILY_HOUR:00` (기본 03:00 KST) 에 동작
- `lifecycle.detect_agent_candidates(window_days=30)` 가 최근 메모리(channels/projects/users/decisions) 를 Sonnet 에게 입력 → 도메인 키워드 반복 + 기존 에이전트 미중복 + 도메인 구체성 통과 후보를 JSON 으로 반환
- `candidates_store` 에 spec 저장 (TTL 7일, 48시간 내 같은 도메인 중복 차단) → `candidate_id` 반환
- 사용자에게 `request_confirmation` 으로 짧은 제안 발송. `original_request_text` 에 `[AGENT_CANDIDATE:<id>]` 토큰 임베드
- 사용자 "응" → `proactive_confirm` 이 원문을 operator 에게 전달 → operator 의 `<agent_candidate_recognition>` 가이드가 토큰 인식 → `propose_candidate_agent(candidate_id)` 도구 호출 → store 에서 spec 로드 → Phase 1 의 `propose_agent()` 6단계 그대로 실행
- 토글: `AGENT_CANDIDATE_SUGGESTER_ENABLED` (기본 True)

## Phase 3 (라이프사이클)

- 자동 archive (운영): 매일 `AGENT_LIFECYCLE_DAILY_HOUR:30` (기본 03:30 KST) 에 `archive_unused_agents(idle_days=AGENT_AUTO_ARCHIVE_DAYS)` 실행. routes 에서 제거 + `generated/<id>/` 디렉토리는 유지하여 `unarchive()` 로 즉시 복귀 가능.
- 승인 UI (운영): Slack DM 의 Block Kit 버튼 `[✅ 승인]` / `[❌ 반려]` (`approval.send_approval_request` + `cc_slack_handlers.handle_agent_factory_approve/reject`). 클릭 시 `chat_update` 로 메시지가 결과 블록으로 교체. 텍스트 회신 폴백(`approve <id>` / `reject <id> <사유>`) 도 유지.
- 웹챗 카드 자동 갱신 (운영): `chat.js` 가 60초 주기 polling (활성 탭에서만) + `visibilitychange` 시 즉시 1회 — 새로고침 없이 1분 내 새 카드 등장.
- 미구현 TODO:
  - UI 에 "👎 이 에이전트 별로" 버튼 → `disable_agent()` 호출
  - registry 에 version 히스토리 (시스템 프롬프트 수정 시)
  - 관리자용 admin 페이지 — 모든 에이전트 일람·수정·롤백·강제 비활성화

## 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| ImportError: app.cc_agents.generated.<id> | generated/<id>/__init__.py 또는 agent.py 누락. registry 에서 status 확인. |
| 승인 DM 안 옴 | `AGENT_APPROVER_SLACK_ID` 가 비었거나 봇이 해당 사용자에게 DM 권한 없음. logs/server.log 의 `[AGENT_APPROVAL]` 라인 확인. |
| 모달에 카드 안 보임 | `/chat/api/agents` 응답 확인. status=approved 인지, routes._AGENT_STREAMERS 에 등록됐는지 점검. |
| dry_run 타임아웃 | 시스템 프롬프트가 너무 길거나 Claude 한도 초과. `skip_dry_run=True` 로 우회 가능 (운영 권장 X). |

## 운영 체크리스트

- [ ] 관리자 Slack user_id 를 `settings.AGENT_APPROVER_SLACK_ID` 에 설정
- [x] `app/main.py` 스케줄러에 `archive_unused_agents` 일 1회 등록 (03:30 KST)
- [x] `candidate_suggester` 로 메모리 분석 → 후보 confirm 발송 (03:00 KST)
- [x] Slack 승인을 Block Kit 버튼으로 처리
- [x] 웹챗 카드 목록 자동 갱신 (60초 polling + visibilitychange)
- [ ] UI 에 "이 에이전트 별로" 피드백 버튼 추가 (Phase 3 잔여)
- [ ] registry.json 백업 정책 결정 (~/MOCO_DATA/ 가 백업되는지 확인)
