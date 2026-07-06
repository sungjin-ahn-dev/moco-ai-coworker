"""
이메일 시퀀스 프로세서
시퀀스 등록, 일시정지, 예정 이메일 처리 + Gmail 실제 발송
"""

import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.models import (
    EmailSequence, EmailEnrollment, EnrollmentStatus,
    SequenceStatus, Activity, ActivityType, Contact,
    now_kst,
)

logger = logging.getLogger(__name__)


async def enroll_contact(
    sequence_id: int,
    contact_id: int,
    db: AsyncSession,
) -> EmailEnrollment:
    """
    연락처를 이메일 시퀀스에 등록한다.

    Args:
        sequence_id: 시퀀스 ID
        contact_id: 연락처 ID
        db: 비동기 DB 세션

    Returns:
        생성된 등록 레코드

    Raises:
        ValueError: 시퀀스나 연락처를 찾을 수 없을 때
    """
    sequence = await db.get(EmailSequence, sequence_id)
    if not sequence:
        raise ValueError(f"시퀀스를 찾을 수 없습니다: {sequence_id}")
    if sequence.status != SequenceStatus.active:
        raise ValueError(f"비활성 시퀀스입니다: {sequence_id}")

    contact = await db.get(Contact, contact_id)
    if not contact:
        raise ValueError(f"연락처를 찾을 수 없습니다: {contact_id}")

    # 이미 활성 등록이 있는지 확인
    existing = await db.execute(
        select(EmailEnrollment)
        .where(EmailEnrollment.sequence_id == sequence_id)
        .where(EmailEnrollment.contact_id == contact_id)
        .where(EmailEnrollment.status == EnrollmentStatus.active)
    )
    if existing.scalar_one_or_none():
        raise ValueError("이미 해당 시퀀스에 등록되어 있습니다.")

    steps = sequence.steps or []
    first_delay = steps[0].get("delay_days", 0) if steps else 0

    enrollment = EmailEnrollment(
        sequence_id=sequence_id,
        contact_id=contact_id,
        current_step=0,
        status=EnrollmentStatus.active,
        next_send_at=now_kst() + timedelta(days=first_delay),
    )
    db.add(enrollment)
    await db.flush()

    logger.info(
        "[CRM Sequence] 등록 완료 sequence=%d, contact=%d, enrollment=%d",
        sequence_id, contact_id, enrollment.id,
    )
    return enrollment


async def pause_enrollment(
    enrollment_id: int,
    db: AsyncSession,
) -> EmailEnrollment:
    """
    시퀀스 등록을 일시정지한다.

    Args:
        enrollment_id: 등록 ID
        db: 비동기 DB 세션

    Returns:
        업데이트된 등록 레코드
    """
    enrollment = await db.get(EmailEnrollment, enrollment_id)
    if not enrollment:
        raise ValueError(f"등록을 찾을 수 없습니다: {enrollment_id}")

    enrollment.status = EnrollmentStatus.paused
    enrollment.next_send_at = None
    await db.flush()

    logger.info("[CRM Sequence] 일시정지 enrollment=%d", enrollment_id)
    return enrollment


def _send_email_via_gmail(to_email: str, subject: str, body: str, contact: Contact,
                          tracking_id: str = None) -> bool:
    """
    Gmail API로 실제 이메일을 발송한다.
    템플릿 변수({{first_name}} 등)를 치환한 후 발송.

    Args:
        to_email: 수신자 이메일
        subject: 제목 (변수 포함 가능)
        body: 본문 (변수 포함 가능)
        contact: 연락처 객체 (변수 치환용)

    Returns:
        bool: 발송 성공 여부
    """
    try:
        from app.cc_tools.gmail.auth_helper import get_gmail_service
        import base64
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        # 템플릿 변수 치환
        variables = {
            "first_name": contact.first_name or "",
            "last_name": contact.last_name or "",
            "full_name": f"{contact.first_name or ''} {contact.last_name or ''}".strip(),
            "email": contact.email or "",
            "phone": contact.phone or "",
        }
        for key, value in variables.items():
            placeholder = "{{" + key + "}}"
            subject = subject.replace(placeholder, value)
            body = body.replace(placeholder, value)

        # 추적 코드 삽입
        if tracking_id:
            try:
                from app.config.settings import get_settings
                from app.cc_web_interface.crm.routes.tracking import inject_tracking
                settings = get_settings()
                public_url = getattr(settings, 'WEB_PUBLIC_URL', '') or ''
                if public_url:
                    base_url = public_url.rstrip('/')
                else:
                    import socket
                    local_ip = socket.gethostbyname(socket.gethostname())
                    base_url = f"https://{local_ip}:8000"
                body = inject_tracking(body, tracking_id, base_url)
            except Exception as te:
                logger.warning(f"[CRM Sequence] 추적 코드 삽입 실패: {te}")

        # Gmail 서비스 (기본 계정으로 발송)
        service = get_gmail_service()

        # 이메일 생성
        message = MIMEMultipart('alternative')
        message['to'] = to_email
        message['subject'] = subject

        # HTML 여부 자동 감지
        if '<html' in body.lower() or '<p>' in body.lower() or '<br' in body.lower():
            msg_part = MIMEText(body, 'html', 'utf-8')
        else:
            msg_part = MIMEText(body, 'plain', 'utf-8')
        message.attach(msg_part)

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        # 발송
        service.users().messages().send(
            userId='me',
            body={'raw': raw}
        ).execute()

        logger.info(f"[CRM Sequence] Gmail 발송 성공: {to_email} - {subject}")
        return True

    except Exception as e:
        logger.error(f"[CRM Sequence] Gmail 발송 실패: {to_email} - {e}")
        return False


