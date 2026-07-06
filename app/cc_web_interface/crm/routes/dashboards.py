"""
수식 기반 대시보드 API
Excel 현황판의 COUNTIF/SUMIF를 실시간 DB 쿼리로 구현
"""

import json
import logging
from typing import Optional
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, case, extract, distinct, text, literal_column, or_, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import (
    Prescription, PatientCompliance, Company, Contact,
    SalesTransaction, ProductListing, KOLPlan, HospitalContract,
    ReferenceData,
)
from app.cc_web_interface.crm.schemas import SuccessResponse

# 제품A 처방관리 원본 데이터만 사용 (중복 제외)
_RX_VALID = Prescription.prescription_code.isnot(None)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboards", tags=["대시보드 (수식기반)"])


def _hospital_category(hospital_type_col):
    """병원 구분을 상급/종합 vs 의원으로 분류하는 CASE 표현식"""
    return case(
        (hospital_type_col.in_(['상급종합', '상급종합병원', '종합병원', '병원']), '상급/종합병원'),
        (hospital_type_col == '의원', '의원'),
        else_='미분류'
    )


@router.get("/prescription-dashboard", response_model=SuccessResponse)
async def prescription_dashboard(
    year: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """제품A 처방 현황판 — Excel E_Sum 시트 수식 재현

    모든 숫자는 prescriptions 테이블에서 실시간 계산:
    - 월별 처방건수 = COUNT(prescribed_date가 해당 월인 레코드)
    - Naive = session_number = 1
    - Repeat = session_number > 1
    - 상급/종합 vs 의원 = companies.hospital_type JOIN
    - Active User = patient_compliance에서 compliance_rate > 0
    - Progress = Actual / Target
    """

    # ── 월별 처방 (병원구분별, Naive/Repeat) ──
    monthly_q = await db.execute(
        select(
            extract("year", Prescription.prescribed_date).label("y"),
            extract("month", Prescription.prescribed_date).label("m"),
            _hospital_category(Company.hospital_type).label("category"),
            func.count(Prescription.id).label("total"),
            func.sum(case((Prescription.session_number <= 1, 1), else_=0)).label("naive"),
            func.sum(case((Prescription.session_number > 1, 1), else_=0)).label("repeat_rx"),
            func.count(distinct(Prescription.patient_id)).label("unique_patients"),
            func.count(distinct(Prescription.doctor_id)).label("unique_doctors"),
        )
        .outerjoin(Company, Prescription.hospital_id == Company.id)
        .where(_RX_VALID, Prescription.prescribed_date.isnot(None))
        .group_by("y", "m", "category")
        .order_by("y", "m")
    )
    rows = monthly_q.all()

    # 구조화
    monthly_data = {}  # {(year, month): {category: {total, naive, repeat, patients, doctors}}}
    for r in rows:
        if not r.y:
            continue
        key = (int(r.y), int(r.m))
        if key not in monthly_data:
            monthly_data[key] = {}
        monthly_data[key][r.category] = {
            "total": r.total,
            "naive": r.naive,
            "repeat": r.repeat_rx,
            "unique_patients": r.unique_patients,
            "unique_doctors": r.unique_doctors,
        }

    # 월별 합계 계산
    months = []
    for (y, m), cats in sorted(monthly_data.items()):
        hospital = cats.get("상급/종합병원", {"total": 0, "naive": 0, "repeat": 0})
        clinic = cats.get("의원", {"total": 0, "naive": 0, "repeat": 0})
        unclassified = cats.get("미분류", {"total": 0, "naive": 0, "repeat": 0})
        total = hospital["total"] + clinic["total"] + unclassified["total"]
        total_naive = hospital["naive"] + clinic["naive"] + unclassified["naive"]
        total_repeat = hospital["repeat"] + clinic["repeat"] + unclassified["repeat"]

        months.append({
            "year": y, "month": m,
            "상급_종합": hospital,
            "의원": clinic,
            "미분류": unclassified,
            "합계": {
                "total": total, "naive": total_naive, "repeat": total_repeat,
            },
            "repeat_ratio": round(total_repeat / total * 100, 1) if total > 0 else 0,
        })

    # ── 연도 합계 ──
    yearly = {}
    for m in months:
        y = m["year"]
        if y not in yearly:
            yearly[y] = {"상급_종합": 0, "의원": 0, "합계": 0, "naive": 0, "repeat": 0}
        yearly[y]["상급_종합"] += m["상급_종합"]["total"]
        yearly[y]["의원"] += m["의원"]["total"]
        yearly[y]["합계"] += m["합계"]["total"]
        yearly[y]["naive"] += m["합계"]["naive"]
        yearly[y]["repeat"] += m["합계"]["repeat"]

    # ── Active Users (순응도 기반) ──
    active_q = await db.execute(
        select(func.count(PatientCompliance.id))
        .where(PatientCompliance.compliance_rate > 0)
    )
    active_users = active_q.scalar() or 0

    total_patients = (await db.execute(
        select(func.count(PatientCompliance.id))
    )).scalar() or 0

    # 월별 Active User (처방 기반 유니크 환자)
    monthly_active = await db.execute(
        select(
            extract("year", Prescription.prescribed_date).label("y"),
            extract("month", Prescription.prescribed_date).label("m"),
            func.count(distinct(Prescription.patient_id)).label("cnt"),
        )
        .where(_RX_VALID, Prescription.prescribed_date.isnot(None))
        .where(Prescription.patient_id.isnot(None))
        .group_by("y", "m")
        .order_by("y", "m")
    )
    active_by_month = [
        {"year": int(r.y), "month": int(r.m), "count": r.cnt}
        for r in monthly_active.all() if r.y
    ]

    # ── 전체 통계 ──
    total_rx = (await db.execute(select(func.count(Prescription.id)).where(_RX_VALID))).scalar() or 0
    total_hospitals = (await db.execute(
        select(func.count(distinct(Prescription.hospital_id)))
        .where(_RX_VALID, Prescription.hospital_id.isnot(None))
    )).scalar() or 0
    total_doctors = (await db.execute(
        select(func.count(distinct(Prescription.doctor_id)))
        .where(_RX_VALID, Prescription.doctor_id.isnot(None))
    )).scalar() or 0

    # 처방과별 (NP/NR)
    by_dept = await db.execute(
        select(
            Prescription.prescription_type,
            func.count(Prescription.id).label("cnt"),
        )
        .where(_RX_VALID, Prescription.prescription_type.isnot(None))
        .group_by(Prescription.prescription_type)
    )
    dept_data = {r[0]: r[1] for r in by_dept.all()}

    return SuccessResponse(data={
        "monthly": months,
        "yearly": yearly,
        "active_users": active_users,
        "total_patients": total_patients,
        "active_by_month": active_by_month,
        "totals": {
            "total_rx": total_rx,
            "total_hospitals": total_hospitals,
            "total_doctors": total_doctors,
            "by_department": dept_data,
        },
    })


@router.get("/listing-dashboard", response_model=SuccessResponse)
async def listing_dashboard(db: AsyncSession = Depends(get_db)):
    """제품A 리스팅 현황판 — Excel E_Sum / 제품A 요약 수식 재현

    - 제품별 리스팅 상태 = ProductListing 테이블 COUNT by status
    - 병원구분별 리스팅 = companies.hospital_type JOIN
    - 담당자별 = companies.territory_owner
    - Target vs Actual = status='done' 개수
    """

    # 제품별 + 상태별 (임시등재 제외)
    product_status = await db.execute(
        select(
            ProductListing.product,
            ProductListing.status,
            func.count(ProductListing.id).label("cnt"),
        )
        .where(or_(ProductListing.pipeline_stage.is_(None), ProductListing.pipeline_stage != "임시등재"))
        .group_by(ProductListing.product, ProductListing.status)
    )
    products = {}
    for r in product_status.all():
        p = r[0] or "기타"
        if p not in products:
            products[p] = {"total": 0, "by_status": {}}
        products[p]["by_status"][r[1]] = r[2]
        products[p]["total"] += r[2]

    # 병원구분별 리스팅 (임시등재 제외)
    by_hospital_type = await db.execute(
        select(
            _hospital_category(Company.hospital_type).label("category"),
            ProductListing.status,
            func.count(ProductListing.id).label("cnt"),
        )
        .join(Company, ProductListing.company_id == Company.id)
        .where(or_(ProductListing.pipeline_stage.is_(None), ProductListing.pipeline_stage != "임시등재"))
        .group_by("category", ProductListing.status)
    )
    by_type = {}
    for r in by_hospital_type.all():
        cat = r[0]
        if cat not in by_type:
            by_type[cat] = {"total": 0, "done": 0, "in_progress": 0, "pending": 0}
        by_type[cat][r[1]] = by_type[cat].get(r[1], 0) + r[2]
        by_type[cat]["total"] += r[2]

    # 담당자별 리스팅 (임시등재 제외)
    by_owner = await db.execute(
        select(
            Company.territory_owner,
            ProductListing.status,
            func.count(ProductListing.id).label("cnt"),
        )
        .join(Company, ProductListing.company_id == Company.id)
        .where(Company.territory_owner.isnot(None))
        .where(or_(ProductListing.pipeline_stage.is_(None), ProductListing.pipeline_stage != "임시등재"))
        .group_by(Company.territory_owner, ProductListing.status)
    )
    owners = {}
    for r in by_owner.all():
        o = r[0]
        if o not in owners:
            owners[o] = {"total": 0, "done": 0, "in_progress": 0}
        owners[o][r[1]] = owners[o].get(r[1], 0) + r[2]
        owners[o]["total"] += r[2]

    # 리스팅 완료 병원의 처방 현황 (리스팅 → 처방 연결, 임시등재 제외)
    listing_to_rx = await db.execute(
        select(
            Company.name,
            func.count(distinct(Prescription.id)).label("rx_count"),
            func.count(distinct(Prescription.doctor_id)).label("doctor_count"),
        )
        .join(ProductListing, ProductListing.company_id == Company.id)
        .outerjoin(Prescription, Prescription.hospital_id == Company.id)
        .where(ProductListing.status == "done")
        .where(or_(ProductListing.pipeline_stage.is_(None), ProductListing.pipeline_stage != "임시등재"))
        .group_by(Company.name)
        .order_by(func.count(distinct(Prescription.id)).desc())
        .limit(20)
    )
    listing_rx = [
        {"hospital": r[0], "rx_count": r[1], "doctor_count": r[2]}
        for r in listing_to_rx.all()
    ]

    return SuccessResponse(data={
        "by_product": products,
        "by_hospital_type": by_type,
        "by_owner": owners,
        "listing_to_prescription": listing_rx,
    })


@router.get("/sales-dashboard", response_model=SuccessResponse)
async def sales_dashboard(db: AsyncSession = Depends(get_db)):
    """매출 현황 대시보드 — Sales Tracking + 혁신의료기술 매출 통합

    - 월별 매출 = SalesTransaction SUM(revenue) GROUP BY year, month
    - 제품별 = SUM(revenue) GROUP BY product
    - 채널별 = SUM(revenue) GROUP BY channel
    - 입금현황 = payment_received 기반
    """

    # 월별
    monthly = await db.execute(
        select(
            SalesTransaction.year,
            SalesTransaction.month,
            func.sum(SalesTransaction.revenue).label("revenue"),
            func.sum(SalesTransaction.quantity).label("quantity"),
            func.sum(case((SalesTransaction.payment_received == True, SalesTransaction.revenue), else_=0)).label("received"),
        )
        .group_by(SalesTransaction.year, SalesTransaction.month)
        .order_by(SalesTransaction.year, SalesTransaction.month)
    )
    monthly_data = [
        {"year": r[0], "month": r[1], "revenue": r[2] or 0,
         "quantity": r[3] or 0, "received": r[4] or 0}
        for r in monthly.all()
    ]

    # 제품별
    by_product = await db.execute(
        select(
            SalesTransaction.product,
            func.sum(SalesTransaction.revenue).label("revenue"),
            func.count(SalesTransaction.id).label("count"),
        )
        .group_by(SalesTransaction.product)
        .order_by(func.sum(SalesTransaction.revenue).desc())
    )
    product_data = [
        {"product": r[0] or "기타", "revenue": r[1] or 0, "count": r[2]}
        for r in by_product.all()
    ]

    # 채널별
    by_channel = await db.execute(
        select(
            SalesTransaction.channel,
            func.sum(SalesTransaction.revenue).label("revenue"),
            func.count(SalesTransaction.id).label("count"),
        )
        .where(SalesTransaction.channel.isnot(None))
        .group_by(SalesTransaction.channel)
        .order_by(func.sum(SalesTransaction.revenue).desc())
    )
    channel_data = [
        {"channel": r[0], "revenue": r[1] or 0, "count": r[2]}
        for r in by_channel.all()
    ]

    # 총계
    totals = await db.execute(
        select(
            func.sum(SalesTransaction.revenue),
            func.sum(SalesTransaction.quantity),
            func.sum(case((SalesTransaction.payment_received == True, SalesTransaction.revenue), else_=0)),
            func.count(SalesTransaction.id),
        )
    )
    t = totals.one()

    return SuccessResponse(data={
        "monthly": monthly_data,
        "by_product": product_data,
        "by_channel": channel_data,
        "totals": {
            "revenue": t[0] or 0,
            "quantity": t[1] or 0,
            "received": t[2] or 0,
            "count": t[3] or 0,
        },
    })


@router.get("/territory-dashboard", response_model=SuccessResponse)
async def territory_dashboard(db: AsyncSession = Depends(get_db)):
    """Territory 현황판 — Acc_sum / Sales R&R 수식 재현

    담당자별:
    - 병원수 = companies.territory_owner COUNT
    - 타겟 병원수 = is_target=true COUNT
    - 커버리지 = 타겟/전체
    - 의사수 = contacts with company in territory COUNT
    - 처방수 = prescriptions through company COUNT
    - 리스팅 완료수 = product_listings status=done COUNT
    """

    # 담당자별 병원
    hospital_q = await db.execute(
        select(
            Company.territory_owner,
            func.count(Company.id).label("total"),
            func.sum(case((Company.is_target == True, 1), else_=0)).label("target"),
        )
        .where(Company.territory_owner.isnot(None))
        .group_by(Company.territory_owner)
    )
    owners = {}
    for r in hospital_q.all():
        owners[r[0]] = {
            "hospital_total": r[1],
            "hospital_target": r[2],
            "coverage": round(r[2] / r[1] * 100, 1) if r[1] > 0 else 0,
        }

    # 담당자별 의사수
    doctor_q = await db.execute(
        select(
            Company.territory_owner,
            func.count(Contact.id).label("cnt"),
        )
        .join(Contact, Contact.company_id == Company.id)
        .where(Company.territory_owner.isnot(None))
        .group_by(Company.territory_owner)
    )
    for r in doctor_q.all():
        if r[0] in owners:
            owners[r[0]]["doctor_count"] = r[1]

    # 담당자별 처방수
    rx_q = await db.execute(
        select(
            Company.territory_owner,
            func.count(Prescription.id).label("cnt"),
        )
        .join(Company, Prescription.hospital_id == Company.id)
        .where(Company.territory_owner.isnot(None))
        .group_by(Company.territory_owner)
    )
    for r in rx_q.all():
        if r[0] in owners:
            owners[r[0]]["rx_count"] = r[1]

    # 담당자별 리스팅 완료
    listing_q = await db.execute(
        select(
            Company.territory_owner,
            func.count(ProductListing.id).label("total"),
            func.sum(case((ProductListing.status == "done", 1), else_=0)).label("done"),
        )
        .join(Company, ProductListing.company_id == Company.id)
        .where(Company.territory_owner.isnot(None))
        .group_by(Company.territory_owner)
    )
    for r in listing_q.all():
        if r[0] in owners:
            owners[r[0]]["listing_total"] = r[1]
            owners[r[0]]["listing_done"] = r[2]

    # 담당자별 매출
    sales_q = await db.execute(
        select(
            Company.territory_owner,
            func.sum(SalesTransaction.revenue).label("revenue"),
        )
        .join(Company, SalesTransaction.company_id == Company.id)
        .where(Company.territory_owner.isnot(None))
        .group_by(Company.territory_owner)
    )
    for r in sales_q.all():
        if r[0] in owners:
            owners[r[0]]["revenue"] = r[1] or 0

    result = [
        {"owner": k, **v}
        for k, v in owners.items()
    ]

    return SuccessResponse(data=result)


@router.get("/competition-dashboard", response_model=SuccessResponse)
async def competition_dashboard(db: AsyncSession = Depends(get_db)):
    """경쟁 현황 — 슈퍼브레인 공략 데이터 기반
    우리 회사(제품A) vs 로완(슈퍼브레인) 선점 현황
    """

    # 슈퍼브레인 공략 데이터는 company.custom_properties에 저장
    # 제품 리스팅 상태로 선점 현황 판단
    listing_q = await db.execute(
        select(
            _hospital_category(Company.hospital_type).label("category"),
            ProductListing.product,
            ProductListing.status,
            func.count(ProductListing.id).label("cnt"),
        )
        .join(Company, ProductListing.company_id == Company.id)
        .group_by("category", ProductListing.product, ProductListing.status)
    )

    data = {}
    for r in listing_q.all():
        cat = r[0]
        if cat not in data:
            data[cat] = {}
        product = r[1]
        if product not in data[cat]:
            data[cat][product] = {}
        data[cat][product][r[2]] = r[3]

    return SuccessResponse(data=data)


@router.get("/kol-dashboard", response_model=SuccessResponse)
async def kol_dashboard(db: AsyncSession = Depends(get_db)):
    """KOL 현황 대시보드
    - 역할별(PM/RM/NR/NP) KOL 수
    - 병원별 KOL 수
    - 외래 스케줄 보유율
    """

    by_type = await db.execute(
        select(
            KOLPlan.plan_type,
            func.count(KOLPlan.id).label("cnt"),
            func.count(distinct(KOLPlan.company_id)).label("hospitals"),
            func.count(distinct(KOLPlan.doctor_id)).label("doctors"),
        )
        .group_by(KOLPlan.plan_type)
    )
    type_data = [
        {"type": r[0] or "기타", "count": r[1], "hospitals": r[2], "doctors": r[3]}
        for r in by_type.all()
    ]

    # Top 병원 (KOL 수)
    top_hospitals = await db.execute(
        select(
            Company.name,
            func.count(KOLPlan.id).label("kol_count"),
        )
        .join(Company, KOLPlan.company_id == Company.id)
        .group_by(Company.name)
        .order_by(func.count(KOLPlan.id).desc())
        .limit(15)
    )
    hospital_data = [
        {"hospital": r[0], "kol_count": r[1]}
        for r in top_hospitals.all()
    ]

    # 전체 통계
    totals = await db.execute(
        select(
            func.count(KOLPlan.id),
            func.count(distinct(KOLPlan.company_id)),
            func.count(distinct(KOLPlan.doctor_id)),
        )
    )
    t = totals.one()

    return SuccessResponse(data={
        "by_type": type_data,
        "top_hospitals": hospital_data,
        "totals": {
            "total_plans": t[0],
            "unique_hospitals": t[1],
            "unique_doctors": t[2],
        },
    })


@router.get("/contract-dashboard", response_model=SuccessResponse)
async def contract_dashboard(db: AsyncSession = Depends(get_db)):
    """유통A 병원계약 현황 — 파이프라인 단계별 진행상황"""

    contracts = await db.execute(
        select(
            HospitalContract.id,
            Company.name,
            HospitalContract.product,
            HospitalContract.contract_status,
            HospitalContract.custom_properties,
        )
        .join(Company, HospitalContract.company_id == Company.id)
        .order_by(Company.name)
    )

    items = []
    for r in contracts.all():
        cp = r[4] or {}
        if isinstance(cp, str):
            import json
            cp = json.loads(cp)
        pipeline = cp.get("pipeline_steps", {})
        items.append({
            "id": r[0],
            "hospital": r[1],
            "product": r[2],
            "status": r[3],
            "담당자": cp.get("담당자"),
            "pipeline": pipeline,
            "Speciality": cp.get("Speciality"),
            "KOLs": cp.get("KOLs"),
        })

    # 파이프라인 단계별 집계
    stages = {}
    for item in items:
        for stage, val in item["pipeline"].items():
            if stage not in stages:
                stages[stage] = {"완료": 0, "진행중": 0, "기타": 0}
            if "완료" in str(val):
                stages[stage]["완료"] += 1
            elif "진행" in str(val):
                stages[stage]["진행중"] += 1
            else:
                stages[stage]["기타"] += 1

    return SuccessResponse(data={
        "contracts": items,
        "pipeline_summary": stages,
        "total": len(items),
    })


# ──────────────────────── Helper: load reference data ────────────────────────

async def _get_reference_data(db: AsyncSession, key: str = None):
    """reference_data 테이블에서 참조 데이터를 로드

    Migration note: 이전에는 companies 테이블의 SFE_참조데이터 레코드에서
    custom_properties로 저장했으나, reference_data 전용 테이블로 이전됨.
    """
    if key:
        result = await db.execute(
            select(ReferenceData.data).where(ReferenceData.key == key)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        return row if isinstance(row, dict) else json.loads(row)
    else:
        # Return all reference data as a dict
        result = await db.execute(select(ReferenceData.key, ReferenceData.data))
        all_data = {}
        for r in result.all():
            data = r[1] if isinstance(r[1], dict) else json.loads(r[1])
            all_data[r[0]] = data
        return all_data


# ──────────────────────── E_Sum 리스팅+처방 현황판 ────────────────────────

@router.get("/e-sum", response_model=SuccessResponse)
async def e_sum_dashboard(db: AsyncSession = Depends(get_db)):
    """E_Sum 리스팅+처방 현황판

    - Listing: Target (stored) vs Actual (COUNT from product_listings WHERE pipeline_stage != '임시등재')
    - Prescription: computed from prescriptions table (monthly, by hospital_type, Naive/Repeat)
    - Progress = Actual / Target
    """
    targets = await _get_reference_data(db, "E_Sum_Targets")
    if not targets:
        targets = {}

    # ── Listing Actual from DB ──
    # Exclude 임시등재 records (those with pipeline_stage='임시등재' or custom_properties containing source=임시등재)
    listing_q = await db.execute(
        select(
            _hospital_category(Company.hospital_type).label("category"),
            extract("year", ProductListing.listed_at).label("y"),
            extract("month", ProductListing.listed_at).label("m"),
            func.count(ProductListing.id).label("cnt"),
        )
        .join(Company, ProductListing.company_id == Company.id)
        .where(or_(ProductListing.pipeline_stage.is_(None), ProductListing.pipeline_stage != "임시등재"))
        .where(ProductListing.listed_at.isnot(None))
        .group_by("category", "y", "m")
        .order_by("y", "m")
    )

    listing_actual = {}
    for r in listing_q.all():
        if not r.y:
            continue
        key = f"{int(r.y)}"
        cat = r.category
        m_str = str(int(r.m)).zfill(2)
        if key not in listing_actual:
            listing_actual[key] = {}
        if cat not in listing_actual[key]:
            listing_actual[key][cat] = {}
        listing_actual[key][cat][m_str] = r.cnt

    # Total listing count (non-임시등재)
    total_listing = await db.execute(
        select(
            _hospital_category(Company.hospital_type).label("category"),
            func.count(ProductListing.id).label("cnt"),
        )
        .join(Company, ProductListing.company_id == Company.id)
        .where(or_(ProductListing.pipeline_stage.is_(None), ProductListing.pipeline_stage != "임시등재"))
        .group_by("category")
    )
    listing_totals = {r[0]: r[1] for r in total_listing.all()}

    # ── Prescription from DB ──
    rx_q = await db.execute(
        select(
            extract("year", Prescription.prescribed_date).label("y"),
            extract("month", Prescription.prescribed_date).label("m"),
            _hospital_category(Company.hospital_type).label("category"),
            func.count(Prescription.id).label("total"),
            func.sum(case((Prescription.session_number <= 1, 1), else_=0)).label("naive"),
            func.sum(case((Prescription.session_number > 1, 1), else_=0)).label("repeat_rx"),
        )
        .outerjoin(Company, Prescription.hospital_id == Company.id)
        .where(_RX_VALID, Prescription.prescribed_date.isnot(None))
        .group_by("y", "m", "category")
        .order_by("y", "m")
    )
    prescription_data = {}
    for r in rx_q.all():
        if not r.y:
            continue
        key = (int(r.y), int(r.m))
        if key not in prescription_data:
            prescription_data[key] = {}
        prescription_data[key][r.category] = {
            "total": r.total,
            "naive": r.naive,
            "repeat": r.repeat_rx,
        }

    # Active users by month
    active_q = await db.execute(
        select(
            extract("year", Prescription.prescribed_date).label("y"),
            extract("month", Prescription.prescribed_date).label("m"),
            func.count(distinct(Prescription.patient_id)).label("cnt"),
        )
        .where(_RX_VALID, Prescription.prescribed_date.isnot(None))
        .where(Prescription.patient_id.isnot(None))
        .group_by("y", "m")
    )
    active_users = {
        f"{int(r.y)}-{str(int(r.m)).zfill(2)}": r.cnt
        for r in active_q.all() if r.y
    }

    # Build prescription monthly array
    rx_months = []
    for (y, m), cats in sorted(prescription_data.items()):
        hospital = cats.get("상급/종합병원", {"total": 0, "naive": 0, "repeat": 0})
        clinic = cats.get("의원", {"total": 0, "naive": 0, "repeat": 0})
        total = hospital["total"] + clinic["total"]
        rx_months.append({
            "year": y, "month": m,
            "상급_종합": hospital,
            "의원": clinic,
            "합계": {"total": total, "naive": hospital["naive"] + clinic["naive"], "repeat": hospital["repeat"] + clinic["repeat"]},
            "active_users": active_users.get(f"{y}-{str(m).zfill(2)}", 0),
        })

    return SuccessResponse(data={
        "listing": {
            "targets": targets.get("listing", {}),
            "actual_by_month": listing_actual,
            "totals": listing_totals,
        },
        "prescription": {
            "targets": targets.get("prescription", {}),
            "monthly": rx_months,
            "active_users": active_users,
        },
    })


# ──────────────────────── OKR ────────────────────────

@router.get("/okr", response_model=SuccessResponse)
async def okr_dashboard(db: AsyncSession = Depends(get_db)):
    """OKR Phasing data + actual achievement from DB"""

    okr = await _get_reference_data(db, "OKR_Phasing")
    if not okr:
        okr = {}

    # Actual listing count (non-임시등재) for producta
    producta_actual = await db.execute(
        select(func.count(ProductListing.id))
        .where(ProductListing.product == "제품A")
        .where(or_(ProductListing.pipeline_stage.is_(None), ProductListing.pipeline_stage != "임시등재"))
    )
    producta_listing_actual = producta_actual.scalar() or 0

    # Actual listing for productc
    productc_actual = await db.execute(
        select(func.count(ProductListing.id))
        .where(ProductListing.product == "제품C")
        .where(or_(ProductListing.pipeline_stage.is_(None), ProductListing.pipeline_stage != "임시등재"))
    )
    productc_listing_actual = productc_actual.scalar() or 0

    # Actual Rx count
    total_rx = await db.execute(select(func.count(Prescription.id)).where(_RX_VALID))
    total_rx_count = total_rx.scalar() or 0

    return SuccessResponse(data={
        "okr_phasing": okr,
        "actuals": {
            "producta_listing": producta_listing_actual,
            "productc_listing": productc_listing_actual,
            "total_rx": total_rx_count,
        },
    })


# ──────────────────────── Biz Plan ────────────────────────

@router.get("/biz-plan", response_model=SuccessResponse)
async def biz_plan_dashboard(db: AsyncSession = Depends(get_db)):
    """Biz Plan targets + actual adoption counts from product_listings"""

    biz_plan = await _get_reference_data(db, "Biz_Plan_Forecasting")
    if not biz_plan:
        biz_plan = {}

    # Actual adoption by product (non-임시등재)
    adoption_q = await db.execute(
        select(
            ProductListing.product,
            _hospital_category(Company.hospital_type).label("category"),
            func.count(ProductListing.id).label("cnt"),
        )
        .join(Company, ProductListing.company_id == Company.id)
        .where(or_(ProductListing.pipeline_stage.is_(None), ProductListing.pipeline_stage != "임시등재"))
        .group_by(ProductListing.product, "category")
    )
    actual_adoption = {}
    for r in adoption_q.all():
        p = r[0] or "기타"
        if p not in actual_adoption:
            actual_adoption[p] = {}
        actual_adoption[p][r[1]] = r[2]

    return SuccessResponse(data={
        "biz_plan": biz_plan,
        "actual_adoption": actual_adoption,
    })


# ──────────────────────── 제품C 요약 ────────────────────────

@router.get("/productc-summary", response_model=SuccessResponse)
async def productc_summary_dashboard(db: AsyncSession = Depends(get_db)):
    """제품C summary data + actual listing counts"""

    summary = await _get_reference_data(db, "제품C_요약")
    if not summary:
        summary = {}

    # Actual productc listing count
    actual = await db.execute(
        select(func.count(ProductListing.id))
        .where(ProductListing.product == "제품C")
        .where(or_(ProductListing.pipeline_stage.is_(None), ProductListing.pipeline_stage != "임시등재"))
    )
    actual_count = actual.scalar() or 0

    return SuccessResponse(data={
        "summary": summary,
        "actual_listing_count": actual_count,
    })


# ──────────────────────── CAT 요약 ────────────────────────

@router.get("/cat-summary", response_model=SuccessResponse)
async def cat_summary_dashboard(db: AsyncSession = Depends(get_db)):
    """CAT test volume data"""

    summary = await _get_reference_data(db, "CAT_요약")
    if not summary:
        summary = {}

    return SuccessResponse(data=summary)


# ──────────────────────── ProductA Report ────────────────────────

@router.get("/producta-report", response_model=SuccessResponse)
async def producta_report(db: AsyncSession = Depends(get_db)):
    """제품A 보고서 기준 데이터 반환

    reference_data 테이블의 producta_report 키에 저장된
    공식 보고서 수치를 반환. 대시보드에서 DB 계산값 대신 사용.
    """
    data = await _get_reference_data(db, "producta_report")
    if not data:
        return SuccessResponse(data=None)
    return SuccessResponse(data=data)


# ──────────────────────── Report Generation via MOCO ────────────────────────

@router.post("/generate-report", response_model=SuccessResponse)
async def generate_monthly_report(db: AsyncSession = Depends(get_db)):
    """MOCO orchestrator를 직접 호출하여 제품A 월간 보고서 생성"""
    import asyncio

    report_prompt = (
        "제품A 월간 처방/리스팅 보고서를 만들어줘. "
        "CRM 데이터(mcp__crm__crm_prescription_stats, mcp__crm__crm_listing_dashboard, "
        "mcp__crm__crm_territory_dashboard, mcp__crm__crm_prescription_dashboard)를 조회해서 "
        "아래 형식으로 마크다운 보고서를 작성해줘. 결과를 마크다운 텍스트로만 반환해.\n\n"
        "포함 항목:\n"
        "1. Executive Summary (핵심 수치 요약)\n"
        "2. 처방 현황 대시보드 (월별 테이블)\n"
        "3. 월별 처방 추이 (신규/재처방/합계/도입처/처방의)\n"
        "4. 병원 리스팅 현황 (병원급별: 상급종합/종합병원/병원/의원NR/의원NP)\n"
        "5. Top 10 처방 병원 (순위/병원명/도입과/처방건수/비급여수가/추정매출)\n"
        "6. 재처방 분석 (월별 추이, 재처방률)\n"
        "7. 매출 현황 (건당 6만원 기준)\n"
        "8. 미처방 리스팅 병원 (리스팅 완료인데 처방 미발생)\n"
        "9. 액션 아이템 (즉시/단기/중기)\n"
        "10. 리스크 & 이슈\n\n"
        "Slack 메시지 전송하지 말고 텍스트만 반환해."
    )

    slack_data = {
        "channel_info": {"name": "CRM Report Generator", "id": "CRM"},
        "channel_members": [],
        "recent_messages": [],
    }
    message_data = {
        "user_id": "CRM_SYSTEM",
        "user_name": "CRM",
        "text": report_prompt,
        "channel_id": "CRM",
        "channel_name": "CRM Report",
    }

    try:
        from app.cc_agents.orchestrator.agent import call_orchestrator_agent

        result = await asyncio.wait_for(
            call_orchestrator_agent(
                user_query=report_prompt,
                slack_data=slack_data,
                message_data=message_data,
                retrieved_memory="",
            ),
            timeout=600,  # 10분
        )

        generated_at = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')

        # 마크다운 → PDF 변환
        pdf_url = None
        try:
            import markdown as md_lib
            import weasyprint
            from pathlib import Path
            import os

            html_content = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body {{ font-family: 'Noto Sans KR', Arial, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; color: #1a1a1a; line-height: 1.6; }}
h1 {{ color: #5B5FC7; border-bottom: 3px solid #5B5FC7; padding-bottom: 8px; }}
h2 {{ color: #374151; border-bottom: 1px solid #e5e7eb; padding-bottom: 6px; margin-top: 30px; }}
h3 {{ color: #4B5563; }}
table {{ border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 13px; }}
th {{ background: #5B5FC7; color: white; padding: 10px 12px; text-align: left; }}
td {{ padding: 8px 12px; border-bottom: 1px solid #e5e7eb; }}
tr:nth-child(even) {{ background: #f9fafb; }}
blockquote {{ border-left: 4px solid #5B5FC7; margin: 16px 0; padding: 8px 16px; background: #f0f0ff; color: #4B5563; }}
code {{ background: #f3f4f6; padding: 2px 6px; border-radius: 4px; font-size: 12px; }}
strong {{ color: #1e3a5f; }}
hr {{ border: none; border-top: 1px solid #e5e7eb; margin: 24px 0; }}
.header {{ text-align: center; margin-bottom: 30px; }}
.header p {{ color: #6b7280; font-size: 13px; }}
.footer {{ text-align: center; color: #9ca3af; font-size: 11px; margin-top: 40px; border-top: 1px solid #e5e7eb; padding-top: 16px; }}
</style></head><body>
<div class="header"><p>ACME | 제품A 사업부</p></div>
{md_lib.markdown(result, extensions=['tables', 'fenced_code'])}
<div class="footer">Generated by MOCO CRM · {generated_at}</div>
</body></html>"""

            # PDF 저장
            from app.config.settings import get_settings
            settings = get_settings()
            base_dir = settings.FILESYSTEM_BASE_DIR or os.getcwd()
            pdf_dir = Path(base_dir) / "files" / "crm_reports"
            pdf_dir.mkdir(parents=True, exist_ok=True)
            pdf_filename = f"producta_report_{generated_at.replace(' ','_').replace(':','')}.pdf"
            pdf_path = pdf_dir / pdf_filename

            weasyprint.HTML(string=html_content).write_pdf(str(pdf_path))
            pdf_url = f"/api/crm/dashboards/reports/download/{pdf_filename}"
            logger.info(f"[REPORT] PDF generated: {pdf_path}")
        except Exception as pdf_err:
            logger.warning(f"[REPORT] PDF generation failed: {pdf_err}")

        return SuccessResponse(data={
            "success": True,
            "report": result,
            "generated_at": generated_at,
            "pdf_url": pdf_url,
        })

    except asyncio.TimeoutError:
        return SuccessResponse(data={"success": False, "message": "보고서 생성 시간 초과 (10분)"})
    except Exception as e:
        logger.error(f"[REPORT] Error: {e}")
        return SuccessResponse(data={"success": False, "message": f"생성 실패: {str(e)}"})


# ──────────────────────── Report PDF Download ────────────────────────

@router.get("/reports/download/{filename}")
async def download_report(filename: str):
    """생성된 보고서 PDF 다운로드"""
    import os
    from pathlib import Path
    from starlette.responses import FileResponse
    from app.config.settings import get_settings

    settings = get_settings()
    base_dir = settings.FILESYSTEM_BASE_DIR or os.getcwd()
    pdf_path = Path(base_dir) / "files" / "crm_reports" / filename

    if not pdf_path.exists():
        return SuccessResponse(data={"error": "파일을 찾을 수 없습니다."})

    return FileResponse(
        str(pdf_path),
        media_type="application/pdf",
        filename=filename,
    )


# ──────────────────────── Reference Data ────────────────────────

@router.get("/reference-data", response_model=SuccessResponse)
async def reference_data(db: AsyncSession = Depends(get_db)):
    """All stored reference data (Biz Plan, Sales R&R, etc.)"""

    data = await _get_reference_data(db)
    return SuccessResponse(data=data)


# ──────────────────────── B2B 영업 요약 ────────────────────────

@router.get("/b2b-summary", response_model=SuccessResponse)
async def b2b_summary(db: AsyncSession = Depends(get_db)):
    """B2B 영업 현황 요약 (CAT / ProductD PET)"""
    cat_filter = func.json_extract(Company.custom_properties, '$.CAT_Hospital_Target').isnot(None)
    pet_filter = func.json_extract(Company.custom_properties, '$.ProductD_PET').isnot(None)
    b2b_filter = or_(cat_filter, pet_filter)

    cat_count = (await db.execute(select(func.count(Company.id)).where(cat_filter))).scalar() or 0
    pet_count = (await db.execute(select(func.count(Company.id)).where(pet_filter))).scalar() or 0
    total = (await db.execute(select(func.count(Company.id)).where(b2b_filter))).scalar() or 0

    by_type = await db.execute(
        select(_hospital_category(Company.hospital_type).label("category"), func.count(Company.id).label("cnt"))
        .where(b2b_filter).group_by("category")
    )
    by_region = await db.execute(
        select(Company.region_1, func.count(Company.id).label("cnt"))
        .where(b2b_filter).where(Company.region_1.isnot(None))
        .group_by(Company.region_1).order_by(func.count(Company.id).desc())
    )
    return SuccessResponse(data={
        "cat_count": cat_count, "pet_count": pet_count, "total": total,
        "by_hospital_type": {r[0]: r[1] for r in by_type.all()},
        "by_region": [{"region": r[0], "count": r[1]} for r in by_region.all()],
    })


# ──────────────────────── 도매/유통 영업 요약 ────────────────────────

@router.get("/wholesale-summary", response_model=SuccessResponse)
async def wholesale_summary(db: AsyncSession = Depends(get_db)):
    """도매/유통 영업 현황 요약 (유통B / 유통A / 프로젝트C)"""
    ezicop_filter = func.json_extract(Company.custom_properties, '$.유통B').isnot(None)
    ezibreed_filter = func.json_extract(Company.custom_properties, '$.유통A_Listing').isnot(None)
    hanshin_filter = func.json_extract(Company.custom_properties, '$.프로젝트C').isnot(None)
    wholesale_filter = or_(ezicop_filter, ezibreed_filter, hanshin_filter)

    ezicop_count = (await db.execute(select(func.count(Company.id)).where(ezicop_filter))).scalar() or 0
    ezibreed_count = (await db.execute(select(func.count(Company.id)).where(ezibreed_filter))).scalar() or 0
    hanshin_count = (await db.execute(select(func.count(Company.id)).where(hanshin_filter))).scalar() or 0
    total = (await db.execute(select(func.count(Company.id)).where(wholesale_filter))).scalar() or 0

    by_region = await db.execute(
        select(Company.region_1, func.count(Company.id).label("cnt"))
        .where(wholesale_filter).where(Company.region_1.isnot(None))
        .group_by(Company.region_1).order_by(func.count(Company.id).desc())
    )
    by_type = await db.execute(
        select(_hospital_category(Company.hospital_type).label("category"), func.count(Company.id).label("cnt"))
        .where(wholesale_filter).group_by("category")
    )
    return SuccessResponse(data={
        "ezicop_count": ezicop_count, "ezibreed_count": ezibreed_count,
        "hanshin_count": hanshin_count, "total": total,
        "by_region": [{"region": r[0], "count": r[1]} for r in by_region.all()],
        "by_hospital_type": {r[0]: r[1] for r in by_type.all()},
    })


# ══════════════════════════════════════════
# 상세 분석 API (7개)
# ══════════════════════════════════════════

# 1. 처방 성숙도 분석
@router.get("/prescription-maturity", response_model=SuccessResponse)
async def prescription_maturity(db: AsyncSession = Depends(get_db)):
    """병원별 처방 성장곡선, 의사 집중도, 세션 회차별 분석"""

    # 병원별 월별 처방 수
    monthly_by_hospital = await db.execute(
        select(
            Company.name.label("hospital"),
            extract("year", Prescription.prescribed_date).label("y"),
            extract("month", Prescription.prescribed_date).label("m"),
            func.count(Prescription.id).label("cnt"),
        )
        .join(Company, Prescription.hospital_id == Company.id)
        .where(_RX_VALID)
        .group_by(Company.name, "y", "m")
        .order_by("y", "m")
    )
    growth_curves = {}
    for r in monthly_by_hospital.all():
        h = r[0]
        if h not in growth_curves:
            growth_curves[h] = []
        growth_curves[h].append({"month": f"{int(r[1])}-{int(r[2]):02d}", "count": r[3]})

    # Naive vs Repeat 월별
    naive_repeat = await db.execute(
        select(
            extract("year", Prescription.prescribed_date).label("y"),
            extract("month", Prescription.prescribed_date).label("m"),
            case((Prescription.session_number == 1, "naive"), else_="repeat").label("type"),
            func.count(Prescription.id).label("cnt"),
        )
        .where(_RX_VALID)
        .group_by("y", "m", "type")
        .order_by("y", "m")
    )
    cohort = {}
    for r in naive_repeat.all():
        key = f"{int(r[0])}-{int(r[1]):02d}"
        if key not in cohort:
            cohort[key] = {"naive": 0, "repeat": 0}
        cohort[key][r[2]] = r[3]

    # 의사별 처방 집중도
    doctor_conc = await db.execute(
        select(
            Contact.first_name.label("doctor"),
            Company.name.label("hospital"),
            func.count(Prescription.id).label("cnt"),
        )
        .join(Contact, Prescription.doctor_id == Contact.id)
        .join(Company, Prescription.hospital_id == Company.id)
        .where(_RX_VALID)
        .group_by(Contact.first_name, Company.name)
        .order_by(func.count(Prescription.id).desc())
    )
    doctor_concentration = [{"doctor": r[0], "hospital": r[1], "count": r[2]} for r in doctor_conc.all()]
    total_rx = sum(d["count"] for d in doctor_concentration)
    # 상위 5명이 차지하는 비율
    top5_count = sum(d["count"] for d in doctor_concentration[:5])
    top5_pct = round(top5_count / total_rx * 100, 1) if total_rx else 0

    # 세션 회차별 분포
    session_dist = await db.execute(
        select(
            Prescription.session_number,
            func.count(Prescription.id).label("cnt"),
        )
        .where(_RX_VALID)
        .where(Prescription.session_number.isnot(None))
        .group_by(Prescription.session_number)
        .order_by(Prescription.session_number)
    )
    session_distribution = [{"session": r[0], "count": r[1]} for r in session_dist.all()]

    # 인사이트 생성
    insights = []
    if top5_pct > 60:
        insights.append({"type": "warning", "icon": "⚠️", "title": "처방 집중도 위험",
            "text": f"상위 5명 의사가 전체 처방의 {top5_pct}%를 차지합니다. 신규 의사 확보가 시급합니다.",
            "action": "신규 의사 대상 영업 강화"})
    # 최근 월 성장 추세
    cohort_months = sorted(cohort.keys())
    if len(cohort_months) >= 2:
        last = cohort[cohort_months[-1]]
        prev = cohort[cohort_months[-2]]
        last_total = last["naive"] + last["repeat"]
        prev_total = prev["naive"] + prev["repeat"]
        if last_total > prev_total:
            insights.append({"type": "success", "icon": "📈", "title": "처방 증가 추세",
                "text": f"최근 월 처방 {last_total}건으로 전월({prev_total}건) 대비 {last_total-prev_total}건 증가.",
                "action": "현재 전략 유지"})
        elif last_total < prev_total:
            insights.append({"type": "danger", "icon": "📉", "title": "처방 감소 추세",
                "text": f"최근 월 처방 {last_total}건으로 전월({prev_total}건) 대비 {prev_total-last_total}건 감소.",
                "action": "원인 분석 및 대응 필요"})
    # 재처방율
    if cohort_months:
        last = cohort[cohort_months[-1]]
        total = last["naive"] + last["repeat"]
        if total > 0:
            rr = round(last["repeat"] / total * 100, 1)
            insights.append({"type": "info", "icon": "🔄", "title": f"최근 월 재처방율 {rr}%",
                "text": f"Naive {last['naive']}건, Repeat {last['repeat']}건. {'재처방율이 양호합니다.' if rr > 40 else '재처방율 개선이 필요합니다.'}",
                "action": "환자 순응도 관리 강화" if rr < 40 else "현행 유지"})
    # 병원 수 변화
    hospital_count = len(growth_curves)
    insights.append({"type": "info", "icon": "🏥", "title": f"처방 발생 병원 {hospital_count}개",
        "text": f"총 {hospital_count}개 병원에서 제품A 처방이 발생했습니다.",
        "action": f"목표 대비 {'양호' if hospital_count > 30 else '추가 확보 필요'}"})

    return SuccessResponse(data={
        "growth_curves": growth_curves,
        "cohort": cohort,
        "doctor_concentration": doctor_concentration[:20],
        "top5_concentration_pct": top5_pct,
        "session_distribution": session_distribution,
        "insights": insights,
    })


# 2. 리스팅→처방 전환 분석
@router.get("/listing-conversion", response_model=SuccessResponse)
async def listing_conversion(db: AsyncSession = Depends(get_db)):
    """리스팅 단계별 퍼널, 미처방 리스팅 진단"""

    # 리스팅 상태별 병원 수 (제품A)
    listing_funnel = await db.execute(
        select(ProductListing.status, func.count(distinct(ProductListing.company_id)).label("cnt"))
        .where(ProductListing.product == "제품A")
        .where(or_(ProductListing.pipeline_stage.is_(None), ProductListing.pipeline_stage != "임시등재"))
        .group_by(ProductListing.status)
    )
    funnel = {r[0]: r[1] for r in listing_funnel.all()}

    # 리스팅 완료인데 처방 0인 병원
    listed_hospitals = await db.execute(
        select(distinct(ProductListing.company_id))
        .where(ProductListing.product == "제품A")
        .where(ProductListing.status == "done")
    )
    listed_ids = [r[0] for r in listed_hospitals.all()]

    rx_hospitals = await db.execute(
        select(distinct(Prescription.hospital_id)).where(_RX_VALID)
    )
    rx_ids = set(r[0] for r in rx_hospitals.all())

    no_rx_ids = [cid for cid in listed_ids if cid not in rx_ids]

    # 미처방 병원 상세
    no_rx_hospitals = []
    if no_rx_ids:
        hospitals = await db.execute(
            select(Company.id, Company.name, Company.hospital_type, Company.region_1, Company.territory_owner)
            .where(Company.id.in_(no_rx_ids))
        )
        no_rx_hospitals = [{"id": r[0], "name": r[1], "type": r[2], "region": r[3], "owner": r[4]} for r in hospitals.all()]

    # 인사이트
    insights = []
    conversion_rate = round(len(rx_ids) / len(listed_ids) * 100, 1) if listed_ids else 0
    insights.append({"type": "info", "icon": "🔄", "title": f"리스팅→처방 전환율 {conversion_rate}%",
        "text": f"리스팅 완료 {len(listed_ids)}개처 중 {len(rx_ids)}개처에서 실제 처방 발생.",
        "action": f"전환율 {'양호' if conversion_rate > 70 else '개선 필요 — 미처방 병원 집중 방문'}"})
    if no_rx_ids:
        insights.append({"type": "danger", "icon": "🚨", "title": f"미처방 병원 {len(no_rx_ids)}개 발견",
            "text": f"리스팅 완료했으나 처방이 한 건도 없는 병원이 {len(no_rx_ids)}개입니다. 즉시 팔로업이 필요합니다.",
            "action": "이번 주 내 담당자별 미처방 병원 방문 계획 수립"})
    if funnel.get("verbal_confirm", 0) > 0:
        insights.append({"type": "warning", "icon": "💬", "title": f"구두확인 {funnel['verbal_confirm']}개 — 정식 등록 전환 필요",
            "text": "구두 확인 상태에서 정식 등록으로 전환되지 않은 병원이 있습니다.",
            "action": "서류 진행 독려"})

    return SuccessResponse(data={
        "funnel": funnel,
        "listed_count": len(listed_ids),
        "rx_count": len(rx_ids),
        "no_rx_count": len(no_rx_ids),
        "no_rx_hospitals": no_rx_hospitals,
        "conversion_rate": conversion_rate,
        "insights": insights,
    })


# 3. 매출 심층 분석
@router.get("/revenue-analysis", response_model=SuccessResponse)
async def revenue_analysis(db: AsyncSession = Depends(get_db)):
    """처방 vs 매출 gap, 월별 추이, 채널별"""

    # 월별 매출 추이
    monthly_rev = await db.execute(
        select(
            SalesTransaction.year, SalesTransaction.month,
            func.sum(SalesTransaction.revenue).label("revenue"),
            func.sum(SalesTransaction.quantity).label("qty"),
        )
        .where(SalesTransaction.product == "제품A")
        .group_by(SalesTransaction.year, SalesTransaction.month)
        .order_by(SalesTransaction.year, SalesTransaction.month)
    )
    monthly_trend = [{"month": f"{r[0]}-{r[1]:02d}", "revenue": float(r[2] or 0), "quantity": r[3] or 0} for r in monthly_rev.all()]

    # 채널별 매출
    channel_rev = await db.execute(
        select(SalesTransaction.channel, func.sum(SalesTransaction.revenue).label("rev"))
        .where(SalesTransaction.product == "제품A")
        .group_by(SalesTransaction.channel)
        .order_by(func.sum(SalesTransaction.revenue).desc())
    )
    by_channel = [{"channel": r[0] or "기타", "revenue": float(r[1] or 0)} for r in channel_rev.all()]

    # 병원별 처방 vs 매출 (상위 20)
    hospital_gap = await db.execute(
        select(
            Company.name,
            func.count(Prescription.id).label("rx_count"),
        )
        .join(Company, Prescription.hospital_id == Company.id)
        .where(_RX_VALID)
        .group_by(Company.name)
        .order_by(func.count(Prescription.id).desc())
        .limit(20)
    )
    rx_by_hospital = [{"hospital": r[0], "rx_count": r[1], "estimated_revenue": r[1] * 60000} for r in hospital_gap.all()]

    return SuccessResponse(data={
        "monthly_trend": monthly_trend,
        "by_channel": by_channel,
        "rx_by_hospital": rx_by_hospital,
    })


# 4. 의사 포텐셜 분석
@router.get("/doctor-potential", response_model=SuccessResponse)
async def doctor_potential(db: AsyncSession = Depends(get_db)):
    """의사별 종합 점수, 타겟 의사, 월별 추이"""

    # 의사별 처방 통계
    doctor_stats = await db.execute(
        select(
            Contact.id,
            Contact.first_name.label("doctor"),
            Company.name.label("hospital"),
            Contact.department,
            func.count(Prescription.id).label("total_rx"),
            func.count(case((Prescription.session_number == 1, 1))).label("naive"),
            func.count(case((Prescription.session_number > 1, 1))).label("repeat"),
        )
        .join(Contact, Prescription.doctor_id == Contact.id)
        .join(Company, Prescription.hospital_id == Company.id)
        .where(_RX_VALID)
        .group_by(Contact.id, Contact.first_name, Company.name, Contact.department)
        .order_by(func.count(Prescription.id).desc())
    )
    doctors = []
    for r in doctor_stats.all():
        total = r[4]
        repeat_rate = round(r[6] / total * 100, 1) if total else 0
        # 포텐셜 점수: 처방 수 × 재처방율 가중치
        score = round(total * (1 + repeat_rate / 100), 1)
        doctors.append({
            "id": r[0], "doctor": r[1], "hospital": r[2], "department": r[3],
            "total_rx": total, "naive": r[5], "repeat": r[6],
            "repeat_rate": repeat_rate, "potential_score": score,
        })

    # 처방 있는 의사 수
    rx_doctor_ids = set(d["id"] for d in doctors)

    # 전체 의료진 중 처방 없는 의사 (리스팅 완료 병원 소속)
    all_contacts = await db.execute(
        select(Contact.id, Contact.first_name, Company.name.label("hospital"), Contact.department)
        .join(Company, Contact.company_id == Company.id)
        .where(Contact.id.notin_(rx_doctor_ids) if rx_doctor_ids else Contact.id.isnot(None))
        .limit(50)
    )
    untapped = [{"doctor": r[1], "hospital": r[2], "department": r[3], "total_rx": 0, "potential_score": 0}
                for r in all_contacts.all()]

    # 의사별 월별 추이 (상위 10명)
    top_ids = [d["id"] for d in doctors[:10]]
    monthly_by_doctor = {}
    if top_ids:
        monthly = await db.execute(
            select(
                Contact.first_name,
                extract("year", Prescription.prescribed_date).label("y"),
                extract("month", Prescription.prescribed_date).label("m"),
                func.count(Prescription.id).label("cnt"),
            )
            .join(Contact, Prescription.doctor_id == Contact.id)
            .where(_RX_VALID)
            .where(Contact.id.in_(top_ids))
            .group_by(Contact.first_name, "y", "m")
            .order_by("y", "m")
        )
        for r in monthly.all():
            name = r[0]
            if name not in monthly_by_doctor:
                monthly_by_doctor[name] = []
            monthly_by_doctor[name].append({"month": f"{int(r[1])}-{int(r[2]):02d}", "count": r[3]})

    return SuccessResponse(data={
        "doctors": doctors,
        "untapped_doctors": untapped[:20],
        "monthly_by_doctor": monthly_by_doctor,
    })


# 5. 지역별 시장 분석
@router.get("/region-analysis", response_model=SuccessResponse)
async def region_analysis(db: AsyncSession = Depends(get_db)):
    """지역별 처방/리스팅/매출, 담당자 리더보드"""

    # 지역별 처방
    rx_by_region = await db.execute(
        select(Company.region_1, func.count(Prescription.id).label("rx"))
        .join(Company, Prescription.hospital_id == Company.id)
        .where(_RX_VALID)
        .where(Company.region_1.isnot(None))
        .group_by(Company.region_1)
        .order_by(func.count(Prescription.id).desc())
    )
    rx_regions = {r[0]: r[1] for r in rx_by_region.all()}

    # 지역별 리스팅 완료
    listing_by_region = await db.execute(
        select(Company.region_1, func.count(ProductListing.id).label("cnt"))
        .join(Company, ProductListing.company_id == Company.id)
        .where(ProductListing.product == "제품A")
        .where(ProductListing.status == "done")
        .where(Company.region_1.isnot(None))
        .group_by(Company.region_1)
    )
    listing_regions = {r[0]: r[1] for r in listing_by_region.all()}

    # 지역별 병원 수
    hospital_by_region = await db.execute(
        select(Company.region_1, func.count(Company.id).label("cnt"))
        .where(Company.region_1.isnot(None))
        .group_by(Company.region_1)
        .order_by(func.count(Company.id).desc())
    )
    all_regions = []
    for r in hospital_by_region.all():
        region = r[0]
        all_regions.append({
            "region": region,
            "hospitals": r[1],
            "rx": rx_regions.get(region, 0),
            "listings": listing_regions.get(region, 0),
            "penetration": round(listing_regions.get(region, 0) / r[1] * 100, 1) if r[1] else 0,
        })

    # 담당자별 성과
    owner_stats = await db.execute(
        select(
            Company.territory_owner,
            func.count(distinct(Company.id)).label("hospitals"),
            func.count(distinct(Prescription.id)).label("rx"),
        )
        .outerjoin(Prescription, Prescription.hospital_id == Company.id)
        .where(Company.territory_owner.isnot(None))
        .group_by(Company.territory_owner)
        .order_by(func.count(distinct(Prescription.id)).desc())
    )
    leaderboard = [{"owner": r[0], "hospitals": r[1], "rx": r[2]} for r in owner_stats.all()]

    return SuccessResponse(data={
        "regions": all_regions,
        "leaderboard": leaderboard,
    })


# 6. 리스크 경보
@router.get("/risk-alerts", response_model=SuccessResponse)
async def risk_alerts(db: AsyncSession = Depends(get_db)):
    """처방 감소 병원, 미처방 리스팅, 단일 의사 의존"""

    now = datetime.now()
    three_months_ago = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    six_months_ago = (now - timedelta(days=180)).strftime("%Y-%m-%d")

    # 최근 3개월 vs 이전 3개월 처방 비교 (병원별)
    recent_rx = await db.execute(
        select(Company.name, func.count(Prescription.id).label("cnt"))
        .join(Company, Prescription.hospital_id == Company.id)
        .where(_RX_VALID)
        .where(func.cast(Prescription.prescribed_date, String) >= three_months_ago)
        .group_by(Company.name)
    )
    recent = {r[0]: r[1] for r in recent_rx.all()}

    prev_rx = await db.execute(
        select(Company.name, func.count(Prescription.id).label("cnt"))
        .join(Company, Prescription.hospital_id == Company.id)
        .where(_RX_VALID)
        .where(func.cast(Prescription.prescribed_date, String) >= six_months_ago)
        .where(func.cast(Prescription.prescribed_date, String) < three_months_ago)
        .group_by(Company.name)
    )
    prev = {r[0]: r[1] for r in prev_rx.all()}

    declining = []
    for h, prev_cnt in prev.items():
        recent_cnt = recent.get(h, 0)
        if recent_cnt < prev_cnt:
            declining.append({"hospital": h, "prev_3m": prev_cnt, "recent_3m": recent_cnt,
                            "change": recent_cnt - prev_cnt, "change_pct": round((recent_cnt - prev_cnt) / prev_cnt * 100, 1)})
    declining.sort(key=lambda x: x["change"])

    # 단일 의사 의존도 높은 병원
    dependency = await db.execute(
        select(
            Company.name,
            func.count(distinct(Prescription.doctor_id)).label("doctor_count"),
            func.count(Prescription.id).label("total_rx"),
        )
        .join(Company, Prescription.hospital_id == Company.id)
        .where(_RX_VALID)
        .group_by(Company.name)
        .having(func.count(distinct(Prescription.doctor_id)) == 1)
        .having(func.count(Prescription.id) >= 5)
        .order_by(func.count(Prescription.id).desc())
    )
    single_doctor = [{"hospital": r[0], "doctor_count": r[1], "total_rx": r[2]} for r in dependency.all()]

    return SuccessResponse(data={
        "declining_hospitals": declining,
        "single_doctor_dependency": single_doctor,
        "alert_count": len(declining) + len(single_doctor),
        "insights": [
            {"type": "danger" if declining else "success", "icon": "📉" if declining else "✅",
             "title": f"처방 감소 병원 {len(declining)}개" if declining else "처방 감소 병원 없음",
             "text": f"최근 3개월 대비 이전 3개월보다 처방이 줄어든 병원이 {len(declining)}개 있습니다. 즉각 원인 파악이 필요합니다." if declining else "모든 처방 병원이 안정적으로 유지되고 있습니다.",
             "action": "감소 병원 긴급 방문" if declining else "현행 유지"},
            {"type": "warning" if single_doctor else "success", "icon": "👤" if single_doctor else "✅",
             "title": f"단일 의사 의존 병원 {len(single_doctor)}개" if single_doctor else "의사 분산 양호",
             "text": f"처방 의사가 1명뿐인 병원이 {len(single_doctor)}개입니다. 해당 의사 이직/퇴직 시 처방이 전면 중단될 위험이 있습니다." if single_doctor else "모든 처방 병원에 2명 이상의 의사가 처방 중입니다.",
             "action": "신규 의사 확보 영업" if single_doctor else "현행 유지"},
        ],
    })


# 7. 영업 추천 액션
@router.get("/sales-recommendations", response_model=SuccessResponse)
async def sales_recommendations(db: AsyncSession = Depends(get_db)):
    """데이터 기반 영업 추천 액션"""

    # 리스팅 진행 중 팔로업 필요 (in_progress 상태)
    followup = await db.execute(
        select(Company.name, Company.territory_owner, Company.region_1, ProductListing.status)
        .select_from(ProductListing)
        .join(Company, ProductListing.company_id == Company.id)
        .where(ProductListing.product == "제품A")
        .where(ProductListing.status == "in_progress")
        .order_by(Company.name)
    )
    followup_hospitals = [{"hospital": r[0], "owner": r[1], "region": r[2], "status": r[3],
                          "action": "리스팅 완료 팔로업", "priority": "높음"} for r in followup.all()]

    # 구두확인 → 정식 진행 필요
    verbal = await db.execute(
        select(Company.name, Company.territory_owner, Company.region_1)
        .select_from(ProductListing)
        .join(Company, ProductListing.company_id == Company.id)
        .where(ProductListing.product == "제품A")
        .where(ProductListing.status == "verbal_confirm")
    )
    verbal_hospitals = [{"hospital": r[0], "owner": r[1], "region": r[2],
                        "action": "구두확인 → 정식 등록 진행", "priority": "높음"} for r in verbal.all()]

    # 처방 활성 병원 중 최근 1개월 처방 없는 곳 (재방문 필요)
    try:
        one_month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        active_no_recent = await db.execute(
            select(Company.name, Company.territory_owner, func.max(Prescription.prescribed_date).label("last_rx"))
            .select_from(Prescription)
            .join(Company, Prescription.hospital_id == Company.id)
            .where(_RX_VALID)
            .group_by(Company.name, Company.territory_owner)
        )
        revisit = []
        for r in active_no_recent.all():
            last = str(r[2])[:10] if r[2] else ""
            if last and last < one_month_ago:
                revisit.append({"hospital": r[0], "owner": r[1], "last_rx": last,
                    "action": "재방문 (처방 공백)", "priority": "중간"})
    except Exception as e:
        logger.error(f"[SALES_REC] revisit query error: {e}")
        revisit = []

    all_actions = followup_hospitals + verbal_hospitals + revisit
    all_actions.sort(key=lambda x: {"높음": 0, "중간": 1, "낮음": 2}.get(x.get("priority", "낮음"), 2))

    insights = []
    if followup_hospitals:
        insights.append({"type": "warning", "icon": "🔄", "title": f"리스팅 팔로업 {len(followup_hospitals)}건",
            "text": f"진행 중인 리스팅 {len(followup_hospitals)}건의 완료를 위해 팔로업이 필요합니다.",
            "action": "이번 주 담당자별 팔로업 일정 수립"})
    if verbal_hospitals:
        insights.append({"type": "info", "icon": "💬", "title": f"구두확인 전환 {len(verbal_hospitals)}건",
            "text": "구두 확인된 병원을 정식 등록으로 전환하면 리스팅 완료 수를 빠르게 늘릴 수 있습니다.",
            "action": "서류 진행 지원"})
    if revisit:
        insights.append({"type": "danger", "icon": "🏥", "title": f"재방문 필요 {len(revisit)}건",
            "text": f"처방 이력이 있으나 최근 30일 이상 처방이 없는 병원 {len(revisit)}곳. 관계 유지를 위한 방문이 필요합니다.",
            "action": "우선순위별 방문 계획"})
    if not all_actions:
        insights.append({"type": "success", "icon": "🎉", "title": "모든 영업 활동이 순조롭습니다",
            "text": "현재 즉각적인 액션이 필요한 항목이 없습니다.", "action": "신규 병원 개척에 집중"})

    return SuccessResponse(data={
        "recommendations": all_actions,
        "followup_count": len(followup_hospitals),
        "verbal_count": len(verbal_hospitals),
        "revisit_count": len(revisit),
        "total_actions": len(all_actions),
        "insights": insights,
    })
