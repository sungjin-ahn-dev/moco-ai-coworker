"""
제품 리스팅 API 라우트
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import ProductListing, Company
from app.cc_web_interface.crm.schemas import (
    ProductListingCreate, ProductListingUpdate, ProductListingRead,
    PaginatedResponse, SuccessResponse, ErrorResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/product-listings", tags=["제품 리스팅"])


@router.get("", response_model=SuccessResponse)
async def list_product_listings(
    company_id: Optional[int] = Query(None),
    product: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """제품 리스팅 목록"""
    query = select(ProductListing)
    if company_id:
        query = query.where(ProductListing.company_id == company_id)
    if product:
        query = query.where(ProductListing.product == product)
    if status:
        query = query.where(ProductListing.status == status)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(ProductListing.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    listings = result.scalars().all()

    items = []
    for l in listings:
        item = ProductListingRead.model_validate(l)
        if l.company_id:
            c = await db.get(Company, l.company_id)
            if c:
                item.company_name = c.name
        items.append(item)

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size, items=items,
    ))


@router.get("/by-product", response_model=SuccessResponse)
async def get_listings_by_product(db: AsyncSession = Depends(get_db)):
    """제품별 리스팅 현황 (칸반 데이터)"""
    q = await db.execute(
        select(
            ProductListing.product,
            ProductListing.status,
            func.count(ProductListing.id).label("cnt"),
        )
        .group_by(ProductListing.product, ProductListing.status)
    )
    data = {}
    for r in q.all():
        product = r[0] or "기타"
        if product not in data:
            data[product] = {}
        data[product][r[1]] = r[2]
    return SuccessResponse(data=data)


@router.post("", response_model=SuccessResponse)
async def create_product_listing(data: ProductListingCreate, db: AsyncSession = Depends(get_db)):
    """제품 리스팅 생성"""
    listing = ProductListing(**data.model_dump())
    db.add(listing)
    await db.flush()
    return SuccessResponse(data=ProductListingRead.model_validate(listing))


@router.get("/{listing_id}", response_model=SuccessResponse)
async def get_product_listing(listing_id: int, db: AsyncSession = Depends(get_db)):
    """제품 리스팅 상세"""
    listing = await db.get(ProductListing, listing_id)
    if not listing:
        return ErrorResponse(message="리스팅을 찾을 수 없습니다.")
    item = ProductListingRead.model_validate(listing)
    if listing.company_id:
        c = await db.get(Company, listing.company_id)
        if c:
            item.company_name = c.name
    return SuccessResponse(data=item)


@router.put("/{listing_id}", response_model=SuccessResponse)
async def update_product_listing(listing_id: int, data: ProductListingUpdate, db: AsyncSession = Depends(get_db)):
    """제품 리스팅 수정"""
    listing = await db.get(ProductListing, listing_id)
    if not listing:
        return ErrorResponse(message="리스팅을 찾을 수 없습니다.")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(listing, key, value)
    await db.flush()
    return SuccessResponse(data=ProductListingRead.model_validate(listing))


@router.delete("/{listing_id}", response_model=SuccessResponse)
async def delete_product_listing(listing_id: int, db: AsyncSession = Depends(get_db)):
    """제품 리스팅 삭제"""
    listing = await db.get(ProductListing, listing_id)
    if not listing:
        return ErrorResponse(message="리스팅을 찾을 수 없습니다.")
    await db.delete(listing)
    await db.flush()
    return SuccessResponse(data={"deleted": listing_id})
