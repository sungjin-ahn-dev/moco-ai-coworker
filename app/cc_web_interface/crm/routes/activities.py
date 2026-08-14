"""
활동 API 라우트
활동 이력 CRUD 및 최근 활동 조회
"""

import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, Query, UploadFile, File, Form
from fastapi.responses import JSONResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.orm import selectinload

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import Activity
from app.cc_web_interface.crm.schemas import (
    ActivityCreate, ActivityUpdate, ActivityRead, ActivityDetailRead,
    PaginatedResponse, SuccessResponse, ErrorResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/activities", tags=["활동"])


def _enrich_activity(activity) -> ActivityDetailRead:
    """활동에 연락처명/이메일, 딜명, 회사명을 포함시킨다."""
    a = ActivityDetailRead.model_validate(activity)
    if activity.contact:
        a.contact_name = f"{activity.contact.first_name} {activity.contact.last_name or ''}".strip()
        a.contact_email = activity.contact.email
    if activity.deal:
        a.deal_name = activity.deal.name
    if activity.company:
        a.company_name = activity.company.name
    return a


@router.get("", response_model=SuccessResponse)
async def list_activities(
    contact_id: Optional[int] = Query(None),
    deal_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    type: Optional[str] = Query(None, description="활동 유형 필터"),
    user_slack_id: Optional[str] = Query(None, description="담당자 필터"),
    search: Optional[str] = Query(None, description="제목/본문 검색"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """활동 목록 조회"""
    base_query = select(Activity)

    if contact_id:
        base_query = base_query.where(Activity.contact_id == contact_id)
    if deal_id:
        base_query = base_query.where(Activity.deal_id == deal_id)
    if company_id:
        base_query = base_query.where(Activity.company_id == company_id)
    if type:
        base_query = base_query.where(Activity.type == type)
    if user_slack_id:
        base_query = base_query.where(Activity.user_slack_id == user_slack_id)
    if search:
        from sqlalchemy import or_
        base_query = base_query.where(or_(
            Activity.subject.ilike(f"%{search}%"),
            Activity.body.ilike(f"%{search}%"),
        ))

    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    base_query = base_query.order_by(Activity.timestamp.desc())
    base_query = base_query.offset((page - 1) * page_size).limit(page_size)
    base_query = base_query.options(
        selectinload(Activity.contact),
        selectinload(Activity.deal),
        selectinload(Activity.company),
    )
    result = await db.execute(base_query)
    activities = result.scalars().all()

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size,
        items=[_enrich_activity(a) for a in activities],
    ))


@router.get("/recent", response_model=SuccessResponse)
async def recent_activities(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """최근 활동 조회 (전체 엔티티)"""
    result = await db.execute(
        select(Activity)
        .options(
            selectinload(Activity.contact),
            selectinload(Activity.deal),
            selectinload(Activity.company),
        )
        .order_by(Activity.timestamp.desc())
        .limit(limit)
    )
    activities = result.scalars().all()
    return SuccessResponse(
        data=[_enrich_activity(a) for a in activities]
    )


@router.get("/{activity_id}", response_model=SuccessResponse)
async def get_activity(activity_id: int, db: AsyncSession = Depends(get_db)):
    """활동 상세 조회 (연락처명, 딜명, 회사명 포함)"""
    result = await db.execute(
        select(Activity)
        .options(
            selectinload(Activity.contact),
            selectinload(Activity.deal),
            selectinload(Activity.company),
        )
        .where(Activity.id == activity_id)
    )
    activity = result.scalar_one_or_none()
    if not activity:
        return ErrorResponse(message="활동을 찾을 수 없습니다.")
    return SuccessResponse(data=_enrich_activity(activity))


@router.post("", response_model=SuccessResponse)
async def create_activity(data: ActivityCreate, db: AsyncSession = Depends(get_db)):
    """새 활동 기록"""
    dump = data.model_dump()
    dump['extra_data'] = dump.pop('metadata', {})
    activity = Activity(**dump)
    db.add(activity)
    await db.flush()
    return SuccessResponse(data=ActivityRead.model_validate(activity))


@router.put("/{activity_id}", response_model=SuccessResponse)
async def update_activity(
    activity_id: int,
    data: ActivityUpdate,
    db: AsyncSession = Depends(get_db),
):
    """활동 수정"""
    activity = await db.get(Activity, activity_id)
    if not activity:
        return ErrorResponse(message="활동을 찾을 수 없습니다.")

    for key, value in data.model_dump(exclude_unset=True).items():
        attr = 'extra_data' if key == 'metadata' else key
        setattr(activity, attr, value)
    await db.flush()
    return SuccessResponse(data=ActivityRead.model_validate(activity))


@router.delete("/{activity_id}", response_model=SuccessResponse)
async def delete_activity(activity_id: int, db: AsyncSession = Depends(get_db)):
    """활동 삭제"""
    activity = await db.get(Activity, activity_id)
    if not activity:
        return ErrorResponse(message="활동을 찾을 수 없습니다.")

    await db.delete(activity)
    await db.flush()
    return SuccessResponse(data={"deleted": activity_id})


@router.get("/sfe-summary/{owner}")
async def get_sfe_summary(owner: str, db: AsyncSession = Depends(get_db)):
    """SFE Summary KPI 데이터 (월별 목표/실적/달성률)"""
    from app.cc_web_interface.crm.models import ReferenceData
    result = await db.execute(select(ReferenceData).where(ReferenceData.key == "SFE_Summary"))
    ref = result.scalars().first()
    if not ref:
        return JSONResponse({"success": False, "message": "SFE_Summary not found"})

    data = json.loads(ref.data) if isinstance(ref.data, str) else ref.data

    # owner에 맞는 섹션 반환
    owner_map = {"Harry": "Harry", "Chloe": "Chloe", "": "Team_Total"}
    section_key = owner_map.get(owner, "Team_Total")

    section = data.get(section_key, {})
    return JSONResponse({"success": True, "summary": section, "owner": owner})


@router.get("/territory/{owner}")
async def get_territory(owner: str, db: AsyncSession = Depends(get_db)):
    """담당자별 병원-의사 목록 (SFE Territory 기반)"""
    from app.cc_web_interface.crm.models import Company, Contact
    from sqlalchemy.orm import selectinload

    # 담당자별 병원 매핑 (SFE Plan 기준)
    TERRITORY = {
        "Harry": [
            {"hospital":"A병원","tier":"Growth","doctors":["의사01","의사02","의사03","의사04"]},
            {"hospital":"B병원","tier":"Loyalty","doctors":["의사05"]},
            {"hospital":"C병원","tier":"Growth","doctors":["의사06"]},
            {"hospital":"D병원","tier":"Growth","doctors":["의사07","의사08"]},
            {"hospital":"E병원","tier":"Growth","doctors":["의사09","의사10","의사11"]},
            {"hospital":"F병원","tier":"Growth","doctors":["의사12","의사13","의사14"]},
            {"hospital":"G병원","tier":"Loyalty","doctors":["의사15"]},
            {"hospital":"H병원","tier":"Loyalty","doctors":["의사16"]},
            {"hospital":"I병원","tier":"Trial","doctors":["의사17","의사18","의사19"]},
            {"hospital":"J병원","tier":"Trial","doctors":["의사20"]},
            {"hospital":"K병원","tier":"Trial","doctors":["의사21"]},
            {"hospital":"L병원","tier":"Trial","doctors":["의사22"]},
            {"hospital":"M병원","tier":"Trial","doctors":["의사23"]},
            {"hospital":"N병원","tier":"Trial","doctors":["의사24"]},
            {"hospital":"O병원","tier":"Maintenance","doctors":["의사25"]},
        ],
        "Chloe": [
            {"hospital":"P병원","tier":"Growth","doctors":["의사26","의사27"]},
            {"hospital":"Q병원","tier":"Growth","doctors":["의사28","의사29","의사30"]},
            {"hospital":"R병원","tier":"Growth","doctors":["의사31","의사32"]},
            {"hospital":"S병원","tier":"Trial","doctors":["의사33","의사34"]},
            {"hospital":"T병원","tier":"Trial","doctors":["의사35","의사36"]},
            {"hospital":"U병원","tier":"Trial","doctors":["의사37"]},
            {"hospital":"V병원","tier":"Trial","doctors":["의사38","의사39","의사40"]},
            {"hospital":"W병원","tier":"Trial","doctors":["의사41","의사42","의사43"]},
            {"hospital":"X병원","tier":"Loyalty","doctors":["의사44"]},
            {"hospital":"Y병원","tier":"Loyalty","doctors":["의사45"]},
            {"hospital":"Z의원","tier":"Loyalty","doctors":["의사46"]},
        ],
    }

    territory = TERRITORY.get(owner, [])

    # DB에서 실제 company_id, contact_id 매칭
    result = []
    for t in territory:
        # 병원 검색
        co_q = await db.execute(select(Company).where(Company.name.contains(t["hospital"].replace("병원","").replace("의원",""))))
        company = co_q.scalars().first()

        hospital_data = {
            "hospital": t["hospital"],
            "tier": t["tier"],
            "company_id": company.id if company else None,
            "doctors": []
        }

        for doc_name in t["doctors"]:
            # 의사 검색
            doc_q = await db.execute(select(Contact).where(Contact.first_name == doc_name))
            contact = doc_q.scalars().first()
            hospital_data["doctors"].append({
                "name": doc_name,
                "contact_id": contact.id if contact else None,
                "department": contact.department if contact else "",
            })

        result.append(hospital_data)

    return JSONResponse({"success": True, "territory": result, "owner": owner})


@router.get("/summary/{owner}")
async def get_owner_summary(owner: str, db: AsyncSession = Depends(get_db)):
    """담당자별 영업 실적 Summary"""
    from sqlalchemy import and_, extract
    from datetime import datetime, timedelta

    now = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0)
    week_start = now - timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0)

    # 이번 달 활동
    month_q = await db.execute(
        select(func.count(Activity.id)).where(
            and_(Activity.user_slack_id == owner, Activity.timestamp >= month_start)
        )
    )
    month_total = month_q.scalar() or 0

    # 이번 달 완료
    all_month = await db.execute(
        select(Activity).where(
            and_(Activity.user_slack_id == owner, Activity.timestamp >= month_start)
        )
    )
    month_items = all_month.scalars().all()
    month_done = sum(1 for a in month_items if (a.extra_data or {}).get("done"))

    # 이번 주 활동
    week_q = await db.execute(
        select(func.count(Activity.id)).where(
            and_(Activity.user_slack_id == owner, Activity.timestamp >= week_start)
        )
    )
    week_total = week_q.scalar() or 0

    # Objective별 분류
    obj_counts = {}
    for a in month_items:
        meta = a.extra_data or {}
        obj = meta.get("call_objective", "기타")
        obj_counts[obj] = obj_counts.get(obj, 0) + 1

    # 병원별 방문 횟수 (이번 달)
    hospital_counts = {}
    for a in month_items:
        h = a.company_name if hasattr(a, 'company_name') else (a.extra_data or {}).get("hospital", "")
        if h:
            hospital_counts[h] = hospital_counts.get(h, 0) + 1

    top_hospitals = sorted(hospital_counts.items(), key=lambda x: -x[1])[:5]

    return JSONResponse({
        "success": True,
        "summary": {
            "month_total": month_total,
            "month_done": month_done,
            "month_rate": round(month_done / month_total * 100) if month_total else 0,
            "week_total": week_total,
            "objective_breakdown": obj_counts,
            "top_hospitals": [{"name": h, "count": c} for h, c in top_hospitals],
        }
    })


