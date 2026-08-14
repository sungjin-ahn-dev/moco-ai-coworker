"""
자동화 API 라우트
자동화 워크플로우 CRUD, 수동 실행, 실행 이력
"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import Automation, AutomationExecution
from app.cc_web_interface.crm.schemas import (
    AutomationCreate, AutomationUpdate, AutomationRead,
    AutomationExecutionRead,
    PaginatedResponse, SuccessResponse, ErrorResponse,
)
from app.cc_web_interface.crm.services.automation import execute_actions

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/automations", tags=["자동화"])


@router.get("", response_model=SuccessResponse)
async def list_automations(
    status: str = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """자동화 목록 조회"""
    query = select(Automation)
    if status:
        query = query.where(Automation.status == status)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(Automation.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    automations = result.scalars().all()

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size,
        items=[AutomationRead.model_validate(a) for a in automations],
    ))


@router.get("/{automation_id}", response_model=SuccessResponse)
async def get_automation(automation_id: int, db: AsyncSession = Depends(get_db)):
    """자동화 상세 조회"""
    automation = await db.get(Automation, automation_id)
    if not automation:
        return ErrorResponse(message="자동화를 찾을 수 없습니다.")
    return SuccessResponse(data=AutomationRead.model_validate(automation))


@router.post("", response_model=SuccessResponse)
async def create_automation(
    data: AutomationCreate, db: AsyncSession = Depends(get_db),
):
    """새 자동화 생성"""
    automation = Automation(
        name=data.name,
        description=data.description,
        trigger_type=data.trigger_type,
        trigger_config=data.trigger_config,
        actions=[a.model_dump() for a in data.actions],
        status=data.status,
    )
    db.add(automation)
    await db.flush()
    return SuccessResponse(data=AutomationRead.model_validate(automation))


@router.put("/{automation_id}", response_model=SuccessResponse)
async def update_automation(
    automation_id: int,
    data: AutomationUpdate,
    db: AsyncSession = Depends(get_db),
):
    """자동화 수정"""
    automation = await db.get(Automation, automation_id)
    if not automation:
        return ErrorResponse(message="자동화를 찾을 수 없습니다.")

    update_data = data.model_dump(exclude_unset=True)
    if "actions" in update_data and update_data["actions"] is not None:
        update_data["actions"] = [
            a.model_dump() if hasattr(a, "model_dump") else a
            for a in update_data["actions"]
        ]
    for key, value in update_data.items():
        setattr(automation, key, value)
    await db.flush()
    return SuccessResponse(data=AutomationRead.model_validate(automation))


@router.delete("/{automation_id}", response_model=SuccessResponse)
async def delete_automation(automation_id: int, db: AsyncSession = Depends(get_db)):
    """자동화 삭제"""
    automation = await db.get(Automation, automation_id)
    if not automation:
        return ErrorResponse(message="자동화를 찾을 수 없습니다.")
    await db.delete(automation)
    await db.flush()
    return SuccessResponse(data={"deleted": automation_id})


@router.post("/{automation_id}/execute", response_model=SuccessResponse)
async def manual_execute(
    automation_id: int,
    db: AsyncSession = Depends(get_db),
):
    """자동화 수동 실행"""
    automation = await db.get(Automation, automation_id)
    if not automation:
        return ErrorResponse(message="자동화를 찾을 수 없습니다.")

    try:
        results = await execute_actions(automation_id, {"manual": True}, db)
        return SuccessResponse(data={"automation_id": automation_id, "results": results})
    except Exception as e:
        return ErrorResponse(message=f"자동화 실행 실패: {str(e)}")


@router.get("/{automation_id}/history", response_model=SuccessResponse)
async def execution_history(
    automation_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """자동화 실행 이력"""
    automation = await db.get(Automation, automation_id)
    if not automation:
        return ErrorResponse(message="자동화를 찾을 수 없습니다.")

    query = select(AutomationExecution).where(
        AutomationExecution.automation_id == automation_id
    )
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(AutomationExecution.executed_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    executions = result.scalars().all()

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size,
        items=[AutomationExecutionRead.model_validate(e) for e in executions],
    ))
