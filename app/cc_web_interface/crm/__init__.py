"""CRM 모듈 — MOCO FastAPI 서버에 통합되는 CRM 백엔드."""

import logging

from fastapi import FastAPI

logger = logging.getLogger(__name__)


async def setup_crm_routes(app: FastAPI) -> None:
    """
    CRM 라우트를 FastAPI 앱에 등록한다.

    1. 데이터베이스 초기화 (테이블 생성)
    2. 모든 CRM 라우트 등록 (/api/crm 접두사)
    3. 기본 파이프라인 생성 (없을 경우)

    Args:
        app: FastAPI 애플리케이션 인스턴스
    """
    # 1. DB 초기화
    from app.cc_web_interface.crm.database import init_db, async_session
    await init_db()

    # 2. 라우트 등록
    from app.cc_web_interface.crm.routes import (
        contacts_router,
        companies_router,
        deals_router,
        pipelines_router,
        activities_router,
        emails_router,
        automations_router,
        tasks_router,
        reports_router,
        forms_router,
        segments_router,
        templates_router,
        tracking_router,
        booking_router,
        relationships_router,
        # Phase 1-6: 의료/제약 데이터 통합
        import_data_router,
        prescriptions_router,
        sales_router,
        product_listings_router,
        kol_plans_router,
        hospital_contracts_router,
        dashboards_router,
        working_days_router,
    )

    app.include_router(contacts_router, prefix="/api/crm")
    app.include_router(companies_router, prefix="/api/crm")
    app.include_router(deals_router, prefix="/api/crm")
    app.include_router(pipelines_router, prefix="/api/crm")
    app.include_router(activities_router, prefix="/api/crm")
    app.include_router(emails_router, prefix="/api/crm")
    app.include_router(automations_router, prefix="/api/crm")
    app.include_router(tasks_router, prefix="/api/crm")
    app.include_router(reports_router, prefix="/api/crm")
    app.include_router(forms_router, prefix="/api/crm")
    app.include_router(segments_router, prefix="/api/crm")
    app.include_router(templates_router, prefix="/api/crm")
    app.include_router(tracking_router, prefix="/api/crm")
    app.include_router(booking_router, prefix="/api/crm")
    app.include_router(relationships_router, prefix="/api/crm")
    # Phase 1-6: 의료/제약 데이터 통합
    app.include_router(import_data_router, prefix="/api/crm")
    app.include_router(prescriptions_router, prefix="/api/crm")
    app.include_router(sales_router, prefix="/api/crm")
    app.include_router(product_listings_router, prefix="/api/crm")
    app.include_router(kol_plans_router, prefix="/api/crm")
    app.include_router(hospital_contracts_router, prefix="/api/crm")
    app.include_router(dashboards_router, prefix="/api/crm")
    app.include_router(working_days_router, prefix="/api/crm")

    # 3. 기본 파이프라인 생성 + 데모 데이터 시드
    from app.cc_web_interface.crm.routes.pipelines import ensure_default_pipeline
    from app.cc_web_interface.crm.seed import seed_demo_data
    async with async_session() as session:
        await ensure_default_pipeline(session)
        await seed_demo_data(session)
        await session.commit()

    logger.info("[CRM] 모듈 초기화 완료 - 20개 라우트 등록됨")
