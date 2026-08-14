"""
reflexion — 실패 자기반성 재시도 (Reflexion; Shinn 2023). 순수 프롬프트 빌더.

현재 오케스트레이터는 context-overflow/idle-timeout 만 처리하고, 도구 실패나
빈 결과에 대한 '반성 후 재시도'가 없다(dead task_executor 에만 naive 재시도 존재).
이 모듈은 직전 실패의 원인을 다음 시도 프롬프트에 주입해 같은 실수를 피하게 한다.

순수 함수라 테스트 가능. 오케스트레이터 루프에 opt-in 으로 배선한다.
"""

from __future__ import annotations


def should_reflect(state: str, final_message: str, error: str = "") -> bool:
    """반성 재시도가 필요한 상태인가.

    - 빈/기본 응답: 실질 실패
    - error 상태(단, 컨텍스트/idle 는 기존 경로가 처리하므로 제외)
    """
    fm = (final_message or "").strip()
    if not fm or fm in ("Unable to generate a response.", "false"):
        return True
    e = (error or "").lower()
    if state in ("error",) and not any(x in e for x in ("context", "413", "idle", "prompt is too long")):
        return True
    return False


def build_reflection_prompt(original_query: str, prev_error: str = "",
                            prev_summary: str = "") -> str:
    """직전 시도의 실패를 반성으로 감싼 재시도 프롬프트."""
    reflection = "이전 시도가 실패했습니다."
    if prev_error:
        reflection += f" 원인(관측): {prev_error[:300]}"
    if prev_summary:
        reflection += f" 직전 진행 요약: {prev_summary[:300]}"

    return (
        f"<reflection>\n{reflection}\n"
        "이번에는: (1) 실패 원인을 피하고, (2) 필요한 도구를 정확히 고르며, "
        "(3) 최종적으로 반드시 사용자에게 답을 전달하세요.\n</reflection>\n\n"
        f"[원 요청]\n{original_query}"
    )


# ---------------------------------------------------------------------------
# 배선 가이드 (orchestrator/agent.py 재시도 루프)
# ---------------------------------------------------------------------------
# for attempt in range(max_retries + 1):
#     ... receive_response 로 final_message 확보 ...
#     from app.cc_utils.reflexion import should_reflect, build_reflection_prompt
#     if attempt < max_retries and should_reflect(state, final_message, last_error):
#         enhanced_query = build_reflection_prompt(user_query, last_error, last_summary)
#         session_id = None   # fresh 시도(또는 동일 세션 유지 선택)
#         continue
#     break
#
# settings.REFLEXION_ENABLED 플래그로 opt-in 하면 라이브 무회귀.
