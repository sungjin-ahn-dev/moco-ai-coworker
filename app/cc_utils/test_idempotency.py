"""
idempotency 모듈 유닛테스트 (런타임 없이 순수 통과).
`python -m app.cc_utils.test_idempotency`
"""

import asyncio
import os
import tempfile

from app.cc_utils import idempotency as ID


# ---- make_key ----
def test_make_key_order_independent():
    # args dict 의 키 순서가 달라도 같은 키
    k1 = ID.make_key("gmail_send_message", {"to": "a@x.com", "subject": "hi"})
    k2 = ID.make_key("gmail_send_message", {"subject": "hi", "to": "a@x.com"})
    assert k1 == k2


def test_make_key_nested_order_independent():
    # 중첩 dict 도 정규화(sort_keys)되어 순서 무관
    k1 = ID.make_key("t", {"a": {"x": 1, "y": 2}, "b": [1, 2]})
    k2 = ID.make_key("t", {"b": [1, 2], "a": {"y": 2, "x": 1}})
    assert k1 == k2


def test_make_key_different_args():
    k1 = ID.make_key("t", {"to": "a@x.com"})
    k2 = ID.make_key("t", {"to": "b@x.com"})
    assert k1 != k2


def test_make_key_different_tool():
    # 같은 args 라도 도구 이름이 다르면 다른 키
    k1 = ID.make_key("tool_a", {"x": 1})
    k2 = ID.make_key("tool_b", {"x": 1})
    assert k1 != k2


def test_make_key_is_sha1_hex():
    k = ID.make_key("t", {"x": 1})
    assert len(k) == 40 and all(c in "0123456789abcdef" for c in k)


# ---- IdempotencyStore.seen / record ----
def test_seen_none_before_record():
    with tempfile.TemporaryDirectory() as d:
        store = ID.IdempotencyStore(os.path.join(d, "idem.jsonl"))
        assert store.seen("nope") is None


def test_record_then_seen():
    with tempfile.TemporaryDirectory() as d:
        store = ID.IdempotencyStore(os.path.join(d, "idem.jsonl"))
        store.record("k1", {"success": True, "id": "m1"})
        assert store.seen("k1") == {"success": True, "id": "m1"}
        assert store.seen("k2") is None


def test_reload_from_file():
    # 재오픈 시 로그를 다시 읽어 이전 기록 복원
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "idem.jsonl")
        s1 = ID.IdempotencyStore(path)
        s1.record("k1", "result-1")
        s2 = ID.IdempotencyStore(path)  # 새 인스턴스, 같은 파일
        assert s2.seen("k1") == "result-1"


def test_last_write_wins_on_reload():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "idem.jsonl")
        s1 = ID.IdempotencyStore(path)
        s1.record("k1", "old")
        s1.record("k1", "new")
        s2 = ID.IdempotencyStore(path)
        assert s2.seen("k1") == "new"


# ---- IdempotencyStore.once ----
def test_once_runs_once_and_caches():
    with tempfile.TemporaryDirectory() as d:
        store = ID.IdempotencyStore(os.path.join(d, "idem.jsonl"))
        calls = {"n": 0}

        def factory():
            async def _run():
                calls["n"] += 1
                return {"success": True, "call": calls["n"]}
            return _run()

        async def scenario():
            r1 = await store.once("k", factory)
            r2 = await store.once("k", factory)  # 같은 key → 캐시
            return r1, r2

        r1, r2 = asyncio.run(scenario())
        assert calls["n"] == 1          # coro_factory 는 딱 1회만 실행
        assert r1 == {"success": True, "call": 1}
        assert r2 == r1                 # 2번째는 캐시값


def test_once_different_keys_run_separately():
    with tempfile.TemporaryDirectory() as d:
        store = ID.IdempotencyStore(os.path.join(d, "idem.jsonl"))
        calls = {"n": 0}

        def factory():
            async def _run():
                calls["n"] += 1
                return calls["n"]
            return _run()

        async def scenario():
            a = await store.once("k1", factory)
            b = await store.once("k2", factory)  # 다른 key → 실제 실행
            return a, b

        a, b = asyncio.run(scenario())
        assert calls["n"] == 2
        assert (a, b) == (1, 2)


def test_once_persists_across_reopen():
    # once 로 기록된 결과가 새 인스턴스에서도 캐시되어 재실행되지 않음
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "idem.jsonl")
        calls = {"n": 0}

        def factory():
            async def _run():
                calls["n"] += 1
                return {"call": calls["n"]}
            return _run()

        async def first():
            store = ID.IdempotencyStore(path)
            return await store.once("k", factory)

        async def second():
            store = ID.IdempotencyStore(path)  # 재오픈
            return await store.once("k", factory)

        r1 = asyncio.run(first())
        r2 = asyncio.run(second())
        assert calls["n"] == 1          # 재오픈 후에도 재실행 안 함
        assert r1 == r2 == {"call": 1}


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
