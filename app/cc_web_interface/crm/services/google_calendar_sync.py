"""
Working Day Event ↔ Google Calendar 양방향 동기화.

- pull_user_calendar: gcal → MOCO (해당 사용자/월의 이벤트를 MOCO에 upsert)
- push_event_to_gcal: MOCO → gcal (단일 이벤트 push, gcal_event_id 채움)
- delete_event_from_gcal: MOCO 삭제에 따른 gcal 이벤트 삭제
- run_full_sync: 양쪽 사용자 모두 sync (스케줄러용)

Service Account의 Domain-Wide Delegation을 사용하므로
사용자별 이메일만 알면 별도 OAuth 없이 동작.
"""

import calendar as _cal
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_agents.event_classifier import call_event_classifier
from app.cc_tools.google_calendar.auth_helper import get_calendar_service_by_email
from app.cc_web_interface.crm.models import WorkingDayEvent, WorkingDayEventType
from app.config.settings import get_settings

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


# ──────────────────── 한국 공휴일 화이트/블랙리스트 ────────────────────

# title에 이 키워드 중 하나가 있으면 공휴일 후보
_HOLIDAY_INCLUDE = [
    "새해", "신정", "설날", "삼일절", "어린이날", "부처님오신날", "현충일",
    "광복절", "추석", "개천절", "한글날", "크리스마스", "성탄",
    "노동절", "근로자의 날", "선거일", "임시공휴일", "쉬는 날",
]
# 위 키워드와 같이 있어도 제외할 패턴 (기념일, 안 쉬는 날)
_HOLIDAY_EXCLUDE = [
    "이브", "어버이날", "스승의 날", "스승의날", "식목일", "제헌절",
    "그믐", "국군의 날", "국군의날", "상공의 날", "발명의 날",
]

KOREAN_HOLIDAY_CALENDAR_ID = "ko.south_korea#holiday@group.v.calendar.google.com"


def is_korean_public_holiday(title: str) -> bool:
    """Google 한국 공휴일 캘린더의 이벤트 제목으로 진짜 쉬는 날인지 판정."""
    if not title:
        return False
    if any(ex in title for ex in _HOLIDAY_EXCLUDE):
        return False
    return any(inc in title for inc in _HOLIDAY_INCLUDE)


# ──────────────────── 사용자 매핑 ────────────────────


def get_sync_user_map() -> Dict[str, str]:
    """{사용자명: 이메일} 매핑을 settings에서 읽어 반환."""
    settings = get_settings()
    raw = settings.WORKING_DAY_GCAL_SYNC_USERS or "{}"
    try:
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"[WD_SYNC] WORKING_DAY_GCAL_SYNC_USERS 파싱 실패: {e}")
        return {}


# ──────────────────── Pull (Google → MOCO) ────────────────────


