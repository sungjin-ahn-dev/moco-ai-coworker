"""
거래 API 라우트
거래 CRUD, 단계 변경, 파이프라인 뷰, 매출 예측
"""

import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, or_, extract
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.orm import selectinload

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import Deal, Pipeline, Contact, Company, now_kst
from app.cc_web_interface.crm.schemas import (
    DealCreate, DealUpdate, DealRead, DealDetailRead, DealStageUpdate,
    PaginatedResponse, SuccessResponse, ErrorResponse,
    PipelineReport, RevenueForecast,
)
from app.cc_web_interface.crm.services.automation import evaluate_triggers

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/deals", tags=["거래"])


def _enrich_deal(deal) -> DealDetailRead:
    """딜에 연락처명/이메일, 회사명을 포함시킨다."""
    d = DealDetailRead.model_validate(deal)
    if deal.contact:
        d.contact_name = f"{deal.contact.first_name} {deal.contact.last_name or ''}".strip()
        d.contact_email = deal.contact.email
    if deal.company:
        d.company_name = deal.company.name
    return d


@router.get("", response_model=SuccessResponse)
async def list_deals(
    search: Optional[str] = Query(None, description="거래명 검색"),
    pipeline_id: Optional[int] = Query(None),
    stage: Optional[str] = Query(None),
    owner: Optional[str] = Query(None),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """거래 목록 조회"""
    base_query = select(Deal)

    if search:
        base_query = base_query.where(Deal.name.ilike(f"%{search}%"))
    if pipeline_id:
        base_query = base_query.where(Deal.pipeline_id == pipeline_id)
    if stage:
        base_query = base_query.where(Deal.stage == stage)
    if owner:
        base_query = base_query.where(Deal.owner_slack_id == owner)

    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    sort_col = getattr(Deal, sort_by, Deal.created_at)
    if sort_order == "asc":
        base_query = base_query.order_by(sort_col.asc())
    else:
        base_query = base_query.order_by(sort_col.desc())

    base_query = base_query.offset((page - 1) * page_size).limit(page_size)
    base_query = base_query.options(
        selectinload(Deal.contact),
        selectinload(Deal.company),
    )
    result = await db.execute(base_query)
    deals = result.scalars().all()

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size,
        items=[_enrich_deal(d) for d in deals],
    ))


@router.get("/forecast", response_model=SuccessResponse)
async def revenue_forecast(
    months: int = Query(6, description="예측 개월 수"),
    db: AsyncSession = Depends(get_db),
):
    """매출 예측 (향후 N개월)"""
    result = await db.execute(
        select(Deal)
        .where(Deal.close_date.isnot(None))
        .where(Deal.stage != "실주")
    )
    deals = result.scalars().all()

    forecasts = defaultdict(lambda: {"expected": 0.0, "weighted": 0.0, "count": 0})
    for deal in deals:
        if deal.close_date:
            month_key = deal.close_date.strftime("%Y-%m")
            forecasts[month_key]["expected"] += deal.amount or 0
            forecasts[month_key]["weighted"] += (deal.amount or 0) * (deal.probability or 0) / 100
            forecasts[month_key]["count"] += 1

    forecast_list = [
        RevenueForecast(
            month=month,
            expected_revenue=data["expected"],
            weighted_revenue=data["weighted"],
            deal_count=data["count"],
        )
        for month, data in sorted(forecasts.items())
    ]
    return SuccessResponse(data=forecast_list[:months])


@router.get("/pipeline/{pipeline_id}", response_model=SuccessResponse)
async def deals_by_pipeline(pipeline_id: int, db: AsyncSession = Depends(get_db)):
    """파이프라인별 거래 (칸반 보드용, 단계별 그룹)"""
    pipeline = await db.get(Pipeline, pipeline_id)
    if not pipeline:
        return ErrorResponse(message="파이프라인을 찾을 수 없습니다.")

    result = await db.execute(
        select(Deal)
        .options(selectinload(Deal.contact), selectinload(Deal.company))
        .where(Deal.pipeline_id == pipeline_id)
    )
    deals = result.scalars().all()

    # stage ID → name 매핑 테이블 생성
    stage_id_to_name = {}
    for stage_info in (pipeline.stages or []):
        sid = stage_info.get("id", "")
        sname = stage_info.get("name", "")
        if sid:
            stage_id_to_name[sid] = sname
        if sname:
            stage_id_to_name[sname] = sname  # name으로 넣은 경우도 매핑

    stages_map = defaultdict(list)
    for deal in deals:
        # deal.stage가 ID든 name이든 name으로 통일
        resolved_name = stage_id_to_name.get(deal.stage, deal.stage)
        stages_map[resolved_name].append(_enrich_deal(deal))

    # 파이프라인 단계 순서에 맞게 정렬
    ordered = []
    for stage_info in (pipeline.stages or []):
        stage_name = stage_info.get("name", "")
        ordered.append({
            "stage": stage_info,
            "deals": stages_map.get(stage_name, []),
        })

    return SuccessResponse(data=ordered)


