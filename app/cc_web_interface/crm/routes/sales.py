"""
매출 관리 API 라우트
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import SalesTransaction, Company
from app.cc_web_interface.crm.schemas import (
    SalesTransactionCreate, SalesTransactionUpdate, SalesTransactionRead,
    SalesSummary,
    PaginatedResponse, SuccessResponse, ErrorResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sales", tags=["매출관리"])


@router.get("", response_model=SuccessResponse)
async def list_sales(
    company_id: Optional[int] = Query(None),
    product: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    ownership: Optional[str] = Query(None),
    payment_received: Optional[bool] = Query(None),
    sort_by: str = Query("year"),
    sort_order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """매출 목록 조회"""
    query = select(SalesTransaction)

    if company_id:
        query = query.where(SalesTransaction.company_id == company_id)
    if product:
        query = query.where(SalesTransaction.product == product)
    if year:
        query = query.where(SalesTransaction.year == year)
    if month:
        query = query.where(SalesTransaction.month == month)
    if ownership:
        query = query.where(SalesTransaction.ownership == ownership)
    if payment_received is not None:
        query = query.where(SalesTransaction.payment_received == payment_received)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    sort_col = getattr(SalesTransaction, sort_by, SalesTransaction.year)
    if sort_order == "asc":
        query = query.order_by(sort_col.asc())
    else:
        query = query.order_by(sort_col.desc())

    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    sales = result.scalars().all()

    items = []
    for s in sales:
        item = SalesTransactionRead.model_validate(s)
        if s.company_id:
            c = await db.get(Company, s.company_id)
            if c:
                item.company_name = c.name
        items.append(item)

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size, items=items,
    ))


@router.get("/summary", response_model=SuccessResponse)
async def get_sales_summary(
    year: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """매출 집계"""
    base_filter = []
    if year:
        base_filter.append(SalesTransaction.year == year)

    # 총 매출/수량/입금
    totals = await db.execute(
        select(
            func.coalesce(func.sum(SalesTransaction.revenue), 0.0),
            func.coalesce(func.sum(SalesTransaction.quantity), 0),
        ).where(*base_filter)
    )
    row = totals.one()
    total_revenue = row[0]
    total_quantity = row[1]

    received = await db.execute(
        select(func.coalesce(func.sum(SalesTransaction.revenue), 0.0))
        .where(SalesTransaction.payment_received == True, *base_filter)
    )
    total_received = received.scalar() or 0.0

    # 월별 추이
    monthly_q = await db.execute(
        select(
            SalesTransaction.year,
            SalesTransaction.month,
            func.sum(SalesTransaction.revenue).label("revenue"),
            func.sum(SalesTransaction.quantity).label("qty"),
        )
        .where(*base_filter)
        .group_by(SalesTransaction.year, SalesTransaction.month)
        .order_by(SalesTransaction.year, SalesTransaction.month)
    )
    monthly_trend = [
        {"year": r[0], "month": r[1], "revenue": r[2] or 0, "quantity": r[3] or 0}
        for r in monthly_q.all()
    ]

    # 제품별
    product_q = await db.execute(
        select(
            SalesTransaction.product,
            func.sum(SalesTransaction.revenue).label("revenue"),
            func.sum(SalesTransaction.quantity).label("qty"),
        )
        .where(*base_filter)
        .group_by(SalesTransaction.product)
        .order_by(func.sum(SalesTransaction.revenue).desc())
    )
    by_product = [
        {"product": r[0] or "기타", "revenue": r[1] or 0, "quantity": r[2] or 0}
        for r in product_q.all()
    ]

    return SuccessResponse(data=SalesSummary(
        total_revenue=total_revenue,
        total_quantity=total_quantity,
        total_received=total_received,
        monthly_trend=monthly_trend,
        by_product=by_product,
    ))


@router.get("/by-product", response_model=SuccessResponse)
async def get_sales_by_product(
    year: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """제품별 매출"""
    base_filter = []
    if year:
        base_filter.append(SalesTransaction.year == year)

    q = await db.execute(
        select(
            SalesTransaction.product,
            func.sum(SalesTransaction.revenue).label("revenue"),
            func.sum(SalesTransaction.quantity).label("qty"),
            func.count(SalesTransaction.id).label("count"),
        )
        .where(*base_filter)
        .group_by(SalesTransaction.product)
        .order_by(func.sum(SalesTransaction.revenue).desc())
    )
    items = [
        {"product": r[0] or "기타", "revenue": r[1] or 0, "quantity": r[2] or 0, "count": r[3]}
        for r in q.all()
    ]
    return SuccessResponse(data=items)


@router.post("", response_model=SuccessResponse)
async def create_sale(data: SalesTransactionCreate, db: AsyncSession = Depends(get_db)):
    """매출 생성"""
    sale = SalesTransaction(**data.model_dump())
    db.add(sale)
    await db.flush()
    return SuccessResponse(data=SalesTransactionRead.model_validate(sale))


@router.get("/{sale_id}", response_model=SuccessResponse)
async def get_sale(sale_id: int, db: AsyncSession = Depends(get_db)):
    """매출 상세 조회"""
    sale = await db.get(SalesTransaction, sale_id)
    if not sale:
        return ErrorResponse(message="매출 데이터를 찾을 수 없습니다.")

    item = SalesTransactionRead.model_validate(sale)
    if sale.company_id:
        c = await db.get(Company, sale.company_id)
        if c:
            item.company_name = c.name
    return SuccessResponse(data=item)


@router.put("/{sale_id}", response_model=SuccessResponse)
async def update_sale(sale_id: int, data: SalesTransactionUpdate, db: AsyncSession = Depends(get_db)):
    """매출 수정"""
    sale = await db.get(SalesTransaction, sale_id)
    if not sale:
        return ErrorResponse(message="매출 데이터를 찾을 수 없습니다.")

    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(sale, key, value)
    await db.flush()
    return SuccessResponse(data=SalesTransactionRead.model_validate(sale))


@router.delete("/{sale_id}", response_model=SuccessResponse)
async def delete_sale(sale_id: int, db: AsyncSession = Depends(get_db)):
    """매출 삭제"""
    sale = await db.get(SalesTransaction, sale_id)
    if not sale:
        return ErrorResponse(message="매출 데이터를 찾을 수 없습니다.")
    await db.delete(sale)
    await db.flush()
    return SuccessResponse(data={"deleted": sale_id})
