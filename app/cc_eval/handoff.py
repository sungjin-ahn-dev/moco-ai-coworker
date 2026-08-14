"""
cc_eval.handoff — 멀티에이전트 handoff 정보 손실률 (entity recall)

오케스트레이터→서브에이전트, 서브에이전트→최종답으로 넘어갈 때 앞 단계의
핵심 엔티티(이름·날짜·ID·수치)가 보존됐는지. 손실은 멀티에이전트 대표 실패모드.

핵심 지표는 순수(entity_recall). 엔티티 추출은 (a) 정규식 기본 + (b) LLM 옵션.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s.lower())


def entity_recall(entities_before: Iterable[str], text_after: str) -> dict:
    """앞 단계 핵심 엔티티가 뒤 단계 텍스트에 얼마나 보존됐나.

        recall = |보존된 엔티티| / |엔티티|
    """
    ents = [e for e in entities_before if e and e.strip()]
    if not ents:
        return {"recall": 1.0, "preserved": [], "lost": [], "n": 0}
    hay = _norm(text_after or "")
    preserved = [e for e in ents if _norm(e) in hay]
    lost = [e for e in ents if _norm(e) not in hay]
    return {"recall": len(preserved) / len(ents),
            "preserved": preserved, "lost": lost, "n": len(ents)}


# 정규식 기반 경량 엔티티 추출(날짜/시간/이메일/URL/@멘션/#채널/수치/따옴표문구)
_PATTERNS = [
    r"\b\d{4}-\d{2}-\d{2}\b",                    # 날짜
    r"\b\d{1,2}:\d{2}\b",                        # 시각
    r"[\w.+-]+@[\w.-]+\.\w+",                    # 이메일
    r"https?://\S+",                             # URL
    r"<@[A-Z0-9]+>|<#[A-Z0-9]+\|?[^>]*>",        # Slack 멘션/채널
    r"\b\d[\d,]*\b",                             # 수치
    r"[\"“']([^\"”']{2,40})[\"”']",              # 따옴표 인용구
]


def extract_entities_regex(text: str) -> list[str]:
    out: list[str] = []
    for pat in _PATTERNS:
        for m in re.findall(pat, text or ""):
            val = m if isinstance(m, str) else (m[0] if m else "")
            if val and val not in out:
                out.append(val)
    return out


def handoff_loss(upstream_text: str, downstream_text: str,
                 extractor=extract_entities_regex) -> dict:
    """상류 텍스트에서 엔티티를 뽑아 하류에서의 보존율/손실율을 계산."""
    ents = extractor(upstream_text)
    r = entity_recall(ents, downstream_text)
    r["loss"] = 1.0 - r["recall"]
    return r


async def extract_entities_llm(text: str) -> list[str]:
    """LLM 기반 핵심 엔티티 추출(정규식이 놓치는 의미 엔티티용). MOCO 런타임 필요."""
    import json
    import os
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage
    from app.cc_utils.sdk_retry import RetryableSDKClient
    from app.cc_utils.prompt_helper import prepare_options
    from app.config.settings import get_settings

    settings = get_settings()
    options = prepare_options(ClaudeAgentOptions(
        system_prompt='텍스트의 핵심 엔티티(인물/날짜/프로젝트/결정/수치)만 JSON 배열로. 예: ["김대표","2026-08-20","배포연기"]',
        model=settings.MODEL_FOR_SIMPLE, permission_mode="bypassPermissions",
        allowed_tools=[], setting_sources=["project"], cwd=os.getcwd(),
    ))
    try:
        async with RetryableSDKClient(options, max_retries=2, agent_name="EVAL_ENTITY") as client:
            await client.query(text)
            async for m in client.receive_response():
                if isinstance(m, ResultMessage):
                    mm = re.search(r"\[.*\]", m.result or "", re.S)
                    return json.loads(mm.group(0)) if mm else []
    except Exception:
        pass
    return []
