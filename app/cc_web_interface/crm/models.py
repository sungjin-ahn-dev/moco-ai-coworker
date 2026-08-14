"""
CRM 데이터 모델
SQLAlchemy ORM 모델 정의 (HubSpot Professional 클론)
"""

import enum
from datetime import datetime, timezone, timedelta

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Text, DateTime, Date,
    ForeignKey, JSON, Enum, Index,
)
from sqlalchemy.orm import relationship

from app.cc_web_interface.crm.database import Base

# KST 타임존
KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


# ──────────────────────── Enums ────────────────────────


class LeadStatus(str, enum.Enum):
    new = "new"
    contacted = "contacted"
    qualified = "qualified"
    unqualified = "unqualified"


class LifecycleStage(str, enum.Enum):
    subscriber = "subscriber"
    lead = "lead"
    mql = "mql"
    sql = "sql"
    opportunity = "opportunity"
    customer = "customer"
    evangelist = "evangelist"


class ActivityType(str, enum.Enum):
    call = "call"
    email = "email"
    meeting = "meeting"
    note = "note"
    task = "task"


class SequenceStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    archived = "archived"


class EnrollmentStatus(str, enum.Enum):
    active = "active"
    completed = "completed"
    paused = "paused"
    bounced = "bounced"


class TriggerType(str, enum.Enum):
    deal_stage_change = "deal_stage_change"
    contact_created = "contact_created"
    lead_score_threshold = "lead_score_threshold"
    form_submission = "form_submission"
    email_opened = "email_opened"
    tag_added = "tag_added"
    manual = "manual"


class AutomationStatus(str, enum.Enum):
    active = "active"
    paused = "paused"


class TaskStatus(str, enum.Enum):
    todo = "todo"
    in_progress = "in_progress"
    done = "done"


class TaskPriority(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


# ──────────────────────── Models ────────────────────────


class Company(Base):
    """회사(병원) 모델"""
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, index=True)
    domain = Column(String(255), index=True)
    industry = Column(String(100))
    employee_count = Column(Integer)
    annual_revenue = Column(Float)
    phone = Column(String(50))
    address = Column(Text)
    city = Column(String(100))
    country = Column(String(100))
    # Phase 1: 병원 마스터 확장
    hospital_code = Column(String(50), unique=True, index=True, nullable=True)
    hospital_type = Column(String(50), nullable=True)  # 상급종합/종합병원/병원/의원
    region_1 = Column(String(50), nullable=True)  # 시도
    region_2 = Column(String(50), nullable=True)  # 구군
    region_3 = Column(String(50), nullable=True)  # 권역
    territory_owner = Column(String(50), nullable=True)  # 영업 담당자
    is_target = Column(Boolean, default=False)
    custom_properties = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=now_kst)
    updated_at = Column(DateTime(timezone=True), default=now_kst, onupdate=now_kst)

    contacts = relationship("Contact", back_populates="company", cascade="all, delete-orphan")
    deals = relationship("Deal", back_populates="company", cascade="all, delete-orphan")
    activities = relationship("Activity", back_populates="company")
    prescriptions = relationship("Prescription", back_populates="company")
    sales_transactions = relationship("SalesTransaction", back_populates="company")
    product_listings = relationship("ProductListing", back_populates="company")
    kol_plans = relationship("KOLPlan", back_populates="company")
    hospital_contracts = relationship("HospitalContract", back_populates="company")


