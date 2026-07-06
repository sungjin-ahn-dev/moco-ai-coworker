"""
CRM Pydantic 스키마
요청/응답 직렬화 모델 정의
"""

from datetime import datetime, date
from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field, ConfigDict, field_validator

# ──────────────────────── Generic Pagination ────────────────────────

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """페이지네이션 응답 래퍼"""
    total: int
    page: int
    page_size: int
    items: List[T]


class SuccessResponse(BaseModel):
    """표준 성공 응답"""
    success: bool = True
    data: Any = None


class ErrorResponse(BaseModel):
    """표준 에러 응답"""
    success: bool = False
    error: bool = True
    message: str


# ──────────────────────── Company ────────────────────────


class CompanyBase(BaseModel):
    name: str
    domain: Optional[str] = None
    industry: Optional[str] = None
    employee_count: Optional[int] = None
    annual_revenue: Optional[float] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    hospital_code: Optional[str] = None
    hospital_type: Optional[str] = None
    region_1: Optional[str] = None
    region_2: Optional[str] = None
    region_3: Optional[str] = None
    territory_owner: Optional[str] = None
    is_target: Optional[bool] = False
    custom_properties: Optional[dict] = Field(default_factory=dict)


class CompanyCreate(CompanyBase):
    pass


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    domain: Optional[str] = None
    industry: Optional[str] = None
    employee_count: Optional[int] = None
    annual_revenue: Optional[float] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    hospital_code: Optional[str] = None
    hospital_type: Optional[str] = None
    region_1: Optional[str] = None
    region_2: Optional[str] = None
    region_3: Optional[str] = None
    territory_owner: Optional[str] = None
    is_target: Optional[bool] = None
    custom_properties: Optional[dict] = None


