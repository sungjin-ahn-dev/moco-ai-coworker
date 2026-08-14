"""
cc_eval.orchestrator_eval — 오케스트레이터 '자체' 평가 (순수, 테스트됨)

실행 품질과 분리해 라우팅/분해/위임 결정만 채점한다. gold plan 대비 비교.
  - routing_accuracy      : 어떤 에이전트/도구군으로 보낼지 옳게 골랐나
  - decomposition_metrics : 계획 스텝의 완결성(recall)·불필요(precision)·과분해
  - delegation_efficiency : 불필요한 위임/왕복 비율
"""

from __future__ import annotations

from typing import Iterable


def routing_accuracy(predicted: str, gold: str) -> float:
    """단일 라우팅 결정 정확도(정확 일치). 1.0/0.0."""
    return 1.0 if (predicted or "").strip() == (gold or "").strip() else 0.0


def routing_accuracy_set(predicted: Iterable[str], gold: Iterable[str]) -> dict:
    """다중 라우팅(여러 에이전트/도구군 선택)의 P/R/F1."""
    p, g = set(predicted or []), set(gold or [])
    tp = len(p & g)
    prec = tp / len(p) if p else (1.0 if not g else 0.0)
    rec = tp / len(g) if g else 1.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {"precision": prec, "recall": rec, "f1": f1,
            "wrong_route": sorted(p - g), "missed_route": sorted(g - p)}


def decomposition_metrics(predicted_steps: list[str], gold_steps: list[str]) -> dict:
    """계획 분해 품질. 스텝 집합 P/R/F1 + 과분해(over-decomposition) 비율.

    과분해 = 예측 스텝 수 / 골드 스텝 수 (1보다 크게 벗어날수록 불필요 분해).
    """
    p, g = set(predicted_steps or []), set(gold_steps or [])
    tp = len(p & g)
    prec = tp / len(p) if p else (1.0 if not g else 0.0)
    rec = tp / len(g) if g else 1.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    over = (len(predicted_steps or []) / len(gold_steps)) if gold_steps else float("nan")
    return {"precision": prec, "recall": rec, "f1": f1,
            "over_decomposition": over,
            "missing_steps": sorted(g - p), "extra_steps": sorted(p - g)}


def delegation_efficiency(n_delegations: int, n_necessary: int) -> dict:
    """위임 효율 = 필요한 위임 / 실제 위임. 낮으면 불필요 왕복이 많음."""
    if n_delegations <= 0:
        return {"efficiency": 1.0 if n_necessary == 0 else 0.0,
                "wasted": 0, "n_delegations": 0}
    eff = min(1.0, n_necessary / n_delegations)
    return {"efficiency": eff, "wasted": max(0, n_delegations - n_necessary),
            "n_delegations": n_delegations}


def evaluate_orchestrator(pred: dict, gold: dict) -> dict:
    """한 케이스의 오케스트레이터 결정 종합 평가.

    pred/gold = {"route": [...], "steps": [...], "n_delegations": int}
    gold 는 "n_necessary" 를 위임 정답으로 사용.
    """
    route = routing_accuracy_set(pred.get("route", []), gold.get("route", []))
    steps = decomposition_metrics(pred.get("steps", []), gold.get("steps", []))
    deleg = delegation_efficiency(pred.get("n_delegations", 0), gold.get("n_necessary", 0))
    # 종합 = 라우팅 F1·분해 F1·위임효율의 평균
    composite = (route["f1"] + steps["f1"] + deleg["efficiency"]) / 3.0
    return {"routing": route, "decomposition": steps, "delegation": deleg,
            "composite": composite}
