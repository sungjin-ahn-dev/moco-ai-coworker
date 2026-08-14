"""
pii — 저장/로깅 전 PII 마스킹 + 보존 TTL (개인정보 최소화).

현재 memory 파일과 run_log(runs.jsonl)에는 사용자가 보낸 원문(prompt/response)이
그대로 적재된다. 이메일·휴대폰·주민번호·카드번호 같은 개인정보가 평문으로 디스크에
남으면 유출·보존기간 위반 리스크가 된다. 이 모듈은 순수 함수로 그런 식별자를
마스킹하고, 로그 항목의 보존 만료 여부(TTL)를 판정한다.

왜 opt-in·무회귀인가:
- 어떤 런타임(MCP/LLM/네트워크)도 임포트하지 않는 순수 텍스트 함수뿐이라
  단독으로는 아무 동작도 바꾸지 않는다.
- 배선 측에서 getattr(settings, 'PII_MASK_ENABLED', False) 처럼 명시 opt-in 할
  때만 저장 경로에 끼어든다. 플래그를 켜지 않으면 기존 동작 그대로(무회귀).
- 비대상(패턴 미매칭) 텍스트는 한 글자도 바꾸지 않고 보존한다.

공개 API:
    mask_pii(text)            -> str  : 4종 패턴 마스킹, 비대상 보존
    mask_record(d, fields)    -> dict : 얕은 복사 후 지정 필드만 mask_pii
    ttl_expired(date_str, days, now=None) -> bool : 보존기간 초과 판정
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

# --- 패턴 -------------------------------------------------------------------
# 이메일: local 앞 2자만 남기고 나머지 마스킹, @domain 은 보존.
_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+\-]+)@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")

# 한국 주민등록번호: ######-####### (6-7). 뒤 6자리 마스킹(성별/세기 1자리는 보존).
# 긴 숫자열 내부 오탐 방지를 위해 양옆 숫자 경계 확인.
_RRN_RE = re.compile(r"(?<!\d)\d{6}-\d{7}(?!\d)")

# 카드번호 16자리 (연속 또는 4자리 그룹, 구분자 -/공백). 뒤 4자리만 보존.
_CARD_RE = re.compile(r"(?<!\d)(?:\d{4}[-\s]?){3}\d{4}(?!\d)")

# 한국 휴대폰: 01[016-9] + (3~4) + 4, 구분자 다양(-, ., 공백, 없음). 뒤 4자리만 보존.
_PHONE_RE = re.compile(r"(?<!\d)01[016-9][-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)")


def _mask_except_last(s: str, keep: int) -> str:
    """문자열 s 안의 숫자 중 마지막 `keep`개만 남기고 나머지 숫자를 '*'로.

    구분자 등 비숫자 문자는 그대로 유지한다.
    """
    digit_pos = [i for i, c in enumerate(s) if c.isdigit()]
    to_mask = set(digit_pos[:-keep]) if keep > 0 else set(digit_pos)
    return "".join("*" if i in to_mask else c for i, c in enumerate(s))


def _mask_last(s: str, n: int) -> str:
    """문자열 s 안의 숫자 중 마지막 `n`개를 '*'로. 비숫자 문자는 유지."""
    digit_pos = [i for i, c in enumerate(s) if c.isdigit()]
    to_mask = set(digit_pos[-n:]) if n > 0 else set()
    return "".join("*" if i in to_mask else c for i, c in enumerate(s))


def _mask_email(m: "re.Match") -> str:
    local, domain = m.group(1), m.group(2)
    return f"{local[:2]}***@{domain}"


def mask_pii(text: str) -> str:
    """텍스트 내 PII(이메일/주민번호/카드/휴대폰)를 마스킹해 반환.

    - 이메일: local 앞 2자만 남기고 ``jo***@domain``.
    - 주민번호(######-#######): 뒤 6자리 마스킹 → ``901231-1******``.
    - 카드번호 16자리: 뒤 4자리만 → ``****-****-****-3456``.
    - 휴대폰(구분자 다양): 뒤 4자리만 → ``***-****-5678``.
    - 어떤 패턴에도 걸리지 않는 텍스트는 그대로 보존한다.

    비어있거나 문자열이 아니면 원본을 그대로 반환한다.
    """
    if not isinstance(text, str) or not text:
        return text

    # 순서 중요: 이메일 먼저(로컬부 숫자가 카드/폰으로 오탐되는 것 방지),
    # 그다음 숫자 기반 패턴. 각 치환 후 마스킹된 자리는 재매칭되지 않는다.
    out = _EMAIL_RE.sub(_mask_email, text)
    out = _RRN_RE.sub(lambda m: _mask_last(m.group(0), 6), out)
    out = _CARD_RE.sub(lambda m: _mask_except_last(m.group(0), 4), out)
    out = _PHONE_RE.sub(lambda m: _mask_except_last(m.group(0), 4), out)
    return out


def mask_record(d: dict, fields: list[str]) -> dict:
    """dict 를 얕은 복사한 뒤 지정 필드(str)만 mask_pii 적용해 반환.

    원본 dict 는 변형하지 않는다. 지정 필드가 없거나 문자열이 아니면 건너뛴다.
    """
    out = dict(d)
    for f in fields:
        v = out.get(f)
        if isinstance(v, str):
            out[f] = mask_pii(v)
    return out


def ttl_expired(date_str: str, days: int, now: Optional[datetime] = None) -> bool:
    """보존기간(days) 초과 여부.

    date_str[:10] 을 YYYY-MM-DD 로 파싱해 (now or datetime.now()) 와의 차이가
    days 를 '초과'하면 True. 정확히 days 이내면 False. 파싱 실패면 False(안전측).

    run_log 의 ``created_at`` ("%Y-%m-%d %H:%M:%S") 나 메모리 프론트매터의
    날짜 문자열 앞 10자만 사용하므로 시각 부분이 붙어 있어도 동작한다.
    """
    try:
        d = datetime.strptime((date_str or "")[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return False
    ref = now or datetime.now()
    return (ref - d) > timedelta(days=days)


# ---------------------------------------------------------------------------
# 배선 가이드 (opt-in, 무회귀)
# ---------------------------------------------------------------------------
# 1) 메모리 저장 경로 (memory_manager/agent.py 또는 메모리 파일 write 지점)
#      from app.cc_utils.pii import mask_pii
#      if getattr(settings, "PII_MASK_ENABLED", False):
#          query = mask_pii(query)          # 저장 직전 원문 마스킹
#
# 2) run_log_store.log_run 의 prompt/response (호출부에서 옵션 마스킹)
#      from app.cc_utils.pii import mask_record
#      entry = {"prompt": prompt, "response": response, ...}
#      if getattr(settings, "PII_MASK_ENABLED", False):
#          entry = mask_record(entry, ["prompt", "response"])
#      run_log.log_run(prompt=entry["prompt"], response=entry["response"], ...)
#
# 3) TTL 스윕 (보존기간 지난 로그 정리 예시)
#      from app.cc_utils.pii import ttl_expired
#      keep = [e for e in run_log.tail(5000)
#              if not ttl_expired(e.get("created_at", ""),
#                                 getattr(settings, "PII_RETENTION_DAYS", 90))]
#      # keep 만 다시 기록 → 만료분 삭제