class Contact(Base):
    """연락처(HCP/의료진) 모델"""
    __tablename__ = "contacts"
    __table_args__ = (
        Index("ix_contacts_email", "email"),
        Index("ix_contacts_lead_status", "lead_status"),
        Index("ix_contacts_lifecycle_stage", "lifecycle_stage"),
        Index("ix_contacts_owner", "owner_slack_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100))
    email = Column(String(255), unique=True)
    phone = Column(String(50))
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True)
    owner_slack_id = Column(String(50))
    lead_score = Column(Integer, default=0)
    lead_status = Column(Enum(LeadStatus), default=LeadStatus.new)
    lifecycle_stage = Column(Enum(LifecycleStage), default=LifecycleStage.lead)
    source = Column(Text)
    tags = Column(JSON, default=list)
    # Phase 2: HCP 확장
    hcp_code = Column(String(50), unique=True, index=True, nullable=True)
    department = Column(String(100), nullable=True)  # 진료과
    sub_specialty = Column(String(100), nullable=True)  # 세부전공
    title_position = Column(String(100), nullable=True)  # 직급
    license_number = Column(String(50), nullable=True)  # 면허번호
    custom_properties = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=now_kst)
    updated_at = Column(DateTime(timezone=True), default=now_kst, onupdate=now_kst)

    company = relationship("Company", back_populates="contacts")
    deals = relationship("Deal", back_populates="contact", cascade="all, delete-orphan")
    activities = relationship("Activity", back_populates="contact", cascade="all, delete-orphan")
    enrollments = relationship("EmailEnrollment", back_populates="contact", cascade="all, delete-orphan")
    form_submissions = relationship("FormSubmission", back_populates="contact")
    prescriptions = relationship("Prescription", back_populates="doctor")
    kol_plans = relationship("KOLPlan", back_populates="doctor")


class Pipeline(Base):
    """영업 파이프라인 모델"""
    __tablename__ = "pipelines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    stages = Column(JSON, nullable=False)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=now_kst)

    deals = relationship("Deal", back_populates="pipeline")


class Deal(Base):
    """거래 모델"""
    __tablename__ = "deals"
    __table_args__ = (
        Index("ix_deals_stage", "stage"),
        Index("ix_deals_owner", "owner_slack_id"),
        Index("ix_deals_pipeline", "pipeline_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    pipeline_id = Column(Integer, ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False)
    stage = Column(String(100), nullable=False)
    amount = Column(Float, default=0.0)
    currency = Column(String(10), default="KRW")
    close_date = Column(DateTime(timezone=True))
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True)
    owner_slack_id = Column(String(50))
    probability = Column(Integer, default=0)
    lost_reason = Column(Text)
    custom_properties = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=now_kst)
    updated_at = Column(DateTime(timezone=True), default=now_kst, onupdate=now_kst)

    pipeline = relationship("Pipeline", back_populates="deals")
    contact = relationship("Contact", back_populates="deals")
    company = relationship("Company", back_populates="deals")
    activities = relationship("Activity", back_populates="deal")


