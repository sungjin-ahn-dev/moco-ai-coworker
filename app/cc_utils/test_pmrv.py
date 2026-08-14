"""pmrv 유닛테스트. `python -m app.cc_utils.test_pmrv`"""

from app.cc_utils import pmrv as P

COMPLEX_Q = "캘린더에서 오늘 일정 조회하고 메일함 요약해서 각각 정리해줘. 단, 광고는 제외하고 형식 맞춰 작성."
SIMPLE_Q = "안녕 오늘 날씨 어때"

_PLAN_JSON = (
    '{"requirements":["일정 조회","메일 요약","광고 제외","형식 준수"],'
    '"subtasks":[{"id":"s1","goal":"오늘 일정 조회","agent":"pm","deps":[]},'
    '{"id":"s2","goal":"메일 요약","agent":"communication","deps":[]}]}'
)


def _stub_llm(calls, verify_seq=None):
    """Plan/Verify/Replan 프롬프트를 인식해 응답하는 stub. verify_seq 로 검증 결과 시퀀스 제어."""
    seq = list(verify_seq or ["COMPLETE\n최종본"])
    st = {"i": 0}

    def fn(prompt):
        calls.append(prompt)
        if "<plan>" in prompt:
            return _PLAN_JSON
        if "<verify>" in prompt:
            r = seq[min(st["i"], len(seq) - 1)]
            st["i"] += 1
            return r
        if "<replan>" in prompt:
            return "보완된 응답"
        return "정상 산출물 텍스트"

    return fn


def test_classify_complex_vs_simple():
    assert P.classify_complexity(COMPLEX_Q) == "complex"
    assert P.classify_complexity(SIMPLE_Q) == "simple"


def test_simple_takes_fast_path():
    r = P.run_pmrv(SIMPLE_Q, llm_fn=_stub_llm([]))
    assert r["complexity"] == "simple" and r["stages"] == ["fast_path"]
    assert r["total_calls"] == 1 and r["wall_turns"] == 1 and r["replans"] == 0


def test_complex_runs_full_pipeline():
    r = P.run_pmrv(COMPLEX_Q, llm_fn=_stub_llm([]))  # verify=COMPLETE → replan 없음
    assert r["complexity"] == "complex"
    assert r["stages"][:3] == ["plan", "map(2)", "reduce"] and "verify" in r["stages"]
    assert r["verified"] is True and r["replans"] == 0
    # plan(1)+map(2)+reduce(1)+verify(1)=5 ; wall plan(1)+독립Map(1)+reduce(1)+verify(1)=4
    assert r["total_calls"] == 5 and r["wall_turns"] == 4


def test_replan_on_gap():
    # verify 가 처음엔 GAPS, replan 후 COMPLETE → replan 1회
    r = P.run_pmrv(COMPLEX_Q, llm_fn=_stub_llm([], verify_seq=["GAPS: 형식 누락\n초안", "COMPLETE\n최종"]))
    assert r["replans"] == 1 and "replan" in r["stages"]
    assert r["total_calls"] == 7  # 5 + replan(1) + re-verify(1)


def test_verify_complete_preserves_draft():
    # 회귀 방지: verify 가 본문 없이 'COMPLETE' 만 반환해도(프롬프트 스펙대로) 원본 응답이
    # 유지되어야 한다. 과거엔 revised='COMPLETE' 가 draft 를 덮어써 응답이 마커로 소실됐다.
    assert P.parse_verify("COMPLETE")["revised"] == ""
    r = P.run_pmrv(COMPLEX_Q, llm_fn=_stub_llm([], verify_seq=["COMPLETE"]))
    assert r["response"] and r["response"] != "COMPLETE"
    assert r["verified"] is True and r["replans"] == 0


def test_reduce_uses_verbatim_original_request():
    p = P.build_reduce_prompt("일정 정리하고 메일 보내줘", ["부분결과A", "부분결과B"])
    assert "일정 정리하고 메일 보내줘" in p and "부분결과A" in p and "<reduce>" in p