@router.get("/{deal_id}", response_model=SuccessResponse)
async def get_deal(deal_id: int, db: AsyncSession = Depends(get_db)):
    """거래 상세 조회 (연락처명, 회사명 포함)"""
    result = await db.execute(
        select(Deal)
        .options(selectinload(Deal.contact), selectinload(Deal.company))
        .where(Deal.id == deal_id)
    )
    deal = result.scalar_one_or_none()
    if not deal:
        return ErrorResponse(message="거래를 찾을 수 없습니다.")
    return SuccessResponse(data=_enrich_deal(deal))


@router.post("", response_model=SuccessResponse)
async def create_deal(data: DealCreate, db: AsyncSession = Depends(get_db)):
    """새 거래 생성"""
    pipeline = await db.get(Pipeline, data.pipeline_id)
    if not pipeline:
        return ErrorResponse(message="파이프라인을 찾을 수 없습니다.")

    deal_data = data.model_dump()

    # stage ID→name 변환 (ID든 name이든 name으로 통일)
    if pipeline.stages and deal_data.get("stage"):
        for stage_info in pipeline.stages:
            if stage_info.get("id") == deal_data["stage"]:
                deal_data["stage"] = stage_info.get("name", deal_data["stage"])
                break

    deal = Deal(**deal_data)
    db.add(deal)
    await db.flush()
    return SuccessResponse(data=DealRead.model_validate(deal))


@router.put("/{deal_id}", response_model=SuccessResponse)
async def update_deal(
    deal_id: int, data: DealUpdate, db: AsyncSession = Depends(get_db),
):
    """거래 수정"""
    deal = await db.get(Deal, deal_id)
    if not deal:
        return ErrorResponse(message="거래를 찾을 수 없습니다.")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(deal, key, value)
    await db.flush()
    return SuccessResponse(data=DealRead.model_validate(deal))


@router.delete("/{deal_id}", response_model=SuccessResponse)
async def delete_deal(deal_id: int, db: AsyncSession = Depends(get_db)):
    """거래 삭제"""
    deal = await db.get(Deal, deal_id)
    if not deal:
        return ErrorResponse(message="거래를 찾을 수 없습니다.")

    await db.delete(deal)
    await db.flush()
    return SuccessResponse(data={"deleted": deal_id})


@router.put("/{deal_id}/stage", response_model=SuccessResponse)
async def change_deal_stage(
    deal_id: int,
    data: DealStageUpdate,
    db: AsyncSession = Depends(get_db),
):
    """거래 단계 변경 (자동화 트리거)"""
    deal = await db.get(Deal, deal_id)
    if not deal:
        return ErrorResponse(message="거래를 찾을 수 없습니다.")

    old_stage = deal.stage

    # stage ID→name 변환 (ID든 name이든 name으로 통일)
    resolved_stage = data.stage
    pipeline = await db.get(Pipeline, deal.pipeline_id)
    if pipeline and pipeline.stages:
        for stage_info in pipeline.stages:
            if stage_info.get("id") == data.stage:
                resolved_stage = stage_info.get("name", data.stage)
                break
            elif stage_info.get("name") == data.stage:
                resolved_stage = data.stage
                break

    deal.stage = resolved_stage
    if data.lost_reason:
        deal.lost_reason = data.lost_reason

    # 파이프라인에서 해당 단계의 확률 가져오기
    if pipeline and pipeline.stages:
        for stage_info in pipeline.stages:
            if stage_info.get("name") == resolved_stage or stage_info.get("id") == data.stage:
                deal.probability = stage_info.get("probability", deal.probability)
                break

    await db.flush()

    # deal_stage_change 자동화 트리거 (resolved_stage 사용)
    await evaluate_triggers(
        "deal_stage_change",
        {
            "deal_id": deal_id,
            "contact_id": deal.contact_id,
            "company_id": deal.company_id,
            "old_stage": old_stage,
            "new_stage": resolved_stage,
        },
        db,
    )

    return SuccessResponse(data=DealRead.model_validate(deal))
