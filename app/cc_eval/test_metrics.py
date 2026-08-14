"""
cc_eval.metrics 유닛테스트 — 봇 런타임 없이 `pytest app/cc_eval/test_metrics.py` 로 검증.

순수 함수라 결정적. pass^k/pass@k 를 손계산 값과 대조한다.
"""

import math

from app.cc_eval import metrics as M


def test_tool_call_metrics_basic():
    r = M.tool_call_metrics(expected=["a", "b", "c"], actual=["a", "b", "x"])
    assert math.isclose(r["precision"], 2 / 3)
    assert math.isclose(r["recall"], 2 / 3)
    assert math.isclose(r["f1"], 2 / 3)
    assert r["missing"] == ["c"]
    assert r["extra"] == ["x"]


def test_tool_call_no_tools_expected():
    # 도구가 필요 없는 케이스: 실제도 비어야 만점
    assert M.tool_call_metrics([], [])["f1"] == 1.0
    assert M.tool_call_metrics([], ["a"])["precision"] == 0.0  # 불필요 호출


def test_tool_hard_gates():
    r = M.tool_call_metrics(["a"], ["a", "send_email"],
                            must_call=["a"], must_not_call=["send_email"])
    assert r["must_call_ok"] is True
    assert r["must_not_call_ok"] is False   # 금지 도구 호출됨 → 가드레일 위반


def test_pass_hat_k_all_success():
    # 5/5 성공 → 어떤 k든 pass^k = 1
    s = [True] * 5
    assert M.pass_hat_k(s, 1) == 1.0
    assert M.pass_hat_k(s, 3) == 1.0


def test_pass_hat_k_partial():
    # 4/5 성공: pass^1 = 4/5, pass^2 = C(4,2)/C(5,2) = 6/10
    s = [True, True, True, True, False]
    assert math.isclose(M.pass_hat_k(s, 1), 0.8)
    assert math.isclose(M.pass_hat_k(s, 2), 6 / 10)
    assert math.isclose(M.pass_hat_k(s, 3), 4 / 10)   # C(4,3)/C(5,3)=4/10


def test_pass_at_k_partial():
    # 4/5 성공: pass@2 = 1 - C(1,2)/C(5,2). C(1,2)=0 → 1.0
    s = [True, True, True, True, False]
    assert math.isclose(M.pass_at_k(s, 2), 1.0)
    # 1/5 성공: pass@2 = 1 - C(4,2)/C(5,2) = 1 - 6/10 = 0.4
    s2 = [True, False, False, False, False]
    assert math.isclose(M.pass_at_k(s2, 2), 0.4)


def test_pass_k_out_of_range():
    assert math.isnan(M.pass_hat_k([True, True], 3))
    assert math.isnan(M.pass_hat_k([], 1))


def test_percentile():
    xs = [10, 20, 30, 40, 50]
    assert M.percentile(xs, 50) == 30
    assert math.isclose(M.percentile(xs, 95), 48.0)   # 선형보간
    assert math.isnan(M.percentile([], 50))


def test_cost_per_success():
    assert math.isclose(M.cost_per_success([1.0, 1.0, 2.0], [True, False, True]), 4.0 / 2)
    assert M.cost_per_success([1.0], [False]) == float("inf")


def test_session_aggregate_stricter_than_request():
    # 세션 A: 2 run(성공,실패) → 세션 실패. 세션 B: 1 run(성공) → 세션 성공.
    runs = [
        {"session_id": "A", "success": True, "elapsed_s": 2.0, "cost_usd": 0.01},
        {"session_id": "A", "success": False, "elapsed_s": 3.0, "cost_usd": 0.02},
        {"session_id": "B", "success": True, "elapsed_s": 1.0, "cost_usd": 0.01},
    ]
    agg = M.session_aggregate(runs)
    assert agg["n_requests"] == 3
    assert agg["n_sessions"] == 2
    assert math.isclose(agg["request_success_rate"], 2 / 3)
    assert math.isclose(agg["session_success_rate"], 1 / 2)   # 요청보다 엄격
    assert math.isclose(agg["session_latency"]["p50"], 3.0)   # 세션 A=5,B=1 → median=3.0


def test_aggregate_case_scores():
    scores = [
        {"success_flags": [True, True, False], "tool_f1": 1.0, "judge_scores": [0.9, 0.8]},
        {"success_flags": [True, True, True], "tool_f1": 0.5, "judge_scores": [1.0]},
    ]
    agg = M.aggregate_case_scores(scores, ks=(1, 2))
    assert agg["n_cases"] == 2
    assert math.isclose(agg["mean_tool_f1"], 0.75)
    # case1 pass^1=2/3, case2 pass^1=1 → 평균 5/6
    assert math.isclose(agg["pass_hat_k"][1], (2 / 3 + 1.0) / 2)


def test_cache_efficiency_warm_hit():
    # 웜 호출(실측 예): 프리픽스 대부분을 캐시에서 읽음
    warm = {"input_tokens": 4823, "cache_creation_input_tokens": 8121, "cache_read_input_tokens": 22444}
    e = M.cache_efficiency(warm)
    assert abs(e["hit_rate"] - 22444 / 35388) < 1e-9
    assert e["cache_read_tokens"] == 22444 and e["cache_creation_tokens"] == 8121
    assert e["prefix_hit_rate"] > 0.7          # 캐시 가능한 프리픽스 중 재사용 비율
    assert e["est_cost_saving"] > 0.4          # 웜 호출은 비용 절감


def test_cache_efficiency_cold_is_premium():
    cold = {"input_tokens": 4594, "cache_creation_input_tokens": 30563, "cache_read_input_tokens": 0}
    e = M.cache_efficiency(cold)
    assert e["hit_rate"] == 0.0                # 콜드 = 히트 0
    assert e["est_cost_saving"] < 0            # 캐시 생성 프리미엄 → 손해가 정상


def test_cache_efficiency_aggregate_and_empty():
    cold = {"input_tokens": 4594, "cache_creation_input_tokens": 30563, "cache_read_input_tokens": 0}
    warm = {"input_tokens": 4823, "cache_creation_input_tokens": 8121, "cache_read_input_tokens": 22444}
    agg = M.cache_efficiency([cold, warm])
    assert agg["n"] == 2 and agg["cache_read_tokens"] == 22444
    assert math.isnan(M.cache_efficiency([])["hit_rate"])


if __name__ == "__main__":
    # pytest 없이도 실행 가능: python -m app.cc_eval.test_metrics
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
