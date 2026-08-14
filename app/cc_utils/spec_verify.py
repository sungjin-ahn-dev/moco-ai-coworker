"""spec_verify — 응답 제출 전 요구사항 자기검증·보완 (명세준수).

오프라인 eval에서 MOCO의 지배 실패모드가 spec_violation(지시·포맷 위반)으로 나타났다.
이는 명세준수 층 문제라, 응답 직전에 요구사항 체크리스트를 추출·대조·보완하는 self-critique
한 턴을 넣어 미준수를 줄인다. 프롬프트 빌더와 휴리스틱으로 구성해 테스트 가능하며,
SPEC_VERIFY_ENABLED 로 opt-in 한다. 단순 요청은 should_verify 로 스킵해 추가 턴을 피한다.
"""

from __future__ import annotations

import re

# 다중요구 신호(요구가 여럿일수록 명세준수 위험↑ → 자기검증 값어치↑)
_REQ_KEYWORDS = ("해줘", "하세요", "해주세요", "정리", "요약", "조회", "작성", "보내", "발송",
                 "추출", "비교", "확인", "포함", "만들", "제외", "형식", "각각", "순서")
_DELIMS = ("그리고", "또한", "그 후", "이후", "단,", "반드시", "제외", "형식")


def _requirement_signals(text: str) -> int:
    """요청의 명시적 요구 개수를 대략 세는 휴리스틱."""
    t = text or ""
    n = len(re.findall(r"[.!?。\n]", t))
    for kw in _REQ_KEYWORDS + _DELIMS:
        n += t.count(kw)
    return n


def should_verify(state: str, final_message: str, user_query: str = "") -> bool:
    """자기검증이 값어치 있는가: 정상 응답 + 다중요구 요청일 때만(단순요청은 스킵)."""
    fm = (final_message or "").strip()
    if not fm or fm in ("Unable to generate a response.", "false"):
        return False
    if state not in ("completed", ""):
        return False
    return _requirement_signals(user_query) >= 4


def build_verification_prompt(user_query: str, draft_response: str) -> str:
    """직전 응답을 요청 요구사항 체크리스트로 자기검증하고 누락을 보완하게 하는 프롬프트."""
    return (
        "<self_verification>\n"
        "응답을 최종 제출하기 전에, 원 요청의 명시적 요구를 스스로 점검하세요.\n"
        "1) 원 요청에서 명시적 요구사항·제약·출력형식·제외조건을 빠짐없이 체크리스트로 추출.\n"
        "2) 각 항목을 방금 응답이 실제로 반영했는지 하나씩 대조.\n"
        "3) 누락·위반이 하나라도 있으면 그것을 보완한 '최종본'을 다시 작성해 사용자에게 전달하세요.\n"
        "   모두 충족했다면 기존 응답을 그대로 유지하세요. (형식·제외조건도 반드시 확인)\n"
        "</self_verification>\n\n"
        f"[원 요청]\n{(user_query or '')[:1500]}\n\n"
        f"[직전 응답]\n{(draft_response or '')[:2000]}"
    )


# orchestrator 배선 (opt-in SPEC_VERIFY_ENABLED): final_message 확보 후 break 직전에
# should_verify(state, final_message, user_query) 가 True면 build_verification_prompt 로
# 한 턴 더 질의해 final_message 를 갱신한다.
