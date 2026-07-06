"""
CRM 태스크 API 라우트
태스크 CRUD, 내 태스크, 완료 처리
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.orm import selectinload

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import CRMTask, TaskStatus, now_kst
from app.cc_web_interface.crm.schemas import (
    CRMTaskCreate, CRMTaskUpdate, CRMTaskRead, CRMTaskDetailRead,
    PaginatedResponse, SuccessResponse, ErrorResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tasks", tags=["태스크"])

_PRIORITY_KO = {"낮음": "low", "보통": "medium", "높음": "high", "긴급": "high"}
_STATUS_KO = {"할일": "todo", "할 일": "todo", "진행중": "in_progress", "완료": "done"}


def _enrich_task(task) -> CRMTaskDetailRead:
    """태스크에 연락처명/이메일, 딜명을 포함시킨다."""
    t = CRMTaskDetailRead.model_validate(task)
    if task.contact:
        t.contact_name = f"{task.contact.first_name} {task.contact.last_name or ''}".strip()
        t.contact_email = task.contact.email
    if task.deal:
        t.deal_name = task.deal.name
    return t


def _normalize_task(data: dict) -> dict:
    if "priority" in data and data["priority"]:
        data["priority"] = _PRIORITY_KO.get(data["priority"], data["priority"])
    if "status" in data and data["status"]:
        data["status"] = _STATUS_KO.get(data["status"], data["status"])
    return data


@router.get("", response_model=SuccessResponse)
async def list_tasks(
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    contact_id: Optional[int] = Query(None),
    deal_id: Optional[int] = Query(None),
    sort_by: str = Query("due_date"),
    sort_order: str = Query("asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """태스크 목록 조회"""
    if status:
        status = _STATUS_KO.get(status, status)
    if priority:
        priority = _PRIORITY_KO.get(priority, priority)

    base_query = select(CRMTask)

    if status:
        base_query = base_query.where(CRMTask.status == status)
    if priority:
        base_query = base_query.where(CRMTask.priority == priority)
    if contact_id:
        base_query = base_query.where(CRMTask.contact_id == contact_id)
    if deal_id:
        base_query = base_query.where(CRMTask.deal_id == deal_id)

    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    sort_col = getattr(CRMTask, sort_by, CRMTask.due_date)
    if sort_order == "asc":
        base_query = base_query.order_by(sort_col.asc().nullslast())
    else:
        base_query = base_query.order_by(sort_col.desc().nullslast())

    base_query = base_query.offset((page - 1) * page_size).limit(page_size)
    base_query = base_query.options(
        selectinload(CRMTask.contact),
        selectinload(CRMTask.deal),
    )
    result = await db.execute(base_query)
    tasks = result.scalars().all()

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size,
        items=[_enrich_task(t) for t in tasks],
    ))


@router.get("/my", response_model=SuccessResponse)
async def my_tasks(
    slack_id: str = Query(..., description="담당자 Slack ID"),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """내 태스크 조회"""
    base_query = select(CRMTask).where(CRMTask.assigned_to_slack_id == slack_id)
    if status:
        base_query = base_query.where(CRMTask.status == status)

    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    base_query = base_query.order_by(CRMTask.due_date.asc().nullslast())
    base_query = base_query.offset((page - 1) * page_size).limit(page_size)
    base_query = base_query.options(
        selectinload(CRMTask.contact),
        selectinload(CRMTask.deal),
    )
    result = await db.execute(base_query)
    tasks = result.scalars().all()

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size,
        items=[_enrich_task(t) for t in tasks],
    ))


@router.get("/{task_id}", response_model=SuccessResponse)
async def get_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """태스크 상세 조회 (연락처명, 딜명 포함)"""
    result = await db.execute(
        select(CRMTask)
        .options(selectinload(CRMTask.contact), selectinload(CRMTask.deal))
        .where(CRMTask.id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        return ErrorResponse(message="태스크를 찾을 수 없습니다.")
    return SuccessResponse(data=_enrich_task(task))


@router.post("", response_model=SuccessResponse)
async def create_task(data: CRMTaskCreate, db: AsyncSession = Depends(get_db)):
    """새 태스크 생성"""
    task = CRMTask(**_normalize_task(data.model_dump()))
    db.add(task)
    await db.flush()
    return SuccessResponse(data=CRMTaskRead.model_validate(task))


@router.put("/{task_id}", response_model=SuccessResponse)
async def update_task(
    task_id: int, data: CRMTaskUpdate, db: AsyncSession = Depends(get_db),
):
    """태스크 수정"""
    task = await db.get(CRMTask, task_id)
    if not task:
        return ErrorResponse(message="태스크를 찾을 수 없습니다.")

    for key, value in _normalize_task(data.model_dump(exclude_unset=True)).items():
        setattr(task, key, value)
    await db.flush()
    return SuccessResponse(data=CRMTaskRead.model_validate(task))


@router.delete("/{task_id}", response_model=SuccessResponse)
async def delete_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """태스크 삭제"""
    task = await db.get(CRMTask, task_id)
    if not task:
        return ErrorResponse(message="태스크를 찾을 수 없습니다.")
    await db.delete(task)
    await db.flush()
    return SuccessResponse(data={"deleted": task_id})


@router.put("/{task_id}/complete", response_model=SuccessResponse)
async def complete_task(task_id: int, db: AsyncSession = Depends(get_db)):
    """태스크 완료 처리"""
    task = await db.get(CRMTask, task_id)
    if not task:
        return ErrorResponse(message="태스크를 찾을 수 없습니다.")

    task.status = TaskStatus.done
    task.completed_at = now_kst()
    await db.flush()
    return SuccessResponse(data=CRMTaskRead.model_validate(task))
