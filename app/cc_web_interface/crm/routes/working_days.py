"""
일정/근무일 API 라우트
휴일·휴가·학회 등 이벤트 CRUD 및 월별 Working Day 산출
+ Google Calendar 양방향 동기화
"""

import calendar
import logging
from datetime import date as date_type, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import (
    Activity, ActivityType, Company, Contact,
    WorkingDayEvent, WorkingDayEventType,
)
from app.cc_web_interface.crm.schemas import (
    WorkingDayEventCreate, WorkingDayEventUpdate, WorkingDayEventRead,
    PaginatedResponse, SuccessResponse, ErrorResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/working-days", tags=["일정/근무일"])

KST = timezone(timedelta(hours=9))

# working_day에서 차감하는 부재 카테고리 (근무 중 활동인 sales_activity·other는 차감 X)
LEAVE_TYPES = {"vacation", "conference", "training"}


# ─────── Activity 자동 연동 도우미 ───────


async def _build_activity_subject(
    db: AsyncSession,
    company_id: Optional[int],
    contact_id: Optional[int],
    fallback: str,
) -> str:
    """병원명 + 의사명으로 Activity subject 생성. 둘 다 없으면 fallback."""
    parts = []
    if company_id:
        c = await db.get(Company, company_id)
        if c:
            parts.append(c.name or "")
    if contact_id:
        d = await db.get(Contact, contact_id)
        if d:
            full = " ".join(x for x in [d.last_name, d.first_name] if x).strip()
            if full:
                parts.append(full)
    if not parts:
        return fallback
    return " · ".join(p for p in parts if p)


async def _upsert_activity_for_event(
    event: WorkingDayEvent, db: AsyncSession,
) -> Optional[Activity]:
    """sales_activity 이벤트이고 hospital+doctor 둘 다 있으면 Activity upsert.

    조건 미충족 → 기존 Activity가 있다면 link 끊기. 새로 안 만듦.
    """
    et = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
    has_both = bool(event.company_id) and bool(event.contact_id)

    if et != "sales_activity" or not has_both:
        # 조건 미충족 — link 끊기 (기존 Activity는 보존)
        event.activity_id = None
        return None

    subject = await _build_activity_subject(
        db, event.company_id, event.contact_id, event.title,
    )
    # start_at 우선, 없으면 start_date를 자정 KST로
    ts = event.start_at or datetime.combine(event.start_date, datetime.min.time()).replace(tzinfo=KST)

    if event.activity_id:
        existing = await db.get(Activity, event.activity_id)
        if existing:
            existing.subject = subject
            existing.company_id = event.company_id
            existing.contact_id = event.contact_id
            existing.timestamp = ts
            existing.user_slack_id = event.user_slack_id
            # metadata 갱신 (source 보존)
            md = existing.extra_data or {}
            if isinstance(md, str):
                try:
                    import json as _json
                    md = _json.loads(md)
                except Exception:
                    md = {}
            md["schedule_source"] = "working_day"
            md["working_day_event_id"] = event.id
            existing.extra_data = md
            # body(메모)는 사용자 입력 보존 — 덮어쓰지 않음
            await db.flush()
            return existing

    # 신규 생성 — SalesActivityPage가 type=call로 필터하므로 call로 통일
    activity = Activity(
        type=ActivityType.call,
        subject=subject,
        body="",
        contact_id=event.contact_id,
        company_id=event.company_id,
        user_slack_id=event.user_slack_id,
        timestamp=ts,
        extra_data={
            "schedule_source": "working_day",
            "working_day_event_id": event.id,
        },
    )
    db.add(activity)
    await db.flush()
    event.activity_id = activity.id
    return activity


# ─────── Google Calendar 양방향 동기화 도우미 ───────


async def _try_push_to_gcal(event: WorkingDayEvent):
    """이벤트를 gcal에 push. 실패해도 라우트 자체는 성공시키기 위해 예외 삼킴."""
    try:
        from app.cc_web_interface.crm.services.google_calendar_sync import (
            push_event_to_gcal, get_sync_user_map,
        )
        from app.config.settings import get_settings
        settings = get_settings()
        if not settings.WORKING_DAY_GCAL_SYNC_ENABLED:
            return
        # public_holiday나 user 미지정은 push 대상 아님
        et = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        if et == "public_holiday" or not event.user_slack_id:
            return
        user_map = get_sync_user_map()
        if event.user_slack_id not in user_map:
            return
        await push_event_to_gcal(event)
    except Exception as e:
        logger.warning(f"[WD] gcal push 실패 (id={getattr(event, 'id', None)}): {e}")


async def _try_delete_from_gcal(event: WorkingDayEvent):
    try:
        from app.cc_web_interface.crm.services.google_calendar_sync import delete_event_from_gcal
        from app.config.settings import get_settings
        settings = get_settings()
        if not settings.WORKING_DAY_GCAL_SYNC_ENABLED:
            return
        await delete_event_from_gcal(event)
    except Exception as e:
        logger.warning(f"[WD] gcal 삭제 실패 (id={getattr(event, 'id', None)}): {e}")


# ──────────────────────── 월별 산출 (특수 라우트 우선) ────────────────────────


@router.get("/summary", response_model=SuccessResponse)
async def working_day_summary(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    user_slack_id: Optional[str] = Query(None, description="비어있으면 회사 영업일 기준"),
    db: AsyncSession = Depends(get_db),
):
    """
    월별 Working Day 산출.

    - **개인 모드** (user_slack_id 지정): 평일 - 공휴일 - 본인 부재(휴가/학회/교육) = 본인 영업일
    - **전체 모드** (user_slack_id 없음): 평일 - 공휴일 = 회사 영업일. personal_leave_days는 팀 합계(참고용).
    """
    _, last_day = calendar.monthrange(year, month)
    first = date_type(year, month, 1)
    last = date_type(year, month, last_day)

    # 1. 평일 수
    total_weekdays = sum(
        1 for d in range(1, last_day + 1)
        if date_type(year, month, d).weekday() < 5
    )

    # 2. 해당 월에 걸치는 이벤트 조회
    q = select(WorkingDayEvent).where(
        WorkingDayEvent.start_date <= last,
        WorkingDayEvent.end_date >= first,
    )
    if user_slack_id:
        # 개인 모드: 공휴일(전사) + 본인 이벤트만
        q = q.where(or_(
            WorkingDayEvent.user_slack_id == user_slack_id,
            WorkingDayEvent.user_slack_id.is_(None),
        ))
    result = await db.execute(q)
    events = result.scalars().all()

    # 3. 일자별로 펼쳐서 카운트 (평일만 차감)
    holiday_dates = set()
    leave_days = 0.0
    breakdown = {
        "public_holiday": 0.0,
        "vacation": 0.0,
        "conference": 0.0,
        "training": 0.0,
        "sales_activity": 0.0,
        "other": 0.0,
    }

    for e in events:
        # 해당 월 내의 평일만 골라낸다
        days_in_month = []
        d = max(e.start_date, first)
        end = min(e.end_date, last)
        while d <= end:
            if d.weekday() < 5:
                days_in_month.append(d)
            d += timedelta(days=1)
        if not days_in_month:
            continue
        unit = 0.5 if e.is_half_day else 1.0
        days_value = len(days_in_month) * unit
        et_key = e.event_type.value if hasattr(e.event_type, "value") else str(e.event_type)

        is_company_holiday = (
            e.event_type == WorkingDayEventType.public_holiday
            and e.user_slack_id is None
        )

        if is_company_holiday:
            for d in days_in_month:
                holiday_dates.add(d)
            breakdown["public_holiday"] += days_value
        else:
            # 모든 개인 이벤트는 breakdown에는 표시 — 차감은 LEAVE_TYPES만
            is_relevant = (
                (user_slack_id and e.user_slack_id == user_slack_id)
                or (not user_slack_id and e.user_slack_id is not None)
            )
            if is_relevant:
                breakdown[et_key] = breakdown.get(et_key, 0.0) + days_value
                if et_key in LEAVE_TYPES:
                    leave_days += days_value

    holiday_count = len(holiday_dates)
    company_working_days = total_weekdays - holiday_count

    if user_slack_id:
        working_days = company_working_days - leave_days
    else:
        working_days = company_working_days

    return SuccessResponse(data={
        "year": year,
        "month": month,
        "user_slack_id": user_slack_id,
        "mode": "user" if user_slack_id else "team",
        "total_weekdays": total_weekdays,
        "public_holidays": holiday_count,
        "personal_leave_days": round(leave_days, 1),
        "working_days": round(working_days, 1),
        "company_working_days": company_working_days,
        "breakdown": {k: round(v, 1) for k, v in breakdown.items()},
    })


# ──────────────────────── CRUD ────────────────────────


@router.get("", response_model=SuccessResponse)
async def list_working_days(
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None, ge=1, le=12),
    user_slack_id: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """일정/근무일 이벤트 목록 조회.

    year+month가 주어지면 해당 월에 걸친 이벤트만 반환.
    user_slack_id가 주어지면 해당 사용자 + 전사 공휴일을 함께 반환.
    """
    base_query = select(WorkingDayEvent)

    if year and month:
        _, last_day = calendar.monthrange(year, month)
        first = date_type(year, month, 1)
        last = date_type(year, month, last_day)
        base_query = base_query.where(
            WorkingDayEvent.start_date <= last,
            WorkingDayEvent.end_date >= first,
        )

    if user_slack_id:
        base_query = base_query.where(or_(
            WorkingDayEvent.user_slack_id == user_slack_id,
            WorkingDayEvent.user_slack_id.is_(None),
        ))

    if event_type:
        base_query = base_query.where(WorkingDayEvent.event_type == event_type)

    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    base_query = base_query.order_by(WorkingDayEvent.start_date.desc())
    base_query = base_query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(base_query)
    items = result.scalars().all()

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size,
        items=[WorkingDayEventRead.model_validate(e) for e in items],
    ))


