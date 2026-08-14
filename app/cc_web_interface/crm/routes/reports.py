"""
리포트 API 라우트
대시보드, 파이프라인 분석, 활동 통계, 리드 소스, 매출 예측, 영업 성과
"""

import logging
from collections import defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import (
    Contact, Company, Deal, Activity, CRMTask,
    TaskStatus, LifecycleStage, now_kst,
    Prescription, SalesTransaction, ProductListing,
)
from app.cc_web_interface.crm.schemas import (
    DashboardStats, PipelineReport, ActivityReport,
    LeadSourceReport, RevenueForecast, SalesPerformance,
    SuccessResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reports", tags=["리포트"])


@router.get("/dashboard", response_model=SuccessResponse)
async def dashboard(db: AsyncSession = Depends(get_db)):
    """대시보드 요약 통계"""
    now = now_kst()
    week_ago = now - timedelta(days=7)

    total_contacts = (await db.execute(
        select(func.count(Contact.id))
    )).scalar() or 0

    total_companies = (await db.execute(
        select(func.count(Company.id)).where(~Company.name.like("__REFERENCE_%"))
    )).scalar() or 0

    total_deals = (await db.execute(
        select(func.count(Deal.id))
    )).scalar() or 0

    # 계약완료 거래의 총 매출
    total_revenue = (await db.execute(
        select(func.coalesce(func.sum(Deal.amount), 0.0))
        .where(Deal.stage == "계약완료")
    )).scalar() or 0.0

    # 진행 중 거래 총액 (계약완료, 실주 제외)
    open_deals_value = (await db.execute(
        select(func.coalesce(func.sum(Deal.amount), 0.0))
        .where(Deal.stage.notin_(["계약완료", "실주"]))
    )).scalar() or 0.0

    # 전환율: customer 단계 연락처 / 전체 연락처
    customer_count = (await db.execute(
        select(func.count(Contact.id))
        .where(Contact.lifecycle_stage == LifecycleStage.customer)
    )).scalar() or 0
    conversion_rate = (customer_count / total_contacts * 100) if total_contacts > 0 else 0.0

    # 기한 초과 태스크
    tasks_overdue = (await db.execute(
        select(func.count(CRMTask.id))
        .where(CRMTask.status != TaskStatus.done)
        .where(CRMTask.due_date < now)
    )).scalar() or 0

    # 이번 주 활동
    activities_this_week = (await db.execute(
        select(func.count(Activity.id))
        .where(Activity.timestamp >= week_ago)
    )).scalar() or 0

    return SuccessResponse(data=DashboardStats(
        total_contacts=total_contacts,
        total_companies=total_companies,
        total_deals=total_deals,
        total_revenue=total_revenue,
        open_deals_value=open_deals_value,
        conversion_rate=round(conversion_rate, 2),
        tasks_overdue=tasks_overdue,
        activities_this_week=activities_this_week,
    ))


@router.get("/pipeline", response_model=SuccessResponse)
async def pipeline_analysis(
    pipeline_id: int = Query(None, description="파이프라인 ID (기본: 전체)"),
    db: AsyncSession = Depends(get_db),
):
    """파이프라인 분석 (단계별 거래 수, 금액)"""
    query = select(
        Deal.stage,
        func.count(Deal.id).label("deal_count"),
        func.coalesce(func.sum(Deal.amount), 0.0).label("total_value"),
        func.coalesce(func.avg(Deal.amount), 0.0).label("avg_value"),
    )
    if pipeline_id:
        query = query.where(Deal.pipeline_id == pipeline_id)
    query = query.group_by(Deal.stage)

    result = await db.execute(query)
    rows = result.all()

    reports = [
        PipelineReport(
            stage=row.stage,
            deal_count=row.deal_count,
            total_value=row.total_value,
            avg_value=round(row.avg_value, 2),
        )
        for row in rows
    ]
    return SuccessResponse(data=reports)


@router.get("/activities", response_model=SuccessResponse)
async def activity_stats(
    days: int = Query(30, description="조회 기간(일)"),
    db: AsyncSession = Depends(get_db),
):
    """활동 통계 (유형별)"""
    since = now_kst() - timedelta(days=days)
    result = await db.execute(
        select(
            Activity.type,
            func.count(Activity.id).label("count"),
        )
        .where(Activity.timestamp >= since)
        .group_by(Activity.type)
    )
    rows = result.all()

    reports = [
        ActivityReport(type=row.type, count=row.count)
        for row in rows
    ]
    return SuccessResponse(data=reports)


@router.get("/lead-sources", response_model=SuccessResponse)
async def lead_sources(db: AsyncSession = Depends(get_db)):
    """리드 소스별 통계"""
    result = await db.execute(
        select(
            func.coalesce(Contact.source, "unknown").label("source"),
            func.count(Contact.id).label("count"),
            func.sum(
                case(
                    (Contact.lifecycle_stage == LifecycleStage.customer, 1),
                    else_=0,
                )
            ).label("converted"),
        )
        .group_by(Contact.source)
    )
    rows = result.all()

    reports = [
        LeadSourceReport(
            source=row.source or "unknown",
            count=row.count,
            converted=row.converted or 0,
        )
        for row in rows
    ]
    return SuccessResponse(data=reports)


@router.get("/revenue-forecast", response_model=SuccessResponse)
async def revenue_forecast_report(
    months: int = Query(6),
    db: AsyncSession = Depends(get_db),
):
    """월별 매출 예측"""
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
            weighted_revenue=round(data["weighted"], 2),
            deal_count=data["count"],
        )
        for month, data in sorted(forecasts.items())
    ]
    return SuccessResponse(data=forecast_list[:months])


