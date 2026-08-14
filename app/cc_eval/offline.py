"""
cc_eval.offline — 재생(replay) 없이 기존 runs.jsonl(prompt+response)만으로
오프라인 LLM-judge 채점을 수행해 '실제 성공률'을 근사한다.

왜 opt-in·무회귀인가:
  - 순수 로직 + 주입형 judge_fn 만 사용한다. 서버의 어떤 실행 경로도 호출하지 않고,
    임포트만으로 부작용이 없다 → 기존 동작에 영향이 없다(무회귀).
  - 실제 배선은 CLI(`python -m app.cc_eval.offline ...`) 나 스케줄러 야간 잡에서
    settings 플래그(getattr(settings, FLAG, default))로 opt-in 하는 것을 전제로 한다.

replay(runner.py)와의 차이:
  - replay 는 오케스트레이터를 다시 돌려야 하므로 활성 MCP·LLM 키·MOCO_DATA 런타임이 필요.
  - offline 은 이미 로깅된 (prompt, response) 텍스트만 judge 에 넣으므로 런타임 불필요.
    단, run_log_store 가 응답을 500자로 잘라 저장하므로 '근사'임을 유의(전량 재생 아님).

judge_fn 은 주입형(외부 의존 격리):
    judge_fn(prompt, response) -> {"score": float, "pass": bool}
  동기/비동기(awaitable) 모두 허용한다. 테스트는 stub 을 주입해 런타임 없이 통과한다.
  rubric 은 judge_fn 이 받을 수 있는 경우에만 전달한다(2-인자 stub 은 그대로 호출).
"""

from __future__ import annotations

import inspect
import json
import math
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Union

from app.cc_eval import metrics as M

# 주입형 judge 콜백 타입. (prompt, response) -> {"score", "pass"} (동기/비동기 모두 허용)
JudgeFn = Callable[[str, str], Union[dict, Awaitable[dict]]]


# ---------------------------------------------------------------------------
# L0 로드: runs.jsonl (관측 로그) — 골든셋 로더와 동형(빈 줄/`#` 주석 무시)
# ---------------------------------------------------------------------------

def load_runs(path: str | Path) -> list[dict]:
    """runs.jsonl 을 dict 리스트로 로드. 빈 줄/`#` 주석 줄과 깨진 JSON 라인은 건너뛴다.

    각 줄은 run_log_store.log_run 이 남긴 레코드
    (type, state, prompt, response, tools_used, elapsed_seconds ...).
    """
    runs: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            runs.append(obj)
    return runs


# ---------------------------------------------------------------------------
# L3 채점: 각 run 을 judge_fn 으로 채점
# ---------------------------------------------------------------------------

async def _call_judge(judge_fn: JudgeFn, prompt: str, response: str, rubric: str) -> dict:
    """judge_fn 을 호출. rubric 은 judge_fn 이 받을 수 있을 때만 전달하고,
    반환이 awaitable 이면 await 한다(동기/비동기 judge 모두 지원)."""
    pass_rubric = False
    if rubric:
        try:
            params = inspect.signature(judge_fn).parameters
            pass_rubric = "rubric" in params or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
        except (TypeError, ValueError):
            pass_rubric = False

    res: Any = judge_fn(prompt, response, rubric=rubric) if pass_rubric else judge_fn(prompt, response)
    if inspect.isawaitable(res):
        res = await res
    return res if isinstance(res, dict) else {"score": 0.0, "pass": False}


async def judge_runs(
    runs: list[dict],
    judge_fn: JudgeFn,
    sample: Optional[int] = None,
    rubric: str = "",
) -> list[dict]:
    """각 run 의 (prompt, response) 를 judge_fn 으로 채점.

    반환: [{"type", "state", "score", "success"(bool)}, ...] (입력 순서 보존).
      - sample 이 주어지면 앞에서 N 개만 채점한다(비용 상한).
      - 빈 response 는 judge 호출 없이 success=False, score=0.0 (텍스트가 없으면 채점 불가).
      - 그 외에는 success = bool(judge["pass"]), score = float(judge["score"]).
    """
    selected = runs[:sample] if (sample is not None and sample >= 0) else runs
    results: list[dict] = []
    for run in selected:
        rtype = run.get("type", "") or "unknown"
        state = run.get("state", "")
        prompt = run.get("prompt", "") or ""
        response = run.get("response", "") or ""

        if not response.strip():
            results.append({"type": rtype, "state": state, "score": 0.0, "success": False})
            continue

        verdict = await _call_judge(judge_fn, prompt, response, rubric)
        score = float(verdict.get("score", 0.0))
        success = bool(verdict.get("pass", False))
        results.append({"type": rtype, "state": state, "score": score, "success": success})
    return results


