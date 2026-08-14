"""
파이프라인 API 라우트
영업 파이프라인 CRUD
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import Pipeline
from app.cc_web_interface.crm.schemas import (
    PipelineCreate, PipelineUpdate, PipelineRead,
    SuccessResponse, ErrorResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/pipelines", tags=["파이프라인"])

# 기본 파이프라인 단계 정의
DEFAULT_PIPELINE_STAGES = [
    {"id": "lead_received", "name": "리드접수", "probability": 10, "order": 1},
    {"id": "needs_analysis", "name": "니즈파악", "probability": 25, "order": 2},
    {"id": "proposal", "name": "제안", "probability": 50, "order": 3},
    {"id": "negotiation", "name": "협상", "probability": 75, "order": 4},
    {"id": "closed_won", "name": "계약완료", "probability": 100, "order": 5},
    {"id": "closed_lost", "name": "실주", "probability": 0, "order": 6},
]


async def ensure_default_pipeline(db: AsyncSession) -> Pipeline:
    """기본 파이프라인이 없으면 생성"""
    result = await db.execute(
        select(Pipeline).where(Pipeline.is_default == True)
    )
    pipeline = result.scalar_one_or_none()
    if pipeline:
        return pipeline

    pipeline = Pipeline(
        name="기본 영업 파이프라인",
        stages=DEFAULT_PIPELINE_STAGES,
        is_default=True,
    )
    db.add(pipeline)
    await db.commit()
    logger.info("[CRM] 기본 파이프라인 생성 완료")
    return pipeline


@router.get("", response_model=SuccessResponse)
async def list_pipelines(db: AsyncSession = Depends(get_db)):
    """전체 파이프라인 목록"""
    result = await db.execute(select(Pipeline).order_by(Pipeline.created_at))
    pipelines = result.scalars().all()
    return SuccessResponse(
        data=[PipelineRead.model_validate(p) for p in pipelines]
    )


@router.get("/{pipeline_id}", response_model=SuccessResponse)
async def get_pipeline(pipeline_id: int, db: AsyncSession = Depends(get_db)):
    """파이프라인 상세 조회"""
    pipeline = await db.get(Pipeline, pipeline_id)
    if not pipeline:
        return ErrorResponse(message="파이프라인을 찾을 수 없습니다.")
    return SuccessResponse(data=PipelineRead.model_validate(pipeline))


@router.post("", response_model=SuccessResponse)
async def create_pipeline(data: PipelineCreate, db: AsyncSession = Depends(get_db)):
    """새 파이프라인 생성"""
    stages_data = [s.model_dump() for s in data.stages]
    pipeline = Pipeline(
        name=data.name,
        stages=stages_data,
        is_default=data.is_default,
    )

    # is_default가 True이면 기존 기본 해제
    if data.is_default:
        result = await db.execute(
            select(Pipeline).where(Pipeline.is_default == True)
        )
        for existing in result.scalars().all():
            existing.is_default = False

    db.add(pipeline)
    await db.flush()
    return SuccessResponse(data=PipelineRead.model_validate(pipeline))


@router.put("/{pipeline_id}", response_model=SuccessResponse)
async def update_pipeline(
    pipeline_id: int,
    data: PipelineUpdate,
    db: AsyncSession = Depends(get_db),
):
    """파이프라인 수정"""
    pipeline = await db.get(Pipeline, pipeline_id)
    if not pipeline:
        return ErrorResponse(message="파이프라인을 찾을 수 없습니다.")

    update_data = data.model_dump(exclude_unset=True)
    if "stages" in update_data and update_data["stages"] is not None:
        update_data["stages"] = [
            s.model_dump() if hasattr(s, "model_dump") else s
            for s in update_data["stages"]
        ]

    if update_data.get("is_default"):
        result = await db.execute(
            select(Pipeline).where(Pipeline.is_default == True)
        )
        for existing in result.scalars().all():
            if existing.id != pipeline_id:
                existing.is_default = False

    for key, value in update_data.items():
        setattr(pipeline, key, value)
    await db.flush()
    return SuccessResponse(data=PipelineRead.model_validate(pipeline))


@router.delete("/{pipeline_id}", response_model=SuccessResponse)
async def delete_pipeline(pipeline_id: int, db: AsyncSession = Depends(get_db)):
    """파이프라인 삭제"""
    pipeline = await db.get(Pipeline, pipeline_id)
    if not pipeline:
        return ErrorResponse(message="파이프라인을 찾을 수 없습니다.")
    if pipeline.is_default:
        return ErrorResponse(message="기본 파이프라인은 삭제할 수 없습니다.")

    await db.delete(pipeline)
    await db.flush()
    return SuccessResponse(data={"deleted": pipeline_id})
