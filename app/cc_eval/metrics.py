"""
cc_eval.metrics — 순수 평가 지표 함수 (런타임 의존 0, pytest 로 검증됨)

두 축을 계산한다:
  품질 축 : task success rate, pass^k / pass@k, tool-call accuracy(P/R/F1)
  시스템 축: 세션 E2E 지연 p50/p95, $/성공-task

핵심 설계 결정:
  - pass^k (τ-bench): k회를 뽑았을 때 '전부' 성공할 확률. 비결정 에이전트의
    신뢰성 지표로 pass@k(하나라도 성공)와 반대 방향. k=1 이면 곧 success rate.
  - 집계는 요청 단위가 아니라 '세션(대화) 단위'를 함께 제공(멀티에이전트 함정).
"""

from __future__ import annotations

import math
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# 도구 호출 정확도 (Tool-call accuracy)
# ---------------------------------------------------------------------------

def tool_call_metrics(
    expected: Iterable[str],
    actual: Iterable[str],
    must_call: Optional[Iterable[str]] = None,
    must_not_call: Optional[Iterable[str]] = None,
) -> dict:
    """기대 도구 집합 대비 실제 호출된 도구의 precision/recall/F1.

    집합 기반(순서·횟수 무시). 순서/중복이 중요하면 trajectory 편집거리를 별도 사용.
    must_call/must_not_call 은 하드 게이트.
    """
    exp = set(expected or [])
    act = set(actual or [])
    tp = len(exp & act)

    # 기대가 비어있으면(도구 불필요 케이스): 실제도 비어야 precision/recall=1
    precision = tp / len(act) if act else (1.0 if not exp else 0.0)
    recall = tp / len(exp) if exp else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    mc = set(must_call or [])
    mnc = set(must_not_call or [])
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "missing": sorted(exp - act),      # 불렀어야 했는데 안 부른 것
        "extra": sorted(act - exp),        # 불필요하게 부른 것
        "must_call_ok": mc.issubset(act),
        "must_not_call_ok": not (mnc & act),
    }


# ---------------------------------------------------------------------------
# pass^k / pass@k  (비결정성)
# ---------------------------------------------------------------------------

def pass_hat_k(successes: list[bool], k: int) -> float:
    """τ-bench pass^k — n회 실행 중 c회 성공일 때, k개를 (비복원) 뽑아 '전부' 성공할 확률.

        pass^k = C(c, k) / C(n, k)

    k=1 이면 c/n (= success rate). 신뢰성이 낮을수록 k가 커질 때 급락한다.
    k > n 이면 정의 불가(nan).
    """
    n = len(successes)
    c = sum(1 for s in successes if s)
    if n == 0 or k <= 0 or k > n:
        return float("nan")
    return math.comb(c, k) / math.comb(n, k)


def pass_at_k(successes: list[bool], k: int) -> float:
    """표준 pass@k — k개를 뽑아 '하나라도' 성공할 확률.

        pass@k = 1 - C(n-c, k) / C(n, k)
    """
    n = len(successes)
    c = sum(1 for s in successes if s)
    if n == 0 or k <= 0 or k > n:
        return float("nan")
    if c == 0:
        return 0.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def success_rate(successes: list[bool]) -> float:
    return (sum(1 for s in successes if s) / len(successes)) if successes else float("nan")


# ---------------------------------------------------------------------------
# 시스템 축 (지연·비용)
# ---------------------------------------------------------------------------