@router.post("/{activity_id}/meeting-report")
async def generate_meeting_report(
    activity_id: int,
    audio: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """미팅 녹음 → Gemini로 자동 보고서 생성 → activity metadata에 저장"""
    activity = await db.get(Activity, activity_id)
    if not activity:
        return JSONResponse({"success": False, "message": "활동을 찾을 수 없습니다."}, status_code=404)

    audio_bytes = await audio.read()
    logger.info(f"[MEETING_REPORT] Activity {activity_id}: received {len(audio_bytes)} bytes audio")

    try:
        from google import genai
        from google.genai import types

        api_key = os.environ.get("GOOGLE_API_KEY", "")
        client = genai.Client(api_key=api_key)

        # Gemini에 오디오 + 프롬프트 전송
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=[
                types.Content(parts=[
                    types.Part.from_bytes(data=audio_bytes, mime_type=audio.content_type or "audio/wav"),
                    types.Part(text="""이 오디오는 의료기기(제품A) 영업 담당자가 병원을 방문하여 의사와 미팅한 녹음입니다.
화자를 구분하여 분석해주세요. 영업 담당자와 의사(또는 병원 관계자)의 발언을 구분하세요.
아래 항목을 JSON 형식으로 정리해주세요. 한국어로 작성하세요.

{
  "doctor_name": "면담 의사/관계자 이름 (언급된 경우, 없으면 '미확인')",
  "transcript": "화자 구분된 대화 내용 (예: '영업: 안녕하세요... / 의사: 네, 어떤...')",
  "summary": "논의 내용 요약 (3-5문장)",
  "doctor_reaction": "의사 반응 (긍정적/부정적/보류/중립)",
  "prescription_mentions": "처방 관련 언급 사항 (처방 의향, 환자 반응, 재처방 등)",
  "next_actions": "다음 액션 아이템 목록",
  "result": "결과 분류 (성공/보류/거절/부재 중 하나)",
  "key_quotes": "의사의 중요 발언 인용 (있으면)",
  "follow_up_date": "다음 방문/연락 예정일 (언급된 경우)"
}

JSON만 출력하세요. 다른 텍스트 없이."""),
                ]),
            ],
        )

        report_text = response.text.strip()
        # JSON 추출 (```json ... ``` 감싸져 있을 수 있음)
        if "```json" in report_text:
            report_text = report_text.split("```json")[1].split("```")[0].strip()
        elif "```" in report_text:
            report_text = report_text.split("```")[1].split("```")[0].strip()

        report = json.loads(report_text)
        logger.info(f"[MEETING_REPORT] Report generated: {report.get('result', 'unknown')}")

        # activity metadata에 보고서 저장
        existing_meta = activity.extra_data or {}
        if isinstance(existing_meta, str):
            existing_meta = json.loads(existing_meta)
        existing_meta["meeting_report"] = report
        existing_meta["done"] = True
        existing_meta["result"] = report.get("result", "")
        activity.extra_data = existing_meta

        await db.flush()

        return JSONResponse({
            "success": True,
            "report": report,
            "message": "미팅 보고서가 생성되었습니다.",
        })

    except json.JSONDecodeError as e:
        logger.error(f"[MEETING_REPORT] JSON parse error: {e}, raw: {report_text[:200]}")
        return JSONResponse({
            "success": False,
            "message": "보고서 생성 실패: AI 응답을 파싱할 수 없습니다.",
            "raw_text": report_text[:500],
        }, status_code=500)
    except Exception as e:
        logger.error(f"[MEETING_REPORT] Error: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "message": f"보고서 생성 실패: {str(e)}",
        }, status_code=500)


