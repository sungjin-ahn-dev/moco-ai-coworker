"""spec_verify 유닛테스트. `python -m app.cc_utils.test_spec_verify`"""

from app.cc_utils import spec_verify as SV


def test_should_verify_multi_requirement():
    q = "메일을 조회하고 요약해서 형식에 맞춰 보내줘, 단 광고는 제외하고 각각 정리해줘"
    assert SV.should_verify("completed", "정상 응답입니다", q) is True


def test_should_verify_simple_skips():
    assert SV.should_verify("completed", "네 안녕하세요", "안녕") is False


def test_should_verify_empty_response():
    assert SV.should_verify("completed", "", "메일 조회하고 요약하고 보내고 제외해줘") is False
    assert SV.should_verify("completed", "Unable to generate a response.", "메일 조회 요약 형식 제외") is False


def test_should_verify_error_state():
    q = "메일 조회하고 요약하고 형식 맞춰 보내고 제외해줘"
    assert SV.should_verify("error", "정상", q) is False


def test_build_prompt_contains_parts():
    p = SV.build_verification_prompt("일정 정리해줘 그리고 메일 보내줘", "초안 응답")
    assert "일정 정리해줘" in p and "초안 응답" in p and "self_verification" in p and "체크리스트" in p


def test_requirement_signals_monotone():
    simple = SV._requirement_signals("안녕")
    multi = SV._requirement_signals("조회하고 요약하고 형식 맞춰 보내줘. 단 제외. 각각 정리.")
    assert multi > simple and multi >= 4


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL  {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
