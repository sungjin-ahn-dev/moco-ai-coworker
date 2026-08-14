"""
cc_eval.credit — 에이전트별 기여도 귀속 (Credit Assignment)

멀티에이전트 성공/실패를 어느 컴포넌트(서브에이전트/도구군)에 귀속시킬지.
방법: counterfactual leave-one-out ablation — 컴포넌트를 하나 끄고 성공률 델타를 잰다.
  credit[c] = success(full) - success(without c)   (양수 클수록 그 컴포넌트가 중요)

pure 집계(credit_from_deltas)는 테스트되고, 실제 ablation 실행은 MOCO 런타임 필요.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Iterable, Optional

from app.cc_eval import metrics as M
from app.cc_eval.schema import GoldenCase


def credit_from_deltas(full_success: float, ablated_success: dict[str, float]) -> dict:
    """full 성공률과 컴포넌트별 ablated 성공률 → credit.

    credit[c] = full - ablated[c].  음수면 그 컴포넌트가 오히려 해가 됐다는 신호.
    """
    credit = {c: full_success - s for c, s in ablated_success.items()}
    ranked = sorted(credit.items(), key=lambda kv: kv[1], reverse=True)
    return {"full_success": full_success, "credit": credit,
            "ranked": ranked,
            "most_critical": ranked[0][0] if ranked else None,
            "harmful": [c for c, v in credit.items() if v < 0]}


# 컴포넌트 하나를 끈 채로 케이스를 k회 실행하고 성공률을 반환하는 함수 타입.
# ablate=None 이면 full 시스템. 구현은 orchestrator_fn 의 allowed_tools/mcp/sub-agent 필터링.
AblationRunFn = Callable[[GoldenCase, Optional[str], int], Awaitable[float]]


async def leave_one_out_credit(
    case: GoldenCase,
    components: Iterable[str],
    run_success_rate: AblationRunFn,
    k: int = 3,
) -> dict:
    """한 케이스에 대해 컴포넌트별 leave-one-out credit 계산.

    run_success_rate(case, ablate, k): ablate 컴포넌트를 끈(=None이면 full) 상태로
    k회 실행한 성공률(0~1)을 반환하는 콜백. (MOCO 런타임에서 orchestrator_fn 을
    allowed_tools/서브에이전트 필터로 구성해 주입.)
    """
    full = await run_success_rate(case, None, k)
    ablated = {}
    for c in components:
        ablated[c] = await run_success_rate(case, c, k)
    return credit_from_deltas(full, ablated)


def aggregate_credit(per_case: list[dict]) -> dict:
    """여러 케이스의 credit 을 컴포넌트별 평균으로 집계."""
    acc: dict[str, list[float]] = {}
    for r in per_case:
        for c, v in r.get("credit", {}).items():
            acc.setdefault(c, []).append(v)
    mean_credit = {c: (sum(vs) / len(vs) if vs else 0.0) for c, vs in acc.items()}
    ranked = sorted(mean_credit.items(), key=lambda kv: kv[1], reverse=True)
    return {"mean_credit": mean_credit, "ranked": ranked,
            "most_critical": ranked[0][0] if ranked else None}
