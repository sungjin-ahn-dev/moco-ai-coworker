"""
세그먼트(스마트 리스트) API 라우트
조건 기반 연락처 그룹 관리
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import Contact, Segment
from app.cc_web_interface.crm.schemas import (
    SegmentCreate, SegmentUpdate, SegmentRead,
    ContactRead, PaginatedResponse, SuccessResponse, ErrorResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/segments", tags=["세그먼트"])


def _build_filter_clause(f):
    """단일 필터 조건을 SQLAlchemy clause로 변환"""
    field = f.get("field", "")
    op = f.get("operator", "eq")
    val = f.get("value")

    # 태그 필터 (JSON array contains)
    if field == "tag":
        # SQLite JSON: tags LIKE '%"value"%'
        return Contact.tags.cast(str).contains(f'"{val}"')

    # 커스텀 속성 필터
    if field.startswith("custom_"):
        prop_name = field.replace("custom_", "", 1)
        # SQLite: json_extract(custom_properties, '$.key')
        from sqlalchemy import text
        json_expr = func.json_extract(Contact.custom_properties, f"$.{prop_name}")
        if op == "eq":
            return json_expr == val
        elif op == "neq":
            return json_expr != val
        elif op == "contains":
            return json_expr.like(f"%{val}%")
        return json_expr == val

    # 표준 필드 매핑
    col_map = {
        "lead_status": Contact.lead_status,
        "lifecycle_stage": Contact.lifecycle_stage,
        "lead_score": Contact.lead_score,
        "source": Contact.source,
        "email": Contact.email,
        "company_id": Contact.company_id,
        "owner_slack_id": Contact.owner_slack_id,
    }
    col = col_map.get(field)
    if col is None:
        return None

    if op == "eq":
        return col == val
    elif op == "neq":
        return col != val
    elif op == "gt":
        return col > val
    elif op == "gte":
        return col >= val
    elif op == "lt":
        return col < val
    elif op == "lte":
        return col <= val
    elif op == "contains":
        return col.ilike(f"%{val}%")
    elif op == "in":
        if isinstance(val, list):
            return col.in_(val)
        return col == val
    return col == val


def _build_segment_query(filters):
    """세그먼트 필터 목록으로 SQLAlchemy query 생성"""
    query = select(Contact)
    clauses = []
    for f in (filters or []):
        clause = _build_filter_clause(f if isinstance(f, dict) else f.dict())
        if clause is not None:
            clauses.append(clause)
    if clauses:
        query = query.where(and_(*clauses))
    return query


@router.get("", response_model=SuccessResponse)
async def list_segments(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """세그먼트 목록 조회"""
    count_query = select(func.count(Segment.id))
    total = (await db.execute(count_query)).scalar() or 0

    result = await db.execute(
        select(Segment)
        .order_by(Segment.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    segments = result.scalars().all()

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size,
        items=[SegmentRead.model_validate(s) for s in segments],
    ))


@router.get("/{segment_id}", response_model=SuccessResponse)
async def get_segment(segment_id: int, db: AsyncSession = Depends(get_db)):
    """세그먼트 상세 조회"""
    segment = await db.get(Segment, segment_id)
    if not segment:
        return ErrorResponse(message="세그먼트를 찾을 수 없습니다.")
    return SuccessResponse(data=SegmentRead.model_validate(segment))


@router.post("", response_model=SuccessResponse)
async def create_segment(data: SegmentCreate, db: AsyncSession = Depends(get_db)):
    """새 세그먼트 생성"""
    filters_raw = [f.model_dump() for f in data.filters]

    # 매칭되는 연락처 수 계산
    query = _build_segment_query(filters_raw)
    count_q = select(func.count()).select_from(query.subquery())
    contact_count = (await db.execute(count_q)).scalar() or 0

    segment = Segment(
        name=data.name,
        description=data.description,
        filters=filters_raw,
        contact_count=contact_count,
    )
    db.add(segment)
    await db.flush()
    return SuccessResponse(data=SegmentRead.model_validate(segment))


@router.put("/{segment_id}", response_model=SuccessResponse)
async def update_segment(
    segment_id: int, data: SegmentUpdate, db: AsyncSession = Depends(get_db),
):
    """세그먼트 수정"""
    segment = await db.get(Segment, segment_id)
    if not segment:
        return ErrorResponse(message="세그먼트를 찾을 수 없습니다.")

    update_data = data.model_dump(exclude_unset=True)
    if "filters" in update_data and update_data["filters"] is not None:
        update_data["filters"] = [
            f.model_dump() if hasattr(f, "model_dump") else f
            for f in update_data["filters"]
        ]
        # 매칭 수 재계산
        query = _build_segment_query(update_data["filters"])
        count_q = select(func.count()).select_from(query.subquery())
        update_data["contact_count"] = (await db.execute(count_q)).scalar() or 0

    for key, value in update_data.items():
        setattr(segment, key, value)
    await db.flush()
    return SuccessResponse(data=SegmentRead.model_validate(segment))


@router.delete("/{segment_id}", response_model=SuccessResponse)
async def delete_segment(segment_id: int, db: AsyncSession = Depends(get_db)):
    """세그먼트 삭제"""
    segment = await db.get(Segment, segment_id)
    if not segment:
        return ErrorResponse(message="세그먼트를 찾을 수 없습니다.")
    await db.delete(segment)
    await db.flush()
    return SuccessResponse(data={"deleted": segment_id})


@router.get("/{segment_id}/contacts", response_model=SuccessResponse)
async def get_segment_contacts(
    segment_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """세그먼트에 해당하는 연락처 목록"""
    segment = await db.get(Segment, segment_id)
    if not segment:
        return ErrorResponse(message="세그먼트를 찾을 수 없습니다.")

    query = _build_segment_query(segment.filters)
    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # 카운트 업데이트
    if segment.contact_count != total:
        segment.contact_count = total
        await db.flush()

    result = await db.execute(
        query.order_by(Contact.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    contacts = result.scalars().all()

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size,
        items=[ContactRead.model_validate(c) for c in contacts],
    ))


@router.post("/{segment_id}/refresh", response_model=SuccessResponse)
async def refresh_segment(segment_id: int, db: AsyncSession = Depends(get_db)):
    """세그먼트 연락처 수 갱신"""
    segment = await db.get(Segment, segment_id)
    if not segment:
        return ErrorResponse(message="세그먼트를 찾을 수 없습니다.")

    query = _build_segment_query(segment.filters)
    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    segment.contact_count = total
    await db.flush()
    return SuccessResponse(data=SegmentRead.model_validate(segment))
