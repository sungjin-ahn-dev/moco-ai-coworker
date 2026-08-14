# cc_eval — MOCO 에이전트 평가 하니스

기존 MOCO에는 **관측 로깅(`run_log_store`)만** 있고 체계적 평가가 없었다. `cc_eval`은
그 위에 오프라인 골든셋 회귀 + 온라인 샘플 평가를 얹는 최소 침습 패키지다.
(사내 `answer_aggregator` LLM-judge 패턴을 재사용 → 신규 외부 의존성 0.)

## 계층
```
L0 신호   runs.jsonl (관측 로그)                         ← 데이터 소스
L1 골든셋 golden_set.jsonl (prompt→기대 도구/결과/루브릭)
L2 실행   runner.replay_case: 오케스트레이터 k회 재생 + trajectory
L3 채점   metrics(순수) + judge(LLM-as-judge)
L4 리포트 report: 케이스/세션 집계 → report.md / report.json
```

## 실행
```bash
# 순수 지표 유닛테스트 (봇 런타임 불필요, 항상 통과해야 함)
python -m app.cc_eval.test_metrics          # 11/11 passed

# 전체 스위트 (MOCO 런타임 필요: 활성 MCP·LLM 키·MOCO_DATA)
cp app/cc_eval/golden_set.example.jsonl app/cc_eval/golden_set.jsonl   # 케이스 채우기
python -m app.cc_eval.report --golden app/cc_eval/golden_set.jsonl --k 3 --out MOCO_DATA/eval
```

## 지표 정의 (metrics.py — 순수 함수)
| 지표 | 정의 | 왜 |
|---|---|---|
| tool-call P/R/F1 | 기대 도구 집합 대비 실제 호출 | 라우팅/도구선택 품질 = MOCO 핵심 실패 모드 |
| **pass^k** | `C(c,k)/C(n,k)` — k회 뽑아 **전부** 성공할 확률 | 비결정 에이전트 **신뢰성**(τ-bench). k=1이면 success rate |
| pass@k | `1 - C(n-c,k)/C(n,k)` — 하나라도 성공 | 최선 케이스(상한) |
| session E2E p50/p95 | 세션 내 run 지연 **합**의 백분위 | 사용자 체감 지연 |
| $/successful-session | 성공 세션당 비용 | TCO |

## 설계 결정 (면접에서 방어할 지점)
1. **요청이 아니라 세션 단위 집계** — 멀티에이전트는 한 대화에 LLM 호출이 여러 번 겹친다.
   요청 평균은 체감을 왜곡하므로 `session_aggregate`가 세션 성공률(= 모든 run 성공)과
   세션 E2E 지연을 별도로 낸다. 세션 성공률이 요청 성공률보다 **항상 낮거나 같다.**
2. **pass^k(전부 성공) ≠ pass@k(하나라도)** — 코딩 벤치는 보통 pass@k(상한)를 쓰지만,
   실서비스 상담 봇은 "매번 되는가"가 중요해 **pass^k**를 주지표로 택했다.
3. **judge 편향 완화** — verbosity bias(루브릭에 '간결·정확 우선' 명시), position bias
   (`compare_pair`가 순서를 뒤집어 2회 평균). self-preference는 judge 모델을 피평가와
   다르게 두는 것으로 완화(운영 선택).
4. **하드 게이트 + soft judge 분리** — `must_call`/`must_not_call`(가드레일 회귀)은
   결정적으로 실패 처리하고, 나머지 품질은 judge 점수로. 둘 다 통과해야 성공.
5. **순수/부작용 분리** — `metrics`는 런타임 0 의존이라 `test_metrics.py`로 CI에서 상시 검증.
   실행부(`runner`,`judge`)만 MOCO 환경 의존.

## 남은 배선 (TODO)
- **도구 궤적 수집**: `runner._default_orchestrator`는 `call_orchestrator_agent`에
  `on_message` 훅이 있으면 tool_use 이름을 수집한다. 이 훅은 `run_log` 정상화
  (tools_used 기록)와 **같은 메커니즘**이므로 함께 배선하면 온라인 tool-accuracy도 얻는다.
- **온라인 상시화**: `scheduler.py`에 야간 잡(기존 checker와 동형)으로 `runs.jsonl` N% 샘플 →
  judge 채점 → success_rate 시계열을 `server.py` 대시보드에 노출.
- **CI 게이트**: `test_metrics`는 지금도 통과. 골든 스위트를 pytest/DeepEval로 CI에 물려
  success_rate·tool-F1 임계값으로 머지 게이팅.

## 참고
평가 이론(pass^k, credit assignment, MAST 실패분류, judge 편향·통계)의 심층은
스터디 문서 `MO14b_MultiAgent_Evaluation_Deep.html` 참조.