class Activity(Base):
    """활동 이력 모델"""
    __tablename__ = "activities"
    __table_args__ = (
        Index("ix_activities_contact", "contact_id"),
        Index("ix_activities_deal", "deal_id"),
        Index("ix_activities_timestamp", "timestamp"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(Enum(ActivityType), nullable=False)
    subject = Column(String(500))
    body = Column(Text)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True)
    deal_id = Column(Integer, ForeignKey("deals.id", ondelete="SET NULL"), nullable=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True)
    user_slack_id = Column(String(50))
    associated_email_id = Column(String(255))
    extra_data = Column("metadata", JSON, default=dict)
    timestamp = Column(DateTime(timezone=True), default=now_kst)
    created_at = Column(DateTime(timezone=True), default=now_kst)

    contact = relationship("Contact", back_populates="activities")
    deal = relationship("Deal", back_populates="activities")
    company = relationship("Company", back_populates="activities")


class EmailTemplate(Base):
    """이메일/뉴스레터/팜플렛 템플릿 모델"""
    __tablename__ = "email_templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    type = Column(String(50), nullable=False, default="email")  # email, newsletter, pamphlet
    subject = Column(String(500))
    body_html = Column(Text, nullable=False)
    body_text = Column(Text)  # 플레인 텍스트 대체 버전
    variables = Column(JSON, default=list)  # 치환 변수 목록 (예: ["first_name", "company_name"])
    thumbnail_url = Column(String(500))
    tags = Column(JSON, default=list)
    status = Column(String(20), default="active")  # active, archived
    created_at = Column(DateTime(timezone=True), default=now_kst)
    updated_at = Column(DateTime(timezone=True), default=now_kst, onupdate=now_kst)


class EmailSequence(Base):
    """이메일 시퀀스 모델"""
    __tablename__ = "email_sequences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    steps = Column(JSON, nullable=False, default=list)
    status = Column(Enum(SequenceStatus), default=SequenceStatus.active)
    created_at = Column(DateTime(timezone=True), default=now_kst)
    updated_at = Column(DateTime(timezone=True), default=now_kst, onupdate=now_kst)

    enrollments = relationship("EmailEnrollment", back_populates="sequence", cascade="all, delete-orphan")


class EmailEnrollment(Base):
    """이메일 시퀀스 등록 모델"""
    __tablename__ = "email_enrollments"
    __table_args__ = (
        Index("ix_enrollments_next_send", "next_send_at"),
        Index("ix_enrollments_status", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    sequence_id = Column(Integer, ForeignKey("email_sequences.id", ondelete="CASCADE"), nullable=False)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    current_step = Column(Integer, default=0)
    retry_count = Column(Integer, default=0)  # 현재 step 재시도 횟수
    waiting_condition = Column(String(50), nullable=True)  # 대기 중인 조건 (on_open, on_click 등)
    status = Column(Enum(EnrollmentStatus), default=EnrollmentStatus.active)
    started_at = Column(DateTime(timezone=True), default=now_kst)
    next_send_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))

    sequence = relationship("EmailSequence", back_populates="enrollments")
    contact = relationship("Contact", back_populates="enrollments")


class Automation(Base):
    """자동화 워크플로우 모델"""
    __tablename__ = "automations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    trigger_type = Column(Enum(TriggerType), nullable=False)
    trigger_config = Column(JSON, default=dict)
    actions = Column(JSON, nullable=False, default=list)
    status = Column(Enum(AutomationStatus), default=AutomationStatus.active)
    execution_count = Column(Integer, default=0)
    last_executed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=now_kst)

    execution_history = relationship("AutomationExecution", back_populates="automation", cascade="all, delete-orphan")


class AutomationExecution(Base):
    """자동화 실행 이력"""
    __tablename__ = "automation_executions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    automation_id = Column(Integer, ForeignKey("automations.id", ondelete="CASCADE"), nullable=False)
    trigger_data = Column(JSON, default=dict)
    results = Column(JSON, default=list)
    success = Column(Boolean, default=True)
    executed_at = Column(DateTime(timezone=True), default=now_kst)

    automation = relationship("Automation", back_populates="execution_history")


class CRMTask(Base):
    """CRM 태스크 모델"""
    __tablename__ = "crm_tasks"
    __table_args__ = (
        Index("ix_tasks_assigned", "assigned_to_slack_id"),
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_due", "due_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(500), nullable=False)
    description = Column(Text)
    due_date = Column(DateTime(timezone=True))
    status = Column(Enum(TaskStatus), default=TaskStatus.todo)
    priority = Column(Enum(TaskPriority), default=TaskPriority.medium)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    deal_id = Column(Integer, ForeignKey("deals.id", ondelete="SET NULL"), nullable=True)
    assigned_to_slack_id = Column(String(50))
    created_at = Column(DateTime(timezone=True), default=now_kst)
    completed_at = Column(DateTime(timezone=True))

    contact = relationship("Contact")
    deal = relationship("Deal")


class Form(Base):
    """웹 폼 모델"""
    __tablename__ = "forms"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    fields = Column(JSON, nullable=False, default=list)
    redirect_url = Column(String(500))
    notification_emails = Column(Text)
    submission_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=now_kst)

    submissions = relationship("FormSubmission", back_populates="form", cascade="all, delete-orphan")