@router.get("/{event_id}", response_model=SuccessResponse)
async def get_working_day(event_id: int, db: AsyncSession = Depends(get_db)):
    event = await db.get(WorkingDayEvent, event_id)
    if not event:
        return ErrorResponse(message="이벤트를 찾을 수 없습니다.")
    return SuccessResponse(data=WorkingDayEventRead.model_validate(event))


@router.post("", response_model=SuccessResponse)
async def create_working_day(
    data: WorkingDayEventCreate, db: AsyncSession = Depends(get_db),
):
    if data.end_date < data.start_date:
        return ErrorResponse(message="종료일은 시작일보다 빠를 수 없습니다.")
    # 공휴일이면 user_slack_id를 강제로 None
    payload = data.model_dump()
    if payload.get("event_type") == WorkingDayEventType.public_holiday.value:
        payload["user_slack_id"] = None
    # start_at/end_at 둘 다 있으면 is_all_day=False 강제 (UI에서 누락해도 보정)
    if payload.get("start_at") and payload.get("end_at"):
        payload["is_all_day"] = False
    payload.setdefault("source", "manual")
    event = WorkingDayEvent(**payload)
    db.add(event)
    await db.flush()
    # sales_activity + hospital+doctor 매칭 시 Activity 자동 upsert
    try:
        await _upsert_activity_for_event(event, db)
    except Exception as e:
        logger.warning(f"[WD] Activity 자동 생성 실패 (event_id={event.id}): {e}")
    # gcal로 push (수동 추가도 양방향 동기화)
    await _try_push_to_gcal(event)
    await db.flush()
    return SuccessResponse(data=WorkingDayEventRead.model_validate(event))