async def process_pending_emails(db: AsyncSession) -> int:
    """
    예정된 이메일을 처리한다.
    스케줄러에서 주기적으로 호출한다.

    Args:
        db: 비동기 DB 세션

    Returns:
        처리된 이메일 수
    """
    now = now_kst()
    result = await db.execute(
        select(EmailEnrollment)
        .where(EmailEnrollment.status == EnrollmentStatus.active)
        .where(EmailEnrollment.next_send_at <= now)
    )
    enrollments = result.scalars().all()
    processed = 0

    for enrollment in enrollments:
        try:
            await _process_enrollment_step(enrollment, db)
            processed += 1
        except Exception as e:
            logger.error(
                "[CRM Sequence] 처리 실패 enrollment=%d: %s",
                enrollment.id, e,
            )

    if processed > 0:
        logger.info("[CRM Sequence] %d건 이메일 처리 완료", processed)

    return processed


async def _check_tracking_condition(
    condition: str, enrollment_id: int, contact_id: int, db: AsyncSession
) -> bool:
    """이메일 추적 조건 확인"""
    try:
        from app.cc_web_interface.crm.models import EmailTracking, MeetingBooking
        # 해당 enrollment의 가장 최근 tracking 조회
        result = await db.execute(
            select(EmailTracking)
            .where(EmailTracking.enrollment_id == enrollment_id)
            .order_by(EmailTracking.sent_at.desc())
            .limit(1)
        )
        tracking = result.scalar_one_or_none()

        if condition == "on_open":
            return tracking is not None and tracking.open_count > 0
        elif condition == "on_no_open":
            return tracking is None or tracking.open_count == 0
        elif condition == "on_click":
            return tracking is not None and tracking.click_count > 0
        elif condition == "on_no_click":
            return tracking is None or tracking.click_count == 0
        elif condition == "on_reply":
            return tracking is not None and tracking.replied
        elif condition == "on_no_reply":
            return tracking is None or not tracking.replied
        elif condition == "on_form_submit":
            # 해당 연락처의 최근 폼 제출 여부
            from app.cc_web_interface.crm.models import FormSubmission
            sub = await db.execute(
                select(FormSubmission)
                .where(FormSubmission.contact_id == contact_id)
                .order_by(FormSubmission.submitted_at.desc())
                .limit(1)
            )
            return sub.scalar_one_or_none() is not None
        elif condition == "on_meeting_booked":
            # 해당 연락처의 미팅 예약 확정 여부
            booking = await db.execute(
                select(MeetingBooking)
                .where(MeetingBooking.contact_id == contact_id)
                .where(MeetingBooking.status == "confirmed")
                .order_by(MeetingBooking.confirmed_at.desc())
                .limit(1)
            )
            return booking.scalar_one_or_none() is not None
    except Exception as e:
        logger.warning(f"[CRM Sequence] 조건 확인 실패: {condition}, {e}")
    return False