@router.post("/meeting-report-audio")
async def transcribe_meeting_audio(audio: UploadFile = File(...)):
    """녹음 파일만 받아서 보고서 생성 (activity 연결 없이 독립 사용)"""
    audio_bytes = await audio.read()
    logger.info(f"[MEETING_REPORT] Standalone: received {len(audio_bytes)} bytes audio")

    try:
        from google import genai
        from google.genai import types

        api_key = os.environ.get("GOOGLE_API_KEY", "")
        client = genai.Client(api_key=api_key)

        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=[
                types.Content(parts=[
                    types.Part.from_bytes(data=audio_bytes, mime_type=audio.content_type or "audio/wav"),
                    types.Part(text="""이 오디오는 의료기기(제품A) 영업 담당자가 병원을 방문하여 의사와 미팅한 녹음입니다.
화자를 구분하여 분석해주세요. 영업 담당자와 의사(또는 병원 관계자)의 발언을 구분하세요.
아래 항목을 JSON 형식으로 정리해주세요. 한국어로 작성하세요.

{
  "doctor_name": "면담 의사/관계자 이름 (언급된 경우, 없으면 '미확인')",
  "transcript": "화자 구분된 대화 내용 (예: '영업: 안녕하세요... / 의사: 네, 어떤...')",
  "summary": "논의 내용 요약 (3-5문장)",
  "doctor_reaction": "의사 반응 (긍정적/부정적/보류/중립)",
  "prescription_mentions": "처방 관련 언급 사항 (처방 의향, 환자 반응, 재처방 등)",
  "next_actions": "다음 액션 아이템 목록",
  "result": "결과 분류 (성공/보류/거절/부재 중 하나)",
  "key_quotes": "의사의 중요 발언 인용 (있으면)",
  "follow_up_date": "다음 방문/연락 예정일 (언급된 경우)"
}

JSON만 출력하세요. 다른 텍스트 없이."""),
                ]),
            ],
        )

        report_text = response.text.strip()
        if "```json" in report_text:
            report_text = report_text.split("```json")[1].split("```")[0].strip()
        elif "```" in report_text:
            report_text = report_text.split("```")[1].split("```")[0].strip()

        report = json.loads(report_text)
        return JSONResponse({"success": True, "report": report})

    except Exception as e:
        logger.error(f"[MEETING_REPORT] Standalone error: {e}", exc_info=True)
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)
