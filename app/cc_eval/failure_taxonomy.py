"""
cc_eval.failure_taxonomy — MAST 계열 멀티에이전트 실패 분류

실패 run 을 카테고리로 라벨링해 '어디를 고쳐야 하나'를 집계 가능하게 한다.
카테고리(MAST: Cemri 2025 기반 단순화):
  spec_violation           역할/지시/포맷 위반, 요청과 다른 일 수행
  inter_agent_misalignment handoff 정보손실·모순·무한 핑퐁·컨텍스트 유실
  verification_gap         검증/종료 판단 실패 → 미완인데 종료, 환각 근거
  tool_error               도구 실패/인증/빈결과를 복구 못함
  routing_error            잘못된 에이전트/도구군 선택
  infra                    타임아웃/컨텍스트오버플로/레이트리밋 등 인프라
  other
"""

from __future__ import annotations

import json
import logging
import os
import re

CATEGORIES = [
    "spec_violation", "inter_agent_misalignment", "verification_gap",
    "tool_error", "routing_error", "infra", "other",
]

_SYS = """You classify a FAILED multi-agent run into exactly one failure category.
카테고리: spec_violation, inter_agent_misalignment, verification_gap, tool_error, routing_error, infra, other.
정의:
- spec_violation: 역할/지시/출력포맷 위반, 요청과 다른 일을 함
- inter_agent_misalignment: handoff 정보손실·모순·핑퐁·컨텍스트 유실
- verification_gap: 미완인데 종료, 검증 없이 환각 근거 제시
- tool_error: 도구 실패/인증/빈결과를 복구 못함
- routing_error: 잘못된 에이전트/도구 선택
- infra: 타임아웃/컨텍스트오버플로/레이트리밋
JSON only: {"category":"<one>","reason":"<한 줄>"}"""


def rule_prelabel(state: str, error: str) -> str | None:
    """LLM 이전 규칙 프리라벨(명백한 인프라 실패는 LLM 없이)."""
    e = (error or "").lower()
    if state in ("timeout",) or "idle timeout" in e:
        return "infra"
    if "413" in e or "context overflow" in e or "prompt is too long" in e:
        return "infra"
    if "rate limit" in e or "429" in e:
        return "infra"
    return None


async def classify_failure(prompt: str, response: str, tool_names: list[str],
                           state: str = "error", error: str = "") -> dict:
    """실패 run 을 카테고리로 분류. 규칙 프리라벨 우선, 아니면 LLM."""
    pre = rule_prelabel(state, error)
    if pre:
        return {"category": pre, "reason": f"rule: {error[:80]}"}

    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage
    from app.cc_utils.sdk_retry import RetryableSDKClient
    from app.cc_utils.prompt_helper import prepare_options
    from app.config.settings import get_settings

    settings = get_settings()
    options = prepare_options(ClaudeAgentOptions(
        system_prompt=_SYS, model=settings.MODEL_FOR_MODERATE,
        permission_mode="bypassPermissions", allowed_tools=[],
        setting_sources=["project"], cwd=os.getcwd(),
    ))
    q = f"[요청]\n{prompt}\n\n[응답]\n{response}\n\n[도구]\n{', '.join(tool_names or []) or '(none)'}\n\n[에러]\n{error or '(none)'}"
    try:
        async with RetryableSDKClient(options, max_retries=2, agent_name="EVAL_FAILURE") as client:
            await client.query(q)
            async for m in client.receive_response():
                if isinstance(m, ResultMessage):
                    mm = re.search(r"\{.*\}", m.result or "", re.S)
                    if mm:
                        d = json.loads(mm.group(0))
                        cat = d.get("category", "other")
                        return {"category": cat if cat in CATEGORIES else "other",
                                "reason": d.get("reason", "")}
    except Exception as e:
        logging.error(f"[EVAL_FAILURE] {e}")
    return {"category": "other", "reason": "classify failed"}


def failure_distribution(labels: list[str]) -> dict:
    """카테고리 분포 집계(순수). 어느 실패모드가 지배적인지."""
    counts = {c: 0 for c in CATEGORIES}
    for l in labels:
        counts[l if l in counts else "other"] += 1
    total = len(labels)
    return {"counts": counts, "total": total,
            "share": {c: (counts[c] / total if total else 0.0) for c in CATEGORIES},
            "top": max(counts, key=counts.get) if total else None}