@router.put("/{event_id}", response_model=SuccessResponse)
async def update_working_day(
    event_id: int,
    data: WorkingDayEventUpdate,
    db: AsyncSession = Depends(get_db),
):
    event = await db.get(WorkingDayEvent, event_id)
    if not event:
        return ErrorResponse(message="이벤트를 찾을 수 없습니다.")
    payload = data.model_dump(exclude_unset=True)
    if payload.get("event_type") == WorkingDayEventType.public_holiday.value:
        payload["user_slack_id"] = None
    for k, v in payload.items():
        setattr(event, k, v)
    if event.end_date < event.start_date:
        return ErrorResponse(message="종료일은 시작일보다 빠를 수 없습니다.")
    if event.start_at and event.end_at:
        event.is_all_day = False
    await db.flush()
    # Activity 자동 upsert (변경된 hospital/doctor 반영)
    try:
        await _upsert_activity_for_event(event, db)
    except Exception as e:
        logger.warning(f"[WD] Activity 자동 갱신 실패 (event_id={event.id}): {e}")
    # gcal로 변경사항 push
    await _try_push_to_gcal(event)
    await db.flush()
    return SuccessResponse(data=WorkingDayEventRead.model_validate(event))


@router.delete("/{event_id}", response_model=SuccessResponse)
async def delete_working_day(event_id: int, db: AsyncSession = Depends(get_db)):
    event = await db.get(WorkingDayEvent, event_id)
    if not event:
        return ErrorResponse(message="이벤트를 찾을 수 없습니다.")
    # 먼저 gcal 측 삭제 시도 (DB 삭제 전에 gcal_event_id를 알아야 하므로)
    await _try_delete_from_gcal(event)
    await db.delete(event)
    await db.flush()
    return SuccessResponse(data={"deleted": event_id})


