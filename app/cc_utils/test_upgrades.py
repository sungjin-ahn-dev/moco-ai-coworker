"""
후속 고도화 모듈 유닛테스트 (순수 부분).
`python -m app.cc_utils.test_upgrades`
"""

import math

from app.cc_utils import memory_hybrid as MH
from app.cc_utils import tool_selector as TS
from app.cc_utils import reflexion as RX


# ---- memory_hybrid (RRF) ----
def test_rrf_basic():
    # 두 랭킹: A가 렉시컬 1위, B가 시맨틱 1위 → RRF로 둘 다 상위
    lex = ["A", "B", "C"]
    sem = ["B", "A", "D"]
    fused = MH.reciprocal_rank_fusion([lex, sem], k=60)
    assert set(fused[:2]) == {"A", "B"}
    assert "C" in fused and "D" in fused


def test_rrf_consensus_wins():
    # 양쪽 모두 1위인 항목이 최상위
    fused = MH.reciprocal_rank_fusion([["X", "Y"], ["X", "Z"]], k=60)
    assert fused[0] == "X"


def test_hybrid_search_no_ranker_is_lexical():
    lex = [{"user_id": "u", "path": "a.md"}, {"user_id": "u", "path": "b.md"}]
    out = MH.hybrid_search(lex, "q", semantic_ranker=None, limit=6)
    assert out == lex   # 회귀 없음


def test_hybrid_search_fuses():
    lex = [{"user_id": "u", "path": "a.md"}, {"user_id": "u", "path": "b.md"}]
    # 시맨틱은 b를 위로
    ranker = lambda q, cands: list(reversed(cands))
    out = MH.hybrid_search(lex, "q", semantic_ranker=ranker, limit=6)
    assert {MH._mem_key(x) for x in out} == {"u/a.md", "u/b.md"}


# ---- tool_selector ----
def test_select_crm():
    ns = TS.select_namespaces("acme 회사 리드 담당자 알려줘")
    assert "mcp__crm__*" in ns
    assert "mcp__slack__*" in ns   # always-on


def test_select_calendar_and_mail():
    ns = TS.select_namespaces("내일 미팅 일정 확인하고 메일 보내줘")
    assert "mcp__google_calendar__*" in ns and "mcp__gmail__*" in ns


def test_select_fallback_all_on_no_match():
    # 도메인 키워드 없음 → 안전하게 ["*"] (무회귀)
    assert TS.select_namespaces("그냥 안녕") == ["*"]


def test_select_respects_available():
    ns = TS.select_namespaces("crm 리드", available={"mcp__slack__*"})
    # crm 서버가 available 에 없으면 제외, 매칭 0 → fallback ["*"]
    assert ns == ["*"]


def test_to_allowed_tools():
    assert TS.to_allowed_tools(["*"]) == ["*"]
    assert TS.to_allowed_tools(["mcp__crm__*"], extra=["TodoWrite"]) == ["mcp__crm__*", "TodoWrite"]


# ---- reflexion ----
def test_should_reflect():
    assert RX.should_reflect("completed", "") is True            # 빈 응답
    assert RX.should_reflect("completed", "정상 답변입니다") is False
    assert RX.should_reflect("error", "tool blew up") is True
    assert RX.should_reflect("error", "", "context overflow 413") is True  # 빈 응답이라 True
    assert RX.should_reflect("error", "정상 답변", "413 context") is False  # 컨텍스트는 기존 경로


def test_build_reflection_prompt():
    p = RX.build_reflection_prompt("일정 정리해줘", prev_error="calendar auth 실패")
    assert "일정 정리해줘" in p and "calendar auth 실패" in p and "<reflection>" in p


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