async def pull_user_calendar(
    user_name: str,
    user_email: str,
    year: int,
    month: int,
    db: AsyncSession,
) -> Dict[str, int]:
    """해당 사용자의 Google Calendar에서 한 달치 이벤트를 가져와 MOCO에 upsert.

    삭제된 이벤트(gcal에 없지만 DB에 gcal_event_id가 남아있는 것)는 DB에서 제거.

    Returns: {created, updated, skipped, deleted}
    """
    service = get_calendar_service_by_email(user_email)

    _, last_day = _cal.monthrange(year, month)
    first = date(year, month, 1)
    last = date(year, month, last_day)

    # gcal API: timeMin/timeMax는 RFC3339. 종일 이벤트도 포함되도록 한 달 + 여유.
    time_min = datetime(year, month, 1, tzinfo=KST).isoformat()
    next_month_first = (date(year, month, last_day) + timedelta(days=1))
    time_max = datetime(
        next_month_first.year, next_month_first.month, next_month_first.day,
        tzinfo=KST,
    ).isoformat()

    try:
        events_result = service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
        ).execute()
    except Exception as e:
        logger.error(f"[WD_SYNC] gcal events.list 실패 (user={user_name}): {e}")
        raise

    items = events_result.get("items", [])

    pulled_ids = set()
    created = updated = skipped = 0

    # 1단계: 분류가 필요한 이벤트를 먼저 추려서 LLM 호출 (DB 트랜잭션 밖)
    # → 트랜잭션이 길게 열려 lock 잡는 것을 방지
    classifications: Dict[str, Tuple[str, bool]] = {}
    for ge in items:
        gcal_id = ge.get("id")
        if not gcal_id:
            continue
        title = ge.get("summary", "") or ""
        description = ge.get("description", "") or ""
        title_for_db = title or "(제목 없음)"
        # 기존 row 조회 — title·note 변동 없으면 LLM skip
        existing_q = await db.execute(
            select(WorkingDayEvent).where(WorkingDayEvent.gcal_event_id == gcal_id)
        )
        existing = existing_q.scalar_one_or_none()
        if existing:
            same_title = (existing.title or "") == title_for_db
            same_note = (existing.note or "") == (description or "")
            if same_title and same_note:
                et = existing.event_type.value if hasattr(existing.event_type, "value") else str(existing.event_type)
                classifications[gcal_id] = (et, bool(existing.is_half_day))
                continue
        # 신규 또는 변동 → LLM 호출
        try:
            classifications[gcal_id] = await call_event_classifier(title, description)
        except Exception as e:
            logger.warning(f"[WD_SYNC] 분류 실패 (gcal_id={gcal_id}): {e}")
            classifications[gcal_id] = ("other", False)

    # 2단계: 분류 결과로 빠른 upsert (LLM 호출 없음 → 트랜잭션 짧음)
    for ge in items:
        start = ge.get("start", {})
        end = ge.get("end", {})
        is_all_day = "date" in start

        title = ge.get("summary", "") or ""
        description = ge.get("description", "") or ""

        start_at_val = None
        end_at_val = None
        try:
            if is_all_day:
                start_d = date.fromisoformat(start["date"])
                end_raw = date.fromisoformat(end["date"])
                end_d = end_raw - timedelta(days=1)  # gcal 종일 end는 exclusive
            else:
                # 시간지정 이벤트: KST로 변환 후 날짜 추출 (UTC면 [:10]이 하루 어긋남)
                start_iso = start["dateTime"].replace("Z", "+00:00")
                end_iso = end["dateTime"].replace("Z", "+00:00")
                start_dt = datetime.fromisoformat(start_iso)
                end_dt = datetime.fromisoformat(end_iso)
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=KST)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=KST)
                start_kst = start_dt.astimezone(KST)
                end_kst = end_dt.astimezone(KST)
                start_at_val = start_kst
                end_at_val = end_kst
                start_d = start_kst.date()
                end_d = end_kst.date()
        except Exception:
            skipped += 1
            continue
        if end_d < start_d:
            skipped += 1
            continue

        gcal_id = ge.get("id")
        if not gcal_id:
            skipped += 1
            continue
        pulled_ids.add(gcal_id)

        title_for_db = title or "(제목 없음)"

        # upsert
        existing_q = await db.execute(
            select(WorkingDayEvent).where(WorkingDayEvent.gcal_event_id == gcal_id)
        )
        existing = existing_q.scalar_one_or_none()
        now = datetime.now(KST)

        # 1단계에서 미리 분류해둔 결과 사용 (LLM 호출 X — 트랜잭션 짧게 유지)
        et, is_half = classifications.get(gcal_id, ("other", False))

        if existing:
            existing.event_type = et
            existing.start_date = start_d
            existing.end_date = end_d
            existing.start_at = start_at_val
            existing.end_at = end_at_val
            existing.is_all_day = is_all_day
            existing.user_slack_id = user_name
            existing.title = title_for_db
            existing.note = description
            existing.is_half_day = is_half
            existing.gcal_user_email = user_email
            existing.last_synced_at = now
            updated += 1
        else:
            ev = WorkingDayEvent(
                event_type=et,
                start_date=start_d,
                end_date=end_d,
                start_at=start_at_val,
                end_at=end_at_val,
                is_all_day=is_all_day,
                user_slack_id=user_name,
                title=title_for_db,
                note=description,
                is_half_day=is_half,
                source="gcal",
                gcal_event_id=gcal_id,
                gcal_user_email=user_email,
                last_synced_at=now,
            )
            db.add(ev)
            created += 1

    # gcal에서 사라진 이벤트 정리: DB에 같은 사용자/기간/gcal_event_id 있는 것 중,
    # 이번 pull에 안 들어온 ID들은 삭제
    stale_q = await db.execute(
        select(WorkingDayEvent).where(
            WorkingDayEvent.gcal_user_email == user_email,
            WorkingDayEvent.gcal_event_id.is_not(None),
            WorkingDayEvent.start_date <= last,
            WorkingDayEvent.end_date >= first,
        )
    )
    deleted = 0
    for ev in stale_q.scalars().all():
        if ev.gcal_event_id not in pulled_ids:
            await db.delete(ev)
            deleted += 1

    await db.commit()
    logger.info(
        f"[WD_SYNC] pull {user_name}({user_email}) {year}-{month}: "
        f"created={created} updated={updated} deleted={deleted} skipped={skipped}"
    )
    return {"created": created, "updated": updated, "skipped": skipped, "deleted": deleted}


# ──────────────────── Push (MOCO → Google) ────────────────────