class CompanyRead(CompanyBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CompanyDetailRead(CompanyRead):
    """회사 상세 (연락처/딜 수 포함)"""
    contact_count: int = 0
    deal_count: int = 0
    total_deal_value: float = 0.0


# ──────────────────────── Contact ────────────────────────


class ContactBase(BaseModel):
    first_name: str
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company_id: Optional[int] = None
    owner_slack_id: Optional[str] = None
    lead_score: Optional[int] = 0
    lead_status: Optional[str] = "new"
    lifecycle_stage: Optional[str] = "lead"
    source: Optional[str] = None
    tags: Optional[List[str]] = Field(default_factory=list)
    hcp_code: Optional[str] = None
    department: Optional[str] = None
    sub_specialty: Optional[str] = None
    title_position: Optional[str] = None
    license_number: Optional[str] = None
    custom_properties: Optional[dict] = Field(default_factory=dict)


class ContactCreate(ContactBase):
    pass


class ContactUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company_id: Optional[int] = None
    owner_slack_id: Optional[str] = None
    lead_score: Optional[int] = None
    lead_status: Optional[str] = None
    lifecycle_stage: Optional[str] = None
    source: Optional[str] = None
    tags: Optional[List[str]] = None
    hcp_code: Optional[str] = None
    department: Optional[str] = None
    sub_specialty: Optional[str] = None
    title_position: Optional[str] = None
    license_number: Optional[str] = None
    custom_properties: Optional[dict] = None


class ContactRead(ContactBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ContactEnrollmentRead(BaseModel):
    """연락처에 포함된 시퀀스 등록 정보"""
    model_config = ConfigDict(from_attributes=True)
    id: int
    sequence_id: int
    sequence_name: Optional[str] = None
    current_step: int = 0
    status: str = "active"
    started_at: Optional[datetime] = None
    next_send_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ContactDetail(ContactRead):
    """연락처 상세 (회사, 거래, 활동, 시퀀스 등록 포함)"""
    company: Optional[CompanyRead] = None
    deals: List["DealRead"] = []
    activities: List["ActivityRead"] = []
    enrollments: List[ContactEnrollmentRead] = []


# ──────────────────────── Pipeline ────────────────────────


class PipelineStage(BaseModel):
    id: str
    name: str
    probability: int = 0
    order: int = 0


class PipelineBase(BaseModel):
    name: str
    stages: List[PipelineStage]
    is_default: bool = False


class PipelineCreate(PipelineBase):
    pass


class PipelineUpdate(BaseModel):
    name: Optional[str] = None
    stages: Optional[List[PipelineStage]] = None
    is_default: Optional[bool] = None


class PipelineRead(PipelineBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: Optional[datetime] = None


# ──────────────────────── Deal ────────────────────────


def _empty_str_to_none(v):
    """Convert empty strings to None for optional fields."""
    if v == "" or v == "null":
        return None
    return v


class DealBase(BaseModel):
    name: str
    pipeline_id: int
    stage: str
    amount: float = 0.0
    currency: str = "KRW"
    close_date: Optional[datetime] = None
    contact_id: Optional[int] = None
    company_id: Optional[int] = None
    owner_slack_id: Optional[str] = None
    probability: int = 0
    lost_reason: Optional[str] = None
    custom_properties: Optional[dict] = Field(default_factory=dict)

    @field_validator("close_date", mode="before")
    @classmethod
    def parse_close_date(cls, v):
        return _empty_str_to_none(v)


class DealCreate(DealBase):
    pass


class DealUpdate(BaseModel):
    name: Optional[str] = None
    pipeline_id: Optional[int] = None
    stage: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    close_date: Optional[datetime] = None
    contact_id: Optional[int] = None
    company_id: Optional[int] = None
    owner_slack_id: Optional[str] = None
    probability: Optional[int] = None
    lost_reason: Optional[str] = None
    custom_properties: Optional[dict] = None

    @field_validator("close_date", mode="before")
    @classmethod
    def parse_close_date(cls, v):
        return _empty_str_to_none(v)


class DealStageUpdate(BaseModel):
    """거래 단계 변경 요청"""
    stage: str
    lost_reason: Optional[str] = None


class DealRead(DealBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DealDetailRead(DealRead):
    """딜 상세 (연락처명, 회사명 포함)"""
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    company_name: Optional[str] = None


# ──────────────────────── Activity ────────────────────────


class ActivityBase(BaseModel):
    type: str
    subject: Optional[str] = None
    body: Optional[str] = None
    contact_id: Optional[int] = None
    deal_id: Optional[int] = None
    company_id: Optional[int] = None
    user_slack_id: Optional[str] = None
    associated_email_id: Optional[str] = None
    metadata: Optional[dict] = Field(default_factory=dict)
    timestamp: Optional[datetime] = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def parse_timestamp(cls, v):
        return _empty_str_to_none(v)


class ActivityCreate(ActivityBase):
    pass


class ActivityUpdate(BaseModel):
    type: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    contact_id: Optional[int] = None
    deal_id: Optional[int] = None
    company_id: Optional[int] = None
    user_slack_id: Optional[str] = None
    associated_email_id: Optional[str] = None
    metadata: Optional[dict] = None
    timestamp: Optional[datetime] = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def parse_timestamp(cls, v):
        return _empty_str_to_none(v)


class ActivityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: int
    type: str
    subject: Optional[str] = None
    body: Optional[str] = None
    contact_id: Optional[int] = None
    deal_id: Optional[int] = None
    company_id: Optional[int] = None
    user_slack_id: Optional[str] = None
    associated_email_id: Optional[str] = None
    metadata: Optional[dict] = Field(default_factory=dict, alias="extra_data")
    timestamp: Optional[datetime] = None
    created_at: Optional[datetime] = None


class ActivityDetailRead(ActivityRead):
    """활동 상세 (연락처명, 딜명, 회사명 포함)"""
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    deal_name: Optional[str] = None
    company_name: Optional[str] = None


# ──────────────────────── Email Template ────────────────────────


class EmailTemplateBase(BaseModel):
    name: str
    description: Optional[str] = None
    type: str = "email"  # email, newsletter, pamphlet
    subject: Optional[str] = None
    body_html: str
    body_text: Optional[str] = None
    variables: Optional[List[str]] = Field(default_factory=list)
    tags: Optional[List[str]] = Field(default_factory=list)
    status: str = "active"


class EmailTemplateCreate(EmailTemplateBase):
    pass


class EmailTemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    type: Optional[str] = None
    subject: Optional[str] = None
    body_html: Optional[str] = None
    body_text: Optional[str] = None
    variables: Optional[List[str]] = None
    tags: Optional[List[str]] = None
    status: Optional[str] = None


class EmailTemplateRead(EmailTemplateBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ──────────────────────── Email Sequence ────────────────────────


class StepAction(BaseModel):
    """분기 시 수행할 액션"""
    type: str = "send_email"  # send_email, create_task, add_tag, notify_slack, change_lead_score, enroll_sequence, end
    config: Optional[dict] = Field(default_factory=dict)
    # send_email: {subject, body} (없으면 step의 기본 subject/body 사용)
    # create_task: {title, due_days}
    # add_tag: {tag}
    # notify_slack: {message}
    # change_lead_score: {delta} (양수/음수)
    # enroll_sequence: {sequence_id}
    # end: 시퀀스 종료


class StepCondition(BaseModel):
    """분기 조건"""
    condition: str  # on_open, on_click, on_reply, on_no_open, on_no_click, on_no_reply, on_form_submit, on_meeting_booked
    delay_days: int = 0  # 이 조건 충족 후 며칠 뒤 액션 수행
    next_step: Optional[int] = None  # 특정 step으로 이동 (None이면 순차)
    actions: Optional[List[StepAction]] = Field(default_factory=list)  # 추가 액션들


class SequenceStep(BaseModel):
    step_number: int
    delay_days: int
    subject_template: str
    body_template: str
    max_retries: int = 0  # 무반응 시 최대 재시도 횟수 (0=재시도 안함)
    retry_delay_days: int = 3  # 재시도 간격 (일)
    conditions: Optional[List[StepCondition]] = Field(default_factory=list)  # 분기 조건


class EmailSequenceBase(BaseModel):
    name: str
    description: Optional[str] = None
    steps: List[SequenceStep] = []
    status: str = "active"


class EmailSequenceCreate(EmailSequenceBase):
    pass


class EmailSequenceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    steps: Optional[List[SequenceStep]] = None
    status: Optional[str] = None


class EmailSequenceRead(EmailSequenceBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class EnrollRequest(BaseModel):
    contact_id: int


class EmailEnrollmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sequence_id: int
    contact_id: int
    current_step: int
    status: str
    started_at: Optional[datetime] = None
    next_send_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class EnrollmentWithContactRead(BaseModel):
    """등록 정보 + 연락처 이름/이메일 포함"""
    model_config = ConfigDict(from_attributes=True)
    id: int
    sequence_id: int
    contact_id: int
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    current_step: int = 0
    status: str = "active"
    started_at: Optional[datetime] = None
    next_send_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class SequenceStats(BaseModel):
    total_enrolled: int = 0
    active: int = 0
    completed: int = 0
    paused: int = 0
    bounced: int = 0


class SequenceDashboardItem(BaseModel):
    """대시보드용 시퀀스 요약 (stats 포함)"""
    id: int
    name: str
    description: Optional[str] = None
    status: str = "active"
    step_count: int = 0
    total_enrolled: int = 0
    active: int = 0
    completed: int = 0
    paused: int = 0
    bounced: int = 0
    created_at: Optional[datetime] = None


class BulkEnrollRequest(BaseModel):
    """벌크 등록 요청 (contact_ids 직접 지정 또는 segment_id 기반)"""
    contact_ids: Optional[List[int]] = None
    segment_id: Optional[int] = None


# ──────────────────────── Automation ────────────────────────


class AutomationAction(BaseModel):
    type: str  # send_email, create_task, update_property, notify_slack, enroll_sequence, change_stage
    config: dict = Field(default_factory=dict)


class AutomationBase(BaseModel):
    name: str
    description: Optional[str] = None
    trigger_type: str
    trigger_config: Optional[dict] = Field(default_factory=dict)
    actions: List[AutomationAction] = []
    status: str = "active"


class AutomationCreate(AutomationBase):
    pass


class AutomationUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    trigger_type: Optional[str] = None
    trigger_config: Optional[dict] = None
    actions: Optional[List[AutomationAction]] = None
    status: Optional[str] = None


class AutomationRead(AutomationBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    execution_count: int = 0
    last_executed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class AutomationExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    automation_id: int
    trigger_data: Optional[dict] = None
    results: Optional[list] = None
    success: bool = True
    executed_at: Optional[datetime] = None


# ──────────────────────── CRM Task ────────────────────────


class CRMTaskBase(BaseModel):
    title: str
    description: Optional[str] = None
    due_date: Optional[datetime] = None
    status: str = "todo"
    priority: str = "medium"
    contact_id: Optional[int] = None
    deal_id: Optional[int] = None
    assigned_to_slack_id: Optional[str] = None

    @field_validator("due_date", mode="before")
    @classmethod
    def parse_due_date(cls, v):
        return _empty_str_to_none(v)


class CRMTaskCreate(CRMTaskBase):
    pass


class CRMTaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[datetime] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    contact_id: Optional[int] = None
    deal_id: Optional[int] = None
    assigned_to_slack_id: Optional[str] = None

    @field_validator("due_date", mode="before")
    @classmethod
    def parse_due_date(cls, v):
        return _empty_str_to_none(v)


class CRMTaskRead(CRMTaskBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class CRMTaskDetailRead(CRMTaskRead):
    """태스크 상세 (연락처명, 딜명 포함)"""
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    deal_name: Optional[str] = None


# ──────────────────────── Form ────────────────────────


class FormField(BaseModel):
    name: str
    label: str
    type: str = "text"
    required: bool = False
    options: Optional[List[str]] = None


class FormBase(BaseModel):
    name: str
    fields: List[FormField] = []
    redirect_url: Optional[str] = None
    notification_emails: Optional[str] = None


class FormCreate(FormBase):
    pass


class FormUpdate(BaseModel):
    name: Optional[str] = None
    fields: Optional[List[FormField]] = None
    redirect_url: Optional[str] = None
    notification_emails: Optional[str] = None


class FormRead(FormBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    submission_count: int = 0
    created_at: Optional[datetime] = None


class FormSubmissionCreate(BaseModel):
    data: dict


class FormSubmissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    form_id: int
    contact_id: Optional[int] = None
    data: dict
    submitted_at: Optional[datetime] = None


# ──────────────────────── Email Tracking ────────────────────────


class EmailTrackingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tracking_id: str
    contact_id: Optional[int] = None
    enrollment_id: Optional[int] = None
    sequence_id: Optional[int] = None
    subject: Optional[str] = None
    recipient_email: Optional[str] = None
    sent_at: Optional[datetime] = None
    open_count: int = 0
    first_opened_at: Optional[datetime] = None
    last_opened_at: Optional[datetime] = None
    click_count: int = 0
    first_clicked_at: Optional[datetime] = None
    clicked_urls: Optional[list] = []
    replied: bool = False
    replied_at: Optional[datetime] = None


class EmailTrackingSummary(BaseModel):
    """연락처별 이메일 추적 요약"""
    total_sent: int = 0
    total_opened: int = 0
    total_clicked: int = 0
    total_replied: int = 0
    open_rate: float = 0.0
    click_rate: float = 0.0
    reply_rate: float = 0.0


# ──────────────────────── Relationship ────────────────────────


class RelationshipCreate(BaseModel):
    from_type: str  # contact, company, deal
    from_id: int
    to_type: str
    to_id: int
    relationship_type: str  # 소속, 겸임, 유통, 보험, 협력, 의뢰, 납품 등
    role: Optional[str] = None
    extra_data: Optional[dict] = Field(default_factory=dict)
    is_primary: bool = False


class RelationshipUpdate(BaseModel):
    relationship_type: Optional[str] = None
    role: Optional[str] = None
    extra_data: Optional[dict] = None
    is_primary: Optional[bool] = None
    status: Optional[str] = None


class RelationshipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    from_type: str
    from_id: int
    to_type: str
    to_id: int
    relationship_type: str
    role: Optional[str] = None
    extra_data: Optional[dict] = None
    is_primary: bool = False
    status: str = "active"
    # 조회 시 이름 포함
    from_name: Optional[str] = None
    to_name: Optional[str] = None
    created_at: Optional[datetime] = None


# ──────────────────────── Meeting Booking ────────────────────────


class MeetingSlot(BaseModel):
    start: str  # ISO datetime
    end: str
    label: Optional[str] = None


class MeetingBookingCreate(BaseModel):
    contact_id: int
    title: Optional[str] = "미팅"
    duration_minutes: int = 30
    slots: List[MeetingSlot]
    message: Optional[str] = None


class MeetingBookingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    token: str
    host_slack_id: str
    host_email: Optional[str] = None
    contact_id: Optional[int] = None
    contact_email: Optional[str] = None
    contact_name: Optional[str] = None
    title: Optional[str] = None
    duration_minutes: int = 30
    slots: list = []
    selected_slot: Optional[dict] = None
    status: str = "pending"
    calendar_event_id: Optional[str] = None
    message: Optional[str] = None
    created_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None


# ──────────────────────── Segment ────────────────────────


class SegmentFilter(BaseModel):
    field: str  # lead_status, lifecycle_stage, lead_score, source, tag, custom_prop
    operator: str  # eq, neq, gt, gte, lt, lte, contains, in
    value: Any


class SegmentBase(BaseModel):
    name: str
    description: Optional[str] = None
    filters: List[SegmentFilter] = []


class SegmentCreate(SegmentBase):
    pass


class SegmentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    filters: Optional[List[SegmentFilter]] = None


class SegmentRead(SegmentBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    contact_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ──────────────────────── Reports ────────────────────────


class DashboardStats(BaseModel):
    total_contacts: int = 0
    total_companies: int = 0
    total_deals: int = 0
    total_revenue: float = 0.0
    open_deals_value: float = 0.0
    conversion_rate: float = 0.0
    tasks_overdue: int = 0
    activities_this_week: int = 0


class PipelineReport(BaseModel):
    stage: str
    deal_count: int = 0
    total_value: float = 0.0
    avg_value: float = 0.0


class ActivityReport(BaseModel):
    type: str
    count: int = 0


class LeadSourceReport(BaseModel):
    source: str
    count: int = 0
    converted: int = 0


class RevenueForecast(BaseModel):
    month: str
    expected_revenue: float = 0.0
    weighted_revenue: float = 0.0
    deal_count: int = 0


class SalesPerformance(BaseModel):
    owner_slack_id: str
    deals_won: int = 0
    deals_lost: int = 0
    total_revenue: float = 0.0
    avg_deal_size: float = 0.0
    win_rate: float = 0.0


# ──────────────────────── Prescription (Phase 3) ────────────────────────


class PrescriptionBase(BaseModel):
    prescription_code: Optional[str] = None
    session_number: int = 1
    platform: Optional[str] = None
    hospital_id: Optional[int] = None
    doctor_id: Optional[int] = None
    patient_id: Optional[str] = None
    prescription_type: Optional[str] = None
    prescribed_date: Optional[datetime] = None
    activated_date: Optional[datetime] = None
    status: str = "active"
    custom_properties: Optional[dict] = Field(default_factory=dict)

    @field_validator("prescribed_date", "activated_date", mode="before")
    @classmethod
    def parse_dates(cls, v):
        return _empty_str_to_none(v)


class PrescriptionCreate(PrescriptionBase):
    pass


class PrescriptionUpdate(BaseModel):
    prescription_code: Optional[str] = None
    session_number: Optional[int] = None
    platform: Optional[str] = None
    hospital_id: Optional[int] = None
    doctor_id: Optional[int] = None
    patient_id: Optional[str] = None
    prescription_type: Optional[str] = None
    prescribed_date: Optional[datetime] = None
    activated_date: Optional[datetime] = None
    status: Optional[str] = None
    custom_properties: Optional[dict] = None


class PrescriptionRead(PrescriptionBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    hospital_name: Optional[str] = None
    doctor_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PrescriptionStats(BaseModel):
    total_prescriptions: int = 0
    np_count: int = 0
    nr_count: int = 0
    unique_hospitals: int = 0
    unique_doctors: int = 0
    unique_patients: int = 0
    monthly_trend: List[dict] = []
    top_hospitals: List[dict] = []
    top_doctors: List[dict] = []


class ComplianceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    patient_id: str
    hospital_id: Optional[int] = None
    doctor_id: Optional[int] = None
    total_sessions: int = 0
    completed_sessions: int = 0
    compliance_rate: float = 0.0
    status: str = "active"
    custom_properties: Optional[dict] = Field(default_factory=dict)
    created_at: Optional[datetime] = None


# ──────────────────────── Sales (Phase 4) ────────────────────────


class SalesTransactionBase(BaseModel):
    year: int
    month: int
    company_id: Optional[int] = None
    product: Optional[str] = None
    channel: Optional[str] = None
    quantity: Optional[int] = 0
    unit_price: Optional[float] = 0.0
    revenue: Optional[float] = 0.0
    revenue_recognized: Optional[bool] = False
    payment_received: Optional[bool] = False
    payment_date: Optional[datetime] = None
    ownership: Optional[str] = None
    custom_properties: Optional[dict] = Field(default_factory=dict)

    @field_validator("payment_date", mode="before")
    @classmethod
    def parse_payment_date(cls, v):
        return _empty_str_to_none(v)


class SalesTransactionCreate(SalesTransactionBase):
    pass


class SalesTransactionUpdate(BaseModel):
    year: Optional[int] = None
    month: Optional[int] = None
    company_id: Optional[int] = None
    product: Optional[str] = None
    channel: Optional[str] = None
    quantity: Optional[int] = None
    unit_price: Optional[float] = None
    revenue: Optional[float] = None
    revenue_recognized: Optional[bool] = None
    payment_received: Optional[bool] = None
    payment_date: Optional[datetime] = None
    ownership: Optional[str] = None
    custom_properties: Optional[dict] = None


class SalesTransactionRead(SalesTransactionBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SalesSummary(BaseModel):
    total_revenue: float = 0.0
    total_quantity: int = 0
    total_received: float = 0.0
    monthly_trend: List[dict] = []
    by_product: List[dict] = []


# ──────────────────────── Product Listing (Phase 5) ────────────────────────


class ProductListingBase(BaseModel):
    company_id: int
    product: str
    status: str = "pending"
    pipeline_stage: Optional[str] = None
    started_at: Optional[datetime] = None
    listed_at: Optional[datetime] = None
    notes: Optional[str] = None
    custom_properties: Optional[dict] = Field(default_factory=dict)

    @field_validator("started_at", "listed_at", mode="before")
    @classmethod
    def parse_dates(cls, v):
        return _empty_str_to_none(v)


class ProductListingCreate(ProductListingBase):
    pass


class ProductListingUpdate(BaseModel):
    company_id: Optional[int] = None
    product: Optional[str] = None
    status: Optional[str] = None
    pipeline_stage: Optional[str] = None
    started_at: Optional[datetime] = None
    listed_at: Optional[datetime] = None
    notes: Optional[str] = None
    custom_properties: Optional[dict] = None


class ProductListingRead(ProductListingBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ──────────────────────── KOL Plan (Phase 5) ────────────────────────


class KOLPlanBase(BaseModel):
    company_id: Optional[int] = None
    doctor_id: Optional[int] = None
    plan_type: Optional[str] = None
    target_product: Optional[str] = None
    clinic_schedule: Optional[dict] = Field(default_factory=dict)
    engagement_status: str = "planned"
    notes: Optional[str] = None
    custom_properties: Optional[dict] = Field(default_factory=dict)


class KOLPlanCreate(KOLPlanBase):
    pass


class KOLPlanUpdate(BaseModel):
    company_id: Optional[int] = None
    doctor_id: Optional[int] = None
    plan_type: Optional[str] = None
    target_product: Optional[str] = None
    clinic_schedule: Optional[dict] = None
    engagement_status: Optional[str] = None
    notes: Optional[str] = None
    custom_properties: Optional[dict] = None


class KOLPlanRead(KOLPlanBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_name: Optional[str] = None
    doctor_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ──────────────────────── Hospital Contract (Phase 5) ────────────────────────


class HospitalContractBase(BaseModel):
    company_id: int
    product: str
    contract_status: str = "pending"
    contract_date: Optional[datetime] = None
    expiry_date: Optional[datetime] = None
    contract_value: float = 0.0
    notes: Optional[str] = None
    custom_properties: Optional[dict] = Field(default_factory=dict)

    @field_validator("contract_date", "expiry_date", mode="before")
    @classmethod
    def parse_dates(cls, v):
        return _empty_str_to_none(v)


class HospitalContractCreate(HospitalContractBase):
    pass


class HospitalContractUpdate(BaseModel):
    company_id: Optional[int] = None
    product: Optional[str] = None
    contract_status: Optional[str] = None
    contract_date: Optional[datetime] = None
    expiry_date: Optional[datetime] = None
    contract_value: Optional[float] = None
    notes: Optional[str] = None
    custom_properties: Optional[dict] = None


class HospitalContractRead(HospitalContractBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ──────────────────────── Import Schemas ────────────────────────


class ImportResult(BaseModel):
    total: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: List[str] = []


# ──────────────────────── Working Day Event ────────────────────────


class WorkingDayEventBase(BaseModel):
    event_type: str  # public_holiday | vacation | conference | training | sales_activity | other
    start_date: date
    end_date: date
    start_at: Optional[datetime] = None  # 시간지정 이벤트 (sales_activity 미팅 등)
    end_at: Optional[datetime] = None
    is_all_day: Optional[bool] = True
    user_slack_id: Optional[str] = None
    title: str
    note: Optional[str] = None
    is_half_day: Optional[bool] = False
    company_id: Optional[int] = None  # 병원 (sales_activity 시)
    contact_id: Optional[int] = None  # 의사
    activity_id: Optional[int] = None  # 자동 생성된 Activity 추적


class WorkingDayEventCreate(WorkingDayEventBase):
    pass


class WorkingDayEventUpdate(BaseModel):
    event_type: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    is_all_day: Optional[bool] = None
    user_slack_id: Optional[str] = None
    title: Optional[str] = None
    note: Optional[str] = None
    is_half_day: Optional[bool] = None
    company_id: Optional[int] = None
    contact_id: Optional[int] = None


class WorkingDayEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    event_type: str
    start_date: date
    end_date: date
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    is_all_day: Optional[bool] = True
    user_slack_id: Optional[str] = None
    title: str
    note: Optional[str] = None
    is_half_day: Optional[bool] = False
    company_id: Optional[int] = None
    contact_id: Optional[int] = None
    activity_id: Optional[int] = None
    source: Optional[str] = "manual"
    gcal_event_id: Optional[str] = None
    gcal_user_email: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# Forward reference 업데이트
ContactDetail.model_rebuild()