async def _execute_step_actions(
    actions: list, contact, enrollment, db: AsyncSession
) -> None:
    """분기 액션 실행"""
    for action in actions:
        action_type = action.get("type", "")
        config = action.get("config", {})

        try:
            if action_type == "create_task":
                from app.cc_web_interface.crm.models import CRMTask, TaskStatus, TaskPriority
                due_days = config.get("due_days", 3)
                task = CRMTask(
                    title=config.get("title", f"시퀀스 팔로업 - {contact.first_name}"),
                    contact_id=contact.id,
                    status=TaskStatus.todo,
                    priority=TaskPriority.high if config.get("priority") == "high" else TaskPriority.medium,
                    due_date=now_kst() + timedelta(days=due_days),
                )
                db.add(task)
                logger.info(f"[CRM Sequence] 태스크 생성: {task.title}")

            elif action_type == "add_tag":
                tag = config.get("tag")
                if tag and contact:
                    tags = contact.tags or []
                    if tag not in tags:
                        contact.tags = tags + [tag]
                    logger.info(f"[CRM Sequence] 태그 추가: {tag}")

            elif action_type == "change_lead_score":
                delta = config.get("delta", 0)
                if contact:
                    contact.lead_score = (contact.lead_score or 0) + delta
                    logger.info(f"[CRM Sequence] 리드 점수 변경: {delta:+d} → {contact.lead_score}")

            elif action_type == "notify_slack":
                try:
                    from app.cc_utils.slack_helper import send_dm
                    msg = config.get("message", f"시퀀스 알림: {contact.first_name} {contact.last_name or ''}")
                    owner = contact.owner_slack_id or enrollment.sequence.owner_slack_id if hasattr(enrollment, 'sequence') else None
                    if owner:
                        send_dm(owner, msg)
                except Exception:
                    pass

            elif action_type == "end":
                enrollment.status = EnrollmentStatus.completed
                enrollment.completed_at = now_kst()
                enrollment.next_send_at = None
                logger.info(f"[CRM Sequence] 시퀀스 종료 (액션): enrollment={enrollment.id}")

        except Exception as e:
            logger.warning(f"[CRM Sequence] 액션 실행 실패: {action_type}, {e}")

    await db.flush()


