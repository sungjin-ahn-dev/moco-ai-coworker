"""
memory_hybrid — 렉시컬 + 시맨틱 하이브리드 메모리 검색 (RRF 융합)

현재 memory_index 는 100% 렉시컬 토큰 스코어링이라 한국어 패러프레이즈
("회의 결정" ↔ "미팅 합의")를 놓친다. 이 모듈은 렉시컬 랭킹과 (선택적) 임베딩
시맨틱 랭킹을 Reciprocal Rank Fusion 으로 합쳐 보완한다.

- reciprocal_rank_fusion : 순수 융합 함수 (테스트됨)
- hybrid_search          : memory_index.search 결과 + embed_fn 을 받아 융합
                           embed_fn=None 이면 렉시컬 그대로 (무변경 = 안전)
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence


def reciprocal_rank_fusion(ranked_lists: Sequence[Sequence], k: int = 60,
                           key: Callable = lambda x: x) -> list:
    """여러 랭킹 리스트를 RRF 로 융합.

        rrf_score(d) = Σ_lists 1 / (k + rank_in_list(d))
    각 아이템의 첫 등장 객체를 유지하고, 융합 점수 내림차순으로 반환.
    """
    scores: dict = {}
    reps: dict = {}
    for rl in ranked_lists:
        for rank, item in enumerate(rl):
            kk = key(item)
            scores[kk] = scores.get(kk, 0.0) + 1.0 / (k + rank + 1)
            reps.setdefault(kk, item)
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [reps[kk] for kk, _ in ordered]


def _mem_key(r: dict) -> str:
    """메모리 엔트리의 안정 식별자 (user_id + path)."""
    return f"{r.get('user_id','')}/{r.get('path','')}"


def hybrid_search(
    lexical_results: list[dict],
    query: str,
    semantic_ranker: Optional[Callable[[str, list[dict]], list[dict]]] = None,
    k: int = 60,
    limit: int = 6,
) -> list[dict]:
    """렉시컬 결과 + (선택) 시맨틱 랭커를 RRF 로 융합.

    semantic_ranker(query, candidates) -> candidates 를 시맨틱 유사도순으로 재정렬한 리스트.
    None 이면 렉시컬 결과를 그대로 반환(회귀 없음).
    """
    if not semantic_ranker or not lexical_results:
        return lexical_results[:limit]
    sem = semantic_ranker(query, lexical_results)
    fused = reciprocal_rank_fusion([lexical_results, sem], k=k, key=_mem_key)
    return fused[:limit]


# ---------------------------------------------------------------------------
# 배선 가이드 (memory_index / memory_retriever)
# ---------------------------------------------------------------------------
# 시맨틱 랭커는 임베딩 백엔드가 필요하다(예: 로컬 multilingual-e5-small, 또는
# 사내 Cogsearch 임베딩 재사용). 예시:
#
#   from sentence_transformers import SentenceTransformer
#   _m = SentenceTransformer("intfloat/multilingual-e5-small")
#   def semantic_ranker(query, cands):
#       import numpy as np
#       qv = _m.encode([query])[0]
#       texts = [f"{c.get('title','')} {' '.join(c.get('tags',[]))}" for c in cands]
#       cvs = _m.encode(texts)
#       sims = cvs @ qv / (np.linalg.norm(cvs,axis=1)*np.linalg.norm(qv)+1e-9)
#       return [c for _, c in sorted(zip(sims, cands), key=lambda t: t[0], reverse=True)]
#
# 그다음 memory_index.search_and_load 에서:
#   from app.cc_utils.memory_hybrid import hybrid_search
#   lex = self.search(query, user_id, channel_id, limit=limit*3)   # 렉시컬 후보 넓게
#   results = hybrid_search(lex, query, semantic_ranker, limit=limit)
