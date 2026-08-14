"""
cc_eval.significance — 통계적 유의성 (순수, 테스트됨)

에이전트 평가는 비결정적이라 "몇 점"이 아니라 "신뢰구간"으로 봐야 한다.
  - bootstrap_ci : 임의 통계량(성공률/평균지연 등)의 부트스트랩 신뢰구간
  - mcnemar      : 두 설정(A vs B)을 같은 케이스로 비교하는 짝검정
                   (프롬프트/라우팅 바꿨을 때 유의미한 개선인지)
"""

from __future__ import annotations

import math
import random
from statistics import mean
from typing import Callable, Sequence


def bootstrap_ci(
    values: Sequence[float],
    statistic: Callable[[Sequence[float]], float] = mean,
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> dict:
    """부트스트랩 백분위 신뢰구간. (point, lo, hi, n) 반환."""
    xs = list(values)
    n = len(xs)
    if n == 0:
        return {"point": float("nan"), "lo": float("nan"), "hi": float("nan"), "n": 0}
    rng = random.Random(seed)
    stats = []
    for _ in range(n_boot):
        sample = [xs[rng.randrange(n)] for _ in range(n)]
        stats.append(statistic(sample))
    stats.sort()
    lo = stats[int((alpha / 2) * n_boot)]
    hi = stats[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return {"point": statistic(xs), "lo": lo, "hi": hi, "n": n,
            "ci": f"{(1 - alpha) * 100:.0f}%"}


def _binom_two_sided_p(b: int, c: int) -> float:
    """McNemar 정확검정: 불일치쌍 b,c 에 대한 양측 이항검정 p (n=b+c, p0=0.5)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    # 양측 = 2 * P(X <= k),  X~Binom(n, 0.5)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) * (0.5 ** n)
    return min(1.0, 2.0 * tail)


def mcnemar(a_correct: Sequence[bool], b_correct: Sequence[bool]) -> dict:
    """두 시스템의 짝지어진 정오(같은 케이스) → McNemar 검정.

    b = A맞고 B틀림, c = A틀리고 B맞음 (일치쌍은 정보 없음).
    exact 이항 p + chi2 근사 p 둘 다 반환. b>c 면 A가 더 나음.
    """
    if len(a_correct) != len(b_correct):
        raise ValueError("paired inputs must be same length")
    b = sum(1 for a, bb in zip(a_correct, b_correct) if a and not bb)
    c = sum(1 for a, bb in zip(a_correct, b_correct) if (not a) and bb)
    # 연속성 보정 chi2 (df=1) 근사, survival = erfc(sqrt(stat/2))
    stat = ((abs(b - c) - 1) ** 2) / (b + c) if (b + c) > 0 else 0.0
    p_chi2 = math.erfc(math.sqrt(stat / 2)) if (b + c) > 0 else 1.0
    return {
        "b_A_only": b, "c_B_only": c, "n_discordant": b + c,
        "chi2": stat, "p_chi2": p_chi2,
        "p_exact": _binom_two_sided_p(b, c),
        "better": ("A" if b > c else ("B" if c > b else "tie")),
    }


def min_runs_for_ci(width_target: float, p_est: float = 0.5) -> int:
    """성공률 CI 반폭을 width_target 이하로 하려면 대략 몇 회 필요한가 (정규근사).

        half_width ≈ 1.96 * sqrt(p(1-p)/n)  →  n ≈ (1.96^2 p(1-p)) / width^2
    """
    if width_target <= 0:
        return 0
    return math.ceil((1.96 ** 2) * p_est * (1 - p_est) / (width_target ** 2))
