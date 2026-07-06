"""
CRM 데이터베이스 설정
SQLAlchemy async 엔진 및 세션 관리
"""

import os
import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)

# DB 파일 경로: ~/.moco/crm.db
DB_DIR = Path(os.path.expanduser("~/.eco"))
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "crm.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False, "timeout": 30},  # 30s busy timeout
)


# 연결마다 WAL 활성화 + busy_timeout 설정 (다중 reader·짧은 락 대기)
from sqlalchemy import event as _sa_event


@_sa_event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _conn_record):
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")  # 30s
        cur.execute("PRAGMA synchronous=NORMAL")  # WAL과 잘 어울림
    finally:
        cur.close()

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI 의존성: 비동기 DB 세션 제공"""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """모든 테이블 생성"""
    from app.cc_web_interface.crm.models import (  # noqa: F401
        Contact, Company, Deal, Pipeline, Activity,
        EmailSequence, EmailEnrollment, Automation,
        CRMTask, Form, FormSubmission, Segment,
        Prescription, PatientCompliance, SalesTransaction,
        ProductListing, KOLPlan, HospitalContract,
        ReferenceData,
        WorkingDayEvent,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Add new columns if they don't exist (migration for existing DBs)
    await _add_missing_columns()
    # Repair any Korean enum values stored by previous frontend bug
    await _repair_enum_values()
    logger.info("[CRM] 데이터베이스 초기화 완료: %s", DB_PATH)


async def _add_missing_columns():
    """Add columns introduced after initial DB creation (e.g. contacts.tags)."""
    migrations = [
        ("contacts", "tags", "TEXT DEFAULT '[]'"),
        ("email_enrollments", "retry_count", "INTEGER DEFAULT 0"),
        ("email_enrollments", "waiting_condition", "VARCHAR(50)"),
        # Phase 1: Company 병원 확장
        ("companies", "hospital_code", "VARCHAR(50)"),
        ("companies", "hospital_type", "VARCHAR(50)"),
        ("companies", "region_1", "VARCHAR(50)"),
        ("companies", "region_2", "VARCHAR(50)"),
        ("companies", "region_3", "VARCHAR(50)"),
        ("companies", "territory_owner", "VARCHAR(50)"),
        ("companies", "is_target", "BOOLEAN DEFAULT 0"),
        # Phase 2: Contact HCP 확장
        ("contacts", "hcp_code", "VARCHAR(50)"),
        ("contacts", "department", "VARCHAR(100)"),
        ("contacts", "sub_specialty", "VARCHAR(100)"),
        ("contacts", "title_position", "VARCHAR(100)"),
        ("contacts", "license_number", "VARCHAR(50)"),
        # Phase 7: Working Day - Google Calendar 양방향 동기화
        ("working_day_events", "source", "VARCHAR(20) DEFAULT 'manual'"),
        ("working_day_events", "gcal_event_id", "VARCHAR(255)"),
        ("working_day_events", "gcal_user_email", "VARCHAR(255)"),
        ("working_day_events", "last_synced_at", "DATETIME"),
        # Phase 8: Working Day - 시간지정 + 병원/의사 연계
        ("working_day_events", "start_at", "DATETIME"),
        ("working_day_events", "end_at", "DATETIME"),
        ("working_day_events", "is_all_day", "BOOLEAN DEFAULT 1"),
        ("working_day_events", "company_id", "INTEGER"),
        ("working_day_events", "contact_id", "INTEGER"),
        ("working_day_events", "activity_id", "INTEGER"),
    ]
    try:
        async with engine.begin() as conn:
            for table, column, col_type in migrations:
                # Check if column exists
                result = await conn.execute(text(f"PRAGMA table_info({table})"))
                columns = [row[1] for row in result.fetchall()]
                if column not in columns:
                    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                    logger.info("[CRM] 컬럼 추가: %s.%s", table, column)
    except Exception as e:
        logger.warning("[CRM] 컬럼 마이그레이션 중 오류 (무시): %s", e)


async def _repair_enum_values():
    """Fix Korean enum values stored by previous frontend bug."""
    repairs = [
        # contacts table
        ("contacts", "lead_status", {
            "신규": "new", "연락중": "contacted", "적격": "qualified",
            "부적격": "unqualified", "전환됨": "customer",
        }),
        ("contacts", "lifecycle_stage", {
            "구독자": "subscriber", "리드": "lead", "기회": "opportunity",
            "고객": "customer", "에반젤리스트": "evangelist",
        }),
        # crm_tasks table
        ("crm_tasks", "priority", {
            "낮음": "low", "보통": "medium", "높음": "high", "긴급": "high",
        }),
        ("crm_tasks", "status", {
            "할일": "todo", "할 일": "todo", "진행중": "in_progress", "완료": "done",
        }),
    ]
    try:
        async with engine.begin() as conn:
            for table, column, mapping in repairs:
                for ko, en in mapping.items():
                    await conn.execute(
                        text(f"UPDATE {table} SET {column} = :en WHERE {column} = :ko"),
                        {"en": en, "ko": ko},
                    )
        logger.info("[CRM] Enum 값 복구 완료")
    except Exception as e:
        logger.warning("[CRM] Enum 값 복구 중 오류 (무시): %s", e)
