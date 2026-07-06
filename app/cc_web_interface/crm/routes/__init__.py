"""CRM API 라우트 모듈"""

from app.cc_web_interface.crm.routes.contacts import router as contacts_router
from app.cc_web_interface.crm.routes.companies import router as companies_router
from app.cc_web_interface.crm.routes.deals import router as deals_router
from app.cc_web_interface.crm.routes.pipelines import router as pipelines_router
from app.cc_web_interface.crm.routes.activities import router as activities_router
from app.cc_web_interface.crm.routes.emails import router as emails_router
from app.cc_web_interface.crm.routes.automations import router as automations_router
from app.cc_web_interface.crm.routes.tasks import router as tasks_router
from app.cc_web_interface.crm.routes.reports import router as reports_router
from app.cc_web_interface.crm.routes.forms import router as forms_router
from app.cc_web_interface.crm.routes.segments import router as segments_router
from app.cc_web_interface.crm.routes.templates import router as templates_router
from app.cc_web_interface.crm.routes.tracking import router as tracking_router
from app.cc_web_interface.crm.routes.booking import router as booking_router
from app.cc_web_interface.crm.routes.relationships import router as relationships_router
# Phase 1-6: 의료/제약 데이터 통합
from app.cc_web_interface.crm.routes.import_data import router as import_data_router
from app.cc_web_interface.crm.routes.prescriptions import router as prescriptions_router
from app.cc_web_interface.crm.routes.sales import router as sales_router
from app.cc_web_interface.crm.routes.product_listings import router as product_listings_router
from app.cc_web_interface.crm.routes.kol_plans import router as kol_plans_router
from app.cc_web_interface.crm.routes.hospital_contracts import router as hospital_contracts_router
from app.cc_web_interface.crm.routes.dashboards import router as dashboards_router
from app.cc_web_interface.crm.routes.working_days import router as working_days_router

__all__ = [
    "contacts_router",
    "companies_router",
    "deals_router",
    "pipelines_router",
    "activities_router",
    "emails_router",
    "automations_router",
    "tasks_router",
    "reports_router",
    "forms_router",
    "segments_router",
    "templates_router",
    "tracking_router",
    "booking_router",
    "relationships_router",
    "import_data_router",
    "prescriptions_router",
    "sales_router",
    "product_listings_router",
    "kol_plans_router",
    "hospital_contracts_router",
    "dashboards_router",
    "working_days_router",
]
