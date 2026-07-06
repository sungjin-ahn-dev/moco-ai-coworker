"""
병원 계약 API 라우트
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import HospitalContract, Company
from app.cc_web_interface.crm.schemas import (
    HospitalContractCreate, HospitalContractUpdate, HospitalContractRead,
    PaginatedResponse, SuccessResponse, ErrorResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/hospital-contracts", tags=["병원 계약"])


@router.get("", response_model=SuccessResponse)
async def list_hospital_contracts(
    company_id: Optional[int] = Query(None),
    product: Optional[str] = Query(None),
    contract_status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """병원 계약 목록"""
    query = select(HospitalContract)
    if company_id:
        query = query.where(HospitalContract.company_id == company_id)
    if product:
        query = query.where(HospitalContract.product == product)
    if contract_status:
        query = query.where(HospitalContract.contract_status == contract_status)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(HospitalContract.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    contracts = result.scalars().all()

    items = []
    for ct in contracts:
        item = HospitalContractRead.model_validate(ct)
        if ct.company_id:
            c = await db.get(Company, ct.company_id)
            if c:
                item.company_name = c.name
        items.append(item)

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size, items=items,
    ))


@router.post("", response_model=SuccessResponse)
async def create_hospital_contract(data: HospitalContractCreate, db: AsyncSession = Depends(get_db)):
    """병원 계약 생성"""
    contract = HospitalContract(**data.model_dump())
    db.add(contract)
    await db.flush()
    return SuccessResponse(data=HospitalContractRead.model_validate(contract))


@router.get("/{contract_id}", response_model=SuccessResponse)
async def get_hospital_contract(contract_id: int, db: AsyncSession = Depends(get_db)):
    """병원 계약 상세"""
    contract = await db.get(HospitalContract, contract_id)
    if not contract:
        return ErrorResponse(message="계약을 찾을 수 없습니다.")
    item = HospitalContractRead.model_validate(contract)
    if contract.company_id:
        c = await db.get(Company, contract.company_id)
        if c:
            item.company_name = c.name
    return SuccessResponse(data=item)


@router.put("/{contract_id}", response_model=SuccessResponse)
async def update_hospital_contract(contract_id: int, data: HospitalContractUpdate, db: AsyncSession = Depends(get_db)):
    """병원 계약 수정"""
    contract = await db.get(HospitalContract, contract_id)
    if not contract:
        return ErrorResponse(message="계약을 찾을 수 없습니다.")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(contract, key, value)
    await db.flush()
    return SuccessResponse(data=HospitalContractRead.model_validate(contract))


@router.delete("/{contract_id}", response_model=SuccessResponse)
async def delete_hospital_contract(contract_id: int, db: AsyncSession = Depends(get_db)):
    """병원 계약 삭제"""
    contract = await db.get(HospitalContract, contract_id)
    if not contract:
        return ErrorResponse(message="계약을 찾을 수 없습니다.")
    await db.delete(contract)
    await db.flush()
    return SuccessResponse(data={"deleted": contract_id})