def percentile(values: list[float], p: float) -> float:
    """선형보간 백분위수 (p in [0,100]). numpy 없이 구현."""
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return float("nan")
    if len(xs) == 1:
        return float(xs[0])
    rank = (p / 100.0) * (len(xs) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return float(xs[lo])
    frac = rank - lo
    return float(xs[lo] * (1 - frac) + xs[hi] * frac)


def latency_summary(latencies: list[float]) -> dict:
    return {
        "p50": percentile(latencies, 50),
        "p95": percentile(latencies, 95),
        "p99": percentile(latencies, 99),
        "mean": (sum(latencies) / len(latencies)) if latencies else float("nan"),
        "n": len(latencies),
    }


def cost_per_success(costs: list[float], successes: list[bool]) -> float:
    """$/성공-task. 성공 0건이면 inf (비용은 났는데 성공이 없음)."""
    total_cost = sum(costs)
    n_success = sum(1 for s in successes if s)
    if n_success == 0:
        return float("inf") if total_cost > 0 else float("nan")
    return total_cost / n_success


# ---------------------------------------------------------------------------
# 세션 단위 집계 (멀티에이전트 함정: 요청이 아니라 세션으로 본다)
# ---------------------------------------------------------------------------

def session_aggregate(runs: list[dict]) -> dict:
    """요청(run) 리스트를 세션 단위로 접어 집계.

    각 run dict: {session_id, success(bool), elapsed_s(float), cost_usd(float)}
    세션 성공 = 그 세션의 모든 run 성공, 세션 E2E 지연 = 세션 내 run 지연 합.
    → 요청 평균이 감추는 '대화 전체 체감'을 드러낸다.
    """
    by_session: dict[str, list[dict]] = {}
    for r in runs:
        by_session.setdefault(r.get("session_id") or "_", []).append(r)

    sess_success: list[bool] = []
    sess_latency: list[float] = []
    sess_cost: list[float] = []
    for _sid, rs in by_session.items():
        sess_success.append(all(bool(r.get("success")) for r in rs))
        sess_latency.append(sum(float(r.get("elapsed_s", 0.0)) for r in rs))
        sess_cost.append(sum(float(r.get("cost_usd", 0.0)) for r in rs))

    req_success = [bool(r.get("success")) for r in runs]
    return {
        "n_requests": len(runs),
        "n_sessions": len(by_session),
        "request_success_rate": success_rate(req_success),
        "session_success_rate": success_rate(sess_success),   # ← 더 엄격, 체감에 가까움
        "session_latency": latency_summary(sess_latency),     # E2E(세션 합)
        "request_latency": latency_summary([float(r.get("elapsed_s", 0.0)) for r in runs]),
        "cost_per_successful_session": cost_per_success(sess_cost, sess_success),
    }


# ---------------------------------------------------------------------------
# 케이스 스코어 집계
# ---------------------------------------------------------------------------

def aggregate_case_scores(scores: list, ks: tuple[int, ...] = (1, 2, 3)) -> dict:
    """CaseScore 리스트(dict/obj) → 전체 요약. pass^k/pass@k 는 케이스별 계산 후 평균."""
    def _get(s, key, default=None):
        return getattr(s, key, None) if not isinstance(s, dict) else s.get(key, default)

    case_success: list[bool] = []           # 케이스 성공(다수결: 성공률>=0.5)
    tool_f1s: list[float] = []
    judge_all: list[float] = []
    passhat = {k: [] for k in ks}
    passat = {k: [] for k in ks}

    for s in scores:
        flags = list(_get(s, "success_flags", []) or [])
        if flags:
            case_success.append(sum(flags) / len(flags) >= 0.5)
            for k in ks:
                ph = pass_hat_k(flags, k)
                pa = pass_at_k(flags, k)
                if not math.isnan(ph):
                    passhat[k].append(ph)
                if not math.isnan(pa):
                    passat[k].append(pa)
        f1 = _get(s, "tool_f1", None)
        if f1 is not None:
            tool_f1s.append(float(f1))
        judge_all.extend(list(_get(s, "judge_scores", []) or []))

    def _avg(xs):
        return (sum(xs) / len(xs)) if xs else float("nan")

    return {
        "n_cases": len(scores),
        "case_success_rate": success_rate(case_success),
        "mean_tool_f1": _avg(tool_f1s),
        "mean_judge_score": _avg(judge_all),
        "pass_hat_k": {k: _avg(passhat[k]) for k in ks},
        "pass_at_k": {k: _avg(passat[k]) for k in ks},
    }


# ---------------------------------------------------------------------------
# 프롬프트/프리픽스 캐시 효율 (Anthropic API/claude CLI usage 기반)
# 각 서브에이전트는 별도 claude CLI 서브프로세스라, 호출마다 반환되는 usage 로
# 프리픽스 캐시 히트율을 '측정'할 수 있다(자체 GPU 서빙 불필요).
# ---------------------------------------------------------------------------

def _usage_get(u, key: str) -> int:
    v = u.get(key) if isinstance(u, dict) else getattr(u, key, 0)
    return int(v or 0)


def cache_efficiency(usages, read_cost_mult: float = 0.1, create_cost_mult: float = 1.25) -> dict:
    """프리픽스(프롬프트) 캐시 효율 — usage 의 cache_read/creation/input 으로 히트율·절감 추정.

    각 usage: {input_tokens, cache_creation_input_tokens, cache_read_input_tokens, ...}
    ('못 재는' 게 아니라 '재면 되는' 값 — API/CLI 가 호출마다 반환.)

        hit_rate        = cache_read / (input + cache_creation + cache_read)
        prefix_hit_rate = cache_read / (cache_creation + cache_read)
        est_cost_saving = 1 - 유효비용/무캐시비용
                          (read≈0.1x, create≈1.25x[5m]/2x[1h] 가정 — 인자로 조정.
                           콜드 호출은 캐시 생성 프리미엄으로 음수(손해)가 정상.)

    단일 usage(dict/obj) 또는 usage 리스트를 받는다.
    """
    if not isinstance(usages, (list, tuple)):
        usages = [usages]
    inp = sum(_usage_get(u, "input_tokens") for u in usages)
    cc = sum(_usage_get(u, "cache_creation_input_tokens") for u in usages)
    cr = sum(_usage_get(u, "cache_read_input_tokens") for u in usages)
    total = inp + cc + cr
    prefix = cc + cr
    baseline = float(total)                       # 캐시 없으면 프리픽스도 전량 full-price
    effective = inp * 1.0 + cc * create_cost_mult + cr * read_cost_mult
    return {
        "n": len(usages),
        "input_tokens": inp,
        "cache_creation_tokens": cc,
        "cache_read_tokens": cr,
        "prefix_tokens": prefix,
        "hit_rate": (cr / total) if total else float("nan"),
        "prefix_hit_rate": (cr / prefix) if prefix else float("nan"),
        "est_cost_saving": (1.0 - effective / baseline) if baseline else float("nan"),
    }