class FormSubmission(Base):
    """폼 제출 기록 모델"""
    __tablename__ = "form_submissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    form_id = Column(Integer, ForeignKey("forms.id", ondelete="CASCADE"), nullable=False)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    data = Column(JSON, nullable=False)
    submitted_at = Column(DateTime(timezone=True), default=now_kst)

    form = relationship("Form", back_populates="submissions")
    contact = relationship("Contact", back_populates="form_submissions")


class Relationship(Base):
    """엔티티 간 관계 모델 (Attio 스타일 네트워크 구조)"""
    __tablename__ = "relationships"
    __table_args__ = (
        Index("ix_rel_from", "from_type", "from_id"),
        Index("ix_rel_to", "to_type", "to_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    from_type = Column(String(50), nullable=False)  # contact, company, deal
    from_id = Column(Integer, nullable=False)
    to_type = Column(String(50), nullable=False)     # contact, company, deal
    to_id = Column(Integer, nullable=False)
    relationship_type = Column(String(100), nullable=False)  # 소속, 겸임, 유통, 보험, 협력, 의뢰, 납품 등
    role = Column(String(100))  # 역할 (예: 신경과 과장, 외래 진료, 총판 등)
    extra_data = Column("rel_metadata", JSON, default=dict)  # 추가 정보 (시작일, 계약조건 등)
    is_primary = Column(Boolean, default=False)  # 주 관계 여부
    status = Column(String(20), default="active")  # active, inactive
    created_at = Column(DateTime(timezone=True), default=now_kst)
    updated_at = Column(DateTime(timezone=True), default=now_kst, onupdate=now_kst)


class EmailTracking(Base):
    """이메일 추적 모델 - 열람/클릭 추적"""
    __tablename__ = "email_trackings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tracking_id = Column(String(64), unique=True, nullable=False, index=True)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True)
    enrollment_id = Column(Integer, nullable=True)
    sequence_id = Column(Integer, nullable=True)
    subject = Column(String(500))
    recipient_email = Column(String(255))
    sent_at = Column(DateTime(timezone=True), default=now_kst)
    open_count = Column(Integer, default=0)
    first_opened_at = Column(DateTime(timezone=True), nullable=True)
    last_opened_at = Column(DateTime(timezone=True), nullable=True)
    click_count = Column(Integer, default=0)
    first_clicked_at = Column(DateTime(timezone=True), nullable=True)
    clicked_urls = Column(JSON, default=list)  # [{url, clicked_at}]
    replied = Column(Boolean, default=False)
    replied_at = Column(DateTime(timezone=True), nullable=True)

    contact = relationship("Contact")


class MeetingBooking(Base):
    """미팅 예약 모델"""
    __tablename__ = "meeting_bookings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(String(64), unique=True, nullable=False, index=True)
    host_slack_id = Column(String(50), nullable=False)
    host_email = Column(String(255))
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    contact_email = Column(String(255))
    contact_name = Column(String(255))
    title = Column(String(500))
    duration_minutes = Column(Integer, default=30)
    slots = Column(JSON, nullable=False, default=list)  # [{start, end, label}]
    selected_slot = Column(JSON, nullable=True)  # {start, end}
    status = Column(String(20), default="pending")  # pending, confirmed, expired, cancelled
    calendar_event_id = Column(String(255), nullable=True)
    message = Column(Text, nullable=True)  # 고객에게 보여줄 메시지
    created_at = Column(DateTime(timezone=True), default=now_kst)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    contact = relationship("Contact")


class Segment(Base):
    """세그먼트(스마트 리스트) 모델 - 조건 기반 연락처 그룹"""
    __tablename__ = "segments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    filters = Column(JSON, nullable=False, default=list)
    contact_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=now_kst)
    updated_at = Column(DateTime(timezone=True), default=now_kst, onupdate=now_kst)


# ──────────────────────── Phase 3: 처방 관리 ────────────────────────


class Prescription(Base):
    """처방 모델"""
    __tablename__ = "prescriptions"
    __table_args__ = (
        Index("ix_prescriptions_hospital", "hospital_id"),
        Index("ix_prescriptions_doctor", "doctor_id"),
        Index("ix_prescriptions_date", "prescribed_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    prescription_code = Column(String(100), unique=True, index=True, nullable=True)
    session_number = Column(Integer, default=1)  # 처방 회차
    platform = Column(String(100), nullable=True)  # 처방 플랫폼
    hospital_id = Column(Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True)
    doctor_id = Column(Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    patient_id = Column(String(100), nullable=True)
    prescription_type = Column(String(10), nullable=True)  # NP/NR
    prescribed_date = Column(DateTime(timezone=True), nullable=True)
    activated_date = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), default="active")
    custom_properties = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=now_kst)
    updated_at = Column(DateTime(timezone=True), default=now_kst, onupdate=now_kst)

    company = relationship("Company", back_populates="prescriptions")
    doctor = relationship("Contact", back_populates="prescriptions")


class PatientCompliance(Base):
    """환자 순응도 모델"""
    __tablename__ = "patient_compliance"
    __table_args__ = (
        Index("ix_compliance_patient", "patient_id"),
        Index("ix_compliance_hospital", "hospital_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(String(100), nullable=False)
    hospital_id = Column(Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True)
    doctor_id = Column(Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    total_sessions = Column(Integer, default=0)
    completed_sessions = Column(Integer, default=0)
    compliance_rate = Column(Float, default=0.0)
    status = Column(String(50), default="active")
    custom_properties = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=now_kst)
    updated_at = Column(DateTime(timezone=True), default=now_kst, onupdate=now_kst)


# ──────────────────────── Phase 4: 매출 관리 ────────────────────────


class SalesTransaction(Base):
    """매출 거래 모델"""
    __tablename__ = "sales_transactions"
    __table_args__ = (
        Index("ix_sales_company", "company_id"),
        Index("ix_sales_year_month", "year", "month"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True)
    product = Column(String(100), nullable=True)
    channel = Column(String(255), nullable=True)  # 채널(거래처)
    quantity = Column(Integer, default=0)
    unit_price = Column(Float, default=0.0)
    revenue = Column(Float, default=0.0)  # 매출(-VAT)
    revenue_recognized = Column(Boolean, default=False)  # 매출인식
    payment_received = Column(Boolean, default=False)  # 입금여부
    payment_date = Column(DateTime(timezone=True), nullable=True)
    ownership = Column(String(50), nullable=True)
    custom_properties = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=now_kst)
    updated_at = Column(DateTime(timezone=True), default=now_kst, onupdate=now_kst)

    company = relationship("Company", back_populates="sales_transactions")


# ──────────────────────── Phase 5: 제품 리스팅/KOL/계약 ────────────────────────


class ProductListing(Base):
    """제품 리스팅 모델"""
    __tablename__ = "product_listings"
    __table_args__ = (
        Index("ix_listing_company", "company_id"),
        Index("ix_listing_status", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    product = Column(String(100), nullable=False)
    status = Column(String(50), default="pending")  # pending/in_progress/done/verbal_confirm
    pipeline_stage = Column(String(100), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    listed_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    custom_properties = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=now_kst)
    updated_at = Column(DateTime(timezone=True), default=now_kst, onupdate=now_kst)

    company = relationship("Company", back_populates="product_listings")


class KOLPlan(Base):
    """KOL 관리 모델"""
    __tablename__ = "kol_plans"
    __table_args__ = (
        Index("ix_kol_company", "company_id"),
        Index("ix_kol_doctor", "doctor_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=True)
    doctor_id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=True)
    plan_type = Column(String(100), nullable=True)  # ILD/COPD etc
    target_product = Column(String(100), nullable=True)
    clinic_schedule = Column(JSON, default=dict)  # 외래 스케줄
    engagement_status = Column(String(50), default="planned")
    notes = Column(Text, nullable=True)
    custom_properties = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=now_kst)
    updated_at = Column(DateTime(timezone=True), default=now_kst, onupdate=now_kst)

    company = relationship("Company", back_populates="kol_plans")
    doctor = relationship("Contact", back_populates="kol_plans")


class HospitalContract(Base):
    """병원 계약 모델"""
    __tablename__ = "hospital_contracts"
    __table_args__ = (
        Index("ix_contract_company", "company_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    product = Column(String(100), nullable=False)
    contract_status = Column(String(50), default="pending")
    contract_date = Column(DateTime(timezone=True), nullable=True)
    expiry_date = Column(DateTime(timezone=True), nullable=True)
    contract_value = Column(Float, default=0.0)
    notes = Column(Text, nullable=True)
    custom_properties = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=now_kst)
    updated_at = Column(DateTime(timezone=True), default=now_kst, onupdate=now_kst)

    company = relationship("Company", back_populates="hospital_contracts")


# ──────────────────────── Phase 6: 참조 데이터 ────────────────────────


class ReferenceData(Base):
    """참조 데이터 저장 모델 — SFE 현황판 등에서 사용하는 정적 참조 데이터"""
    __tablename__ = "reference_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(200), nullable=False, unique=True, index=True)
    data = Column(Text, nullable=False)  # JSON string
    created_at = Column(DateTime(timezone=True), default=now_kst)


# ──────────────────────── Phase 7: 일정/근무일 ────────────────────────


class WorkingDayEventType(str, enum.Enum):
    public_holiday = "public_holiday"   # 공휴일 (전사)
    vacation = "vacation"               # 개인 휴가 (working_day 차감)
    conference = "conference"           # 학회 참석 (working_day 차감)
    training = "training"               # 교육 (working_day 차감)
    sales_activity = "sales_activity"   # 영업 활동 — 방문·KOL 미팅·외근 (차감 X)
    other = "other"                     # 기타 사내 회의·개인 일정 (차감 X)


class WorkingDayEvent(Base):
    """일정/근무일 이벤트 — 휴일·휴가·학회 + 병원/의사 미팅까지 모두 관리"""
    __tablename__ = "working_day_events"
    __table_args__ = (
        Index("ix_working_day_events_user", "user_slack_id"),
        Index("ix_working_day_events_dates", "start_date", "end_date"),
        Index("ix_working_day_events_company", "company_id"),
        Index("ix_working_day_events_contact", "contact_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(Enum(WorkingDayEventType), nullable=False)
    # 종일 단위 (호환·월별 집계용)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    # 시간지정 (sales_activity 등 미팅 시간 관리용 — KST aware)
    start_at = Column(DateTime(timezone=True), nullable=True)
    end_at = Column(DateTime(timezone=True), nullable=True)
    is_all_day = Column(Boolean, default=True, nullable=False)
    user_slack_id = Column(String(50))   # null이면 전사(공휴일) 이벤트
    title = Column(String(255), nullable=False)
    note = Column(Text)
    is_half_day = Column(Boolean, default=False)
    # 병원·의사 연계 (sales_activity 시 채워짐)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    # 자동 생성된 Activity 추적 (메모 보존 위해 cascade X)
    activity_id = Column(Integer, ForeignKey("activities.id", ondelete="SET NULL"), nullable=True)
    # Google Calendar 양방향 동기화
    source = Column(String(20), default="manual")     # "manual" | "gcal"
    gcal_event_id = Column(String(255), index=True)   # 매핑된 Google Calendar 이벤트 ID
    gcal_user_email = Column(String(255))             # 어느 사용자 캘린더에서 왔는지
    last_synced_at = Column(DateTime(timezone=True))  # 마지막 동기화 시각
    created_at = Column(DateTime(timezone=True), default=now_kst)
    updated_at = Column(DateTime(timezone=True), default=now_kst, onupdate=now_kst)

    company = relationship("Company", foreign_keys=[company_id])
    contact = relationship("Contact", foreign_keys=[contact_id])
    activity = relationship("Activity", foreign_keys=[activity_id])