# ─────── Google Calendar 동기화 (수동 트리거) ───────


@router.post("/sync-google", response_model=SuccessResponse)
async def sync_google_calendar(
    user: Optional[str] = Query(None, description="비어있으면 매핑된 모든 사용자 동기화"),
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None, ge=1, le=12),
    db: AsyncSession = Depends(get_db),
):
    """Google Calendar → MOCO 단방향 pull. (양방향 push는 create/update/delete에서 자동)

    year/month 미지정 시 현재 월.
    """
    from app.cc_web_interface.crm.services.google_calendar_sync import (
        pull_user_calendar, get_sync_user_map, seed_korean_public_holidays,
    )
    from datetime import datetime, timezone, timedelta as _td
    from app.config.settings import get_settings
    settings = get_settings()
    if not settings.WORKING_DAY_GCAL_SYNC_ENABLED:
        return ErrorResponse(message="Google Calendar 동기화가 비활성화돼있습니다.")

    user_map = get_sync_user_map()
    if not user_map:
        return ErrorResponse(message="동기화 대상 사용자 매핑이 없습니다.")

    targets = []
    if user:
        if user not in user_map:
            return ErrorResponse(message=f"사용자 '{user}' 매핑을 찾을 수 없습니다.")
        targets = [(user, user_map[user])]
    else:
        targets = list(user_map.items())

    KST = timezone(_td(hours=9))
    now_d = datetime.now(KST).date()
    y = year or now_d.year
    m = month or now_d.month

    results = []
    # 사용자 캘린더 sync
    for name, email in targets:
        try:
            r = await pull_user_calendar(name, email, y, m, db)
            r.update({"kind": "user", "user": name, "email": email, "year": y, "month": m})
            results.append(r)
        except Exception as e:
            logger.exception(f"[WD] sync 실패 user={name}")
            results.append({"kind": "user", "user": name, "error": str(e)})

    # 한국 공휴일 시드 (현재 + 다음 연도)
    for hy in sorted({y, y + 1}):
        try:
            hr = await seed_korean_public_holidays(hy, db)
            hr.update({"kind": "holidays", "year": hy})
            results.append(hr)
        except Exception as e:
            logger.exception(f"[WD] 공휴일 시드 실패 year={hy}")
            results.append({"kind": "holidays", "year": hy, "error": str(e)})

    return SuccessResponse(data={"results": results, "year": y, "month": m})
