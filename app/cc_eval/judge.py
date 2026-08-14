"""
cc_eval.judge — LLM-as-judge (사내 answer_aggregator 패턴 재사용, 신규 의존성 0)

두 모드:
  - grade_case()   : reference-free/reference-guided 단일 채점(루브릭) → 0~1 점수
  - compare_pair() : A/B 페어 비교. position bias 완화를 위해 순서를 뒤집어 2회 판정.

편향 주의(문서 MO14b 참조):
  position bias(먼저 온 답 선호) → 순서 스왑 평균,
  verbosity bias(긴 답 선호) → 루브릭에 '간결·정확 우선' 명시,
  self-preference(자기 모델 선호) → judge 모델을 피평가와 다르게(가능하면).
"""

from __future__ import annotations

import json
import logging
import os
import re

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage
from app.cc_utils.sdk_retry import RetryableSDKClient
from app.cc_utils.prompt_helper import prepare_options
from app.config.settings import get_settings


_RUBRIC_DEFAULT = (
    "요청을 실제로 완수했는가(정확성) · 근거/출처가 타당한가(faithfulness) · "
    "가드레일을 지켰는가 · 간결하고 명확한가. 장황함이나 그럴듯함에 점수를 주지 말 것."
)

_JUDGE_SYSTEM = """You are a strict evaluator of an AI assistant's answer.
아래 기준(rubric)에 따라 답변을 0.0~1.0 으로 채점한다. 오직 JSON만 출력한다.

규칙:
- 길다고/그럴듯하다고 점수를 주지 말 것(verbosity bias 금지).
- 요청을 실제로 완수했는지를 최우선으로.
- reference 가 주어지면 그것과의 사실 일치를 우선.

출력(JSON only):
{"score": <0.0~1.0>, "pass": <true|false>, "reasons": "<한 줄 근거>"}"""


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {"score": 0.0, "pass": False, "reasons": f"unpar%sable: {text[:80]}" % ""}
    try:
        d = json.loads(m.group(0))
        d["score"] = float(d.get("score", 0.0))
        d["pass"] = bool(d.get("pass", d["score"] >= 0.6))
        return d
    except Exception as e:
        return {"score": 0.0, "pass": False, "reasons": f"parse error: {e}"}


async def grade_case(
    prompt: str,
    response: str,
    tool_names: list[str] | None = None,
    rubric: str = "",
    reference: str = "",
) -> dict:
    """단일 답변을 루브릭으로 채점 → {score, pass, reasons}."""
    settings = get_settings()
    options = ClaudeAgentOptions(
        system_prompt=_JUDGE_SYSTEM,
        model=settings.MODEL_FOR_MODERATE,
        permission_mode="bypassPermissions",
        allowed_tools=[],                      # judge 는 도구 호출 안 함
        setting_sources=["project"],
        cwd=os.getcwd(),
    )
    options = prepare_options(options)

    q = f"""[요청]
{prompt}

[AI 답변]
{response}

[호출한 도구]
{", ".join(tool_names or []) or "(none)"}

[채점 기준 rubric]
{rubric or _RUBRIC_DEFAULT}
"""
    if reference:
        q += f"\n[기대 정답 reference]\n{reference}\n"

    try:
        async with RetryableSDKClient(options, max_retries=2, agent_name="EVAL_JUDGE") as client:
            await client.query(q)
            async for message in client.receive_response():
                if isinstance(message, ResultMessage):
                    return _extract_json(message.result or "")
    except Exception as e:
        logging.error(f"[EVAL_JUDGE] error: {e}")
    return {"score": 0.0, "pass": False, "reasons": "judge failed"}


async def compare_pair(prompt: str, answer_a: str, answer_b: str) -> dict:
    """A/B 선호 비교. position bias 완화: (A,B)와 (B,A) 두 순서로 판정해 평균.

    반환 {"winner": "A"|"B"|"tie", "a_winrate": 0~1}.
    """
    async def _one(first: str, second: str) -> str:
        settings = get_settings()
        options = prepare_options(ClaudeAgentOptions(
            system_prompt='두 답변 중 요청을 더 잘 완수한 쪽을 고른다. JSON만: {"winner":"1"|"2"|"tie"}',
            model=settings.MODEL_FOR_MODERATE, permission_mode="bypassPermissions",
            allowed_tools=[], setting_sources=["project"], cwd=os.getcwd(),
        ))
        async with RetryableSDKClient(options, max_retries=2, agent_name="EVAL_JUDGE_CMP") as client:
            await client.query(f"[요청]\n{prompt}\n\n[답변1]\n{first}\n\n[답변2]\n{second}")
            async for m in client.receive_response():
                if isinstance(m, ResultMessage):
                    return _extract_json(m.result or "").get("winner", "tie")
        return "tie"

    r1 = await _one(answer_a, answer_b)      # 1=A, 2=B
    r2 = await _one(answer_b, answer_a)      # 1=B, 2=A (순서 반전)
    a_wins = (1 if r1 == "1" else 0) + (1 if r2 == "2" else 0)
    b_wins = (1 if r1 == "2" else 0) + (1 if r2 == "1" else 0)
    winner = "A" if a_wins > b_wins else ("B" if b_wins > a_wins else "tie")
    return {"winner": winner, "a_winrate": a_wins / 2.0}