def _event_to_gcal_body(event: WorkingDayEvent) -> dict:
    """WorkingDayEvent를 gcal API body로 변환.

    is_all_day=False + start_at/end_at 있으면 시간지정, 아니면 종일.
    """
    et_str = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
    type_label = {
        "vacation": "🏖️ 휴가",
        "conference": "🎓 학회",
        "training": "📚 교육",
        "sales_activity": "💼 영업 활동",
        "other": "📌 일정",
    }.get(et_str, "📌 일정")
    title = event.title
    if event.is_half_day and "반차" not in title:
        title = f"{title} (반차)"

    use_datetime = (
        getattr(event, "is_all_day", True) is False
        and event.start_at is not None
        and event.end_at is not None
    )

    if use_datetime:
        # 시간지정 이벤트 — KST tz 부착 후 RFC3339
        start_dt = event.start_at if event.start_at.tzinfo else event.start_at.replace(tzinfo=KST)
        end_dt = event.end_at if event.end_at.tzinfo else event.end_at.replace(tzinfo=KST)
        start_field = {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Seoul"}
        end_field = {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Seoul"}
    else:
        # 종일 이벤트 — gcal은 end가 exclusive이므로 +1
        start_field = {"date": event.start_date.isoformat()}
        end_field = {"date": (event.end_date + timedelta(days=1)).isoformat()}

    body = {
        "summary": title,
        "description": (event.note or "") + f"\n\n— {type_label} (MOCO 동기화)",
        "start": start_field,
        "end": end_field,
        "extendedProperties": {
            "private": {
                "moco_source": "working_day",
                "moco_event_type": et_str,
                "moco_event_id": str(event.id) if event.id else "",
            }
        },
    }
    return body


async def push_event_to_gcal(event: WorkingDayEvent) -> Optional[str]:
    """단일 MOCO 이벤트를 사용자 캘린더에 push.

    공휴일(public_holiday)이거나 user_slack_id가 없으면 push 안 함.
    Returns gcal_event_id 또는 None.
    """
    et = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
    if et == "public_holiday":
        return None
    if not event.user_slack_id:
        return None

    user_map = get_sync_user_map()
    email = user_map.get(event.user_slack_id)
    if not email:
        logger.warning(f"[WD_SYNC] 사용자 {event.user_slack_id}의 이메일을 찾을 수 없음")
        return None

    service = get_calendar_service_by_email(email)
    body = _event_to_gcal_body(event)

    try:
        if event.gcal_event_id:
            result = service.events().update(
                calendarId="primary",
                eventId=event.gcal_event_id,
                body=body,
            ).execute()
        else:
            result = service.events().insert(
                calendarId="primary",
                body=body,
            ).execute()
        gid = result.get("id")
        event.gcal_event_id = gid
        event.gcal_user_email = email
        event.last_synced_at = datetime.now(KST)
        return gid
    except Exception as e:
        logger.error(f"[WD_SYNC] push 실패 (event={event.id}, user={event.user_slack_id}): {e}")
        return None


async def delete_event_from_gcal(event: WorkingDayEvent) -> bool:
    """MOCO 삭제에 따른 gcal 이벤트 제거. 매핑이 없으면 noop."""
    if not event.gcal_event_id or not event.gcal_user_email:
        return False
    try:
        service = get_calendar_service_by_email(event.gcal_user_email)
        service.events().delete(
            calendarId="primary",
            eventId=event.gcal_event_id,
        ).execute()
        return True
    except Exception as e:
        # 404(이미 삭제) 등은 무시
        msg = str(e)
        if "404" in msg or "Not Found" in msg or "deleted" in msg.lower():
            return True
        logger.warning(f"[WD_SYNC] gcal 삭제 실패 (event={event.id}): {e}")
        return False


# ──────────────────── 한국 공휴일 시드 ────────────────────


async def seed_korean_public_holidays(
    year: int,
    db: AsyncSession,
    impersonate_email: Optional[str] = None,
) -> Dict[str, int]:
    """Google 한국 공휴일 캘린더에서 해당 연도의 공휴일을 가져와 MOCO에 upsert.

    user_slack_id=NULL, source='gcal_holiday', gcal_event_id에 Google 이벤트 ID 저장.
    제목 기반 화이트리스트로 진짜 쉬는 날만 import.

    impersonate_email: Service Account가 어떤 사용자로 위임 받을지. 매핑된 첫 사용자 사용.
    """
    user_map = get_sync_user_map()
    if not impersonate_email:
        if not user_map:
            return {"created": 0, "updated": 0, "skipped": 0, "deleted": 0, "error": "no_user_map"}
        impersonate_email = list(user_map.values())[0]

    service = get_calendar_service_by_email(impersonate_email)

    time_min = datetime(year, 1, 1, tzinfo=KST).isoformat()
    time_max = datetime(year, 12, 31, 23, 59, tzinfo=KST).isoformat()

    try:
        r = service.events().list(
            calendarId=KOREAN_HOLIDAY_CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            maxResults=100,
        ).execute()
    except Exception as e:
        logger.error(f"[WD_SYNC] 한국 공휴일 캘린더 조회 실패: {e}")
        return {"created": 0, "updated": 0, "skipped": 0, "deleted": 0, "error": str(e)}

    items = r.get("items", [])
    pulled_ids = set()
    created = updated = skipped = 0
    now = datetime.now(KST)

    for ge in items:
        title = ge.get("summary", "") or ""
        if not is_korean_public_holiday(title):
            skipped += 1
            continue

        start = ge.get("start", {})
        end = ge.get("end", {})
        if "date" not in start:
            skipped += 1
            continue
        try:
            start_d = date.fromisoformat(start["date"])
            end_raw = date.fromisoformat(end["date"])
        except Exception:
            skipped += 1
            continue
        end_d = end_raw - timedelta(days=1)

        gcal_id = ge.get("id")
        if not gcal_id:
            skipped += 1
            continue
        pulled_ids.add(gcal_id)

        existing_q = await db.execute(
            select(WorkingDayEvent).where(WorkingDayEvent.gcal_event_id == gcal_id)
        )
        existing = existing_q.scalar_one_or_none()

        if existing:
            existing.event_type = WorkingDayEventType.public_holiday.value
            existing.start_date = start_d
            existing.end_date = end_d
            existing.user_slack_id = None
            existing.title = title
            existing.note = "한국 공휴일 (자동)"
            existing.is_half_day = False
            existing.source = "gcal_holiday"
            existing.last_synced_at = now
            updated += 1
        else:
            ev = WorkingDayEvent(
                event_type=WorkingDayEventType.public_holiday.value,
                start_date=start_d,
                end_date=end_d,
                user_slack_id=None,
                title=title,
                note="한국 공휴일 (자동)",
                is_half_day=False,
                source="gcal_holiday",
                gcal_event_id=gcal_id,
                gcal_user_email=None,
                last_synced_at=now,
            )
            db.add(ev)
            created += 1

    # 같은 연도의 source='gcal_holiday' 중 이번에 안 들어온 것은 삭제 (취소된 공휴일)
    year_first = date(year, 1, 1)
    year_last = date(year, 12, 31)
    stale_q = await db.execute(
        select(WorkingDayEvent).where(
            WorkingDayEvent.source == "gcal_holiday",
            WorkingDayEvent.start_date >= year_first,
            WorkingDayEvent.start_date <= year_last,
        )
    )
    deleted = 0
    for ev in stale_q.scalars().all():
        if ev.gcal_event_id and ev.gcal_event_id not in pulled_ids:
            await db.delete(ev)
            deleted += 1

    await db.commit()
    logger.info(
        f"[WD_SYNC] 한국 공휴일 {year}: created={created} updated={updated} "
        f"deleted={deleted} skipped={skipped}"
    )
    return {"created": created, "updated": updated, "skipped": skipped, "deleted": deleted}


# ──────────────────── 전체 sync (스케줄러용) ────────────────────


async def run_full_sync(months_ahead: int = 1) -> List[Dict]:
    """모든 매핑된 사용자에 대해 현재 월 + 향후 N개월 sync.
    + 현재 연도 + 다음 연도의 한국 공휴일 시드.

    DB 세션은 자체적으로 만든다.
    """
    from app.cc_web_interface.crm.database import async_session

    user_map = get_sync_user_map()
    if not user_map:
        return []

    today = datetime.now(KST).date()
    months: List[Tuple[int, int]] = []
    for delta in range(months_ahead + 1):
        m = today.month + delta
        y = today.year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        months.append((y, m))

    results: List[Dict] = []
    async with async_session() as db:
        # 1. 한국 공휴일 시드 (현재 + 다음 연도)
        for y in {today.year, today.year + 1}:
            try:
                hr = await seed_korean_public_holidays(y, db)
                hr.update({"kind": "holidays", "year": y})
                results.append(hr)
            except Exception as e:
                logger.error(f"[WD_SYNC] 공휴일 시드 {y} 실패: {e}")
                results.append({"kind": "holidays", "year": y, "error": str(e)})

        # 2. 사용자별 캘린더 sync
        for user_name, email in user_map.items():
            for (y, m) in months:
                try:
                    r = await pull_user_calendar(user_name, email, y, m, db)
                    r.update({"kind": "user", "user": user_name, "year": y, "month": m})
                    results.append(r)
                except Exception as e:
                    logger.error(f"[WD_SYNC] {user_name} {y}-{m} sync 실패: {e}")
                    results.append({"kind": "user", "user": user_name, "year": y, "month": m, "error": str(e)})
    return results
