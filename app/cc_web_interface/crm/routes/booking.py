"""
미팅 예약 API 라우트
슬롯 생성, 고객 확정 페이지, 캘린더 연동
"""

import logging
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import (
    MeetingBooking, Contact, Activity, ActivityType, now_kst,
)
from app.cc_web_interface.crm.schemas import (
    MeetingBookingCreate, MeetingBookingRead,
    PaginatedResponse, SuccessResponse, ErrorResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/booking", tags=["미팅 예약"])


@router.post("", response_model=SuccessResponse)
async def create_booking(
    data: MeetingBookingCreate,
    host_slack_id: str = Query(..., description="담당자 Slack ID"),
    db: AsyncSession = Depends(get_db),
):
    """미팅 예약 슬롯을 생성하고 토큰을 반환합니다."""
    contact = await db.get(Contact, data.contact_id)
    if not contact:
        return ErrorResponse(message="연락처를 찾을 수 없습니다.")

    token = uuid.uuid4().hex[:16]

    # 담당자 이메일 조회
    host_email = None
    try:
        from app.cc_utils.slack_helper import get_user_info
        user_info = get_user_info(host_slack_id)
        host_email = user_info.get("email") if user_info else None
    except Exception:
        pass

    booking = MeetingBooking(
        token=token,
        host_slack_id=host_slack_id,
        host_email=host_email,
        contact_id=contact.id,
        contact_email=contact.email,
        contact_name=f"{contact.first_name} {contact.last_name or ''}".strip(),
        title=data.title or "미팅",
        duration_minutes=data.duration_minutes,
        slots=[s.model_dump() for s in data.slots],
        message=data.message,
    )
    db.add(booking)
    await db.flush()

    logger.info(f"[BOOKING] Created booking token={token} for contact={contact.id}")

    return SuccessResponse(data={
        "booking_id": booking.id,
        "token": token,
        "booking_url": f"/api/crm/booking/{token}",
        "contact_name": booking.contact_name,
        "slots_count": len(data.slots),
    })


@router.get("/{token}")
async def booking_page(token: str, db: AsyncSession = Depends(get_db)):
    """고객이 보는 미팅 예약 페이지"""
    result = await db.execute(
        select(MeetingBooking).where(MeetingBooking.token == token)
    )
    booking = result.scalar_one_or_none()

    if not booking:
        return HTMLResponse("<h1>유효하지 않은 링크입니다.</h1>", status_code=404)

    if booking.status == "confirmed":
        slot = booking.selected_slot or {}
        return HTMLResponse(f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>미팅 확정</title>
<style>body{{font-family:-apple-system,sans-serif;background:#f8fafc;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0}}
.card{{background:white;border-radius:16px;padding:2.5rem;max-width:500px;width:90%;box-shadow:0 4px 24px rgba(0,0,0,0.08);text-align:center}}
.check{{font-size:3rem;margin-bottom:1rem}}h2{{color:#1e293b;margin-bottom:0.5rem}}p{{color:#64748b;font-size:0.95rem}}</style></head>
<body><div class="card"><div class="check">✅</div><h2>미팅이 확정되었습니다</h2>
<p><strong>{booking.title}</strong></p><p>{slot.get('label', slot.get('start', ''))}</p>
<p style="margin-top:1rem;color:#94a3b8;font-size:0.85rem">캘린더 초대가 발송됩니다.</p></div></body></html>""")

    if booking.status in ("expired", "cancelled"):
        return HTMLResponse("""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>만료된 링크</title>
<style>body{font-family:-apple-system,sans-serif;background:#f8fafc;display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0}
.card{background:white;border-radius:16px;padding:2.5rem;max-width:500px;width:90%;box-shadow:0 4px 24px rgba(0,0,0,0.08);text-align:center}
h2{color:#1e293b}</style></head>
<body><div class="card"><div style="font-size:3rem;margin-bottom:1rem">⏰</div><h2>이 링크는 만료되었습니다</h2>
<p style="color:#64748b">담당자에게 새 링크를 요청해주세요.</p></div></body></html>""", status_code=410)

    # 슬롯 선택 페이지 렌더링
    slots_html = ""
    for i, slot in enumerate(booking.slots or []):
        label = slot.get("label", slot.get("start", f"옵션 {i+1}"))
        slots_html += f"""
        <button onclick="confirmSlot({i})" class="slot">
            <span class="time">{label}</span>
            <span class="dur">{booking.duration_minutes}분</span>
        </button>"""

    message_html = f'<p class="msg">{booking.message}</p>' if booking.message else ""

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{booking.title} - 미팅 예약</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f8fafc;display:flex;justify-content:center;padding:2rem}}
.card{{background:white;border-radius:16px;padding:2.5rem;max-width:500px;width:100%;box-shadow:0 4px 24px rgba(0,0,0,0.08)}}
h2{{color:#1e293b;font-size:1.3rem;margin-bottom:0.25rem}}
.sub{{color:#64748b;font-size:0.9rem;margin-bottom:1.5rem}}
.msg{{background:#f1f5f9;border-radius:8px;padding:0.75rem;color:#475569;font-size:0.85rem;margin-bottom:1.5rem}}
.slots{{display:flex;flex-direction:column;gap:0.75rem}}
.slot{{display:flex;justify-content:space-between;align-items:center;padding:1rem 1.25rem;border:2px solid #e2e8f0;border-radius:12px;cursor:pointer;background:white;font-size:0.95rem;transition:all 0.2s}}
.slot:hover{{border-color:#3b82f6;background:#eff6ff}}
.time{{font-weight:600;color:#1e293b}}.dur{{color:#94a3b8;font-size:0.85rem}}
.loading{{display:none;text-align:center;padding:2rem;color:#64748b}}
</style></head>
<body><div class="card">
<h2>{booking.title}</h2>
<p class="sub">{booking.contact_name}님, 편한 시간을 선택해주세요.</p>
{message_html}
<div class="slots" id="slots">{slots_html}</div>
<div class="loading" id="loading">⏳ 예약 확정 중...</div>
</div>
<script>
async function confirmSlot(index) {{
    document.getElementById('slots').style.display='none';
    document.getElementById('loading').style.display='block';
    try {{
        const resp = await fetch('/api/crm/booking/{token}/confirm', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{slot_index: index}})
        }});
        const data = await resp.json();
        if (data.success) {{
            location.reload();
        }} else {{
            alert(data.message || '예약 실패');
            document.getElementById('slots').style.display='flex';
            document.getElementById('loading').style.display='none';
        }}
    }} catch(e) {{
        alert('네트워크 오류');
        document.getElementById('slots').style.display='flex';
        document.getElementById('loading').style.display='none';
    }}
}}
</script></body></html>""")


@router.post("/{token}/confirm", response_model=SuccessResponse)
async def confirm_booking(
    token: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """고객이 시간 슬롯을 선택하여 미팅을 확정합니다."""
    result = await db.execute(
        select(MeetingBooking).where(MeetingBooking.token == token)
    )
    booking = result.scalar_one_or_none()

    if not booking:
        return ErrorResponse(message="유효하지 않은 링크입니다.")
    if booking.status != "pending":
        return ErrorResponse(message="이미 처리된 예약입니다.")

    slot_index = body.get("slot_index", 0)
    slots = booking.slots or []
    if slot_index >= len(slots):
        return ErrorResponse(message="유효하지 않은 시간입니다.")

    selected = slots[slot_index]
    booking.selected_slot = selected
    booking.status = "confirmed"
    booking.confirmed_at = now_kst()

    # CRM Activity 기록
    activity = Activity(
        type=ActivityType.meeting,
        subject=f"미팅 확정: {booking.title}",
        body=f"시간: {selected.get('label', selected.get('start', ''))}\n담당자: {booking.host_email or booking.host_slack_id}",
        contact_id=booking.contact_id,
        extra_data={
            "booking_id": booking.id,
            "selected_slot": selected,
            "duration_minutes": booking.duration_minutes,
            "auto_booking": True,
        },
    )
    db.add(activity)

    # Google Calendar 이벤트 생성 시도
    try:
        from app.cc_tools.google_calendar.auth_helper import get_calendar_service
        service = get_calendar_service(slack_user_id=booking.host_slack_id)

        event = {
            "summary": booking.title,
            "start": {"dateTime": selected["start"], "timeZone": "Asia/Seoul"},
            "end": {"dateTime": selected["end"], "timeZone": "Asia/Seoul"},
            "attendees": [],
        }
        if booking.contact_email:
            event["attendees"].append({"email": booking.contact_email})
        if booking.host_email:
            event["attendees"].append({"email": booking.host_email})

        created = service.events().insert(
            calendarId="primary",
            body=event,
            sendUpdates="all",
        ).execute()

        booking.calendar_event_id = created.get("id")
        logger.info(f"[BOOKING] Calendar event created: {created.get('id')}")
    except Exception as e:
        logger.warning(f"[BOOKING] Calendar event creation failed: {e}")

    # Slack 알림 시도
    try:
        from app.cc_utils.slack_helper import send_dm
        label = selected.get("label", selected.get("start", ""))
        send_dm(
            booking.host_slack_id,
            f"📅 미팅이 확정되었습니다!\n"
            f"• 고객: {booking.contact_name} ({booking.contact_email})\n"
            f"• 제목: {booking.title}\n"
            f"• 시간: {label}\n"
            f"• 캘린더에 자동 등록되었습니다."
        )
    except Exception as e:
        logger.warning(f"[BOOKING] Slack notification failed: {e}")

    await db.commit()

    logger.info(f"[BOOKING] Confirmed: token={token}, slot={selected}")

    return SuccessResponse(data={
        "booking_id": booking.id,
        "status": "confirmed",
        "selected_slot": selected,
    })


@router.get("/list/all", response_model=SuccessResponse)
async def list_bookings(
    status: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """미팅 예약 목록 조회"""
    query = select(MeetingBooking)
    if status:
        query = query.where(MeetingBooking.status == status)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(MeetingBooking.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    bookings = result.scalars().all()

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size,
        items=[MeetingBookingRead.model_validate(b) for b in bookings],
    ))
