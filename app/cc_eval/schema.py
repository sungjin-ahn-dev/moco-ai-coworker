"""cc_eval 데이터 스키마 — 순수 dataclass (런타임 의존 없음)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class GoldenCase:
    """평가 골든 케이스 하나.

    expected_tools : 이 요청에서 (순서 무관) 호출되어야 하는 도구 이름 집합.
    must_call      : 반드시 호출되어야 함 (누락 시 실패).
    must_not_call  : 절대 호출되면 안 됨 (호출 시 실패) — 가드레일 회귀 탐지.
    rubric         : LLM-as-judge 채점 기준(자연어). reference 가 없을 때 사용.
    reference      : 기대 결과(정답 텍스트/요지). 있으면 reference-guided judge.
    session_id     : 같은 대화(세션)로 묶을 키 — 세션 단위 집계에 사용.
    """

    id: str
    prompt: str
    expected_tools: list[str] = field(default_factory=list)
    must_call: list[str] = field(default_factory=list)
    must_not_call: list[str] = field(default_factory=list)
    rubric: str = ""
    reference: str = ""
    tags: list[str] = field(default_factory=list)
    session_id: str = ""

    @staticmethod
    def from_dict(d: dict) -> "GoldenCase":
        known = {f for f in GoldenCase.__dataclass_fields__}
        return GoldenCase(**{k: v for k, v in d.items() if k in known})


@dataclass
class ToolCall:
    name: str
    ok: bool = True
    args_summary: str = ""


@dataclass
class RunResult:
    """골든 케이스 1회 실행 결과 (k회 반복 중 1개)."""

    case_id: str
    run_index: int = 0
    response: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    state: str = "completed"          # completed | error | timeout
    error: str = ""
    elapsed_s: float = 0.0
    cost_usd: float = 0.0
    session_id: str = ""

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.tool_calls]

    @property
    def hard_ok(self) -> bool:
        """도구 실행/상태 레벨의 하드 성공 (judge 이전)."""
        return self.state == "completed" and all(t.ok for t in self.tool_calls)


@dataclass
class CaseScore:
    """한 케이스(k회 실행)의 채점 결과."""

    case_id: str
    n_runs: int = 0
    success_flags: list[bool] = field(default_factory=list)   # judge+hard 종합 성공
    judge_scores: list[float] = field(default_factory=list)
    tool_f1: float = 0.0
    tool_precision: float = 0.0
    tool_recall: float = 0.0
    must_call_ok: bool = True
    must_not_call_ok: bool = True
    latencies: list[float] = field(default_factory=list)
    costs: list[float] = field(default_factory=list)
    session_id: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def load_golden_set(path: str | Path) -> list[GoldenCase]:
    """JSONL 골든셋 로드. 빈 줄/`#` 주석 줄 무시."""
    cases: list[GoldenCase] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cases.append(GoldenCase.from_dict(json.loads(line)))
    return cases
