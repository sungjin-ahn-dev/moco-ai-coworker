"""
폼 API 라우트
웹 폼 CRUD 및 공개 제출 처리
"""

import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.cc_web_interface.crm.database import get_db
from app.cc_web_interface.crm.models import (
    Form, FormSubmission, Contact, Activity, ActivityType,
    LeadStatus, LifecycleStage,
)
from app.cc_web_interface.crm.schemas import (
    FormCreate, FormUpdate, FormRead,
    FormSubmissionCreate, FormSubmissionRead,
    PaginatedResponse, SuccessResponse, ErrorResponse,
)
from app.cc_web_interface.crm.services.automation import evaluate_triggers

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/forms", tags=["폼"])


@router.get("", response_model=SuccessResponse)
async def list_forms(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """폼 목록 조회"""
    count_query = select(func.count(Form.id))
    total = (await db.execute(count_query)).scalar() or 0

    result = await db.execute(
        select(Form)
        .order_by(Form.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    forms = result.scalars().all()

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size,
        items=[FormRead.model_validate(f) for f in forms],
    ))


@router.get("/{form_id}", response_model=SuccessResponse)
async def get_form(form_id: int, db: AsyncSession = Depends(get_db)):
    """폼 상세 조회"""
    form = await db.get(Form, form_id)
    if not form:
        return ErrorResponse(message="폼을 찾을 수 없습니다.")
    return SuccessResponse(data=FormRead.model_validate(form))


@router.post("", response_model=SuccessResponse)
async def create_form(data: FormCreate, db: AsyncSession = Depends(get_db)):
    """새 폼 생성"""
    form = Form(
        name=data.name,
        fields=[f.model_dump() for f in data.fields],
        redirect_url=data.redirect_url,
        notification_emails=data.notification_emails,
    )
    db.add(form)
    await db.flush()
    return SuccessResponse(data=FormRead.model_validate(form))


@router.put("/{form_id}", response_model=SuccessResponse)
async def update_form(
    form_id: int, data: FormUpdate, db: AsyncSession = Depends(get_db),
):
    """폼 수정"""
    form = await db.get(Form, form_id)
    if not form:
        return ErrorResponse(message="폼을 찾을 수 없습니다.")

    update_data = data.model_dump(exclude_unset=True)
    if "fields" in update_data and update_data["fields"] is not None:
        update_data["fields"] = [
            f.model_dump() if hasattr(f, "model_dump") else f
            for f in update_data["fields"]
        ]
    for key, value in update_data.items():
        setattr(form, key, value)
    await db.flush()
    return SuccessResponse(data=FormRead.model_validate(form))


@router.delete("/{form_id}", response_model=SuccessResponse)
async def delete_form(form_id: int, db: AsyncSession = Depends(get_db)):
    """폼 삭제"""
    form = await db.get(Form, form_id)
    if not form:
        return ErrorResponse(message="폼을 찾을 수 없습니다.")
    await db.delete(form)
    await db.flush()
    return SuccessResponse(data={"deleted": form_id})


@router.get("/{form_id}/submissions", response_model=SuccessResponse)
async def list_submissions(
    form_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """폼 제출 목록 조회"""
    form = await db.get(Form, form_id)
    if not form:
        return ErrorResponse(message="폼을 찾을 수 없습니다.")

    query = select(FormSubmission).where(FormSubmission.form_id == form_id)
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(FormSubmission.submitted_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    submissions = result.scalars().all()

    return SuccessResponse(data=PaginatedResponse(
        total=total, page=page, page_size=page_size,
        items=[FormSubmissionRead.model_validate(s) for s in submissions],
    ))


@router.get("/{form_id}/submit", response_class=HTMLResponse)
async def render_form(form_id: int, db: AsyncSession = Depends(get_db)):
    """공개 폼 HTML 렌더링 - 브라우저에서 접속하면 채울 수 있는 폼 표시"""
    form = await db.get(Form, form_id)
    if not form:
        return HTMLResponse("<h1>폼을 찾을 수 없습니다</h1>", status_code=404)

    fields_html = ""
    for f in (form.fields or []):
        name = f.get("name", "")
        label = f.get("label", name)
        ftype = f.get("type", "text")
        required = "required" if f.get("required") else ""
        req_mark = ' <span style="color:#F2545B">*</span>' if f.get("required") else ""

        if ftype == "textarea":
            inp = f'<textarea name="{name}" rows="4" {required} style="width:100%;padding:10px 14px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;font-family:inherit;resize:vertical;"></textarea>'
        elif ftype == "select":
            opts_html = '<option value="">선택하세요</option>'
            for o in f.get("options", []):
                opts_html += f'<option value="{o}">{o}</option>'
            inp = f'<select name="{name}" {required} style="width:100%;padding:10px 14px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;background:#fff;">{opts_html}</select>'
        else:
            html_type = {"tel": "tel", "email": "email", "number": "number", "date": "date"}.get(ftype, "text")
            inp = f'<input type="{html_type}" name="{name}" {required} style="width:100%;padding:10px 14px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;"/>'

        fields_html += f'<div style="margin-bottom:20px;"><label style="display:block;font-size:14px;font-weight:500;color:#374151;margin-bottom:6px;">{label}{req_mark}</label>{inp}</div>'

    redirect_url = form.redirect_url or ""
    redirect_js = f'window.location.href="{redirect_url}";' if redirect_url else 'document.getElementById("result").innerHTML="<p style=\\"color:#00BDA5;font-weight:600;\\">제출이 완료되었습니다!</p>";form.reset();'

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{form.name} - MOCO CRM</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; background: #f5f8fa; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }}
  .form-card {{ background: #fff; border-radius: 16px; box-shadow: 0 4px 24px rgba(0,0,0,0.08); max-width: 560px; width: 100%; padding: 40px; }}
  .form-title {{ font-size: 24px; font-weight: 700; color: #33475B; margin-bottom: 8px; }}
  .form-subtitle {{ font-size: 14px; color: #8899A6; margin-bottom: 32px; }}
  .submit-btn {{ background: #FF7A59; color: #fff; border: none; padding: 12px 32px; font-size: 15px; font-weight: 600; border-radius: 8px; cursor: pointer; width: 100%; transition: background 0.2s; }}
  .submit-btn:hover {{ background: #e8613e; }}
  .submit-btn:disabled {{ background: #ccc; cursor: not-allowed; }}
  input:focus, textarea:focus, select:focus {{ outline: none; border-color: #FF7A59; box-shadow: 0 0 0 3px rgba(255,122,89,0.15); }}
  .powered {{ text-align: center; margin-top: 24px; font-size: 12px; color: #aaa; }}
</style>
</head>
<body>
<div class="form-card">
  <h1 class="form-title">{form.name}</h1>
  <p class="form-subtitle">아래 정보를 입력해 주세요.</p>
  <form id="publicForm" onsubmit="return handleSubmit(event)">
    {fields_html}
    <button type="submit" class="submit-btn" id="submitBtn">제출하기</button>
  </form>
  <div id="result" style="margin-top:16px;text-align:center;"></div>
  <p class="powered">Powered by MOCO CRM</p>
</div>
<script>
async function handleSubmit(e) {{
  e.preventDefault();
  const btn = document.getElementById('submitBtn');
  btn.disabled = true; btn.textContent = '제출 중...';
  const form = document.getElementById('publicForm');
  const fd = new FormData(form);
  const data = {{}};
  fd.forEach((v, k) => {{ data[k] = v; }});
  try {{
    const res = await fetch(window.location.pathname, {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ data: data }})
    }});
    if (!res.ok) throw new Error('제출 실패');
    {redirect_js}
  }} catch(err) {{
    document.getElementById('result').innerHTML = '<p style="color:#F2545B;">제출에 실패했습니다. 다시 시도해 주세요.</p>';
  }}
  btn.disabled = false; btn.textContent = '제출하기';
  return false;
}}
</script>
</body>
</html>"""
    return HTMLResponse(html)


@router.post("/{form_id}/submit", response_model=SuccessResponse)
async def submit_form(
    form_id: int,
    data: FormSubmissionCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    공개 폼 제출 처리

    1. 이메일로 기존 연락처 매칭 또는 새 연락처 생성
    2. 폼 제출 기록 저장
    3. 활동 기록 생성
    4. form_submission 자동화 트리거
    """
    form = await db.get(Form, form_id)
    if not form:
        return ErrorResponse(message="폼을 찾을 수 없습니다.")

    submitted_data = data.data
    email = submitted_data.get("email")
    contact_id = None

    # 연락처 매칭 또는 생성
    if email:
        result = await db.execute(
            select(Contact).where(Contact.email == email)
        )
        contact = result.scalar_one_or_none()

        if not contact:
            contact = Contact(
                first_name=submitted_data.get("first_name", submitted_data.get("name", "Unknown")),
                last_name=submitted_data.get("last_name", ""),
                email=email,
                phone=submitted_data.get("phone"),
                lead_status=LeadStatus.new,
                lifecycle_stage=LifecycleStage.lead,
                source=f"form:{form.name}",
            )
            db.add(contact)
            await db.flush()

        contact_id = contact.id

    # 제출 기록
    submission = FormSubmission(
        form_id=form_id,
        contact_id=contact_id,
        data=submitted_data,
    )
    db.add(submission)

    # 제출 카운트 증가
    form.submission_count = (form.submission_count or 0) + 1

    # 활동 기록
    activity = Activity(
        type=ActivityType.note,
        subject=f"폼 제출: {form.name}",
        body=str(submitted_data),
        contact_id=contact_id,
        extra_data={"form_id": form_id, "submission_data": submitted_data},
    )
    db.add(activity)
    await db.flush()

    # form_submission 자동화 트리거
    await evaluate_triggers(
        "form_submission",
        {
            "form_id": form_id,
            "contact_id": contact_id,
            "data": submitted_data,
        },
        db,
    )

    return SuccessResponse(data=FormSubmissionRead.model_validate(submission))
