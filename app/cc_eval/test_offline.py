"""
cc_eval.offline 유닛테스트 — 봇 런타임(MCP/LLM) 없이 검증.

judge_fn 은 주입형이라 stub 으로 대체해 채점 로직만 결정적으로 확인한다.
`python -m app.cc_eval.test_offline`  (또는 `pytest app/cc_eval/test_offline.py`)
"""

import asyncio
import json
import math
import tempfile
from pathlib import Path

from app.cc_eval import offline as OFF


# stub judge: 응답 텍스트가 있으면 통과(1점), 없으면 실패(0점).
# (offline 은 빈 response 를 judge 호출 전에 걸러내므로, 이 stub 은 사실상 항상 pass.)
def _stub_judge(prompt, response):
    ok = len(response) > 0
    return {"score": 1.0 if ok else 0.0, "pass": ok}


def _fake_runs():
    # 5개 중 2개(빈 response) → success=False 여야 한다.
    return [
        {"type": "simple_chat", "state": "completed", "prompt": "p1", "response": "answer one"},
        {"type": "simple_chat", "state": "completed", "prompt": "p2", "response": ""},
        {"type": "operator", "state": "completed", "prompt": "p3", "response": "did the task"},
        {"type": "operator", "state": "error", "prompt": "p4", "response": ""},
        {"type": "memory", "state": "completed", "prompt": "p5", "response": "recalled it"},
    ]


# ---- load_runs ----
def test_load_runs_skips_blank_and_comments():
    lines = [
        "# 주석 줄",
        "",
        json.dumps({"type": "simple_chat", "prompt": "a", "response": "b"}, ensure_ascii=False),
        "   ",
        json.dumps({"type": "operator", "prompt": "c", "response": "d"}, ensure_ascii=False),
        "{not valid json",  # 깨진 줄은 건너뛴다
    ]
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "runs.jsonl"
        p.write_text("\n".join(lines), encoding="utf-8")
        runs = OFF.load_runs(p)
    assert len(runs) == 2
    assert runs[0]["type"] == "simple_chat" and runs[1]["type"] == "operator"


# ---- judge_runs ----
def test_judge_runs_empty_response_is_failure():
    results = asyncio.run(OFF.judge_runs(_fake_runs(), _stub_judge))
    assert len(results) == 5
    # 각 result 는 4개 키를 가진다.
    assert set(results[0]) == {"type", "state", "score", "success"}
    successes = [r["success"] for r in results]
    # index 1, 3 (빈 response) 만 실패.
    assert successes == [True, False, True, False, True]
    # 빈 response 는 score 0.0.
    assert results[1]["score"] == 0.0 and results[3]["score"] == 0.0


def test_judge_runs_empty_response_short_circuits_even_if_judge_says_pass():
    # judge 가 무조건 pass 를 줘도 빈 response 는 success=False 여야 한다(단락 평가).
    always_pass = lambda prompt, response: {"score": 1.0, "pass": True}
    results = asyncio.run(OFF.judge_runs(_fake_runs(), always_pass))
    assert results[1]["success"] is False
    assert results[3]["success"] is False


def test_judge_runs_sample_takes_first_n():
    results = asyncio.run(OFF.judge_runs(_fake_runs(), _stub_judge, sample=2))
    assert len(results) == 2
    assert [r["success"] for r in results] == [True, False]


def test_judge_runs_supports_async_judge_fn():
    # CLI 는 grade_case 를 async 래핑한다 → awaitable judge_fn 도 지원해야 한다.
    async def _async_judge(prompt, response):
        return {"score": 0.5, "pass": True}

    results = asyncio.run(OFF.judge_runs(_fake_runs(), _async_judge))
    # 빈 response 2개는 여전히 False, 나머지는 True(score 0.5).
    assert [r["success"] for r in results] == [True, False, True, False, True]
    assert math.isclose(results[0]["score"], 0.5)


def test_judge_runs_forwards_rubric_when_accepted():
    seen = {}

    def _rubric_aware(prompt, response, rubric=""):
        seen["rubric"] = rubric
        return {"score": 1.0, "pass": True}

    asyncio.run(OFF.judge_runs(_fake_runs()[:1], _rubric_aware, rubric="간결·정확 우선"))
    assert seen["rubric"] == "간결·정확 우선"


# ---- aggregate ----
def test_aggregate_success_rate_and_mean_and_by_type():
    results = asyncio.run(OFF.judge_runs(_fake_runs(), _stub_judge))
    agg = OFF.aggregate(results)

    assert agg["n"] == 5
    # 3/5 성공.
    assert math.isclose(agg["success_rate"], 0.6)
    # 점수 [1,0,1,0,1] → 평균 0.6.
    assert math.isclose(agg["mean_score"], 0.6)

    bt = agg["by_type"]
    assert bt["simple_chat"]["n"] == 2
    assert math.isclose(bt["simple_chat"]["success_rate"], 0.5)   # [True, False] = 1/2
    assert bt["operator"]["n"] == 2
    assert math.isclose(bt["operator"]["success_rate"], 0.5)      # [True, False] = 1/2
    assert bt["memory"]["n"] == 1
    assert math.isclose(bt["memory"]["success_rate"], 1.0)        # [True] = 1/1


def test_aggregate_empty_is_nan():
    agg = OFF.aggregate([])
    assert agg["n"] == 0
    assert math.isnan(agg["success_rate"])
    assert math.isnan(agg["mean_score"])
    assert agg["by_type"] == {}


# ---- to_markdown ----
def test_to_markdown_renders_types_and_handles_nan():
    results = asyncio.run(OFF.judge_runs(_fake_runs(), _stub_judge))
    md = OFF.to_markdown(OFF.aggregate(results))
    assert "# MOCO Offline Judge Report" in md
    assert "60.0%" in md            # 전체 success rate
    assert "simple_chat" in md and "operator" in md and "memory" in md
    # 빈 집계는 nan 을 'n/a' 로 렌더.
    assert "n/a" in OFF.to_markdown(OFF.aggregate([]))


if __name__ == "__main__":
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
