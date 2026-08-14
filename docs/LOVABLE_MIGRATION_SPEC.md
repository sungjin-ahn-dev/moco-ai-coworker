# MOCO CRM → Lovable 이관 사양서

> **목적**: 제품A Lovable 대시보드 측에서 MOCO CRM의 데이터 모델·기능을 참고해 동등한 시스템을 재구현할 수 있도록 정리한 사양서.
> **데이터 이관 X** — 빈 시스템부터 신규 구축. 단 MOCO 스키마는 Lovable LLM이 매핑 판단에 활용할 수 있도록 상세 기록.

---

## 0. Quick Reference

- **MOCO CRM** = "HubSpot Professional 클론" + 의료기기/DTx 도메인 특화 (병원·HCP·처방·매출·KOL)
- **현 구현 stack**: Python FastAPI + SQLAlchemy + **SQLite** + React(SPA, single HTML)
- **Lovable 권장 stack**: React + TypeScript + Tailwind + shadcn/ui + **Supabase (Postgres + Auth + Realtime + Storage)**
- **25개 테이블 / 10개 enum / 23개 API 라우터 그룹 / 12개 메인 UI 페이지**
- **2개 영업 담당자 페르소나**: `Harry`, `Chloe` — territory_owner 컬럼으로 분기
- **타임존**: KST (Asia/Seoul) 고정. 모든 timestamp는 timezone-aware
- **언어**: UI 한국어 (코드·DB는 영문 식별자, 표시 라벨만 한국어)

---

## 1. 비즈니스 도메인

### 1.1 회사 소개

**회사A / ProductA** — 신경·인지 분야 디지털 헬스케어. 주요 제품:
- **ProductA** — 인지·기억훈련 디지털 치료제 (DTx)
- **ProductC** — 인지 스크리닝 검사
- **ProductE** — 진단 보조 도구

고객은 **병원·의사(HCP)**. 영업은 직접 방문·KOL 미팅·학회·이메일.

### 1.2 핵심 워크플로

```
  병원 발굴 → 의사 발굴 → 미팅·디테일콜 → 제품 리스팅(채택) → 처방 →
   ↓                                          ↓                ↓
  Deal 파이프라인                          KOL 관리         매출 인식·입금
  (영업 stage 관리)                       (외래·우호도)      (월별 집계)
```

### 1.3 페르소나

| 이름 | 역할 | DB 표현 |
|---|---|---|
| **Harry** | 영업 담당 1 | `territory_owner = 'Harry'`, `owner_slack_id` |
| **Chloe** | 영업 담당 2 | `territory_owner = 'Chloe'`, `owner_slack_id` |
| **전사** | 공통 (휴일 등) | `user_slack_id = NULL` |

영업 담당 별로 자기 병원·자기 처방·자기 일정만 보는 게 기본. 전체 보기도 가능.

### 1.4 외부 통합

- **Slack** — 알림·메시지·승인 (현재 MOCO bot 통해)
- **Google Calendar** — 일정/근무일 양방향 동기화 (Service Account + Domain-Wide Delegation)
- **이메일** — SMTP 발송, 추적(open·click)
- **Microsoft 365** — Outlook (선택)
- **NCP SENS / Solapi** — SMS·알림톡

Lovable 측에선 위 통합을 Supabase Edge Functions 또는 별도 백엔드로 구현하면 됨. Phase 1은 통합 제외하고 CRUD UI만으로도 가치 있음.

---

## 2. 데이터 모델 (25 테이블 전체 스키마)

### 2.1 ERD 한 장 요약

```
companies (병원)              ← 모든 도메인의 hub
  ├─ contacts (의사)          ← 1:N
  ├─ deals (거래)             ← 1:N
  ├─ activities (활동)        ← 1:N
  ├─ prescriptions (처방)     ← hospital_id 1:N + doctor_id (contacts)
  ├─ sales_transactions      ← 1:N (월별 매출)
  ├─ product_listings        ← 1:N
  ├─ kol_plans               ← 1:N + doctor_id
  ├─ hospital_contracts      ← 1:N
  └─ working_day_events      ← company_id, contact_id (sales 미팅)

contacts (의사·HCP)
  ├─ deals, activities, enrollments, form_submissions
  └─ company_id → companies

deals (거래)
  ├─ pipeline_id → pipelines
  ├─ contact_id, company_id
  └─ activities → 1:N

activities (활동 — call/email/meeting/note/task)
  └─ contact_id·deal_id·company_id 다중 FK (선택)

email_sequences ─ steps[]
  └─ email_enrollments → contact_id (시퀀스에 등록된 사람)
       └─ email_trackings (open·click·reply)

forms ─ form_submissions → contact_id
relationships (다대다 네트워크 — Attio 스타일)
segments (스마트 리스트 — JSON 필터)
patient_compliance (처방 환자 순응도)
crm_tasks (할 일)
reference_data (정적 참조)
working_day_events ← 일정 = 휴가/학회/공휴일/영업미팅 통합
  ├─ activity_id → activities (sales 미팅 시 자동 생성)
  └─ gcal_event_id (Google Calendar 동기화)
```

### 2.2 Enum 10개

```python
LeadStatus           = new | contacted | qualified | unqualified
LifecycleStage       = subscriber | lead | mql | sql | opportunity | customer | evangelist
ActivityType         = call | email | meeting | note | task
SequenceStatus       = active | paused | archived
EnrollmentStatus     = active | completed | paused | bounced
TriggerType          = deal_stage_change | contact_created | lead_score_threshold |
                       form_submission | email_opened | tag_added | manual
AutomationStatus     = active | paused
TaskStatus           = todo | in_progress | done
TaskPriority         = low | medium | high
WorkingDayEventType  = public_holiday | vacation | conference | training |
                       sales_activity | other
```

⚠️ **DB 저장은 영문 enum value**. 한국어 라벨은 UI에서만 매핑 (이전에 한글로 저장됐다가 enum mismatch로 깨진 경험 있음).

### 2.3 테이블 상세 (DDL 형태 — Postgres 변환본)

#### 2.3.1 `companies` (병원)

```sql
CREATE TABLE companies (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255) NOT NULL,                  -- 병원명
  domain VARCHAR(255),                         -- 웹사이트 도메인
  industry VARCHAR(100),
  employee_count INTEGER,
  annual_revenue FLOAT,
  phone VARCHAR(50),
  address TEXT,
  city VARCHAR(100),
  country VARCHAR(100),
  -- 의료기관 마스터 확장
  hospital_code VARCHAR(50) UNIQUE,            -- 보건복지부 요양기관 기호
  hospital_type VARCHAR(50),                   -- 상급종합/종합/병원/의원
  region_1 VARCHAR(50),                        -- 시도
  region_2 VARCHAR(50),                        -- 구·군
  region_3 VARCHAR(50),                        -- 권역
  territory_owner VARCHAR(50),                 -- 'Harry' | 'Chloe'
  is_target BOOLEAN DEFAULT FALSE,             -- 타겟 병원 여부
  custom_properties JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_companies_name ON companies(name);
CREATE INDEX ix_companies_domain ON companies(domain);
```

#### 2.3.2 `contacts` (의사·HCP)

```sql
CREATE TABLE contacts (
  id SERIAL PRIMARY KEY,
  first_name VARCHAR(100) NOT NULL,
  last_name VARCHAR(100),                      -- 한국 이름은 last_name(성) + first_name(이름)
  email VARCHAR(255) UNIQUE,
  phone VARCHAR(50),
  company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
  owner_slack_id VARCHAR(50),                  -- 담당 영업 (Slack 사용자 ID)
  lead_score INTEGER DEFAULT 0,                -- 0~100
  lead_status VARCHAR(20) DEFAULT 'new',       -- enum LeadStatus
  lifecycle_stage VARCHAR(20) DEFAULT 'lead',  -- enum LifecycleStage
  source TEXT,                                 -- 유입 경로
  tags JSONB DEFAULT '[]',                     -- 문자열 배열
  -- HCP 확장
  hcp_code VARCHAR(50) UNIQUE,                 -- 의사 코드 (사내 부여)
  department VARCHAR(100),                     -- 진료과 (신경과·내과 등)
  sub_specialty VARCHAR(100),                  -- 세부전공
  title_position VARCHAR(100),                 -- 직급 (과장·교수·원장)
  license_number VARCHAR(50),                  -- 면허번호
  custom_properties JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_contacts_email ON contacts(email);
CREATE INDEX ix_contacts_lead_status ON contacts(lead_status);
CREATE INDEX ix_contacts_lifecycle_stage ON contacts(lifecycle_stage);
CREATE INDEX ix_contacts_owner ON contacts(owner_slack_id);
```

#### 2.3.3 `pipelines` (영업 단계 정의)

