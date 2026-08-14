"""pmrv — 복잡도 게이팅 Plan-Map-Reduce-Verify-Replan 오케스트레이션.

검증 중심(verification-centric) 멀티에이전트 오케스트레이션. 복잡 요청을 요구 체크리스트와
서브태스크 DAG로 분해(Plan)하고, 독립 서브태스크를 도메인 서브에이전트에 병렬 실행(Map,
orchestrator에서 execute_dag 배선)한 뒤, 결과를 원 요청 기준으로 종합(Reduce)하고,
원 요청과 대조해 검증하고 누락이 있으면 그 부분만 재계획(Verify-Replan)한다. Verify는 응답
제출 전에 도는 test-time verification 패스다. 단순 요청은 fast-path로 오버헤드가 없다.

프롬프트 빌더와 휴리스틱으로 구성하고 llm_fn/executor_fn 을 주입받아 파이프라인을 단위 테스트한다.
orchestrator에 PMRV_ENABLED 로 opt-in 배선한다.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Optional

from app.cc_utils.spec_verify import _requirement_signals, should_verify

# Map 대상 도메인 서브에이전트 (execute_dag 레지스트리와 일치)
_SUBAGENTS = ("research", "communication", "code", "pm", "document", "data", "web")

# 복잡도 판정용 도메인 키워드
_DOMAINS = (
    "캘린더", "일정", "미팅", "회의", "메일", "gmail", "이메일", "메일함", "드라이브", "drive",
    "문서", "슬랙", "slack", "dm", "채널", "crm", "거래처", "clickup", "할일", "태스크", "전화", "리포트",
)
# 의존 체인 신호 (선행 결과가 필요한 요청 — 순차 실행)
_DEP_HINTS = (
    "그 결과", "그걸로", "그것으로", "기반으로", "바탕으로", "한 다음", "한 뒤", "후에", "이후에",
    "확인 후", "조회해서", "받아서", "→", "then", "based on",
)

MAX_REPLAN = 1


# ---------------------------------------------------------------------------
# 복잡도 판정
# ---------------------------------------------------------------------------
def classify_complexity(query: str) -> str:
    """다중요구(요구신호 6 이상) 또는 다도메인(2 이상)이면 complex, 아니면 simple."""
    q = (query or "").lower()
    signals = _requirement_signals(query)
    domains = sum(1 for kw in _DOMAINS if kw in q)
    return "complex" if (signals >= 6 or domains >= 2) else "simple"


# ---------------------------------------------------------------------------
# 계획 — 요구사항 + 서브태스크 DAG
# ---------------------------------------------------------------------------
def build_plan_prompt(query: str) -> str:
    """요청을 요구사항과 서브태스크 DAG(의존성 포함)로 분해하게 하는 프롬프트."""
    return (
        "<plan>\n"
        "다음 [요청]을 실행 계획으로 분해하세요. 원 요청의 모든 명시적 요구·제약·출력형식·제외조건을\n"
        "하나도 빠짐없이 보존해야 합니다. 각 서브태스크의 agent 는 아래 도메인 서브에이전트 중 하나입니다:\n"
        "research·communication·code·pm·document·data·web. 서로 독립인 서브태스크는 병렬 실행되므로\n"
        "deps 를 비우고, 선행 결과가 필요한 서브태스크만 deps 에 선행 id 를 넣으세요.\n"
        "오직 JSON만 출력:\n"
        '{"requirements": ["요구/제약/형식/제외조건을 검증 가능한 문장으로 전부"],\n'
        ' "subtasks": [{"id": "s1", "goal": "구체 실행 목표", '
        '"agent": "research|communication|code|pm|document|data|web", "deps": ["선행 subtask id"]}]}\n'
        "</plan>\n\n"
        f"[요청]\n{(query or '')[:1500]}"
    )


def parse_plan(text: str) -> dict:
    """Plan 출력(JSON)을 파싱한다. 실패하면 단일 서브태스크로 폴백한다."""
    fallback = {
        "requirements": [],
        "subtasks": [{"id": "s1", "goal": "(요청 전체 처리)", "agent": "research", "deps": []}],
    }
    m = re.search(r"\{.*\}", re.sub(r"```(json)?", "", text or ""), re.S)
    if not m:
        return fallback
    try:
        d = json.loads(m.group(0))
    except Exception:
        return fallback
    subs_raw = d.get("subtasks") or []
    subtasks = []
    for i, s in enumerate(subs_raw):
        if not isinstance(s, dict):
            continue
        deps = s.get("deps") or []
        agent = str(s.get("agent") or s.get("domain") or "research")
        subtasks.append({
            "id": str(s.get("id") or f"s{i + 1}"),
            "goal": str(s.get("goal") or ""),
            "agent": agent if agent in _SUBAGENTS else "research",
            "deps": [str(x) for x in deps] if isinstance(deps, list) else [],
        })
    if not subtasks:
        return fallback
    return {"requirements": list(d.get("requirements") or []), "subtasks": subtasks}


def dag_depth(subtasks: list[dict]) -> int:
    """서브태스크 DAG의 최장 의존 체인 길이. 모두 독립이면 1."""
    by_id = {s["id"]: s for s in subtasks}

    def depth(sid: str, seen: tuple = ()) -> int:
        if sid in seen:  # 사이클 방지
            return 1
        s = by_id.get(sid)
        deps = [d for d in (s.get("deps") if s else []) if d in by_id]
        return 1 + max((depth(d, seen + (sid,)) for d in deps), default=0)

    return max((depth(s["id"]) for s in subtasks), default=1)


def independent_subtasks(subtasks: list[dict]) -> list[dict]:
    """deps 가 없는(=병렬 디스패치 가능한) 서브태스크만 반환."""
    return [s for s in subtasks if not s.get("deps")]


def topological_layers(subtasks: list[dict]) -> list[list[dict]]:
    """서브태스크 DAG를 위상(topological) 레벨로 분해한다.

    같은 레벨의 서브태스크는 서로 독립이라 병렬 실행 가능하고, 뒤 레벨은 앞 레벨의
    결과에 의존한다(선행 결과를 공유 워크스페이스로 전달). 모두 독립이면 단일
    레벨(=전부 병렬)이 된다. deps 가 미지의 id 를 가리키면 무시하며, 사이클이 남으면
    남은 노드를 한 레벨로 몰아 교착을 방지한다. 입력 순서를 레벨 내에서 보존한다.
    """
    by_id = {s["id"]: s for s in subtasks}
    order = [s["id"] for s in subtasks]
    remaining = set(order)
    done: set = set()
    layers: list[list[dict]] = []
    while remaining:
        ready = [
            sid for sid in order
            if sid in remaining
            and all((d not in by_id) or (d in done) for d in by_id[sid].get("deps", []))
        ]
        if not ready:  # 사이클/교착 방지 — 남은 노드를 한 레벨로
            ready = [sid for sid in order if sid in remaining]
        layers.append([by_id[sid] for sid in ready])
        for sid in ready:
            remaining.discard(sid)
            done.add(sid)
    return layers


# ---------------------------------------------------------------------------
# 종합
# ---------------------------------------------------------------------------
def build_reduce_prompt(query: str, subtask_results: list[str]) -> str:
    """서브태스크 결과들을 원 요청 기준으로 단일 산출물로 종합하게 하는 프롬프트."""
    joined = "\n\n".join(
        f"[서브태스크 {i + 1} 결과]\n{(r or '')[:1500]}" for i, r in enumerate(subtask_results)
    )
    return (
        "<reduce>\n"
        "아래 서브태스크 결과들을 종합해 원 요청에 대한 '단일 최종 산출물'로 조립하세요.\n"
        "요약이 아니라 원 요청을 기준으로: 출력형식·제외조건·모든 명시 요구를 그대로 반영하고,\n"
        "서브태스크 간 상충/중복은 원 요청 의도에 맞게 정리하세요.\n"
        "</reduce>\n\n"
        f"[원 요청]\n{(query or '')[:1500]}\n\n{joined}"
    )


# ---------------------------------------------------------------------------
# 검증 + 재계획 (test-time verification → gap 시 targeted replan)
# ---------------------------------------------------------------------------
def build_verify_prompt(query: str, draft: str) -> str:
    """응답을 원 요청과 대조해 완결/누락을 판정하고 보완본을 내게 하는 프롬프트."""
    return (
        "<verify>\n"
        "원 요청의 모든 명시적 요구·제약·출력형식·제외조건을 이 응답이 충족했는지 항목별로 점검하세요.\n"
        "모두 충족했으면 첫 줄에 'COMPLETE' 만 쓰고, 누락·위반이 있으면 첫 줄에 'GAPS: <누락 항목 요약>'\n"
        "을 쓴 뒤 다음 줄부터 그 부분을 보완한 최종본을 작성하세요.\n"
        "</verify>\n\n"
        f"[원 요청]\n{(query or '')[:1500]}\n\n[응답]\n{(draft or '')[:2000]}"
    )


def parse_verify(text: str) -> dict:
    """verify 출력을 {complete, gaps, revised} 로 파싱.

    revised 는 '보완이 필요할 때의 보완본'만 담는다. 완결(COMPLETE)이거나 형식이
    모호하면 revised 를 빈 문자열로 두어, 호출측의 `reduced = v["revised"] or reduced`
    가 원본 draft 를 그대로 유지하도록 한다(마커 'COMPLETE' 로 응답이 덮이는 회귀 방지).
    """
    t = (text or "").strip()
    parts = t.split("\n", 1)
    head = parts[0].strip() if parts else ""
    body = parts[1].strip() if len(parts) > 1 else ""
    if head.upper().startswith("COMPLETE"):
        return {"complete": True, "gaps": "", "revised": ""}
    if head.upper().startswith("GAPS:"):
        return {"complete": False, "gaps": head[5:].strip(), "revised": body}
    return {"complete": True, "gaps": "", "revised": ""}  # 모호하면 완료로 간주, 원본 유지


def build_replan_prompt(query: str, draft: str, gaps: str) -> str:
    """검증에서 드러난 누락만 보완해 재실행하게 하는 프롬프트."""
    return (
        "<replan>\n"
        "아래 [누락]으로 지적된 부분만 보완해 원 요청을 완전히 충족시키세요. 이미 충족된 부분은 유지.\n"
        "</replan>\n\n"
        f"[원 요청]\n{(query or '')[:1500]}\n\n[누락]\n{(gaps or '')[:600]}\n\n"
        f"[현재 응답]\n{(draft or '')[:2000]}"
    )


# ---------------------------------------------------------------------------
# 파이프라인
# ---------------------------------------------------------------------------
def run_pmrv(
    query: str,
    *,
    llm_fn: Callable[[str], str],
    executor_fn: Optional[Callable[[dict, str], str]] = None,
    verify: bool = True,
    max_replan: int = MAX_REPLAN,
) -> dict:
    """PMRV 파이프라인을 실행한다 (Plan-Map-Reduce-Verify-Replan).

    Args:
        llm_fn: 프롬프트를 받아 텍스트를 반환. Plan/Reduce/Verify/Replan 및 executor 미지정 시 Map에 사용.
        executor_fn: (subtask, query)를 받아 결과를 반환하는 Map 실행기. orchestrator 에서는
            독립 서브태스크를 execute_dag 로 병렬 디스패치한다(여기 순수 모델은 순차 호출).
        verify: True면 다중요구 응답에 대해 검증(+누락 시 replan)을 수행.
        max_replan: 검증에서 누락이 남을 때 재계획 반복 상한.

    Returns:
        response, complexity, total_calls, wall_turns, stages, n_subtasks, verified, replans.
        total_calls = 누적 호출 수, wall_turns = 임계경로 턴 수(독립 Map은 병렬이라 1로 계산).
    """
    exec_fn = executor_fn or (
        lambda st, q: llm_fn(f"[서브태스크]\n{st.get('goal', '')}\n\n[원 요청]\n{(q or '')[:800]}")
    )

    if classify_complexity(query) == "simple":
        resp = exec_fn({"id": "s1", "goal": query, "agent": "research", "deps": []}, query)
        return {
            "response": resp, "complexity": "simple", "total_calls": 1, "wall_turns": 1,
            "stages": ["fast_path"], "n_subtasks": 1, "verified": False, "replans": 0,
        }

    plan = parse_plan(llm_fn(build_plan_prompt(query)))
    subs = plan["subtasks"]

    # Map — orchestrator 는 독립 서브태스크를 execute_dag 로 병렬 디스패치
    results = [exec_fn(st, query) for st in subs]

    reduced = llm_fn(build_reduce_prompt(query, results))

    stages = ["plan", f"map({len(subs)})", "reduce"]
    total_calls = 1 + len(subs) + 1          # plan + 서브태스크 수 + reduce
    wall_turns = 1 + dag_depth(subs) + 1      # plan + Map 임계경로(독립=1) + reduce

    verified = False
    replans = 0
    if verify and should_verify("completed", reduced, query):
        v = parse_verify(llm_fn(build_verify_prompt(query, reduced)))
        reduced = v["revised"] or reduced
        verified = True
        stages.append("verify")
        total_calls += 1
        wall_turns += 1
        while not v["complete"] and replans < max_replan:
            reduced = llm_fn(build_replan_prompt(query, reduced, v["gaps"]))
            v = parse_verify(llm_fn(build_verify_prompt(query, reduced)))
            reduced = v["revised"] or reduced
            replans += 1
            stages.append("replan")
            total_calls += 2       # replan + re-verify
            wall_turns += 2

    return {
        "response": reduced, "complexity": "complex", "total_calls": total_calls,
        "wall_turns": wall_turns, "stages": stages, "n_subtasks": len(subs),
        "verified": verified, "replans": replans,
    }


# ---------------------------------------------------------------------------
# 턴수 추정 (실행 없이 지연/비용 분석용)
# ---------------------------------------------------------------------------
def turn_profile(query: str) -> dict:
    """요청을 실행하지 않고 PMRV 턴 프로파일을 추정한다.

    delta_wall 은 reactive(단일 패스) 대비 추가되는 임계경로 턴 수다. 단순 요청은 0,
    복잡+독립 요청은 Map 이 병렬(execute_dag)이라 임계경로가 짧다.
    """
    if classify_complexity(query) == "simple":
        return {
            "complexity": "simple", "est_subtasks": 1, "total_calls": 1,
            "wall_turns": 1, "delta_wall": 0, "verify": False, "dependent": False,
        }
    q = (query or "").lower()
    est_sub = min(max(sum(1 for kw in _DOMAINS if kw in q), 2), 5)  # 복잡 요청은 최소 2, 상한 5
    dependent = any(h in (query or "") for h in _DEP_HINTS)
    map_depth = est_sub if dependent else 1                         # 의존체인이면 순차, 독립이면 병렬(1)
    verify = _requirement_signals(query) >= 4
    v = 1 if verify else 0
    total = 1 + est_sub + 1 + v
    wall = 1 + map_depth + 1 + v
    return {
        "complexity": "complex", "est_subtasks": est_sub, "total_calls": total,
        "wall_turns": wall, "delta_wall": wall - 1, "verify": verify, "dependent": dependent,
    }


# orchestrator 배선 (opt-in PMRV_ENABLED) — orchestrator/agent.py 의 _run_pmrv_pipeline 이
# 복잡 요청을 Plan-Map-Reduce-Verify-Replan 으로 처리한다. Map 은 독립 서브태스크를
# TaskExecutor.execute_dag 로 병렬 디스패치하고 실패 시 순차로 폴백한다. run_pmrv 는 동일
# 파이프라인의 동기·주입형 버전으로 단위 테스트에 쓴다. 단순 요청은 이 경로를 타지 않는다.