@router.get("/sales-performance", response_model=SuccessResponse)
async def sales_performance(db: AsyncSession = Depends(get_db)):
    """영업 담당자별 성과"""
    result = await db.execute(
        select(Deal).where(Deal.owner_slack_id.isnot(None))
    )
    deals = result.scalars().all()

    perf = defaultdict(lambda: {"won": 0, "lost": 0, "revenue": 0.0, "amounts": []})
    for deal in deals:
        owner = deal.owner_slack_id
        if deal.stage == "계약완료":
            perf[owner]["won"] += 1
            perf[owner]["revenue"] += deal.amount or 0
            perf[owner]["amounts"].append(deal.amount or 0)
        elif deal.stage == "실주":
            perf[owner]["lost"] += 1

    reports = []
    for owner, data in perf.items():
        total = data["won"] + data["lost"]
        reports.append(SalesPerformance(
            owner_slack_id=owner,
            deals_won=data["won"],
            deals_lost=data["lost"],
            total_revenue=data["revenue"],
            avg_deal_size=round(
                data["revenue"] / data["won"], 2
            ) if data["won"] > 0 else 0.0,
            win_rate=round(data["won"] / total * 100, 2) if total > 0 else 0.0,
        ))

    return SuccessResponse(data=reports)


@router.get("/territory", response_model=SuccessResponse)
async def territory_report(db: AsyncSession = Depends(get_db)):
    """Territory별 성과 리포트"""
    hospital_q = await db.execute(
        select(
            Company.territory_owner,
            func.count(Company.id).label("hospital_count"),
        )
        .where(Company.territory_owner.isnot(None))
        .group_by(Company.territory_owner)
    )
    hospital_data = {r[0]: {"hospital_count": r[1]} for r in hospital_q.all()}

    rx_q = await db.execute(
        select(
            Company.territory_owner,
            func.count(Prescription.id).label("rx_count"),
        )
        .join(Company, Prescription.hospital_id == Company.id)
        .where(Company.territory_owner.isnot(None))
        .group_by(Company.territory_owner)
    )
    for r in rx_q.all():
        if r[0] in hospital_data:
            hospital_data[r[0]]["rx_count"] = r[1]

    sales_q = await db.execute(
        select(
            Company.territory_owner,
            func.coalesce(func.sum(SalesTransaction.revenue), 0.0).label("revenue"),
        )
        .join(Company, SalesTransaction.company_id == Company.id)
        .where(Company.territory_owner.isnot(None))
        .group_by(Company.territory_owner)
    )
    for r in sales_q.all():
        if r[0] in hospital_data:
            hospital_data[r[0]]["revenue"] = r[1]

    listing_q = await db.execute(
        select(
            Company.territory_owner,
            func.count(ProductListing.id).label("listing_done"),
        )
        .join(Company, ProductListing.company_id == Company.id)
        .where(Company.territory_owner.isnot(None))
        .where(ProductListing.status == "done")
        .group_by(Company.territory_owner)
    )
    for r in listing_q.all():
        if r[0] in hospital_data:
            hospital_data[r[0]]["listing_done"] = r[1]

    result = []
    for owner, data in hospital_data.items():
        result.append({
            "territory_owner": owner,
            "hospital_count": data.get("hospital_count", 0),
            "rx_count": data.get("rx_count", 0),
            "revenue": data.get("revenue", 0.0),
            "listing_done": data.get("listing_done", 0),
        })

    return SuccessResponse(data=result)


@router.get("/prescription-trends", response_model=SuccessResponse)
async def prescription_trends(db: AsyncSession = Depends(get_db)):
    """처방 추이 리포트"""
    from sqlalchemy import extract

    monthly_q = await db.execute(
        select(
            extract("year", Prescription.prescribed_date).label("y"),
            extract("month", Prescription.prescribed_date).label("m"),
            func.count(Prescription.id).label("total"),
            func.count(func.distinct(Prescription.hospital_id)).label("hospitals"),
            func.count(func.distinct(Prescription.doctor_id)).label("doctors"),
        )
        .where(Prescription.prescribed_date.isnot(None))
        .group_by("y", "m")
        .order_by("y", "m")
    )

    trend = [
        {
            "year": int(r.y), "month": int(r.m),
            "total": r.total, "hospitals": r.hospitals, "doctors": r.doctors,
        }
        for r in monthly_q.all() if r.y
    ]
    return SuccessResponse(data=trend)


@router.get("/product-adoption", response_model=SuccessResponse)
async def product_adoption(db: AsyncSession = Depends(get_db)):
    """제품 도입 현황 리포트"""
    listing_q = await db.execute(
        select(
            ProductListing.product,
            ProductListing.status,
            func.count(ProductListing.id).label("cnt"),
        )
        .group_by(ProductListing.product, ProductListing.status)
    )

    products = {}
    for r in listing_q.all():
        product = r[0] or "기타"
        if product not in products:
            products[product] = {"total": 0, "by_status": {}}
        products[product]["by_status"][r[1]] = r[2]
        products[product]["total"] += r[2]

    rx_by_type = await db.execute(
        select(
            Prescription.prescription_type,
            func.count(Prescription.id).label("cnt"),
        )
        .group_by(Prescription.prescription_type)
    )
    rx_data = {r[0]: r[1] for r in rx_by_type.all()}

    return SuccessResponse(data={
        "listings": products,
        "prescriptions_by_type": rx_data,
    })