```sql
CREATE TABLE pipelines (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  stages JSONB NOT NULL,                       -- [{"id":"qualified","name":"적격","probability":20}, ...]
  is_default BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### 2.3.4 `deals` (거래·기회)

```sql
CREATE TABLE deals (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  pipeline_id INTEGER NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
  stage VARCHAR(100) NOT NULL,                 -- pipelines.stages[].id 참조
  amount FLOAT DEFAULT 0,                      -- 거래 금액
  currency VARCHAR(10) DEFAULT 'KRW',
  close_date TIMESTAMPTZ,                      -- 예상 종결일
  contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
  company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
  owner_slack_id VARCHAR(50),
  probability INTEGER DEFAULT 0,               -- 0~100 확률
  lost_reason TEXT,
  custom_properties JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### 2.3.5 `activities` (모든 활동·콜·미팅·이메일·노트·태스크)

```sql
CREATE TABLE activities (
  id SERIAL PRIMARY KEY,
  type VARCHAR(20) NOT NULL,                   -- enum ActivityType
  subject VARCHAR(500),
  body TEXT,                                   -- 메모 본문
  contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
  deal_id INTEGER REFERENCES deals(id) ON DELETE SET NULL,
  company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
  user_slack_id VARCHAR(50),
  associated_email_id VARCHAR(255),
  metadata JSONB DEFAULT '{}',                 -- 자유 형식 추가 데이터
                                                -- 자주 쓰는 키:
                                                -- call_objective: '리스팅'/'처방'/'Detail Call' 등
                                                -- product, customer, hospital, department
                                                -- done: 완료 체크
                                                -- schedule_source: 'working_day' (일정에서 자동 생성)
                                                -- working_day_event_id: 연결된 일정 ID
  timestamp TIMESTAMPTZ DEFAULT NOW(),         -- 활동 발생 시각
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

> **중요**: SalesActivityPage가 `type='call'`로 필터링해서 표시. 일정/근무일에서 자동 생성하는 미팅도 `type='call'`로 통일됨 (UI 표시 위해).

#### 2.3.6 `email_templates`

```sql
CREATE TABLE email_templates (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  type VARCHAR(50) DEFAULT 'email',            -- email | newsletter | pamphlet
  subject VARCHAR(500),
  body_html TEXT NOT NULL,
  body_text TEXT,
  variables JSONB DEFAULT '[]',                -- 치환 변수 ['{{first_name}}', '{{company_name}}']
  thumbnail_url VARCHAR(500),
  tags JSONB DEFAULT '[]',
  status VARCHAR(20) DEFAULT 'active',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### 2.3.7 `email_sequences` (drip 캠페인)

```sql
CREATE TABLE email_sequences (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  steps JSONB NOT NULL,                        -- [{
                                                --   step_no, delay_days, template_id,
                                                --   wait_for: 'on_open'/'on_click'/null,
                                                --   condition: {...}
                                                -- }]
  status VARCHAR(20) DEFAULT 'active',         -- enum SequenceStatus
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### 2.3.8 `email_enrollments`

```sql
CREATE TABLE email_enrollments (
  id SERIAL PRIMARY KEY,
  sequence_id INTEGER NOT NULL REFERENCES email_sequences(id) ON DELETE CASCADE,
  contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  current_step INTEGER DEFAULT 0,
  retry_count INTEGER DEFAULT 0,
  waiting_condition VARCHAR(50),               -- 'on_open' 같은 대기 조건
  status VARCHAR(20) DEFAULT 'active',         -- enum EnrollmentStatus
  started_at TIMESTAMPTZ DEFAULT NOW(),
  next_send_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ
);
CREATE INDEX ix_enrollments_next_send ON email_enrollments(next_send_at);
CREATE INDEX ix_enrollments_status ON email_enrollments(status);
```

#### 2.3.9 `automations` + `automation_executions` (워크플로우)

```sql
CREATE TABLE automations (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  trigger_type VARCHAR(50) NOT NULL,           -- enum TriggerType
  trigger_config JSONB DEFAULT '{}',           -- trigger 별 파라미터
  actions JSONB NOT NULL,                      -- [{"type":"send_email","template_id":12}, ...]
  status VARCHAR(20) DEFAULT 'active',
  execution_count INTEGER DEFAULT 0,
  last_executed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE automation_executions (
  id SERIAL PRIMARY KEY,
  automation_id INTEGER NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
  trigger_data JSONB DEFAULT '{}',
  results JSONB DEFAULT '[]',
  success BOOLEAN DEFAULT TRUE,
  executed_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### 2.3.10 `crm_tasks` (할 일)

```sql
CREATE TABLE crm_tasks (
  id SERIAL PRIMARY KEY,
  title VARCHAR(500) NOT NULL,
  description TEXT,
  due_date TIMESTAMPTZ,
  status VARCHAR(20) DEFAULT 'todo',           -- todo|in_progress|done
  priority VARCHAR(20) DEFAULT 'medium',       -- low|medium|high
  contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
  deal_id INTEGER REFERENCES deals(id) ON DELETE SET NULL,
  assigned_to_slack_id VARCHAR(50),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  completed_at TIMESTAMPTZ
);
```

#### 2.3.11 `forms` + `form_submissions`

```sql
CREATE TABLE forms (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  fields JSONB NOT NULL,                       -- [{name, label, type, required}, ...]
  redirect_url VARCHAR(500),
  notification_emails TEXT,
  submission_count INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE form_submissions (
  id SERIAL PRIMARY KEY,
  form_id INTEGER NOT NULL REFERENCES forms(id) ON DELETE CASCADE,
  contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
  data JSONB NOT NULL,
  submitted_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### 2.3.12 `relationships` (다대다 네트워크, Attio 스타일)

```sql
CREATE TABLE relationships (
  id SERIAL PRIMARY KEY,
  from_type VARCHAR(50) NOT NULL,              -- 'contact' | 'company' | 'deal'
  from_id INTEGER NOT NULL,
  to_type VARCHAR(50) NOT NULL,
  to_id INTEGER NOT NULL,
  relationship_type VARCHAR(100) NOT NULL,     -- 소속 | 겸임 | 유통 | 보험 | 협력 | 의뢰 | 납품
  role VARCHAR(100),                           -- '신경과 과장' | '외래 진료' | '총판'
  rel_metadata JSONB DEFAULT '{}',
  is_primary BOOLEAN DEFAULT FALSE,
  status VARCHAR(20) DEFAULT 'active',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_rel_from ON relationships(from_type, from_id);
CREATE INDEX ix_rel_to ON relationships(to_type, to_id);
```

#### 2.3.13 `email_trackings`

```sql
CREATE TABLE email_trackings (
  id SERIAL PRIMARY KEY,
  tracking_id VARCHAR(64) UNIQUE NOT NULL,     -- URL에 박히는 추적 토큰
  contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
  enrollment_id INTEGER,
  sequence_id INTEGER,
  subject VARCHAR(500),
  recipient_email VARCHAR(255),
  sent_at TIMESTAMPTZ DEFAULT NOW(),
  open_count INTEGER DEFAULT 0,
  first_opened_at TIMESTAMPTZ,
  last_opened_at TIMESTAMPTZ,
  click_count INTEGER DEFAULT 0,
  first_clicked_at TIMESTAMPTZ,
  clicked_urls JSONB DEFAULT '[]',             -- [{url, clicked_at}]
  replied BOOLEAN DEFAULT FALSE,
  replied_at TIMESTAMPTZ
);
```

#### 2.3.14 `meeting_bookings`

```sql
CREATE TABLE meeting_bookings (
  id SERIAL PRIMARY KEY,
  token VARCHAR(64) UNIQUE NOT NULL,           -- 외부 공유 URL 토큰
  host_slack_id VARCHAR(50) NOT NULL,
  host_email VARCHAR(255),
  contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
  contact_email VARCHAR(255),
  contact_name VARCHAR(255),
  title VARCHAR(500),
  duration_minutes INTEGER DEFAULT 30,
  slots JSONB NOT NULL,                        -- [{start, end, label}]
  selected_slot JSONB,                         -- {start, end}
  status VARCHAR(20) DEFAULT 'pending',
  calendar_event_id VARCHAR(255),
  message TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  confirmed_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ
);
```

#### 2.3.15 `segments`

```sql
CREATE TABLE segments (
  id SERIAL PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  filters JSONB NOT NULL,                      -- [{"field":"lead_status","op":"eq","value":"qualified"}]
  contact_count INTEGER DEFAULT 0,             -- 캐시된 매칭 수
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### 2.3.16 `prescriptions` (처방)

```sql
CREATE TABLE prescriptions (
  id SERIAL PRIMARY KEY,
  prescription_code VARCHAR(100) UNIQUE,       -- 처방 코드 (CG-12345 등)
  session_number INTEGER DEFAULT 1,            -- 처방 회차
  platform VARCHAR(100),                       -- '제품A 의료진 웹' 등
  hospital_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
  doctor_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
  patient_id VARCHAR(100),                     -- 환자 익명 ID
  prescription_type VARCHAR(10),               -- NP (신규) | NR (재처방)
  prescribed_date TIMESTAMPTZ,
  activated_date TIMESTAMPTZ,                  -- 환자가 활성화한 날
  status VARCHAR(50) DEFAULT 'active',
  custom_properties JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### 2.3.17 `patient_compliance` (환자 순응도)

```sql
CREATE TABLE patient_compliance (
  id SERIAL PRIMARY KEY,
  patient_id VARCHAR(100) NOT NULL,
  hospital_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
  doctor_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
  total_sessions INTEGER DEFAULT 0,
  completed_sessions INTEGER DEFAULT 0,
  compliance_rate FLOAT DEFAULT 0,             -- 0.0 ~ 1.0
  status VARCHAR(50) DEFAULT 'active',
  custom_properties JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### 2.3.18 `sales_transactions` (매출)

```sql
CREATE TABLE sales_transactions (
  id SERIAL PRIMARY KEY,
  year INTEGER NOT NULL,
  month INTEGER NOT NULL,                      -- 1~12
  company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
  product VARCHAR(100),                        -- 'ProductA' | 'ProductC' | 'ProductE'
  channel VARCHAR(255),                        -- 채널(거래처)
  quantity INTEGER DEFAULT 0,
  unit_price FLOAT DEFAULT 0,
  revenue FLOAT DEFAULT 0,                     -- 매출 (VAT 제외)
  revenue_recognized BOOLEAN DEFAULT FALSE,
  payment_received BOOLEAN DEFAULT FALSE,
  payment_date TIMESTAMPTZ,
  ownership VARCHAR(50),                       -- 'Harry' | 'Chloe' 등
  custom_properties JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### 2.3.19 `product_listings` (제품 채택·리스팅 진행)

```sql
CREATE TABLE product_listings (
  id SERIAL PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  product VARCHAR(100) NOT NULL,
  status VARCHAR(50) DEFAULT 'pending',        -- pending | in_progress | done | verbal_confirm
  pipeline_stage VARCHAR(100),                 -- 리스팅 파이프라인 단계
  started_at TIMESTAMPTZ,
  listed_at TIMESTAMPTZ,                       -- 정식 리스팅 완료 시각
  notes TEXT,
  custom_properties JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### 2.3.20 `kol_plans` (Key Opinion Leader 관리)

```sql
CREATE TABLE kol_plans (
  id SERIAL PRIMARY KEY,
  company_id INTEGER REFERENCES companies(id) ON DELETE CASCADE,
  doctor_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
  plan_type VARCHAR(100),                      -- 'ILD' | 'COPD' 등 질환 영역
  target_product VARCHAR(100),
  clinic_schedule JSONB DEFAULT '{}',          -- 외래 스케줄 (요일·시간)
  engagement_status VARCHAR(50) DEFAULT 'planned',
  notes TEXT,
  custom_properties JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### 2.3.21 `hospital_contracts`

```sql
CREATE TABLE hospital_contracts (
  id SERIAL PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  product VARCHAR(100) NOT NULL,
  contract_status VARCHAR(50) DEFAULT 'pending',
  contract_date TIMESTAMPTZ,
  expiry_date TIMESTAMPTZ,
  contract_value FLOAT DEFAULT 0,
  notes TEXT,
  custom_properties JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### 2.3.22 `reference_data` (정적 참조용 KV)

```sql
CREATE TABLE reference_data (
  id SERIAL PRIMARY KEY,
  key VARCHAR(200) UNIQUE NOT NULL,            -- 'sfe_master', 'product_catalog' 등
  data TEXT NOT NULL,                          -- JSON 문자열
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

#### 2.3.23 `working_day_events` ⭐ (일정/근무일 — 가장 풍부한 도메인)

```sql
CREATE TABLE working_day_events (
  id SERIAL PRIMARY KEY,
  event_type VARCHAR(20) NOT NULL,             -- enum WorkingDayEventType
  -- 종일 단위 (월별 집계용)
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  -- 시간지정 (sales_activity 미팅 등)
  start_at TIMESTAMPTZ,
  end_at TIMESTAMPTZ,
  is_all_day BOOLEAN NOT NULL DEFAULT TRUE,
  user_slack_id VARCHAR(50),                   -- NULL = 전사(공휴일 등)
  title VARCHAR(255) NOT NULL,
  note TEXT,
  is_half_day BOOLEAN DEFAULT FALSE,           -- 반차 (0.5일로 계산)
  -- 병원/의사 연계 (sales_activity 시)
  company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
  contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
  -- 자동 생성된 Activity 추적 (메모 보존 위해 NULL set)
  activity_id INTEGER REFERENCES activities(id) ON DELETE SET NULL,
  -- Google Calendar 양방향 동기화
  source VARCHAR(20) DEFAULT 'manual',         -- 'manual' | 'gcal' | 'gcal_holiday'
  gcal_event_id VARCHAR(255),
  gcal_user_email VARCHAR(255),
  last_synced_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ix_wde_user ON working_day_events(user_slack_id);
CREATE INDEX ix_wde_dates ON working_day_events(start_date, end_date);
CREATE INDEX ix_wde_company ON working_day_events(company_id);
CREATE INDEX ix_wde_contact ON working_day_events(contact_id);
CREATE INDEX ix_wde_gcal ON working_day_events(gcal_event_id);
```

**핵심 룰**:
- `event_type IN ('vacation','conference','training')` → 부재 (working_day 차감)
- `event_type IN ('sales_activity','other','public_holiday')` → 차감 X
- `public_holiday` + `user_slack_id IS NULL` = 전사 공휴일
- `sales_activity` + `company_id IS NOT NULL AND contact_id IS NOT NULL` → activities 테이블에 자동 row 생성 (type='call', metadata.schedule_source='working_day')

---

## 3. 핵심 비즈니스 로직 (재구현 필수)

### 3.1 월별 Working Day 산출 (`GET /working-days/summary`)

```python
# 의사 코드
def working_day_summary(year, month, user_slack_id=None):
    total_weekdays = count_weekdays_in_month(year, month)  # 월~금 일수
    events = query_events_overlapping_month(year, month)

    holiday_dates = set()
    leave_days = 0.0
    breakdown = {t: 0.0 for t in WorkingDayEventType}

    LEAVE_TYPES = {'vacation', 'conference', 'training'}  # 부재만

    for event in events:
        days_in_month = [d for d in date_range(event.start_date, event.end_date)
                         if d.month == month and is_weekday(d)]
        unit = 0.5 if event.is_half_day else 1.0
        days_value = len(days_in_month) * unit

        if event.event_type == 'public_holiday' and event.user_slack_id is None:
            for d in days_in_month: holiday_dates.add(d)
            breakdown['public_holiday'] += days_value
        elif (user_slack_id and event.user_slack_id == user_slack_id) \
             or (not user_slack_id and event.user_slack_id is not None):
            breakdown[event.event_type] += days_value
            if event.event_type in LEAVE_TYPES:
                leave_days += days_value

    company_working_days = total_weekdays - len(holiday_dates)
    user_working_days = company_working_days - leave_days if user_slack_id else company_working_days

    return {
        "year": year, "month": month,
        "total_weekdays": total_weekdays,
        "public_holidays": len(holiday_dates),
        "personal_leave_days": round(leave_days, 1),
        "working_days": round(user_working_days, 1),
        "company_working_days": company_working_days,
        "breakdown": {k: round(v, 1) for k, v in breakdown.items()},
    }
```

### 3.2 Sales Activity 자동 생성 ⭐ (Working Day ↔ Activity 양방향 연결)

#### 3.2.1 자동 생성 조건

일정 등록·수정 시 다음 **모두 만족**할 때 Activity가 자동 upsert됨:
- `event_type = 'sales_activity'`
- `company_id` NOT NULL
- `contact_id` NOT NULL

조건 **미충족** 시:
- 기존에 `activity_id` 있었다면 `activity_id = NULL`로 link 끊기
- 기존 Activity row는 **삭제 X** (사용자가 작성한 메모 보존)

#### 3.2.2 upsert 로직 (의사 코드)

```python
async def upsert_activity_for_event(event, db):
    """
    sales_activity + hospital + doctor 매칭 시 Activity 자동 upsert.
    """
    if event.event_type != 'sales_activity':
        event.activity_id = None
        return None
    if not (event.company_id and event.contact_id):
        event.activity_id = None
        return None

    # 자동 제목: "병원명 · 의사 풀네임"
    company = await db.get(Company, event.company_id)
    contact = await db.get(Contact, event.contact_id)
    parts = []
    if company: parts.append(company.name or "")
    if contact:
        full = " ".join(x for x in [contact.last_name, contact.first_name] if x).strip()
        if full: parts.append(full)
    subject = " · ".join(parts) or event.title

    # 시각 = start_at 우선, 없으면 start_date 자정 KST
    ts = event.start_at or datetime.combine(event.start_date, time.min).replace(tzinfo=KST)

    if event.activity_id:
        # ─── UPDATE 분기 ───
        existing = await db.get(Activity, event.activity_id)
        if existing:
            existing.subject = subject
            existing.company_id = event.company_id
            existing.contact_id = event.contact_id
            existing.timestamp = ts
            existing.user_slack_id = event.user_slack_id
            # ⚠️ existing.body 절대 덮어쓰지 X — 사용자가 미팅 후 작성한 메모 보존
            # metadata만 보강
            md = existing.metadata or {}
            if isinstance(md, str):
                md = json.loads(md)
            md["schedule_source"] = "working_day"
            md["working_day_event_id"] = event.id
            existing.metadata = md
            return existing

    # ─── INSERT 분기 ───
    activity = Activity(
        type='call',                           # ⭐ SalesActivityPage가 type='call' 필터링하므로 통일
        subject=subject,
        body='',                               # 빈 메모 — 사용자가 미팅 후 작성
        contact_id=event.contact_id,
        company_id=event.company_id,
        user_slack_id=event.user_slack_id,
        timestamp=ts,
        metadata={
            "schedule_source": "working_day",
            "working_day_event_id": event.id,
        },
    )
    db.add(activity)
    await db.flush()
    event.activity_id = activity.id
    return activity
```

#### 3.2.3 양방향 점프 흐름 (UI)

**Working Day → Activity** (메모 작성용):
1. 일정/근무일 페이지에서 sales_activity 이벤트 클릭 → 수정 폼 열림
2. 폼 헤더에 **"💼 Activity에서 메모 작성 →"** 버튼 노출
   - 조건: `event_type='sales_activity'` AND `company_id` AND `contact_id` AND `activity_id`
3. 클릭 → `sessionStorage.setItem('crm-focus-activity-id', activityId)` → setPage('sales_activity')
4. SalesActivityPage가 mount되며 sessionStorage 읽음 → 해당 row로 scroll + 강조 ring

**Activity → Working Day** (역방향 — 필요 시):
- `activity.metadata.working_day_event_id` 사용
- SalesActivityPage 상세에서 "일정에서 보기" 링크 추가 가능

#### 3.2.4 metadata 정확한 키 목록 (Activity)

```typescript
// SalesActivityPage가 사용하는 metadata 키
{
  // 일정에서 자동 생성된 경우만
  schedule_source: "working_day",
  working_day_event_id: 123,

  // SalesActivityPage UI 입력 필드
  hospital: "OO병원",        // (subject로 대체 가능)
  customer: "김OO 과장",
  department: "신경과",
  call_objective: "리스팅" | "처방" | "처방교육" | "네카동의서명"
                  | "Admin(전화,문자 등)" | "Admin(Desk work, 리스팅서류 등)"
                  | "Detail Call" | "제품설명회" | "기타",
  product: "ProductA" | "ProductC" | "ProductE",

  // 완료 상태 (체크박스)
  done: boolean,
}
```

> **중요**: `metadata` 컬럼은 SQLAlchemy의 `extra_data = Column("metadata", JSON)`로 정의됨 (Python에서 `event.metadata`는 ORM의 reserved keyword라 `extra_data`로 우회). API 응답에서는 `metadata` 또는 `extra_data` 둘 다 받을 수 있도록 frontend 처리.

### 3.3 Google Calendar 양방향 동기화 ⭐ (가장 복잡·중요)

**Service Account + Domain-Wide Delegation** 사용 (사용자별 OAuth 불필요).

설정 JSON:
```json
{
  "WORKING_DAY_GCAL_SYNC_ENABLED": true,
  "WORKING_DAY_GCAL_SYNC_USERS": {
    "Harry": "harry@example.com",
    "Chloe": "chloe@example.com"
  }
}
```

#### 3.3.1 Pull (gcal → CRM) — 매 30분 cron + 수동 `POST /working-days/sync-google`

**핵심 로직** (의사 코드):

```python
async def pull_user_calendar(user_name, user_email, year, month, db):
    """
    한 사용자의 한 달치 gcal 이벤트를 CRM에 upsert.
    삭제된 이벤트(gcal에 없지만 DB에 gcal_event_id 있는 것)도 정리.
    """
    service = get_calendar_service_by_email(user_email)

    # 1. gcal API 파라미터 — RFC3339 (KST tz 포함)
    time_min = datetime(year, month, 1, tzinfo=KST).isoformat()
    next_month_first = date(year, month, last_day_of_month) + timedelta(days=1)
    time_max = datetime(next_month_first.year, next_month_first.month,
                        next_month_first.day, tzinfo=KST).isoformat()

    items = service.events().list(
        calendarId="primary",
        timeMin=time_min, timeMax=time_max,
        singleEvents=True,             # 반복 이벤트를 각 발생으로 펼침
        orderBy="startTime",
        maxResults=250,
    ).execute()["items"]

    # 2. ★ LLM 분류는 DB 트랜잭션 밖에서 batch (SQLite 락 회피)
    classifications = {}
    for ge in items:
        title = ge.get("summary", "")
        description = ge.get("description", "")
        existing = await db_select_existing_by_gcal_id(ge["id"])
        if existing and existing.title == title and existing.note == description:
            # 제목·설명 동일 → 기존 분류 재사용 (LLM skip)
            classifications[ge["id"]] = (existing.event_type, existing.is_half_day)
        else:
            try:
                classifications[ge["id"]] = await call_event_classifier(title, description)
            except Exception:
                classifications[ge["id"]] = ("other", False)

    # 3. 분류 결과로 빠른 upsert (LLM 호출 없음 → 트랜잭션 짧음)
    pulled_ids = set()
    for ge in items:
        start = ge["start"]; end = ge["end"]
        is_all_day = "date" in start
        start_at_val = None; end_at_val = None

        if is_all_day:
            start_d = date.fromisoformat(start["date"])
            end_raw = date.fromisoformat(end["date"])
            end_d = end_raw - timedelta(days=1)   # ⭐ gcal end는 exclusive
        else:
            # ⭐ KST 변환 핵심 — 단순 슬라이싱 [:10]은 하루 밀림
            start_iso = start["dateTime"].replace("Z", "+00:00")
            end_iso = end["dateTime"].replace("Z", "+00:00")
            start_dt = datetime.fromisoformat(start_iso)
            end_dt = datetime.fromisoformat(end_iso)
            if start_dt.tzinfo is None: start_dt = start_dt.replace(tzinfo=KST)
            if end_dt.tzinfo is None: end_dt = end_dt.replace(tzinfo=KST)
            start_kst = start_dt.astimezone(KST)
            end_kst = end_dt.astimezone(KST)
            start_at_val = start_kst
            end_at_val = end_kst
            start_d = start_kst.date()
            end_d = end_kst.date()

        if end_d < start_d:
            continue

        et, is_half = classifications[ge["id"]]
        upsert_working_day_event(
            gcal_event_id=ge["id"],
            event_type=et,
            start_date=start_d, end_date=end_d,
            start_at=start_at_val, end_at=end_at_val,
            is_all_day=is_all_day,
            user_slack_id=user_name,
            title=title or "(제목 없음)",
            note=description,
            is_half_day=is_half,
            source="gcal",
            gcal_user_email=user_email,
            last_synced_at=now_kst(),
        )
        pulled_ids.add(ge["id"])

    # 4. gcal에서 사라진 이벤트 정리
    for stale in db_select_stale(user_email, year, month, exclude_ids=pulled_ids):
        await db_delete(stale)

    await db.commit()
```

#### 3.3.2 Push (CRM → gcal) — create/update/delete 시 자동

```python
def event_to_gcal_body(event):
    type_label = {
        "vacation": "🏖️ 휴가", "conference": "🎓 학회",
        "training": "📚 교육", "sales_activity": "💼 영업 활동",
        "other": "📌 일정",
    }.get(event.event_type, "📌 일정")

    title = event.title
    if event.is_half_day and "반차" not in title:
        title = f"{title} (반차)"

    use_datetime = (
        not event.is_all_day
        and event.start_at is not None
        and event.end_at is not None
    )

    if use_datetime:
        start_dt = event.start_at if event.start_at.tzinfo else event.start_at.replace(tzinfo=KST)
        end_dt = event.end_at if event.end_at.tzinfo else event.end_at.replace(tzinfo=KST)
        start_field = {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Seoul"}
        end_field = {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Seoul"}
    else:
        # ⭐ gcal end는 exclusive이므로 push 시 +1day
        start_field = {"date": event.start_date.isoformat()}
        end_field = {"date": (event.end_date + timedelta(days=1)).isoformat()}

    return {
        "summary": title,
        "description": (event.note or "") + f"\n\n— {type_label} (MOCO 동기화)",
        "start": start_field, "end": end_field,
        "extendedProperties": {
            "private": {
                "moco_source": "working_day",
                "moco_event_type": event.event_type,
                "moco_event_id": str(event.id),
            }
        },
    }
```

**Push 조건**:
- `event.event_type='public_holiday'` → push 안 함 (전사 공휴일은 gcal에 자동으로 있음)
- `event.user_slack_id` 없으면 → push 안 함 (개인 캘린더 대상 X)
- WORKING_DAY_GCAL_SYNC_USERS에 매핑 안 된 user → push 안 함

#### 3.3.3 한국 공휴일 별도 동기화

Google이 제공하는 한국 공휴일 캘린더:
```
KOREAN_HOLIDAY_CALENDAR_ID = "ko.south_korea#holiday@group.v.calendar.google.com"
```

Service Account가 매핑된 첫 사용자로 impersonate해서 이 캘린더 조회 → `source='gcal_holiday'`, `user_slack_id=NULL`로 저장.

**제목 기반 화이트/블랙리스트**로 진짜 쉬는 날만 import:
```python
HOLIDAY_INCLUDE = ["새해","신정","설날","삼일절","어린이날","부처님오신날","현충일",
                   "광복절","추석","개천절","한글날","크리스마스","성탄","노동절",
                   "근로자의 날","선거일","임시공휴일","쉬는 날"]
HOLIDAY_EXCLUDE = ["이브","어버이날","스승의 날","식목일","제헌절","그믐",
                   "국군의 날","상공의 날","발명의 날"]
```

#### 3.3.4 LLM 분류기 (`call_event_classifier`)

별도 agent로 분리 (`app/cc_agents/event_classifier/agent.py` 참조).

```python
async def call_event_classifier(title: str, description: str = "") -> tuple[str, bool]:
    """
    Claude Haiku 1회 호출. 캘린더 이벤트를 6 카테고리로 분류.
    Returns: (event_type, is_half_day). 실패 시 ('other', False).
    """
    system_prompt = """You are a calendar event classifier.

Classify into ONE of:
- vacation: 휴가·연차·반차·휴무·월차·PTO·off
- conference: 학회·학술대회·세미나·심포지엄·포럼
- training: 교육·연수·워크샵·트레이닝
- sales_activity: 병원 방문·KOL 미팅·고객 디너·발표·외근·약국 방문 등
- other: 사내 회의·개인 일정·일반 미팅
(public_holiday는 별도 source='gcal_holiday'에서만 사용)

Detect half-day from "반차"·"half day"·"오전반차"·"오후반차".

Output: single line JSON
{"event_type":"...","is_half_day":true|false}
"""
    # ... (Claude Haiku 호출, JSON 파싱)
```

#### 3.3.5 동시성·DB 락 회피 (실전 fix)

SQLite는 단일 writer. 긴 트랜잭션 중 다른 요청 들어오면 `database is locked` 에러. Postgres에선 발생 X (Supabase는 안전). 하지만 LLM 호출처럼 오래 걸리는 작업은 여전히 **트랜잭션 밖**에서 처리하는 게 좋은 패턴.

**SQLite 환경 fix (참고용)**:
```python
@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragmas(dbapi_conn, _conn_record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")        # 동시 reader 허용
    cur.execute("PRAGMA busy_timeout=30000")      # 30s 락 대기
    cur.execute("PRAGMA synchronous=NORMAL")      # WAL과 잘 어울림
    cur.close()
```

**Postgres는 기본적으로 처리**. Supabase Edge Function이 gcal pull 할 때:
- LLM 분류 → 트랜잭션 밖에서 미리 batch
- DB upsert → 짧은 트랜잭션

### 3.4 이메일 시퀀스 처리

매 시간 worker가 실행:
```python
def process_enrollments():
    due = query(EmailEnrollment).filter(
        status='active', next_send_at <= now()
    )
    for enrollment in due:
        step = sequence.steps[enrollment.current_step]
        if step.wait_for and not waiting_condition_met(enrollment, step.wait_for):
            continue  # 조건 미충족 — 대기 유지

        send_email(template_id=step.template_id, contact=enrollment.contact)
        track = EmailTracking.create(...)
        enrollment.current_step += 1
        if enrollment.current_step >= len(sequence.steps):
            enrollment.status = 'completed'
        else:
            next_step = sequence.steps[enrollment.current_step]
            enrollment.next_send_at = now() + timedelta(days=next_step.delay_days)
```

### 3.5 Automation Trigger

```python
TRIGGER_EVENTS = {
    'deal_stage_change': on_deal_stage_change,
    'contact_created': on_contact_created,
    'lead_score_threshold': on_lead_score_threshold_crossed,
    'form_submission': on_form_submitted,
    'email_opened': on_email_opened,
    'tag_added': on_tag_added,
}

def fire(trigger_type, data):
    for automation in active_automations(trigger_type):
        if matches(automation.trigger_config, data):
            for action in automation.actions:
                execute_action(action, data)
            log_execution(automation.id, data, success=True)
```

### 3.6 매출 집계 (`GET /sales/summary`)

```python
# 연/월/제품/채널/담당자 별 그룹 집계
SELECT
  year, month, product, channel, ownership,
  SUM(quantity) as qty,
  SUM(revenue) as total_revenue,
  SUM(CASE WHEN payment_received THEN revenue ELSE 0 END) as received,
  SUM(CASE WHEN NOT payment_received THEN revenue ELSE 0 END) as outstanding
FROM sales_transactions
WHERE year = ?
GROUP BY year, month, product, channel, ownership
```

### 3.7 Lead Scoring (`services/scoring.py`)

연락처의 행동·이력 기반 점수 (0~100) 자동 계산. `contacts.lead_score` 필드에 저장.

```python
SCORE_WEIGHTS = {
    'email_open': 1,
    'email_click': 3,
    'form_submission': 10,
    'meeting_attended': 15,
    'deal_created': 20,
    'deal_won': 30,
    'days_since_last_activity_penalty': -1,  # 30일 무활동마다 -1
}

def calculate_lead_score(contact: Contact) -> int:
    score = 0
    # 이메일 추적: open/click 횟수
    tracking = sum_email_tracking_for_contact(contact.id)
    score += tracking.open_count * 1 + tracking.click_count * 3

    # form submission
    score += count_form_submissions(contact.id) * 10

    # meeting attended (activity type='meeting')
    score += count_activities(contact.id, type='meeting') * 15

    # deal stage
    deals = get_deals_for_contact(contact.id)
    score += sum(20 if d.stage in ('opportunity', 'proposal') else 0 for d in deals)
    score += sum(30 if d.stage == 'closed_won' else 0 for d in deals)

    # 비활동 페널티
    days_inactive = days_since_last_activity(contact.id)
    score -= days_inactive // 30

    return max(0, min(100, score))
```

스케줄러로 매일 새벽 모든 contact 재계산 + automation trigger (`lead_score_threshold`).

### 3.9 Hospital360 데이터 fetch (`GET /companies/{id}/360`)

단일 endpoint로 병원 통합 정보 반환. Lovable은 이걸 클라이언트 측에서 여러 쿼리로 분할해도 OK.

**응답 구조**:
```typescript
{
  company: CompanyDetail,                  // 병원 기본 정보 + contact_count, deal_count, total_deal_value
  doctors: Contact[],                       // 소속 의사 list (limit 50)
  prescriptions: {
    total: number,                          // 누적 처방 건수
    np_count: number,                       // 신규 처방
    nr_count: number,                       // 재처방
    recent: Prescription[],                 // 최근 10건
    by_doctor: Array<{doctor_id, doctor_name, count}>,  // 의사별 집계
    monthly_trend: Array<{year, month, count}>,         // 월별 trend
  },
  sales: {
    total_revenue: number,
    by_product: Array<{product, revenue, qty}>,
    by_channel: Array<{channel, revenue}>,
    monthly: Array<{year, month, revenue}>,
    outstanding: number,                    // 입금 대기 금액
  },
  product_listings: ProductListing[],       // 진행중인 리스팅
  contracts: HospitalContract[],            // 계약 (만료 임박 우선)
  kol_plans: KOLPlan[],                     // KOL 의사 외래·engagement
  activities: {
    recent: Activity[],                     // 최근 10건
    count_by_type: {call:N, email:N, meeting:N, note:N},
    last_contact_date: ISO,
  },
  relationships: Relationship[],            // 다대다 관계 (총판·계열사 등)
}
```

**구현 의사 코드**:
```python
async def get_company_360(company_id, db):
    company = await db.get(Company, company_id)
    detail = CompanyDetailRead.model_validate(company)

    # 1. counts
    detail.contact_count = await count(Contact, company_id=company_id)
    deal_q = await db.execute(
        select(func.count(Deal.id), func.coalesce(func.sum(Deal.amount), 0))
        .where(Deal.company_id == company_id)
    )
    detail.deal_count, detail.total_deal_value = deal_q.one()

    # 2. doctors (limit 50)
    doctors = await db.execute(
        select(Contact).where(Contact.company_id == company_id).limit(50)
    )

    # 3. prescriptions
    rx_total = await count(Prescription, hospital_id=company_id, status='active')
    rx_np = await count(Prescription, hospital_id=company_id, prescription_type='NP', status='active')
    rx_nr = await count(Prescription, hospital_id=company_id, prescription_type='NR', status='active')
    rx_recent = await fetch_recent(Prescription, hospital_id=company_id, limit=10,
                                    join_doctor_name=True)
    rx_by_doctor = await group_by(Prescription, doctor_id, hospital_id=company_id)
    rx_monthly = await group_by_month(Prescription, hospital_id=company_id)

    # 4. sales
    sales_total = await sum(SalesTransaction.revenue, company_id=company_id)
    sales_by_product = await group_by(SalesTransaction, product, company_id=company_id)
    sales_by_channel = await group_by(SalesTransaction, channel, company_id=company_id)
    sales_monthly = await group_by(SalesTransaction, [year, month], company_id=company_id)
    sales_outstanding = await sum(SalesTransaction.revenue,
                                   company_id=company_id, payment_received=False)

    # 5. listings, contracts, kol_plans, activities, relationships
    listings = await list_all(ProductListing, company_id=company_id)
    contracts = await list_all(HospitalContract, company_id=company_id)
    kol_plans = await list_all(KOLPlan, company_id=company_id)
    activities_recent = await fetch_recent(Activity, company_id=company_id, limit=10)
    activity_count_by_type = await group_by(Activity, type, company_id=company_id)
    last_contact = await fetch_last(Activity, company_id=company_id).timestamp
    relationships = await query_relationships(from_or_to='company', id=company_id)

    return SuccessResponse(data={
        company=detail, doctors=doctors.scalars().all(),
        prescriptions={total, np_count, nr_count, recent, by_doctor, monthly_trend},
        sales={total_revenue, by_product, by_channel, monthly, outstanding},
        product_listings=listings, contracts=contracts, kol_plans=kol_plans,
        activities={recent, count_by_type, last_contact_date},
        relationships=relationships,
    })
```

**Hospital360Page UI 구성** (6 카드 + 6 탭):

```
┌─────────────────────────────────────────────────────────────┐
│ 🏥 OO병원              [🏥 상급종합] [Harry] 서울 강남구    │
├─────────────────────────────────────────────────────────────┤
│  [의사 12]  [처방 234]  [매출 ₩14M]  [리스팅 3]            │  ← KPI 카드 4개
│  [계약 2]   [KOL 1]                                          │
└─────────────────────────────────────────────────────────────┘
┌──────────────────────────┬──────────────────────────────────┐
│ 의사 (12)                 │ 최근 처방 (총 234건)              │
│ - 김OO과장 (신경과)        │ - 김과장 NP 2026-05-10           │
│ - 이OO원장 (내과)          │ - 이원장 NR 2026-05-09           │
│ ...                       │ ...                              │
├──────────────────────────┼──────────────────────────────────┤
│ 매출 trend (월별 차트)    │ 활동 timeline                    │
│ ─────────────              │ - call 2026-05-15 김과장 메모   │
│   |   /\                   │ - meeting 2026-05-12...          │
│   |  /  \                  │ ...                              │
└──────────────────────────┴──────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ 진행중 리스팅·계약·KOL                                       │
└─────────────────────────────────────────────────────────────┘
```

### 3.10 Automation 액션 종류 (`services/automation.py`)

**8 가지 액션 타입** (Lovable에서 그대로 재구현):

```typescript
type AutomationAction =
  | { type: 'create_task', title: string, description?: string,
       due_in_days?: number, priority?: 'low'|'medium'|'high',
       assigned_to_slack_id?: string }

  | { type: 'update_property', target: 'contact'|'deal'|'company',
       target_id?: number,    // 명시 없으면 context.contact_id 등 사용
       field: string,         // e.g. 'lead_status', 'lifecycle_stage'
       value: any }

  | { type: 'change_stage', deal_id?: number, new_stage: string,
       reason?: string }

  | { type: 'send_email', template_id: number,
       recipient_contact_id?: number,
       schedule_in_minutes?: number }

  | { type: 'notify_slack', channel?: string, user_id?: string,
       message: string,       // 변수 치환 {{contact.first_name}} 등 지원
       attachments?: [...] }

  | { type: 'enroll_sequence', sequence_id: number,
       contact_id?: number }

  | { type: 'update_lead_score', delta: number,
       contact_id?: number,
       reason?: string }      // 변경 사유 (감사 로그용)

  | { type: 'add_tag', tag: string,
       target: 'contact'|'company',
       target_id?: number };
```

**실행 흐름** (`_execute_single_action`):

```python
async def execute_action(action_type, config, context, db):
    """
    context = {
        'trigger_data': {...},
        'contact_id': N, 'deal_id': N, 'company_id': N,
        'triggered_at': datetime,
    }
    """
    if action_type == "create_task":
        task = CRMTask(
            title=render(config['title'], context),
            description=render(config.get('description', ''), context),
            due_date=now + timedelta(days=config.get('due_in_days', 7)),
            priority=config.get('priority', 'medium'),
            assigned_to_slack_id=config.get('assigned_to_slack_id'),
            contact_id=context.get('contact_id'),
            deal_id=context.get('deal_id'),
        )
        db.add(task); await db.flush()
        return {'task_id': task.id}

    elif action_type == "update_property":
        target_id = config.get('target_id') or context.get(f"{config['target']}_id")
        obj = await db.get(MODELS[config['target']], target_id)
        setattr(obj, config['field'], config['value'])
        return {'updated': True}

    elif action_type == "change_stage":
        deal = await db.get(Deal, config.get('deal_id') or context['deal_id'])
        old_stage = deal.stage
        deal.stage = config['new_stage']
        # 자동 Activity 추가 (감사용)
        db.add(Activity(type='note', subject=f"Stage 변경: {old_stage} → {deal.stage}",
                        deal_id=deal.id, company_id=deal.company_id,
                        body=config.get('reason', '')))
        return {'from': old_stage, 'to': deal.stage}

    elif action_type == "send_email":
        # template 로드 → variables 치환 → 발송 큐 enqueue (or 즉시)
        # EmailTracking row 생성
        ...

    elif action_type == "notify_slack":
        msg = render(config['message'], context)
        await slack.chat_postMessage(channel=config['channel'], text=msg)
        ...

    elif action_type == "enroll_sequence":
        enrollment = EmailEnrollment(
            sequence_id=config['sequence_id'],
            contact_id=config.get('contact_id') or context['contact_id'],
            next_send_at=now,
        )
        db.add(enrollment)
        ...

    elif action_type == "update_lead_score":
        contact = await db.get(Contact, config.get('contact_id') or context['contact_id'])
        contact.lead_score = max(0, min(100, contact.lead_score + config['delta']))
        # 감사 Activity
        db.add(Activity(type='note',
                        subject=f"Lead score {config['delta']:+d}",
                        body=config.get('reason', ''),
                        contact_id=contact.id,
                        metadata={'lead_score_change': config['delta']}))
        return {'new_score': contact.lead_score}

    elif action_type == "add_tag":
        target_id = config.get('target_id') or context.get(f"{config['target']}_id")
        obj = await db.get(MODELS[config['target']], target_id)
        tags = obj.tags or []
        if config['tag'] not in tags:
            tags.append(config['tag'])
            obj.tags = tags
        ...
```

**Trigger types** (7가지):
```typescript
type TriggerType =
  | 'deal_stage_change'      // trigger_config: {from_stage?, to_stage?}
  | 'contact_created'         // trigger_config: {filters?: [...]}
  | 'lead_score_threshold'    // trigger_config: {threshold: 50, direction: 'above'|'below'}
  | 'form_submission'         // trigger_config: {form_id: N}
  | 'email_opened'            // trigger_config: {sequence_id?: N, template_id?: N}
  | 'tag_added'               // trigger_config: {tag: '...'}
  | 'manual';                 // 사용자가 버튼 클릭 시
```

### 3.11 ProductA 비즈니스 Metric (계산식 상세)

#### 3.11.1 처방 NP/NR 분류

```python
# prescription_type 값:
#   "NP" = New Prescription (신규 처방, 환자 첫 처방)
#   "NR" = New Repeat (재처방, 기존 환자의 다음 회차)

# 유효 처방 필터 (집계 시 공통)
_RX_VALID = and_(
    Prescription.status == "active",
    Prescription.prescription_type.in_(["NP", "NR"]),  # 빈 값·타이핑 오류 제외
)

# 통계
np_count = count(Prescription, _RX_VALID, prescription_type="NP")
nr_count = count(Prescription, _RX_VALID, prescription_type="NR")
total_rx = np_count + nr_count

# NP/NR 비율은 의사·병원의 처방 패턴 indicator
np_ratio = np_count / total_rx if total_rx > 0 else 0
```

#### 3.11.2 매출 환산 (ProductA 1처방 = ₩60,000)

UI 라벨 예시: `subtitle: "처방 ${total_rx}건 × ₩60,000"`

```python
PRODUCTA_UNIT_PRICE = 60000  # 처방 1건당 단가
total_producta_revenue = total_rx * PRODUCTA_UNIT_PRICE
```

단, **실제 매출은 `sales_transactions` 테이블의 `revenue` 합계**가 source of truth. 처방 단가 × 처방수는 추정값.

#### 3.11.3 병원 유형별 분류 (Tertiary vs Clinic)

```python
# hospital_type 값들
HOSPITAL_TYPES_TERTIARY = ["상급종합", "상급종합병원", "종합병원", "병원"]
HOSPITAL_TYPES_CLINIC = ["의원"]

# 필터링
if filter == "tertiary":
    query = query.where(Company.hospital_type.in_(HOSPITAL_TYPES_TERTIARY))
elif filter == "clinic":
    query = query.where(Company.hospital_type == "의원")
```

대시보드에서 "상급종합 처방 N건 / 의원 처방 M건" 같이 표시.

#### 3.11.4 환자 순응도 (Compliance Rate)

```python
compliance_rate = completed_sessions / total_sessions  # 0.0 ~ 1.0

# 처방 활성화 기준
total_sessions = 10                                       # default 처방 회차
completed_sessions = count_completed_in_platform(patient_id)
compliance_rate = completed_sessions / total_sessions if total_sessions > 0 else 0

# 상태
status = "active" if compliance_rate >= 0.3 else "at_risk"  # 30% 미만은 이탈 위험
```

대시보드 `PatientCompliance` 페이지에서 compliance_rate 내림차순 정렬, 30% 미만 빨간색 강조.

#### 3.11.5 처방 → 매출 전환 추정 (`/dashboards/listing-conversion`)

```
리스팅 시작 → (median 60일) → 첫 처방 발생 → 매출 인식

conversion_rate = (리스팅 done인 병원 중 처방 발생한 병원수) / (전체 리스팅 done)
median_days_to_first_rx = median(병원별 first_rx_date - listed_at)
```

#### 3.11.6 의사 잠재력 score (`/dashboards/doctor-potential`)

```python
# 의사별 가중합
def doctor_potential_score(doctor):
    score = 0
    # 본인 처방 활동
    score += doctor.prescription_count * 5             # 본인이 직접 처방한 양
    score += doctor.unique_patients * 3                # 처방 환자 다양성
    # 소속 병원 영향력
    score += doctor.company.hospital_type_weight * 10  # 상급종합 가중치 ↑
    score += doctor.company.region_weight * 2          # 권역 (수도권 가중치 ↑)
    # KOL 여부
    if has_kol_plan(doctor): score += 30
    # 활동 활성도
    score += recent_activity_count(doctor, days=90) * 2
    return min(100, score)
```

대시보드에서 score 내림차순 → "이번주 우선 방문할 의사 top 20" 영업 추천.

#### 3.11.7 위험 알림 (`/dashboards/risk-alerts`)

룰 베이스:
```
- 계약 만료 30일 이내 + 자동 갱신 미설정
- 처방 이탈 (지난달 vs 이번달 처방 -50% 이상)
- 의사 활동 중단 (90일+ no activity)
- 환자 compliance 30% 미만 (10명+)
- listing 진행 60일+ 정체
```

각 알림은 NotificationBell·AdvancedAnalyticsPage에 표시.

### 3.8 Meeting Report 자동 생성 — 음성·텍스트 두 경로

#### 3.8.1 텍스트 기반 (`POST /activities/{id}/meeting-report`)

기존 메모(`body`)를 LLM에게 보내 정제·structured summary 생성:

```python
async def generate_meeting_report(activity: Activity) -> dict:
    """
    raw body → 구조화된 미팅 리포트.
    Returns: {summary, action_items[], decisions[], next_steps[]}
    """
    prompt = f"""다음 미팅 메모를 정제해 4 섹션으로 정리하세요:

[메모]
{activity.body}

[참석자]
- 영업: {activity.user_slack_id}
- 고객: {activity.contact.full_name} ({activity.company.name})

JSON으로:
{{
  "summary": "...",
  "action_items": ["...", "..."],
  "decisions": ["..."],
  "next_steps": ["..."]
}}
"""
    result = await llm_call(prompt)
    activity.metadata = {**activity.metadata, "meeting_report": result}
    return result
```

#### 3.8.2 음성 기반 (`POST /activities/meeting-report-audio`) ⭐

**음성 녹음 파일 → STT → 위 텍스트 처리** 흐름:

```
[모바일·웹 녹음] → multipart upload → STT (Whisper)
                                       ↓
                                  transcript
                                       ↓
                               (위 generate_meeting_report)
                                       ↓
                          새 Activity 생성 (type='meeting', body=정제본)
```

영업담당이 미팅 끝나고 차에서 5분 녹음 → 자동으로 정제된 리포트가 CRM에 들어옴.

Lovable에선 Supabase Storage에 audio upload + Edge Function으로 OpenAI Whisper 또는 비슷한 STT 호출.

---

## 4. API Endpoints (23 라우터)

모든 endpoint는 prefix `/api/crm` 아래.

| Prefix | Tag | 주요 endpoint |
|---|---|---|
| `/companies` | 회사 | `GET /` (list)·`GET /{id}` · `POST /` · `PUT /{id}` · `DELETE /{id}` · `GET /search?q=` |
| `/contacts` | 연락처 | 동일 + `?company_id=` 필터 |
| `/pipelines` | 파이프라인 | 동일 |
| `/deals` | 거래 | 동일 + `?stage=&owner=&pipeline_id=` |
| `/activities` | 활동 | 동일 + `?type=&search=&user_slack_id=` |
| `/emails/templates` | 이메일 템플릿 | 동일 |
| `/emails/sequences` | 이메일 시퀀스 | 동일 + `POST /{id}/enroll` |
| `/automations` | 자동화 | 동일 + `POST /{id}/trigger` |
| `/tasks` | 태스크 | 동일 + `?status=&assigned_to=` |
| `/forms` | 폼 | 동일 |
| `/relationships` | 관계 | 동일 + `?from_type=&from_id=` |
| `/track` | 이메일 추적 | `GET /open/{tracking_id}` (1px pixel) · `GET /click/{tracking_id}?url=` |
| `/booking` | 미팅 예약 | `POST /create` · `GET /{token}` · `POST /{token}/confirm` |
| `/segments` | 세그먼트 | 동일 + `GET /{id}/contacts` |
| `/prescriptions` | 처방 | 동일 + `?hospital_id=&doctor_id=&date_from=&date_to=` |
| `/sales` | 매출 | 동일 + `GET /summary` · `GET /by-product` |
| `/product-listings` | 제품 리스팅 | 동일 |
| `/kol-plans` | KOL 관리 | 동일 |
| `/hospital-contracts` | 병원 계약 | 동일 |
| `/import` | 데이터 임포트 | `POST /csv/{table}` |
| `/working-days` | 일정/근무일 | 동일 + `GET /summary` · `POST /sync-google` |
| `/dashboards` | 대시보드 | `GET /overview` · `GET /sales-pipeline` 등 다양한 집계 |
| `/reports` | 리포트 | export 등 |

### 4.1 표준 응답 포맷

```json
// SuccessResponse
{ "success": true, "data": <T>, "message": null }

// PaginatedResponse (data 필드 안)
{ "items": [...], "total": 123, "page": 1, "page_size": 50 }

// ErrorResponse
{ "success": false, "data": null, "message": "에러 메시지" }
```

### 4.2 핵심 Request/Response Schema 예시

**WorkingDayEventRead** (response):
```typescript
{
  id: number,
  event_type: 'public_holiday'|'vacation'|'conference'|'training'|'sales_activity'|'other',
  start_date: string,         // "YYYY-MM-DD"
  end_date: string,
  start_at?: string,          // ISO datetime with tz, e.g. "2026-05-15T10:00:00+09:00"
  end_at?: string,
  is_all_day: boolean,
  user_slack_id?: string,
  title: string,
  note?: string,
  is_half_day: boolean,
  company_id?: number,
  contact_id?: number,
  activity_id?: number,
  source: 'manual'|'gcal'|'gcal_holiday',
  gcal_event_id?: string,
  gcal_user_email?: string,
  last_synced_at?: string,
  created_at: string,
  updated_at: string,
}
```

**WorkingDayEventCreate** (request POST `/working-days`):
```typescript
{
  event_type: required,
  start_date: required,
  end_date: required,
  start_at?: ISO,
  end_at?: ISO,
  is_all_day?: boolean (default true),
  user_slack_id?: string,
  title: required,
  note?: string,
  is_half_day?: boolean (default false),
  company_id?: number,
  contact_id?: number,
}
// 자동 처리:
// - event_type='public_holiday' → user_slack_id=NULL 강제
// - start_at AND end_at 둘 다 있으면 → is_all_day=false 강제
// - sales_activity + company_id + contact_id → activity 자동 upsert
```

**Summary Response** (`GET /working-days/summary?year=Y&month=M&user_slack_id=?`):
```typescript
{
  year: number, month: number,
  user_slack_id?: string,
  mode: 'user' | 'team',
  total_weekdays: number,            // 평일 수
  public_holidays: number,            // 공휴일 수 (전사)
  personal_leave_days: number,        // 본인 부재 일수 (LEAVE_TYPES만)
  working_days: number,               // 최종 영업일 (소수점 0.5 가능)
  company_working_days: number,       // 회사 기준 (평일-공휴일)
  breakdown: {
    public_holiday: number,
    vacation: number,
    conference: number,
    training: number,
    sales_activity: number,           // 차감 X (참고용)
    other: number,                    // 차감 X (참고용)
  }
}
```

**ActivityRead** (response):
```typescript
{
  id: number,
  type: 'call'|'email'|'meeting'|'note'|'task',
  subject?: string,
  body?: string,                      // 메모 본문
  contact_id?: number,
  deal_id?: number,
  company_id?: number,
  contact_name?: string,              // join된 표시명
  company_name?: string,              // join된 표시명
  user_slack_id?: string,
  associated_email_id?: string,
  metadata: object,                   // 자유 형식 (위 3.2.4 참조)
  timestamp: string,                  // ISO
  created_at: string,
}
```

### 4.3 Dashboards endpoints (25개) — `/dashboards`

비즈니스 도메인 별 대시보드 데이터 fetch. 모두 `GET`, query params로 `year`·`month`·`owner` 등.

| Endpoint | 응답 핵심 |
|---|---|
| `/prescription-dashboard` | 월별 처방수·NP/NR 비율·병원 top10·의사 top10 |
| `/listing-dashboard` | 제품별 listing 상태 분포·이번달 신규 listing·진행 중인 listing pipeline |
| `/sales-dashboard` | 월별 매출 trend·제품별·채널별·담당자별 집계·입금 대기 |
| `/territory-dashboard` | territory_owner 별 (Harry/Chloe) 활동·매출·처방 비교 |
| `/competition-dashboard` | 경쟁사 동향 (custom_properties 기반) |
| `/kol-dashboard` | KOL 의사 engagement·외래 schedule·이번주 미팅 |
| `/contract-dashboard` | 계약 만료 임박·이번달 갱신 필요 |
| `/e-sum` | Executive summary (CEO/임원용) |
| `/okr` | OKR 진행률 (사내 목표 관리) |
| `/biz-plan` | 사업 계획 (분기·년간) |
| `/productc-summary` | ProductC 제품 현황 |
| `/cat-summary` | ProductD 제품 현황 (CAT = ProductD Assessment Tool) |
| `/producta-report` | ProductA 처방·매출 종합 |
| `POST /generate-report` | LLM으로 자연어 보고서 생성 (ProductA report 등) |
| `/reports/download/{filename}` | 생성된 PDF/Excel download |
| `/reference-data` | reference_data 테이블 조회 (SFE 마스터 등) |
| `/b2b-summary` | B2B 채널 분석 (병원 단위 계약) |
| `/wholesale-summary` | 도매·총판 채널 |
| `/prescription-maturity` | 처방 성숙도 분석 (병원·의사별 단계) |
| `/listing-conversion` | listing → 처방 전환율 |
| `/revenue-analysis` | 매출 심층 분석 (성장률·이상치) |
| `/doctor-potential` | 의사 잠재력 score (활동·처방·KOL 종합) |
| `/region-analysis` | 지역별 (시도·구군) 분석 |
| `/risk-alerts` | 위험 알림 (계약 만료·처방 중단·이탈 의사) |
| `/sales-recommendations` | 영업 추천 (이번주 방문 우선순위 의사) |

### 4.4 Reports endpoints (9개) — `/reports`

| Endpoint | 용도 |
|---|---|
| `/dashboard` | 통합 리포트 dashboard |
| `/pipeline` | 영업 파이프라인 리포트 |
| `/activities` | 활동 분석 리포트 |
| `/lead-sources` | 리드 유입 경로 분석 |
| `/revenue-forecast` | 매출 예측 (현재 deals 기반) |
| `/sales-performance` | 영업 실적 평가 |
| `/territory` | territory 분석 |
| `/prescription-trends` | 처방 trend 분석 |
| `/product-adoption` | 제품 채택률 |

### 4.5 Import endpoints (11개) — `/import` (CSV/Excel 업로드)

| Endpoint | Body | 용도 |
|---|---|---|
| `POST /reload-db` | none | DB 스키마 재로드 (admin only) |
| `POST /hospitals` | multipart | 병원 마스터 import (medical institution list) |
| `POST /hospitals-territory` | multipart | territory_owner 일괄 지정 |
| `POST /hcps` | multipart | 의사·HCP 마스터 import |
| `POST /doctor-info` | multipart | 의사 추가 정보 (진료과·전공 등) |
| `POST /prescriptions` | multipart | 처방 일괄 import |
| `POST /compliance` | multipart | 환자 순응도 import |
| `POST /sales` | multipart | 매출 일괄 import (월별 SFE master) |
| `POST /product-listings` | multipart | 제품 리스팅 import |
| `POST /kol-plans` | multipart | KOL 계획 import |
| `POST /hospital-contracts` | multipart | 병원 계약 import |

각 endpoint는 CSV 헤더 매핑 → 행별 `Create` schema 변환 → bulk insert. 중복 row는 `unique key`(hospital_code·hcp_code·prescription_code 등) 기준 upsert.

### 4.6 Activities 특수 endpoints

| Endpoint | 용도 |
|---|---|
| `GET /activities/recent?limit=N` | 최근 활동 N개 (홈 dashboard·NotificationBell용) |
| `GET /activities/sfe-summary/{owner}` | SFE dashboard용 — 영업담당자별 활동 집계 |
| `GET /activities/territory/{owner}` | territory별 활동 분석 |
| `GET /activities/summary/{owner}` | 활동 요약 (월별·type별) |
| `POST /activities/{id}/meeting-report` | 미팅 후 리포트 자동 생성 (LLM으로 body 정제·summary 추가) |
| `POST /activities/meeting-report-audio` | ⭐ **음성 녹음 → STT → 리포트** (Whisper 또는 비슷한 STT 사용) |

### 4.8 Pydantic Schemas 전수 (112 class) — 카테고리별 매핑

모든 entity는 **`Base`·`Create`·`Update`·`Read`·`Detail`** 5가지 표준 패턴.
- `Base` = 공통 필드 (create와 read가 공유)
- `Create` = POST request body (Base 그대로 또는 일부 required로 강화)
- `Update` = PUT request body (모든 필드 Optional)
- `Read` = GET response body (id·created_at·updated_at 포함)
- `Detail` = Read 확장 (관련 entity counts·관계 데이터 포함)

#### 4.8.1 표준 응답 wrapper (3)
```
PaginatedResponse[T] · SuccessResponse · ErrorResponse
```

#### 4.8.2 Entity별 schema 매핑 (entity × pattern)

| Entity | Base | Create | Update | Read | Detail |
|---|---|---|---|---|---|
| Company | ✅ | ✅ | ✅ | ✅ | ✅ CompanyDetailRead (+ contact_count, deal_count, total_deal_value) |
| Contact | ✅ | ✅ | ✅ | ✅ | ✅ ContactDetail (+ enrollments[], deals[], activities[]) |
| Pipeline | ✅ + PipelineStage | ✅ | ✅ | ✅ | — |
| Deal | ✅ | ✅ | ✅ | ✅ | ✅ DealDetailRead. 별도: DealStageUpdate (PATCH stage만) |
| Activity | ✅ | ✅ | ✅ | ✅ | ✅ ActivityDetailRead (+ contact·company·deal 정보 join) |
| EmailTemplate | ✅ | ✅ | ✅ | ✅ | — |
| EmailSequence | ✅ + SequenceStep, StepAction, StepCondition | ✅ | ✅ | ✅ | — |
| EmailEnrollment | — | EnrollRequest, BulkEnrollRequest | — | ✅ EmailEnrollmentRead, EnrollmentWithContactRead, SequenceStats, SequenceDashboardItem | — |
| Automation | ✅ + AutomationAction | ✅ | ✅ | ✅ | AutomationExecutionRead |
| CRMTask | ✅ | ✅ | ✅ | ✅ | ✅ CRMTaskDetailRead |
| Form | ✅ + FormField | ✅ | ✅ | ✅ | FormSubmissionCreate, FormSubmissionRead |
| EmailTracking | — | — | — | ✅ EmailTrackingRead | EmailTrackingSummary |
| Relationship | — | RelationshipCreate | RelationshipUpdate | RelationshipRead | — |
| MeetingBooking | — | MeetingBookingCreate + MeetingSlot | — | MeetingBookingRead | — |
| Segment | ✅ + SegmentFilter | ✅ | ✅ | ✅ | — |
| Prescription | ✅ | ✅ | ✅ | ✅ | PrescriptionStats |
| PatientCompliance | — | — | — | ComplianceRead | — |
| SalesTransaction | ✅ | ✅ | ✅ | ✅ | SalesSummary |
| ProductListing | ✅ | ✅ | ✅ | ✅ | — |
| KOLPlan | ✅ | ✅ | ✅ | ✅ | — |
| HospitalContract | ✅ | ✅ | ✅ | ✅ | — |
| WorkingDayEvent | ✅ | ✅ | ✅ | ✅ | — |

#### 4.8.3 별도 reporting schema (구조화된 dashboard 응답)

```
DashboardStats · PipelineReport · ActivityReport ·
LeadSourceReport · RevenueForecast · SalesPerformance ·
PrescriptionStats · SalesSummary · ImportResult
```

#### 4.8.4 표준 패턴 코드 템플릿

```python
# 예시: Prescription
class PrescriptionBase(BaseModel):
    prescription_code: Optional[str] = None
    session_number: Optional[int] = 1
    platform: Optional[str] = None
    hospital_id: Optional[int] = None
    doctor_id: Optional[int] = None
    patient_id: Optional[str] = None
    prescription_type: Optional[str] = None    # "NP" | "NR"
    prescribed_date: Optional[datetime] = None
    activated_date: Optional[datetime] = None
    status: Optional[str] = "active"
    custom_properties: dict = Field(default_factory=dict)

class PrescriptionCreate(PrescriptionBase):
    hospital_id: int                            # required로 강화
    doctor_id: int

class PrescriptionUpdate(BaseModel):
    # 모든 필드 Optional (PATCH semantics)
    prescription_code: Optional[str] = None
    session_number: Optional[int] = None
    platform: Optional[str] = None
    prescription_type: Optional[str] = None
    status: Optional[str] = None
    custom_properties: Optional[dict] = None

class PrescriptionRead(PrescriptionBase):
    id: int
    created_at: datetime
    updated_at: datetime
    # 관계 데이터 (lazy-join 또는 별도 query)
    hospital_name: Optional[str] = None         # join companies.name
    doctor_name: Optional[str] = None           # join contacts (first+last_name)
    model_config = ConfigDict(from_attributes=True)
```

이 패턴을 25개 entity 모두에 적용 → 총 112 class.

### 4.7 Tracking endpoints — `/track`

| Endpoint | 용도 |
|---|---|
| `GET /open/{tracking_id}` | 1px 투명 픽셀 응답 + open_count++ |
| `GET /click/{tracking_id}?url=X` | redirect to X + click_count++·clicked_urls 기록 |
| `GET /contact/{contact_id}` | 특정 연락처의 모든 추적 결과 |
| `GET /sequence/{sequence_id}` | 시퀀스 단위 추적 통계 |

---

## 5. UI 페이지 (12 메인 페이지)

각 페이지는 React 컴포넌트. **dark mode 지원** (`<body data-theme="dark">`).

### 5.1 Dashboard (홈)
- KPI 카드 4~8개 (이번달 매출·신규 거래·미해결 task·최근 활동 등)
- 최근 활동 타임라인
- 영업 파이프라인 funnel
- 매출 트렌드 그래프 (월별)

### 5.2 Companies (병원)
- 테이블 + 검색·필터 (region·hospital_type·territory_owner·is_target)
- 상세 페이지: 기본정보 + 의사 목록·거래·처방·매출·관계·활동 타임라인
- 등록 폼: 모든 필드. region drill-down (시도→구군→권역)

### 5.3 Contacts (의사)
- 테이블 + 검색 (이름·이메일·진료과)
- 상세: 기본·HCP 정보·소속 병원·관련 거래·활동·이메일 추적
- lead_score·lead_status·lifecycle_stage 표시

### 5.4 Deals (거래)
- **칸반 보드** (default) — pipeline.stages로 컬럼
- 리스트 뷰 토글
- 드래그&드롭으로 stage 변경
- 카드: 거래명·금액·company·contact·close_date

### 5.5 Activities / Sales Activity ⭐

`GET /activities?type=call` 으로 필터링해서 표시.

**뷰 모드** (toggle):
- **주간 뷰** (default) — 월~일 7컬럼 grid
  - 요일별 카드 list
  - 드래그&드롭으로 요일 변경 (date 갱신)
  - 오늘 컬럼은 파란 테두리 강조
- **리스트 뷰** — 테이블
  - 컬럼: ☐(done체크) · 일자 · 병원 · 과 · 고객 · Objective · 제품 · 메모

**기능**:
- 검색 (병원명·고객명·메모)
- 탭: 전체 / Harry / Chloe (`user_slack_id` 필터)
- 매 row에 **done 체크박스** — 체크 시 `activity.metadata.done=true` + 의료진·병원 타임라인에 별도 note Activity 자동 추가
- "💡 계획 추가" 버튼 → 신규 폼

**신규 폼 필드**:
```
- hospital (autocomplete /companies)
- doctor (autocomplete /contacts, 병원 선택 시 company_id 자동 필터)
- date (default: 오늘 YMD)
- call_objective (select: 9 options 위 metadata 섹션 참조)
- product (select: ProductA·ProductC·ProductE)
- 메모 (textarea — body 필드)
- assigned to (user_slack_id select)
```

**Working Day와 연결**:
- `metadata.schedule_source='working_day'` 인 Activity는 카드에 ⏰ 아이콘 + "일정에서 자동 생성" 라벨 표시
- 클릭 시 그 일정의 sales_activity 이벤트로 점프 가능 (`metadata.working_day_event_id` 활용)
- **focus 기능**: WorkingDayPage에서 점프해온 경우 `sessionStorage['crm-focus-activity-id']` 읽어서:
  - 리스트 뷰로 자동 전환
  - 해당 row id로 scroll + emerald-400 ring 3초 강조
  - 3초 후 ring 제거 + sessionStorage clear

### 5.6 Working Days (일정/근무일) ⭐⭐ (가장 풍부)

#### 5.6.1 페이지 헤더

```
🗓️ 일정 / 근무일                [🔄 Google 동기화]  [+ 일정 추가]

[전체] [Harry] [Chloe]                 ← user_slack_id 탭

[ 평일 22일 ] [ 공휴일 1일 ] [ 본인부재 3일 ] [ 영업일 18일 ]   ← KPI 4 카드

[ 🏛️ 공휴일 1 ] [ 🏖️ 휴가 2 ] [ 🎓 학회 1 ] [ 💼 영업 활동 8 ]  ← breakdown chip

[ ◀ 2026년 5월 ▶ ]  이번달    [ 월 | 주 | 일 | 목록 ] ← 네비·뷰 토글
```

#### 5.6.2 4 뷰 모드

**🗓️ 월 뷰** (`viewMode='month'`, default):
- 7컬럼 × 6행 grid (이전·다음 월 패딩 포함)
- 각 셀: 날짜 + 이벤트 카드 (최대 3개 + "+N more")
- 카드: `{emoji} {time?} {user?} {title}` (timePrefix는 is_all_day=false일 때만)
- 빈 셀 클릭 → 그 날짜로 신규 등록 폼
- 카드 클릭 → 수정 폼
- 오늘 셀은 날짜에 동그란 파란 배경

**📅 주 뷰** (`viewMode='week'`):
```
       월   화   수   목   금   토   일
종일  [반차] [학회][공휴일]            ← 종일 row (is_all_day=true 이벤트)
      ─────────────────────────────
 8h
 9h        ┌───────┐
10h        │OO병원 │ ← 시간지정 sales_activity 블록 (드래그 가능)
11h        │ 김과장 │
12h        └───────┘
 ...
21h
```
- 시간 그리드: **8시~21시** (HOURS = `[8,9,...,21]`, 14행)
- HOUR_HEIGHT = **40px** (1시간)
- 종일 row 별도 분리 (위)
- 시간지정 블록 (아래): `top = (start_hour - 8) * 40 + minute/60 * 40`, `height = duration_min/60 * 40`
- 빈 슬롯 클릭 → 그 시각으로 sales_activity 등록 폼 (시작 시간 자동 채움)
- 블록 클릭 → 수정 폼
- 블록 드래그 (mousedown→mousemove→mouseup):
  - **15분 스냅** — `deltaMin = Math.round(deltaPx / 40 * 60 / 15) * 15`
  - mouseup 시 PUT `/working-days/{id}` (start_at·end_at·start_date·end_date 갱신)
  - is_all_day=false 자동 유지

**📅 일 뷰** (`viewMode='day'`):
- 위와 동일하지만 1컬럼

**📋 목록 뷰** (`viewMode='list'`):
- 테이블: 유형 · 기간 · 대상 · 제목 · 메모 · 🗑️

#### 5.6.3 등록 폼 필드

```typescript
{
  event_type: select,         // 6 옵션
  start_date: date,
  end_date: date,
  user_slack_id: select,      // 전체/Harry/Chloe (event_type='public_holiday'면 disabled)
  title: text,
  note: text,

  // 시간지정 토글
  is_all_day: checkbox (default: !sales_activity ? true : false),
  start_at: datetime-local,   // is_all_day=false 일 때만 노출
  end_at: datetime-local,

  // ⭐ sales_activity 일 때만 노출 (조건부)
  hospital_name + company_id:  AutocompleteInput,  // /companies
  doctor_name + contact_id:    AutocompleteInput,  // /contacts

  // 반차
  is_half_day: checkbox,      // 0.5일 계산
}
```

폼 헤더 (수정 모드):
```
일정 수정   [💼 Activity에서 메모 작성 →]  [삭제]  [×]
               └─ 조건: sales_activity AND company_id AND contact_id AND activity_id
```

#### 5.6.4 "Google 동기화" 버튼

`POST /working-days/sync-google?user={tab}&year=Y&month=M` 호출
- 응답에 `{results: [{kind, user|year, created, updated, deleted, skipped, error?}]}`
- 토스트로 결과 요약 (예: "사용자 일정: 추가 3 · 갱신 2 | 공휴일: 추가 0 · 갱신 1")

### 5.7 Prescriptions (처방)
- 테이블 + 필터 (hospital·doctor·prescription_type·기간)
- 빠른 입력 (Quick Input): 병원·의사·NP/NR·count·platform
- 월별·일별 통계
- 환자 순응도(`patient_compliance`) 연동

### 5.8 Sales (매출)
- 연/월 필터
- 제품별·채널별·담당자별 집계
- 입금 여부 필터
- 엑셀 export

### 5.9 Product Listings
- 병원별 제품 리스팅 진행 현황
- pipeline_stage drag&drop

### 5.10 KOL Plans
- KOL 의사·외래 스케줄 카드 grid

### 5.11 Hospital Contracts
- 계약 만료 임박 알림 (expiry_date)

### 5.12 Settings / Tasks / Forms / Segments / Email Templates 등
- 보조 기능. 우선순위 낮음.

### 5.16 Sidebar 메뉴 + Routing (전체 36 페이지) ⭐

#### 5.16.1 Sidebar 구조

좌측 사이드바는 **5 섹션 × 그룹·서브** 구조 (collapsible):

```typescript
const NAV_ITEMS = [
  // ───── 영업채널 ─────
  { id:'medical', label:'의료기관', icon:'🏥', color:'#06B6D4', section:'영업채널',
    children: [
      { id:'companies', label:'병의원' },
      { id:'health_check_center', label:'건진센터' },
      { id:'contacts', label:'의료진' },
    ]
  },
  { id:'b2b', label:'보험/금융/B2B', icon:'💼', color:'#10B981', section:'영업채널',
    children: [
      { id:'b2b_memory', label:'제품D메모리' },
      { id:'b2b_care', label:'기억챙김' },
    ]
  },
  { id:'wholesale', label:'도매/유통', icon:'📦', color:'#F97316', section:'영업채널',
    children: [{ id:'wholesale_pharma', label:'전문의약품 유통' }]
  },

  // ───── 프로덕트 ─────
  { id:'producta', label:'제품A', icon:'💊', color:'#8B5CF6', section:'프로덕트',
    children: [
      { id:'prescriptions', label:'처방' },
      { id:'product_listings', label:'리스팅' },
    ]
  },
  { id:'productc', label:'제품C', icon:'🖥️', color:'#EC4899', section:'프로덕트',
    children: [{ id:'productc_listings', label:'리스팅' }]
  },
  { id:'productd', label:'제품D', icon:'🧪', color:'#F59E0B', section:'프로덕트',
    children: [
      { id:'productd_memory', label:'제품D메모리' },
      { id:'productd_reagent', label:'제품B 시약' },
    ]
  },

  // ───── 영업활동 ─────
  { id:'sales_activity', label:'Sales Activity', icon:'🤝', color:'#10B981', section:'영업활동' },
  { id:'kol',            label:'KOL 관리',       icon:'🎓', color:'#F97316', section:'영업활동' },
  { id:'sales_mgmt',     label:'매출관리',       icon:'💰', color:'#22C55E', section:'영업활동' },
  { id:'working_day',    label:'일정/근무일',    icon:'📅', color:'#6366F1', section:'영업활동' },

  // ───── 분석 ─────
  { id:'hospital360',         label:'Hospital 360', icon:'🔍', color:'#0EA5E9', section:'분석' },
  { id:'sfe_dashboard',       label:'SFE 현황판',   icon:'📈', color:'#F97316', section:'분석' },
  { id:'advanced_analytics',  label:'상세 분석',     icon:'🧠', color:'#6366F1', section:'분석' },

  // ───── 설정 ─────
  { id:'import_data', label:'데이터 임포트', icon:'📥', color:'#6366F1', section:'설정' },
];
```

#### 5.16.2 PAGE_MAP (URL/page key → React 컴포넌트)

전체 **36 페이지** mapping:

```typescript
const PAGE_MAP = {
  // 기본
  dashboard:           DashboardPage,
  contacts:            ContactsPage,
  companies:           CompaniesPage,
  deals:               DealsPage,

  // 제품A
  prescriptions:       PrescriptionsPage,
  sales_mgmt:          SalesPage,
  product_listings:    ProductListingsPage,
  producta:            ProductADashboardPage,

  // 제품C·체크
  productc:           ProductCDashboardPage,
  productc_listings:  ProductCListingsPage,
  productd:            ProductDDashboardPage,
  productd_memory:     MockPage,        // 미구현
  productd_reagent:      MockPage,        // 미구현

  // B2B·도매
  b2b:                 B2BDashboardPage,
  b2b_memory:          MockPage,
  b2b_care:            MockPage,
  wholesale:           WholesaleDashboardPage,
  wholesale_pharma:    MockPage,

  // 영업활동
  kol:                 KOLPage,
  sales_activity:      SalesActivityPage,
  working_day:         WorkingDayPage,
  quick_input:         QuickInputPage,

  // 분석
  hospital360:         Hospital360Page,
  sfe_dashboard:       SFEDashboardPage,
  advanced_analytics:  AdvancedAnalyticsPage,
  medical:             MedicalDashboardPage,
  health_check_center: HealthCheckPage,

  // 보조
  segments:            SegmentsPage,
  tasks:               TasksPage,
  activities:          ActivityFeedPage,
  templates:           TemplatesPage,
  sequences:           SequencesPage,
  automations:         AutomationsPage,
  forms:               FormsPage,
  reports:             ReportsPage,

  // 설정
  import_data:         DataImportPage,
};
```

#### 5.16.3 라우팅 구현

현재 MOCO는 **single-page** (no URL routing). `page` state가 어떤 컴포넌트 렌더링할지 결정.
Lovable에선 React Router 사용 → URL = `/crm/{page-key}` (예: `/crm/working_day`, `/crm/hospital360`).

```typescript
// React Router 매핑
<Routes>
  <Route path="/crm" element={<Layout/>}>
    <Route index element={<DashboardPage/>} />
    {Object.entries(PAGE_MAP).map(([key, Component]) => (
      <Route path={key.replace(/_/g, '-')} element={<Component/>} key={key} />
    ))}
  </Route>
</Routes>
```

#### 5.16.4 QuickCreateMenu (우상단 + 버튼)

어디서나 빠른 생성:
```typescript
const items = [
  { icon:'👤', label:'연락처',  page:'contacts' },
  { icon:'💼', label:'딜',     page:'deals' },
  { icon:'✅', label:'태스크',  page:'tasks' },
  { icon:'📧', label:'시퀀스',  page:'sequences' },
  { icon:'✉️', label:'템플릿',  page:'templates' },
];
```
각 항목 클릭 → 해당 페이지로 이동 + 신규 폼 자동 open.

#### 5.16.5 GlobalSearch (⌘K / Ctrl+K)

검색바에 typing → `companies·contacts·deals·prescriptions` 4개 entity 동시 검색 (각 `?search=q&limit=5`). 결과 클릭 → 해당 페이지로 이동 + filter 적용.

### 5.15 추가 페이지 — 비즈니스 도메인 별 전문 대시보드 ⭐

MOCO는 단순 CRM이 아니라 **비즈니스 도메인 별 전문 대시보드**가 많음. Lovable 측에서는 Phase 2~3에서 점진 구현 권장. 각 페이지는 dashboards.py endpoint에서 데이터 가져옴.

| UI 페이지 | API endpoint | 용도 |
|---|---|---|
| **Hospital360** (`Hospital360Landing` + `Hospital360Page`) | 여러 endpoint 조합 | 단일 병원의 360도 뷰 — 처방·매출·계약·KOL·활동·관계 한 화면 |
| **SFE Dashboard** (`SFEDashboardPage`) | `/activities/sfe-summary/{owner}`·`/dashboards/okr` | Sales Force Effectiveness 현황판 — 영업담당자 활동 평가 |
| **AdvancedAnalytics** (`AdvancedAnalyticsPage`) | `/dashboards/risk-alerts`·`/sales-recommendations` | 위험 알림·영업 추천 (간단한 ML 인사이트) |
| **MedicalDashboard** (`MedicalDashboardPage`) | `/dashboards/producta-report` | 의료진 대상 처방 현황 |
| **HealthCheck** (`HealthCheckPage`) | `/dashboards/productc-summary` | ProductC 검사 현황 |
| **ProductADashboard** | `/dashboards/producta-report`·`/prescription-dashboard` | ProductA 제품 전용 대시보드 |
| **ProductCDashboard** + **ProductCListings** | `/dashboards/productc-summary`·`/dashboards/listing-dashboard` | ProductC 제품 |
| **ProductDDashboard** | `/dashboards/cat-summary` | ProductD 제품 |
| **B2BDashboard** (`B2BDashboardPage`) | `/dashboards/b2b-summary` | B2B 채널 (병원 일괄 계약) |
| **WholesaleDashboard** (`WholesaleDashboardPage`) | `/dashboards/wholesale-summary` | 도매·총판 채널 |
| **KOLPage** | `/kol-plans` + `/dashboards/kol-dashboard` | KOL 의사 관리 (외래 schedule·engagement) |
| **QuickInputPage** | `/prescriptions` + `/product-listings` + `/contacts` | ⭐ 빠른 입력 — 처방·리스팅·연락처를 1~2초 안에 |
| **DataImportPage** | `/import/*` | CSV/Excel 일괄 import (11종) |
| **NotificationBell** | `/tasks/my`·`/dashboards/risk-alerts` | 상단 알림 종 |
| **QuickCreateMenu** | 다양 | 우상단 "+" 버튼 — 어디서나 빠른 생성 |

#### 5.15.1 Hospital360 (가장 중요한 분석 페이지)

**Hospital360Landing** — 병원 검색·선택 → Hospital360Page로 진입

**Hospital360Page** — 좌측 sidebar: 기본정보 / 우측: 6 탭
- **개요**: 매출 trend·처방 trend·KOL count·계약 상태 카드
- **의사**: 소속 의사 list + lead_score sort
- **처방**: prescription history + compliance rate
- **매출**: sales_transactions 월별
- **계약**: hospital_contracts 진행 상태
- **활동**: activities timeline + 메모

이 페이지가 일선 영업담당자가 미팅 전 5분 read 용도. **검토 후 Lovable 측에 가장 가치 있는 페이지**.

#### 5.15.2 SFE Dashboard

영업담당(Harry/Chloe)별 KPI 카드:
- 이번달 활동 수 (call·email·meeting별)
- 처방 신규 수 / 재처방 수
- 매출 달성률 (목표 대비)
- 처리 안 된 task 수
- 미팅 후 후속 활동률

`/activities/sfe-summary/{owner}` + `/dashboards/okr`로 데이터 fetch.

#### 5.15.3 QuickInput Page (빠른 입력 전용 페이지)

```
[처방] [리스팅] [연락처]   ← 모드 토글

병원: [Autocomplete....]
의사: [Autocomplete....]   ← 병원 선택 시 의사 list 필터
타입: [NP / NR]
회차: [1]
플랫폼: [제품A 의료진 웹]
[저장]

최근 입력 (Recent):
- 2026-05-15  OO병원 김OO과장 NP 1회차
- 2026-05-14  XX의원 이OO원장 NR 3회차
- ...
```

영업담당자가 일과 끝나고 한꺼번에 처방 입력하는 page. 1건 입력에 5초 이내.

### 5.13 공통 컴포넌트
- `AutocompleteInput` — 검색 API endpoint·`?search=` 자동 append → suggestion 드롭다운
- `EmptyState` — 빈 상태 placeholder
- `Tabs / Cards / Modal / Toast` — shadcn/ui 기반

### 5.14 ⚠️ 날짜 처리 함정 (실제 운영 fix)

JavaScript `Date`·`toISOString()` 사용 시 KST에서 **하루 밀림 버그** 자주 발생. 반드시 다음 helper 사용:

```typescript
// ✅ Local timezone 기준 YYYY-MM-DD
const YMD = (d?: Date | string): string => {
  if (!d) return '';
  const dt = d instanceof Date ? d : new Date(d);
  return `${dt.getFullYear()}-${String(dt.getMonth()+1).padStart(2,'0')}-${String(dt.getDate()).padStart(2,'0')}`;
};

// ✅ "YYYY-MM-DD" → local time Date (new Date("YYYY-MM-DD")는 UTC 자정이라 위험!)
const PARSE_YMD = (s?: string): Date | null => {
  if (!s) return null;
  const [y, m, d] = String(s).slice(0,10).split('-').map(Number);
  return new Date(y, m - 1, d);
};

// ❌ 절대 쓰지 말 것:
// new Date("2026-05-15").toISOString().slice(0,10) → KST 11:30분이면 "2026-05-14" 나옴
// new Date(year, month-1, day).toISOString().slice(0,10) → UTC 변환되어 하루 밀림
```

캘린더 cell key·이벤트 매핑 key를 일관되게 `YMD()`로 통일해야 매칭됨. 실제 버그: cell key는 `toISOString` 사용 → 하루 빠른 키, event key는 `e.start_date` 그대로 → 매칭 실패 → 이벤트가 하루 빠른 셀에 표시됨.

---

## 6. 통합 기능

### 6.1 Google Calendar (gcal)

**셋업**: Google Workspace Admin → Service Account 생성 → Domain-Wide Delegation 권한 부여 → JSON key 다운로드.

```python
# 의사 코드
service = build(
    'calendar', 'v3',
    credentials=ServiceAccountCredentials.from_json(key, scope=['calendar'])
                .with_subject(target_user_email)  # 위임
)
service.events().list(calendarId='primary', timeMin=..., timeMax=...).execute()
```

Supabase에선 **Edge Function**(Deno)로 같은 로직 구현 가능. credentials를 Supabase Vault에 저장.

### 6.2 이메일 발송

옵션:
- Resend (Lovable에서 가장 자연스러움)
- Mailgun
- AWS SES
- SMTP 직접

추적은 `tracking_id`를 URL에 박아 픽셀(`/track/open/{id}`)·리다이렉트(`/track/click/{id}?url=`)로 수집.

### 6.3 SMS·알림톡

- NCP SENS (현재 사용 중) — Project: producta
- Solapi 대안
- Lovable에선 Edge Function으로 HMAC-SHA256 서명 후 호출

### 6.4 CSV 임포트

`POST /import/csv/{table}` — multipart upload → 컬럼 매핑 UI → batch insert.
**기존 데이터 import 스크립트**: `import_medical_institutions.py`, `import_sales_to_crm.py` 등 참고 (코드는 `_archive/1_oneshot_scripts/`에 있음).

---

## 7. 마이그레이션 매핑 가이드 (SQLite → Supabase Postgres)

### 7.1 타입 매핑

| SQLite (현재) | Postgres (Lovable) | 비고 |
|---|---|---|
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `SERIAL PRIMARY KEY` 또는 `BIGSERIAL` | UUID로 가도 OK |
| `VARCHAR(N)` | `VARCHAR(N)` | 동일 |
| `TEXT` | `TEXT` | 동일 |
| `FLOAT` | `DOUBLE PRECISION` 또는 `NUMERIC` | 금액은 NUMERIC(18,2) 권장 |
| `BOOLEAN` | `BOOLEAN` | 동일 |
| `DATETIME(timezone=True)` | `TIMESTAMPTZ` | 동일 |
| `DATE` | `DATE` | 동일 |
| `JSON` | `JSONB` | jsonb가 인덱싱·검색 빠름 |
| `Enum` | `VARCHAR + CHECK` 또는 native `CREATE TYPE` | check 권장 (마이그레이션 쉬움) |

### 7.2 RLS (Row-Level Security) 권장

Supabase는 Postgres RLS 활성화 가능. territory_owner 기반:

```sql
ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
CREATE POLICY territory_isolation ON companies
  USING (
    territory_owner = current_setting('app.current_user')::text
    OR current_setting('app.is_admin', true) = 'true'
  );
```

### 7.3 Auth 매핑

현재: `owner_slack_id` 같은 Slack ID를 사용자 식별자로 사용.
Lovable: Supabase Auth의 `auth.uid()` 사용. 매핑 테이블 만들기:

```sql
CREATE TABLE user_profiles (
  user_id UUID PRIMARY KEY REFERENCES auth.users(id),
  display_name VARCHAR(100),                   -- 'Harry' | 'Chloe'
  slack_id VARCHAR(50),                        -- 호환용
  email VARCHAR(255),
  role VARCHAR(20) DEFAULT 'sales',           -- sales | admin
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

기존 `owner_slack_id`·`assigned_to_slack_id`·`user_slack_id`·`host_slack_id` 등은 `user_profiles.slack_id`로 lookup해서 `user_id` UUID로 매핑하거나, 그대로 `display_name`(Harry/Chloe)으로 저장.

### 7.4 한국어 표시 라벨 매핑

UI 라벨용 enum→한국어 매핑 (frontend에서):

```typescript
const LABEL = {
  LeadStatus: {new:'신규', contacted:'연락중', qualified:'적격', unqualified:'부적격'},
  LifecycleStage: {subscriber:'구독자', lead:'리드', mql:'MQL', sql:'SQL',
                    opportunity:'기회', customer:'고객', evangelist:'에반젤리스트'},
  ActivityType: {call:'콜', email:'이메일', meeting:'미팅', note:'메모', task:'태스크'},
  TaskStatus: {todo:'할 일', in_progress:'진행중', done:'완료'},
  TaskPriority: {low:'낮음', medium:'보통', high:'높음'},
  WorkingDayEventType: {
    public_holiday:'공휴일', vacation:'휴가', conference:'학회',
    training:'교육', sales_activity:'영업 활동', other:'기타',
  }
};
```

### 7.5 색·아이콘 가이드

```typescript
const TYPE_META = {
  public_holiday: {color:'#EF4444', bg:'#FEE2E2', emoji:'🏛️'},
  vacation:       {color:'#3B82F6', bg:'#DBEAFE', emoji:'🏖️'},
  conference:     {color:'#8B5CF6', bg:'#EDE9FE', emoji:'🎓'},
  training:       {color:'#F59E0B', bg:'#FEF3C7', emoji:'📚'},
  sales_activity: {color:'#10B981', bg:'#D1FAE5', emoji:'💼'},
  other:          {color:'#6B7280', bg:'#F3F4F6', emoji:'📌'},
};
```

---

## 8. 비기능 요구사항

| 항목 | 요구사항 |
|---|---|
| **인증** | Supabase Auth (Google OAuth 권장 — gcal·gmail 통합 용이) |
| **권한** | 본인 territory 데이터 우선 표시·전체 보기 옵션. admin은 모두 보기 |
| **타임존** | Asia/Seoul 고정. 모든 timestamp는 timestamptz. 표시는 KST |
| **언어** | UI 한국어. 코드 식별자·DB는 영문 |
| **반응형** | 데스크탑 우선 (영업담당 PC 사용). 모바일은 read-only 우선 |
| **성능** | 1만개 row까지 페이지네이션 없이 OK. 그 이상은 server-side filter |
| **백업** | Supabase 자동 일일 백업 활용 |

---

## 9. Phase 별 구현 우선순위 (제안)

**Phase 1 — MVP (2~3주)**
1. Auth + user_profiles
2. companies + contacts CRUD
3. activities (sales 활동 입력·주간 뷰)
4. working_days (월/주 뷰 + sales_activity 자동 Activity)

**Phase 2 — 영업 확장 (2~3주)**
5. deals + pipelines (칸반)
6. prescriptions + 빠른 입력
7. sales_transactions + 집계 dashboard
8. product_listings

**Phase 3 — 고급 (2~3주)**
9. email_templates + sequences + tracking
10. automations
11. KOL plans + hospital_contracts
12. Google Calendar 양방향 동기화

**Phase 4 — 폴리시 (1~2주)**
13. forms + 외부 booking
14. segments + dynamic list
15. CSV import·export
16. RLS·권한 세분화

---

## 10. 참고 자료 (이 문서 외)

소스 코드 (현 SQLite MOCO 기준):

```
app/cc_web_interface/crm/
├── models.py            ← 모든 SQLAlchemy 모델 정의 (이 문서의 source of truth)
├── schemas.py           ← Pydantic 입출력 schema
├── database.py          ← DB 초기화 + 자동 마이그레이션 (ALTER TABLE ADD COLUMN)
├── seed.py              ← 초기 데이터
├── routes/              ← 23개 라우터
│   ├── working_days.py  ← 가장 풍부 (월별 집계·gcal 동기화·자동 Activity)
│   ├── activities.py
│   ├── companies.py
│   └── ... (총 23 파일)
├── services/
│   └── google_calendar_sync.py  ← gcal 양방향 동기화 핵심 로직
└── static/
    └── index.html       ← 단일 React SPA. 모든 페이지 컴포넌트가 여기에
```

특히 **`models.py`**(689줄)·**`static/index.html`**(약 22000줄)이 Source of Truth. 이 문서가 모호하면 두 파일 참조.

---

## 11. 자주 묻는 결정 사항

| 질문 | 답 |
|---|---|
| 한국 의료기관 코드(요양기관 기호) | `hospital_code` 컬럼. 사용자 수동 입력 + CSV import |
| 의사 면허번호 검증 | `license_number` 자유 입력. 검증 API 통합은 추후 |
| Slack ID 어떻게 처리? | `user_profiles.slack_id`로 호환. UI엔 display_name 노출 |
| 다국가 지원? | 현재 한국 only. country='Korea' fixed |
| 환자 개인정보? | `patient_id` 익명 문자열만 저장. 실명·주민번호 X |
| 첨부파일? | Supabase Storage 사용. `custom_properties`에 URL 저장 |

---

**문서 끝.** 추가로 필요한 디테일·예제 코드 요청 시 알려주세요.
