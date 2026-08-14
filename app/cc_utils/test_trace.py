"""
trace 모듈 유닛테스트 (런타임 없이 순수).
`python -m app.cc_utils.test_trace`
"""

import logging

from app.cc_utils import trace as T


# ---- 기본값 / id 포맷 ----
def test_default_is_dash():
    # 아무것도 바인딩하지 않은 상태(다른 테스트는 모두 정리함)
    assert T.get_trace_id() == "-"


def test_new_trace_id_format():
    a = T.new_trace_id()
    b = T.new_trace_id()
    assert len(a) == 8 and len(b) == 8
    assert all(c in "0123456789abcdef" for c in a)
    assert a != b   # 사실상 항상 다름


# ---- bind / reset ----
def test_bind_then_get_matches():
    token = T.bind_trace_id("abc12345")
    try:
        assert T.get_trace_id() == "abc12345"
    finally:
        T.reset_trace_id(token)
    assert T.get_trace_id() == "-"   # 복원


# ---- new_trace 컨텍스트 매니저 ----
def test_new_trace_enter_and_exit():
    assert T.get_trace_id() == "-"
    with T.new_trace() as tid:
        assert len(tid) == 8
        assert T.get_trace_id() == tid   # 진입 시 바인딩
    assert T.get_trace_id() == "-"       # 이탈 시 복원


def test_new_trace_prefix():
    with T.new_trace(prefix="orch-") as tid:
        assert tid.startswith("orch-")
        assert len(tid) == 5 + 8
        assert T.get_trace_id() == tid


def test_new_trace_nested_restores_outer():
    with T.new_trace() as outer:
        assert T.get_trace_id() == outer
        with T.new_trace() as inner:
            assert inner != outer
            assert T.get_trace_id() == inner
        # 중첩 이탈 후 바깥 값 복원 보장
        assert T.get_trace_id() == outer
    assert T.get_trace_id() == "-"


# ---- TraceFilter ----
def test_trace_filter_sets_record():
    filt = T.TraceFilter()
    record = logging.LogRecord(
        name="x", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hi", args=(), exc_info=None,
    )
    with T.new_trace() as tid:
        assert filt.filter(record) is True   # 레코드를 막지 않음
        assert record.trace_id == tid


def test_trace_filter_default_when_unbound():
    filt = T.TraceFilter()
    record = logging.LogRecord(
        name="x", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hi", args=(), exc_info=None,
    )
    assert filt.filter(record) is True
    assert record.trace_id == "-"


# ---- install_trace_logging ----
def test_install_attaches_and_is_idempotent():
    # 격리된 로거에 핸들러 하나 달고 검증(루트 전역 오염 방지)
    logger = logging.getLogger("moco.test.trace")
    logger.handlers = []
    handler = logging.NullHandler()
    logger.addHandler(handler)

    n1 = T.install_trace_logging(logger)
    assert n1 == 1
    assert any(isinstance(f, T.TraceFilter) for f in handler.filters)

    # 두 번째 호출은 멱등 — 중복 부착 없음
    n2 = T.install_trace_logging(logger)
    assert n2 == 0
    assert sum(isinstance(f, T.TraceFilter) for f in handler.filters) == 1


def test_install_no_handlers_returns_zero():
    logger = logging.getLogger("moco.test.trace.empty")
    logger.handlers = []
    assert T.install_trace_logging(logger) == 0   # 예외 없이 0


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
