"""
cc_eval — MOCO 에이전트 평가 하니스 (Agent Evaluation Harness)

관측 로깅만 있던 MOCO에 체계적 오프라인/온라인 평가를 추가한다.

계층:
  L0  신호      — runs.jsonl(관측 로그)  ← 데이터 소스
  L1  골든셋    — golden_set.jsonl (prompt → 기대 도구/결과/루브릭)
  L2  실행      — runner.replay_case: 오케스트레이터를 k회 재생, trajectory 수집
  L3  채점      — metrics(task success, tool-call accuracy, pass^k) + judge(LLM-as-judge)
  L4  리포트    — report: 케이스/세션 집계 → markdown/json

설계 원칙:
  - metrics 는 순수 함수(런타임 의존 0) → pytest 로 즉시 검증(test_metrics.py).
  - judge 는 사내 answer_aggregator 패턴을 그대로 복제 → 신규 의존성 0.
  - 집계는 "요청 단위가 아니라 세션 단위"(멀티에이전트 함정) 를 기본 지원.
"""

from app.cc_eval import metrics, schema  # noqa: F401

__all__ = ["metrics", "schema"]
