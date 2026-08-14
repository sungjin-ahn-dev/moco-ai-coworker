"""cc_eval.report — CaseScore/RunResult 집계 → markdown + json 리포트 (순수)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.cc_eval import metrics as M


def build_report(scores: list, runs: list, ks: tuple[int, ...] = (1, 2, 3)) -> dict:
    """전체 요약 dict. 품질 축 + 시스템 축(세션 단위)."""
    agg = M.aggregate_case_scores(scores, ks=ks)

    run_dicts = [{
        "session_id": getattr(r, "session_id", ""),
        "success": getattr(r, "hard_ok", False),
        "elapsed_s": getattr(r, "elapsed_s", 0.0),
        "cost_usd": getattr(r, "cost_usd", 0.0),
    } for r in runs]
    sess = M.session_aggregate(run_dicts)

    return {"generated_at": datetime.now().isoformat(timespec="seconds"),
            "quality": agg, "system": sess}


def to_markdown(report: dict) -> str:
    q, s = report["quality"], report["system"]
    ph = q["pass_hat_k"]; pa = q["pass_at_k"]
    L = [
        "# MOCO Agent Eval Report",
        f"_generated: {report['generated_at']}_", "",
        "## 품질 축", "",
        f"- cases: **{q['n_cases']}**  |  case success rate: **{q['case_success_rate']:.1%}**",
        f"- mean tool-call F1: **{q['mean_tool_f1']:.3f}**  |  mean judge: **{q['mean_judge_score']:.3f}**",
        "",
        "| k | pass^k (전부 성공) | pass@k (하나라도) |",
        "|---|---|---|",
    ]
    for k in sorted(ph):
        L.append(f"| {k} | {ph[k]:.3f} | {pa[k]:.3f} |")
    L += [
        "", "## 시스템 축 (세션 단위)", "",
        f"- requests: {s['n_requests']}  |  sessions: **{s['n_sessions']}**",
        f"- request success: {s['request_success_rate']:.1%}  |  "
        f"**session success: {s['session_success_rate']:.1%}** (요청보다 엄격 = 체감)",
        f"- session E2E latency  p50 **{s['session_latency']['p50']:.1f}s** / "
        f"p95 **{s['session_latency']['p95']:.1f}s**",
        f"- $/successful-session: {s['cost_per_successful_session']}",
    ]
    return "\n".join(L)


def write_report(scores: list, runs: list, out_dir: str | Path) -> dict:
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    report = build_report(scores, runs)
    (out / "report.json").write_text(
        json.dumps({"report": report,
                    "cases": [s.to_dict() if hasattr(s, "to_dict") else s for s in scores]},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    md = to_markdown(report)
    (out / "report.md").write_text(md, encoding="utf-8")
    return report


# CLI: python -m app.cc_eval.report  (골든셋 재생 → 리포트)
if __name__ == "__main__":
    import argparse, asyncio
    from app.cc_eval.runner import run_suite

    ap = argparse.ArgumentParser(description="Run MOCO agent eval suite")
    ap.add_argument("--golden", default="app/cc_eval/golden_set.jsonl")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--out", default="MOCO_DATA/eval")
    ap.add_argument("--no-judge", action="store_true")
    a = ap.parse_args()

    scores, runs = asyncio.run(run_suite(a.golden, k=a.k, use_judge=not a.no_judge))
    rep = write_report(scores, runs, a.out)
    print(to_markdown(rep))
