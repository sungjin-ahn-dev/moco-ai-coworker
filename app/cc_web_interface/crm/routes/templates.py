"""
이메일 템플릿 API 라우트
이메일, 뉴스레터, 팜플렛 템플릿 CRUD + 미리보기 + 렌더링
"""

import logging
import re

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import EmailTemplate
from app.cc_web_interface.crm.schemas import (
    EmailTemplateCreate, EmailTemplateUpdate, EmailTemplateRead,
    PaginatedResponse, SuccessResponse, ErrorResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/emails/templates", tags=["이메일 템플릿"])


@router.get("", response_model=SuccessResponse)
async def list_templates(
    type: str = Query(None, description="템플릿 유형 필터 (email, newsletter, pamphlet)"),
    status: str = Query(None, description="상태 필터 (active, archived)"),
    tag: str = Query(None, description="태그 필터"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """템플릿 목록 조회"""
    query = select(EmailTemplate)
    if type:
        query = query.where(EmailTemplate.type == type)
    if status:
        query = query.where(EmailTemplate.status == status)
    if tag:
        query = query.where(EmailTemplate.tags.cast(str).contains(f'"{tag}"'))

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(EmailTemplate.updated_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    templates = result.scalars().all()

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size,
        items=[EmailTemplateRead.model_validate(t) for t in templates],
    ))


@router.get("/{template_id}", response_model=SuccessResponse)
async def get_template(template_id: int, db: AsyncSession = Depends(get_db)):
    """템플릿 상세 조회"""
    template = await db.get(EmailTemplate, template_id)
    if not template:
        return ErrorResponse(message="템플릿을 찾을 수 없습니다.")
    return SuccessResponse(data=EmailTemplateRead.model_validate(template))


@router.post("", response_model=SuccessResponse)
async def create_template(
    data: EmailTemplateCreate, db: AsyncSession = Depends(get_db),
):
    """새 템플릿 생성"""
    # body_html에서 변수 자동 추출 ({{variable_name}} 패턴)
    detected_vars = re.findall(r'\{\{(\w+)\}\}', data.body_html)
    variables = list(set((data.variables or []) + detected_vars))

    template = EmailTemplate(
        name=data.name,
        description=data.description,
        type=data.type,
        subject=data.subject,
        body_html=data.body_html,
        body_text=data.body_text,
        variables=variables,
        tags=data.tags or [],
        status=data.status,
    )
    db.add(template)
    await db.flush()
    return SuccessResponse(data=EmailTemplateRead.model_validate(template))


@router.put("/{template_id}", response_model=SuccessResponse)
async def update_template(
    template_id: int,
    data: EmailTemplateUpdate,
    db: AsyncSession = Depends(get_db),
):
    """템플릿 수정"""
    template = await db.get(EmailTemplate, template_id)
    if not template:
        return ErrorResponse(message="템플릿을 찾을 수 없습니다.")

    update_data = data.model_dump(exclude_unset=True)

    # body_html이 변경되면 변수 재추출
    if "body_html" in update_data and update_data["body_html"]:
        detected_vars = re.findall(r'\{\{(\w+)\}\}', update_data["body_html"])
        existing_vars = update_data.get("variables") or template.variables or []
        update_data["variables"] = list(set(existing_vars + detected_vars))

    for key, value in update_data.items():
        setattr(template, key, value)
    await db.flush()
    return SuccessResponse(data=EmailTemplateRead.model_validate(template))


@router.delete("/{template_id}", response_model=SuccessResponse)
async def delete_template(template_id: int, db: AsyncSession = Depends(get_db)):
    """템플릿 삭제"""
    template = await db.get(EmailTemplate, template_id)
    if not template:
        return ErrorResponse(message="템플릿을 찾을 수 없습니다.")
    await db.delete(template)
    await db.flush()
    return SuccessResponse(data={"deleted": template_id})


@router.post("/{template_id}/render", response_model=SuccessResponse)
async def render_template(
    template_id: int,
    variables: dict = {},
    db: AsyncSession = Depends(get_db),
):
    """템플릿에 변수를 적용하여 렌더링합니다."""
    template = await db.get(EmailTemplate, template_id)
    if not template:
        return ErrorResponse(message="템플릿을 찾을 수 없습니다.")

    rendered_subject = template.subject or ""
    rendered_html = template.body_html or ""
    rendered_text = template.body_text or ""

    for key, value in variables.items():
        placeholder = "{{" + key + "}}"
        rendered_subject = rendered_subject.replace(placeholder, str(value))
        rendered_html = rendered_html.replace(placeholder, str(value))
        rendered_text = rendered_text.replace(placeholder, str(value))

    return SuccessResponse(data={
        "template_id": template_id,
        "subject": rendered_subject,
        "body_html": rendered_html,
        "body_text": rendered_text,
    })


@router.get("/{template_id}/preview")
async def preview_template(template_id: int, db: AsyncSession = Depends(get_db)):
    """템플릿 HTML 미리보기"""
    template = await db.get(EmailTemplate, template_id)
    if not template:
        return HTMLResponse("<h1>템플릿을 찾을 수 없습니다.</h1>", status_code=404)
    return HTMLResponse(template.body_html)


@router.post("/{template_id}/duplicate", response_model=SuccessResponse)
async def duplicate_template(template_id: int, db: AsyncSession = Depends(get_db)):
    """템플릿을 복제합니다."""
    template = await db.get(EmailTemplate, template_id)
    if not template:
        return ErrorResponse(message="템플릿을 찾을 수 없습니다.")

    new_template = EmailTemplate(
        name=f"{template.name} (복사본)",
        description=template.description,
        type=template.type,
        subject=template.subject,
        body_html=template.body_html,
        body_text=template.body_text,
        variables=template.variables,
        tags=template.tags,
        status="active",
    )
    db.add(new_template)
    await db.flush()
    return SuccessResponse(data=EmailTemplateRead.model_validate(new_template))
