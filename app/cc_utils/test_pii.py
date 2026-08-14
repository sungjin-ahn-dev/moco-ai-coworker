"""
pii 모듈 유닛테스트 (순수, 런타임 불필요).
`python -m app.cc_utils.test_pii`
"""

from datetime import datetime

from app.cc_utils import pii as P


# ---- mask_pii: 이메일 ----
def test_mask_email_basic():
    assert P.mask_pii("john.doe@example.com") == "jo***@example.com"


def test_mask_email_short_local():
    # local 이 짧아도 앞 2자(이하)만 남기고 마스킹
    assert P.mask_pii("a@b.co") == "a***@b.co"
    assert P.mask_pii("ab@x.io") == "ab***@x.io"


def test_mask_email_in_sentence():
    out = P.mask_pii("연락은 harry@emocog.com 으로 주세요")
    assert "ha***@emocog.com" in out
    assert "연락은" in out and "주세요" in out   # 비대상 보존


# ---- mask_pii: 휴대폰 (구분자 다양) ----
def test_mask_phone_variants():
    assert P.mask_pii("010-1234-5678") == "***-****-5678"
    assert P.mask_pii("01012345678") == "*******5678"
    assert P.mask_pii("010 1234 5678") == "*** **** 5678"
    assert P.mask_pii("010.1234.5678") == "***.****.5678"


def test_mask_phone_10digit():
    # 구형 10자리(01X-XXX-XXXX)도 뒤 4자리만
    assert P.mask_pii("011-123-4567") == "***-***-4567"


def test_mask_phone_in_sentence():
    out = P.mask_pii("제 번호는 010-9876-5432 입니다")
    assert out == "제 번호는 ***-****-5432 입니다"


# ---- mask_pii: 주민번호 ----
def test_mask_rrn():
    # 뒤 6자리 마스킹, 성별/세기 1자리 보존
    assert P.mask_pii("901231-1234567") == "901231-1******"


def test_mask_rrn_in_sentence():
    out = P.mask_pii("주민번호 850101-2345678 확인")
    assert out == "주민번호 850101-2****** 확인"


# ---- mask_pii: 카드번호 ----
def test_mask_card_grouped():
    assert P.mask_pii("1234-5678-9012-3456") == "****-****-****-3456"


def test_mask_card_contiguous():
    assert P.mask_pii("1234567890123456") == "************3456"


def test_mask_card_spaced():
    assert P.mask_pii("1234 5678 9012 3456") == "**** **** **** 3456"


# ---- mask_pii: 비대상 보존 ----
def test_non_target_preserved():
    txt = "안녕하세요 오늘 회의는 3시 회의실 A 입니다"
    assert P.mask_pii(txt) == txt   # 변형 없음


def test_short_numbers_preserved():
    # 4자리/6자리 단독 숫자는 PII 아님 → 보존
    txt = "코드 1234 방번호 567890 입니다"
    assert P.mask_pii(txt) == txt


def test_empty_and_non_str():
    assert P.mask_pii("") == ""
    assert P.mask_pii(None) is None


def test_mixed_multiple_pii():
    txt = "메일 kim@corp.com 전화 010-1111-2222 카드 1111-2222-3333-4444"
    out = P.mask_pii(txt)
    assert "ki***@corp.com" in out
    assert "***-****-2222" in out
    assert "****-****-****-4444" in out
    assert "메일" in out and "전화" in out and "카드" in out


# ---- mask_record ----
def test_mask_record_only_fields():
    d = {"prompt": "메일 john@x.com", "response": "ok", "user_id": "U1"}
    out = P.mask_record(d, ["prompt"])
    assert out["prompt"] == "메일 jo***@x.com"
    assert out["response"] == "ok"       # 비지정 필드 보존
    assert out["user_id"] == "U1"


def test_mask_record_shallow_copy_no_mutation():
    d = {"prompt": "010-1234-5678"}
    out = P.mask_record(d, ["prompt"])
    assert out is not d                  # 얕은 복사본
    assert d["prompt"] == "010-1234-5678"  # 원본 불변
    assert out["prompt"] == "***-****-5678"


def test_mask_record_missing_or_nonstr_field():
    d = {"prompt": "hi", "count": 5}
    out = P.mask_record(d, ["prompt", "count", "absent"])
    assert out["prompt"] == "hi"
    assert out["count"] == 5              # 비문자열은 건너뜀


# ---- ttl_expired ----
def test_ttl_within_days_false():
    now = datetime(2026, 1, 15)
    assert P.ttl_expired("2026-01-10", 10, now=now) is False   # 5일 경과 < 10


def test_ttl_boundary_exact_false():
    now = datetime(2026, 1, 20)
    # 정확히 10일 경과 → 초과 아님 → False
    assert P.ttl_expired("2026-01-10", 10, now=now) is False


def test_ttl_exceeded_true():
    now = datetime(2026, 1, 20)
    assert P.ttl_expired("2026-01-09", 10, now=now) is True     # 11일 경과 > 10


def test_ttl_accepts_datetime_suffix():
    now = datetime(2026, 1, 20)
    # created_at 형식("%Y-%m-%d %H:%M:%S")이라도 앞 10자만 사용
    assert P.ttl_expired("2026-01-05 13:45:00", 10, now=now) is True


def test_ttl_parse_failure_false():
    assert P.ttl_expired("not-a-date", 10) is False
    assert P.ttl_expired("", 10) is False
    assert P.ttl_expired(None, 10) is False


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
