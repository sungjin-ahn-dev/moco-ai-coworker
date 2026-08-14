"""
cc_eval.runner — 골든 케이스를 오케스트레이터로 재생(replay)하고 채점한다.

E2E 실행은 MOCO 런타임(활성 MCP·LLM 키·MOCO_DATA)이 필요하므로, 이 모듈은
그 환경 안에서 실행되는 것을 전제로 한다. metrics(순수)와 달리 여기부턴 부작용 있음.

도구 궤적(tool trajectory) 수집:
  기본 어댑터는 call_orchestrator_agent 를 호출해 (응답/상태/지연)을 얻는다.
  도구 이름 궤적은 orchestrator 의 on_message 훅으로 수집한다(logging 정상화와 공유).
  훅이 없으면 tool_calls=[] 로 남고, 해당 케이스의 tool-accuracy 는 집계에서 제외된다.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

from app.cc_eval import metrics as M
from app.cc_eval.schema import GoldenCase, RunResult, CaseScore, ToolCall, load_golden_set

# (response, tool_names, cost_usd) 를 반환하는 오케스트레이터 어댑터 타입
OrchestratorFn = Callable[[GoldenCase], Awaitable[tuple[str, list[str], float]]]


async def _default_orchestrator(case: GoldenCase) -> tuple[str, list[str], float]:
    """실 오케스트레이터 어댑터. MOCO 런타임에서만 동작."""
    from app.cc_agents.orchestrator.agent import call_orchestrator_agent

    tools: list[str] = []
    # on_message 훅으로 tool_use 블록의 이름을 수집(있으면).
    def _sink(msg):
        for block in getattr(msg, "content", []) or []:
            if getattr(block, "type", "") == "tool_use" or block.__class__.__name__ == "ToolUseBlock":
                name = getattr(block, "name", "")
                if name:
                    tools.append(name)

    slack_data = {"channel_id": "EVAL", "members": [], "history": []}
    message_data = {"user_id": "EVAL_USER", "user_name": "eval",
                    "channel_id": "EVAL", "text": case.prompt, "message_ts": "0"}

    kwargs = {}
    # 오케스트레이터가 on_message 훅을 지원하면 넘긴다(하위호환: 없으면 생략).
    try:
        import inspect
        if "on_message" in inspect.signature(call_orchestrator_agent).parameters:
            kwargs["on_message"] = _sink
    except Exception:
        pass

    response = await call_orchestrator_agent(case.prompt, slack_data, message_data, "", **kwargs)
    return response, tools, 0.0


async def replay_case(
    case: GoldenCase,
    k: int = 1,
    orchestrator_fn: Optional[OrchestratorFn] = None,
) -> list[RunResult]:
    """케이스를 k회 재생. 비결정성 측정을 위해 k>1 권장."""
    fn = orchestrator_fn or _default_orchestrator
    results: list[RunResult] = []
    for i in range(k):
        t0 = time.monotonic()
        try:
            response, tool_names, cost = await fn(case)
            results.append(RunResult(
                case_id=case.id, run_index=i, response=response,
                tool_calls=[ToolCall(name=n) for n in tool_names],
                state="completed", elapsed_s=time.monotonic() - t0,
                cost_usd=cost, session_id=case.session_id or case.id,
            ))
        except asyncio.TimeoutError as e:
            results.append(RunResult(case_id=case.id, run_index=i, state="timeout",
                                     error=str(e), elapsed_s=time.monotonic() - t0,
                                     session_id=case.session_id or case.id))
        except Exception as e:
            results.append(RunResult(case_id=case.id, run_index=i, state="error",
                                     error=str(e), elapsed_s=time.monotonic() - t0,
                                     session_id=case.session_id or case.id))
    return results


async def score_case(case: GoldenCase, runs: list[RunResult], use_judge: bool = True) -> CaseScore:
    """케이스의 k회 실행을 채점 → CaseScore.

    성공 판정 = 하드게이트(상태 completed · must_call/must_not_call) AND judge.pass.
    tool F1 은 궤적이 있는 run 들의 평균(없으면 0으로 남기고 has_traj=False 표시).
    """
    score = CaseScore(case_id=case.id, n_runs=len(runs), session_id=case.session_id or case.id)
    tool_f1s, tool_ps, tool_rs = [], [], []
    mc_ok_all, mnc_ok_all, has_traj = True, True, False

    for r in runs:
        hard_ok = r.state == "completed"
        if r.tool_calls or case.expected_tools or case.must_call or case.must_not_call:
            tm = M.tool_call_metrics(case.expected_tools, r.tool_names,
                                     case.must_call, case.must_not_call)
            if r.tool_calls:
                has_traj = True
                tool_f1s.append(tm["f1"]); tool_ps.append(tm["precision"]); tool_rs.append(tm["recall"])
            mc_ok_all = mc_ok_all and tm["must_call_ok"]
            mnc_ok_all = mnc_ok_all and tm["must_not_call_ok"]
            hard_ok = hard_ok and tm["must_call_ok"] and tm["must_not_call_ok"]

        judge_score = 1.0
        judge_pass = True
        if use_judge and r.state == "completed":
            from app.cc_eval.judge import grade_case
            j = await grade_case(case.prompt, r.response, r.tool_names, case.rubric, case.reference)
            judge_score, judge_pass = j.get("score", 0.0), j.get("pass", False)
        score.judge_scores.append(judge_score if r.state == "completed" else 0.0)
        score.success_flags.append(bool(hard_ok and judge_pass))
        score.latencies.append(r.elapsed_s)
        score.costs.append(r.cost_usd)

    def _avg(xs):
        return sum(xs) / len(xs) if xs else 0.0
    score.tool_f1, score.tool_precision, score.tool_recall = _avg(tool_f1s), _avg(tool_ps), _avg(tool_rs)
    score.must_call_ok, score.must_not_call_ok = mc_ok_all, mnc_ok_all
    score.notes = "" if has_traj else "no tool trajectory (on_message 훅 필요)"
    return score


async def run_suite(
    golden_path: str | Path,
    k: int = 3,
    use_judge: bool = True,
    orchestrator_fn: Optional[OrchestratorFn] = None,
) -> tuple[list[CaseScore], list[RunResult]]:
    """골든셋 전체를 재생+채점. (scores, all_runs) 반환 → report.build_report 로 넘김."""
    cases = load_golden_set(golden_path)
    all_scores, all_runs = [], []
    for case in cases:
        runs = await replay_case(case, k=k, orchestrator_fn=orchestrator_fn)
        all_runs.extend(runs)
        all_scores.append(await score_case(case, runs, use_judge=use_judge))
    return all_scores, all_runs