# ---------------------------------------------------------------------------
# L4 집계
# ---------------------------------------------------------------------------

def aggregate(results: list[dict]) -> dict:
    """채점 결과를 요약. 전체 success_rate·mean_score + type 별 분해.

    반환: {n, success_rate, mean_score, by_type: {type: {n, success_rate}}}.
    (success_rate 는 metrics.success_rate 와 동형: 빈 입력이면 nan.)
    """
    successes = [bool(r.get("success")) for r in results]
    scores = [float(r.get("score", 0.0)) for r in results]

    by_type: dict[str, dict] = {}
    groups: dict[str, list[dict]] = {}
    for r in results:
        groups.setdefault(r.get("type", "") or "unknown", []).append(r)
    for t, rs in groups.items():
        by_type[t] = {
            "n": len(rs),
            "success_rate": M.success_rate([bool(x.get("success")) for x in rs]),
        }

    return {
        "n": len(results),
        "success_rate": M.success_rate(successes),
        "mean_score": (sum(scores) / len(scores)) if scores else float("nan"),
        "by_type": by_type,
    }


# ---------------------------------------------------------------------------
# 리포트 렌더 (순수) — aggregate → markdown
# ---------------------------------------------------------------------------

def _pct(x: float) -> str:
    return "n/a" if (x is None or (isinstance(x, float) and math.isnan(x))) else f"{x:.1%}"


def _num(x: float) -> str:
    return "n/a" if (x is None or (isinstance(x, float) and math.isnan(x))) else f"{x:.3f}"


def to_markdown(agg: dict) -> str:
    """aggregate 결과를 markdown 표로 렌더(리포트 저장/출력용)."""
    L = [
        "# MOCO Offline Judge Report",
        "_replay 없이 runs.jsonl(prompt+response)만으로 근사한 성공률_",
        "",
        f"- runs judged: **{agg['n']}**",
        f"- approx success rate: **{_pct(agg['success_rate'])}**",
        f"- mean judge score: **{_num(agg['mean_score'])}**",
        "",
        "| type | n | success rate |",
        "|---|---|---|",
    ]
    for t in sorted(agg["by_type"]):
        row = agg["by_type"][t]
        L.append(f"| {t} | {row['n']} | {_pct(row['success_rate'])} |")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# CLI: python -m app.cc_eval.offline --runs .../runs.jsonl --sample 100
#   실제 judge_fn 은 app.cc_eval.judge.grade_case 를 async 래핑(런타임 필요).
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import asyncio

    from app.cc_eval.judge import grade_case  # 런타임(LLM) 필요 — CLI 에서만 임포트

    ap = argparse.ArgumentParser(
        description="Offline LLM-judge scoring of runs.jsonl (재생/MCP 불필요)"
    )
    ap.add_argument("--runs", default="/home/user/MOCO_DATA/logs/runs.jsonl")
    ap.add_argument("--sample", type=int, default=None, help="앞에서 N 개만 채점(비용 상한)")
    ap.add_argument("--rubric", default="", help="LLM-judge 채점 기준(비우면 judge 기본 rubric)")
    ap.add_argument("--out", default="", help="markdown 저장 경로(비우면 stdout 만)")
    a = ap.parse_args()

    async def _judge_fn(prompt: str, response: str) -> dict:
        # judge 는 텍스트만 필요 — 도구/트래젝토리 없이 (prompt, response) 로 채점.
        return await grade_case(prompt, response, rubric=a.rubric)

    _runs = load_runs(a.runs)
    _results = asyncio.run(judge_runs(_runs, _judge_fn, sample=a.sample, rubric=a.rubric))
    _agg = aggregate(_results)
    _md = to_markdown(_agg)
    print(_md)
    if a.out:
        Path(a.out).write_text(_md, encoding="utf-8")
        print(f"\n[saved] {a.out}")
