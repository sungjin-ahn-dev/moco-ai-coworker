"""
tool_selector — tool-RAG lite: 쿼리 기반 MCP 서버 네임스페이스 선별 (순수)

현재 오케스트레이터는 15~20개 MCP 서버(도구 221개)를 전량 로드하고
allowed_tools=["*"] 로 전부 노출한다. 매 요청 도구정의 블록에 221개 스키마가
실려 attention·오선택·토큰이 커진다. 이 모듈은 의도 기반으로 관련 서버
네임스페이스만 골라 allowed_tools 를 좁힌다.

안전: 매칭이 없으면 fallback_all=True 로 ["*"] 를 반환 → 현재 동작과 동일(무회귀).
subprocess(CLI) 여부와 무관 — MOCO 가 ClaudeAgentOptions.allowed_tools 를 직접 통제.
"""

from __future__ import annotations

import re
from typing import Iterable

# 항상 노출(응답/위임/스킬에 필수) — 도메인 키워드 매칭과 무관하게 유지.
# mcp__agents__*(call_sub_agent 위임)와 mcp__skills__* 를 빼면 tool-RAG 활성 시
# 위임 프롬프트가 지시하는 도구가 필터링돼 tool-not-found 가 나므로 반드시 always-on.
ALWAYS_ON = ["mcp__slack__*", "mcp__agents__*", "mcp__skills__*"]

# 서버 네임스페이스 → 트리거 키워드(한/영)
KEYWORD_MAP: dict[str, list[str]] = {
    "mcp__crm__*": ["crm", "영업", "고객", "리드", "거래", "파이프라인", "담당자", "회사", "미팅로그"],
    "mcp__google_calendar__*": ["일정", "캘린더", "미팅", "회의", "예약", "스케줄", "약속"],
    "mcp__gmail__*": ["메일", "이메일", "gmail", "메일함", "발송", "회신"],
    "mcp__google_drive__*": ["드라이브", "문서", "파일", "폴더", "drive", "docs", "스프레드시트"],
    "mcp__clickup__*": ["clickup", "태스크", "할일", "티켓", "업무", "프로젝트관리"],
    "mcp__skills__*": ["스킬", "skill", "ppt", "발표", "pdf", "docx", "xlsx", "템플릿"],
}


def _tokenize(text: str) -> set[str]:
    text = (text or "").lower()
    return set(re.findall(r"[가-힣]+|[a-z0-9]+", text))


def select_namespaces(
    query: str,
    available: Iterable[str] | None = None,
    keyword_map: dict[str, list[str]] = KEYWORD_MAP,
    always_on: list[str] = ALWAYS_ON,
    fallback_all: bool = True,
) -> list[str]:
    """쿼리로 관련 서버 네임스페이스(allowed_tools 패턴)를 선별.

    available 이 주어지면 그 안의 패턴만(있는 서버만). 매칭 0개면:
      fallback_all=True → ["*"] (무회귀·안전)  /  False → always_on 만.
    """
    toks = _tokenize(query)
    avail = set(available) if available is not None else None

    selected: list[str] = []
    for ns, kws in keyword_map.items():
        if avail is not None and ns not in avail:
            continue
        if any(_kw_hit(kw, toks, query) for kw in kws):
            selected.append(ns)

    on = [ns for ns in always_on if (avail is None or ns in avail)]
    result = list(dict.fromkeys(on + selected))   # 중복 제거·순서 유지

    if not selected:                              # 도메인 매칭 실패
        return ["*"] if fallback_all else (result or ["*"])
    return result


def _kw_hit(kw: str, toks: set[str], query: str) -> bool:
    # 영문/복합어는 부분일치, 한글 단어는 토큰 일치
    if re.search(r"[a-z0-9]", kw):
        return kw in (query or "").lower()
    return kw in toks


def to_allowed_tools(namespaces: list[str], extra: list[str] | None = None) -> list[str]:
    """선별된 네임스페이스를 ClaudeAgentOptions.allowed_tools 리스트로."""
    if namespaces == ["*"]:
        return ["*"]
    return list(dict.fromkeys(namespaces + (extra or [])))