def test_plan_prompt_and_parse_roundtrip():
    prompt = P.build_plan_prompt(COMPLEX_Q)
    assert "<plan>" in prompt and "subtasks" in prompt and "research" in prompt
    plan = P.parse_plan(_PLAN_JSON)
    assert len(plan["subtasks"]) == 2 and plan["subtasks"][0]["agent"] == "pm"
    assert plan["requirements"]


def test_parse_plan_agent_defaults_to_valid():
    plan = P.parse_plan('{"subtasks":[{"id":"s1","goal":"x","agent":"calendar","deps":[]}]}')
    assert plan["subtasks"][0]["agent"] == "research"  # 미등록 agent → research 폴백


def test_parse_plan_garbage_falls_back():
    plan = P.parse_plan("이건 JSON이 아님")
    assert len(plan["subtasks"]) == 1 and plan["subtasks"][0]["agent"] in P._SUBAGENTS


def test_dag_depth_independent_vs_chain():
    indep = [{"id": "s1", "deps": []}, {"id": "s2", "deps": []}]
    chain = [{"id": "s1", "deps": []}, {"id": "s2", "deps": ["s1"]}, {"id": "s3", "deps": ["s2"]}]
    assert P.dag_depth(indep) == 1 and P.dag_depth(chain) == 3


def test_independent_subtasks():
    subs = [{"id": "s1", "deps": []}, {"id": "s2", "deps": ["s1"]}]
    assert [s["id"] for s in P.independent_subtasks(subs)] == ["s1"]


def test_topological_layers():
    indep = [{"id": "s1", "deps": []}, {"id": "s2", "deps": []}]
    chain = [{"id": "s1", "deps": []}, {"id": "s2", "deps": ["s1"]}, {"id": "s3", "deps": ["s2"]}]
    diamond = [
        {"id": "s1", "deps": []},
        {"id": "s2", "deps": ["s1"]},
        {"id": "s3", "deps": ["s1"]},
        {"id": "s4", "deps": ["s2", "s3"]},
    ]
    ids = lambda layers: [[s["id"] for s in lyr] for lyr in layers]
    assert ids(P.topological_layers(indep)) == [["s1", "s2"]]        # 전부 병렬(단일 레벨)
    assert ids(P.topological_layers(chain)) == [["s1"], ["s2"], ["s3"]]
    assert ids(P.topological_layers(diamond)) == [["s1"], ["s2", "s3"], ["s4"]]
    # 레벨 수 == dag_depth
    assert len(P.topological_layers(chain)) == P.dag_depth(chain)
    # 사이클도 교착 없이 전부 배치
    cyc = [{"id": "a", "deps": ["b"]}, {"id": "b", "deps": ["a"]}]
    assert sum(len(lyr) for lyr in P.topological_layers(cyc)) == 2


def test_parse_verify_variants():
    assert P.parse_verify("COMPLETE\n본문")["complete"] is True
    g = P.parse_verify("GAPS: 형식 누락\n보완본")
    assert g["complete"] is False and "형식" in g["gaps"] and g["revised"] == "보완본"
    assert P.parse_verify("모호한 텍스트")["complete"] is True  # 모호 → 완료


def test_turn_profile_simple_zero_overhead():
    prof = P.turn_profile(SIMPLE_Q)
    assert prof["complexity"] == "simple" and prof["delta_wall"] == 0


def test_turn_profile_complex_independent_bounded():
    prof = P.turn_profile(COMPLEX_Q)
    assert prof["complexity"] == "complex" and prof["dependent"] is False and prof["wall_turns"] == 4


def test_turn_profile_dependent_chain_costs_more():
    dep_q = "캘린더에서 회의 조회하고 그 결과를 바탕으로 참석자에게 메일 보내줘. 형식 맞춰 각각 정리."
    prof = P.turn_profile(dep_q)
    assert prof["dependent"] is True and prof["wall_turns"] > 4


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
        except Exception as e:
            failed += 1
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