async def _process_enrollment_step(
    enrollment: EmailEnrollment,
    db: AsyncSession,
) -> None:
    """단일 등록의 현재 단계 처리 + 분기 조건 + Gmail 발송"""
    sequence = await db.get(EmailSequence, enrollment.sequence_id)
    if not sequence or sequence.status != SequenceStatus.active:
        enrollment.status = EnrollmentStatus.paused
        await db.flush()
        return

    steps = sequence.steps or []
    current_step_idx = enrollment.current_step

    if current_step_idx >= len(steps):
        enrollment.status = EnrollmentStatus.completed
        enrollment.completed_at = now_kst()
        enrollment.next_send_at = None
        await db.flush()
        return

    step = steps[current_step_idx]
    contact = await db.get(Contact, enrollment.contact_id)
    conditions = step.get("conditions", [])

    # ── 조건 대기 중인 경우: 조건 체크 후 분기 ──
    if enrollment.waiting_condition:
        matched_condition = None
        for cond in conditions:
            cond_type = cond.get("condition", "")
            if await _check_tracking_condition(cond_type, enrollment.id, enrollment.contact_id, db):
                matched_condition = cond
                break

        if matched_condition:
            # 조건 매칭됨 → 액션 실행 + 다음 step 이동
            enrollment.waiting_condition = None
            enrollment.retry_count = 0
            actions = matched_condition.get("actions", [])
            if actions:
                await _execute_step_actions(actions, contact, enrollment, db)

            # 조건에 next_step 지정되어 있으면 해당 step으로 이동
            next_step = matched_condition.get("next_step")
            if next_step is not None and next_step < len(steps):
                enrollment.current_step = next_step
            else:
                enrollment.current_step = current_step_idx + 1

            if enrollment.status == EnrollmentStatus.completed:
                await db.flush()
                return

            if enrollment.current_step < len(steps):
                next_delay = steps[enrollment.current_step].get("delay_days", 1)
                enrollment.next_send_at = now_kst() + timedelta(days=next_delay)
            else:
                enrollment.status = EnrollmentStatus.completed
                enrollment.completed_at = now_kst()
                enrollment.next_send_at = None
            await db.flush()
            return

        else:
            # 조건 미충족 → 재시도 또는 무반응 분기
            max_retries = step.get("max_retries", 0)
            retry_delay = step.get("retry_delay_days", 3)

            if max_retries > 0 and enrollment.retry_count < max_retries:
                # 재시도: 같은 이메일 재발송
                enrollment.retry_count += 1
                enrollment.next_send_at = now_kst() + timedelta(days=retry_delay)
                enrollment.waiting_condition = None  # 재발송 후 다시 조건 대기
                logger.info(f"[CRM Sequence] 재시도 {enrollment.retry_count}/{max_retries}: enrollment={enrollment.id}")
                # 재발송 진행 (아래 발송 로직으로 fallthrough)
            else:
                # 무반응 분기 확인 (on_no_open, on_no_click 등)
                no_response_cond = None
                for cond in conditions:
                    if cond.get("condition", "").startswith("on_no_"):
                        no_response_cond = cond
                        break

                if no_response_cond:
                    enrollment.waiting_condition = None
                    enrollment.retry_count = 0
                    actions = no_response_cond.get("actions", [])
                    if actions:
                        await _execute_step_actions(actions, contact, enrollment, db)

                    next_step = no_response_cond.get("next_step")
                    if next_step is not None and next_step < len(steps):
                        enrollment.current_step = next_step
                        delay = no_response_cond.get("delay_days", steps[next_step].get("delay_days", 1))
                        enrollment.next_send_at = now_kst() + timedelta(days=delay)
                    else:
                        enrollment.current_step = current_step_idx + 1
                        if enrollment.current_step < len(steps):
                            enrollment.next_send_at = now_kst() + timedelta(days=steps[enrollment.current_step].get("delay_days", 1))
                        else:
                            enrollment.status = EnrollmentStatus.completed
                            enrollment.completed_at = now_kst()
                            enrollment.next_send_at = None
                else:
                    # 무반응 분기 없으면 그냥 다음 step
                    enrollment.waiting_condition = None
                    enrollment.retry_count = 0
                    enrollment.current_step = current_step_idx + 1
                    if enrollment.current_step < len(steps):
                        enrollment.next_send_at = now_kst() + timedelta(days=steps[enrollment.current_step].get("delay_days", 1))
                    else:
                        enrollment.status = EnrollmentStatus.completed
                        enrollment.completed_at = now_kst()
                        enrollment.next_send_at = None

                await db.flush()
                return

    # ── 이메일 발송 ──
    subject = step.get("subject_template", "")
    body = step.get("body_template", "")

    # 추적 ID 생성 및 DB 기록
    tracking_id = None
    try:
        from app.cc_web_interface.crm.routes.tracking import generate_tracking_id
        from app.cc_web_interface.crm.models import EmailTracking
        tracking_id = generate_tracking_id()
        tracking_record = EmailTracking(
            tracking_id=tracking_id,
            contact_id=enrollment.contact_id,
            enrollment_id=enrollment.id,
            sequence_id=enrollment.sequence_id,
            subject=subject,
            recipient_email=contact.email if contact else None,
        )
        db.add(tracking_record)
    except Exception as te:
        logger.warning(f"[CRM Sequence] 추적 레코드 생성 실패: {te}")
        tracking_id = None

    # Gmail 발송
    email_sent = False
    if contact and contact.email:
        email_sent = _send_email_via_gmail(contact.email, subject, body, contact, tracking_id)
    else:
        logger.warning(f"[CRM Sequence] 이메일 주소 없음 - enrollment={enrollment.id}")

    # 활동 기록
    activity = Activity(
        type=ActivityType.email,
        subject=subject,
        body=body,
        contact_id=enrollment.contact_id,
        extra_data={
            "sequence_id": enrollment.sequence_id,
            "step_number": step.get("step_number", current_step_idx + 1),
            "enrollment_id": enrollment.id,
            "auto_sequence": True,
            "email_sent": email_sent,
            "retry_count": enrollment.retry_count,
        },
    )
    db.add(activity)

    # 발송 실패 → bounced
    if not email_sent and contact and contact.email:
        enrollment.status = EnrollmentStatus.bounced
        enrollment.next_send_at = None
        await db.flush()
        return

    # ── 분기 조건이 있으면 조건 대기 모드 ──
    if conditions:
        # 긍정 조건 (on_open, on_click 등)이 있는지 확인
        has_positive = any(not c.get("condition", "").startswith("on_no_") for c in conditions)
        if has_positive:
            # 조건 체크 대기 (다음 스케줄러 실행 시 확인)
            check_delay = min(
                (c.get("delay_days", 3) for c in conditions if not c.get("condition", "").startswith("on_no_")),
                default=3
            )
            enrollment.waiting_condition = "checking"
            enrollment.next_send_at = now_kst() + timedelta(days=check_delay)
            await db.flush()
            logger.info(f"[CRM Sequence] 조건 대기 시작: enrollment={enrollment.id}, check_in={check_delay}일")
            return

    # ── 분기 없으면 그냥 다음 step ──
    next_step_idx = current_step_idx + 1
    enrollment.current_step = next_step_idx
    enrollment.retry_count = 0

    if next_step_idx < len(steps):
        next_delay = steps[next_step_idx].get("delay_days", 1)
        enrollment.next_send_at = now_kst() + timedelta(days=next_delay)
    else:
        enrollment.status = EnrollmentStatus.completed
        enrollment.completed_at = now_kst()
        enrollment.next_send_at = None

    await db.flush()

    logger.info(
        "[CRM Sequence] 이메일 발송 enrollment=%d, step=%d, contact=%s, sent=%s",
        enrollment.id, current_step_idx + 1,
        contact.email if contact else "unknown", email_sent,
    )

    await db.flush()

    logger.info(
        "[CRM Sequence] 이메일 발송 enrollment=%d, step=%d, contact=%s, sent=%s",
        enrollment.id,
        current_step_idx + 1,
        contact.email if contact else "unknown",
        email_sent,
    )
