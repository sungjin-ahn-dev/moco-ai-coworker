"""
cc_eval 멀티에이전트 방법론 유닛테스트 (순수 부분).
`python -m app.cc_eval.test_methods`
"""

import math

from app.cc_eval import significance as S
from app.cc_eval import handoff as H
from app.cc_eval import orchestrator_eval as O
from app.cc_eval import credit as C
from app.cc_eval import failure_taxonomy as F


# ---- significance ----
def test_bootstrap_ci_contains_point():
    xs = [1, 1, 1, 0, 0, 1, 1, 0, 1, 1]   # 성공률 0.7
    r = S.bootstrap_ci(xs, seed=1)
    assert math.isclose(r["point"], 0.7)
    assert r["lo"] <= r["point"] <= r["hi"]
    assert 0.0 <= r["lo"] <= r["hi"] <= 1.0


def test_mcnemar_a_better():
    # A는 다 맞고 B는 6건 틀림 → A가 유의하게 나음 (b=6,c=0 → p=2/64=0.03125)
    a = [True] * 10
    b = [False] * 6 + [True] * 4
    r = S.mcnemar(a, b)
    assert r["better"] == "A"
    assert r["b_A_only"] == 6 and r["c_B_only"] == 0
    assert r["p_exact"] < 0.05


def test_mcnemar_tie():
    a = [True, False, True, False]
    b = [True, False, True, False]
    r = S.mcnemar(a, b)
    assert r["better"] == "tie" and r["n_discordant"] == 0


def test_min_runs():
    # 반폭 0.1, p=0.5 → n ≈ 96
    assert 90 <= S.min_runs_for_ci(0.1, 0.5) <= 100


# ---- handoff ----
def test_entity_recall():
    r = H.entity_recall(["2026-08-20", "김대표", "배포"], "배포 일정은 2026-08-20 입니다")
    assert math.isclose(r["recall"], 2 / 3)
    assert "김대표" in r["lost"]


def test_extract_entities_regex():
    ents = H.extract_entities_regex("회의는 2026-08-20 14:00, a@b.com 로 공유")
    assert "2026-08-20" in ents and "14:00" in ents and "a@b.com" in ents


def test_handoff_loss():
    r = H.handoff_loss("결정: 2026-08-20 배포", "그때 배포하기로 했어요")
    assert 0.0 <= r["loss"] <= 1.0
    assert "2026-08-20" in r["lost"]   # 날짜 유실


# ---- orchestrator ----
def test_routing_set():
    r = O.routing_accuracy_set(["crm", "slack"], ["crm", "calendar"])
    assert math.isclose(r["f1"], 0.5)
    assert r["wrong_route"] == ["slack"] and r["missed_route"] == ["calendar"]


def test_decomposition_over():
    r = O.decomposition_metrics(["a", "b", "c", "d"], ["a", "b"])
    assert r["over_decomposition"] == 2.0
    assert set(r["extra_steps"]) == {"c", "d"}


def test_delegation_efficiency():
    assert math.isclose(O.delegation_efficiency(4, 2)["efficiency"], 0.5)
    assert O.delegation_efficiency(4, 2)["wasted"] == 2


def test_evaluate_orchestrator_composite():
    pred = {"route": ["crm"], "steps": ["a", "b"], "n_delegations": 2}
    gold = {"route": ["crm"], "steps": ["a", "b"], "n_necessary": 2}
    r = O.evaluate_orchestrator(pred, gold)
    assert math.isclose(r["composite"], 1.0)   # 완벽


# ---- credit ----
def test_credit_from_deltas():
    r = C.credit_from_deltas(0.9, {"research": 0.4, "code": 0.85, "web": 0.95})
    assert math.isclose(r["credit"]["research"], 0.5)
    assert r["most_critical"] == "research"    # 끄면 가장 많이 떨어짐
    assert r["harmful"] == ["web"]             # 끄니 오히려 오름(해로움)


# ---- failure taxonomy ----
def test_rule_prelabel():
    assert F.rule_prelabel("timeout", "idle timeout") == "infra"
    assert F.rule_prelabel("error", "API Error 413 context overflow") == "infra"
    assert F.rule_prelabel("error", "some tool blew up") is None


def test_failure_distribution():
    d = F.failure_distribution(["infra", "infra", "tool_error", "spec_violation"])
    assert d["total"] == 4 and d["counts"]["infra"] == 2
    assert d["top"] == "infra"


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL  {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
