"""
outbox 모듈 유닛테스트 (런타임 없이 순수 파일 I/O 만).
`python -m app.cc_utils.test_outbox`
"""

import os
import tempfile

from app.cc_utils.outbox import Outbox


def _tmp_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    os.remove(path)  # 존재하지 않는 새 경로에서 시작(빈 아웃박스)
    return path


# ---- 기본 put / pending ----
def test_put_then_pending():
    path = _tmp_path()
    try:
        ob = Outbox(path)
        assert ob.pending() == []            # 비어 있음
        ob.put({"kind": "memory", "n": 1})
        ob.put({"kind": "task", "n": 2})
        assert len(ob.pending()) == 2        # put 2 → pending 2
    finally:
        os.path.exists(path) and os.remove(path)


def test_put_returns_unique_ids():
    path = _tmp_path()
    try:
        ob = Outbox(path)
        a = ob.put({"n": 1})
        b = ob.put({"n": 2})
        assert isinstance(a, str) and isinstance(b, str) and a != b
    finally:
        os.path.exists(path) and os.remove(path)


# ---- mark_done ----
def test_mark_done_reduces_pending():
    path = _tmp_path()
    try:
        ob = Outbox(path)
        first = ob.put({"n": 1})
        ob.put({"n": 2})
        ob.mark_done(first)
        pend = ob.pending()
        assert len(pend) == 1                # mark_done 1개 → pending 1
        assert pend[0]["n"] == 2             # 남은 것은 두 번째(원래 순서 보존)
    finally:
        os.path.exists(path) and os.remove(path)


# ---- 재오픈(크래시 세이프) ----
def test_reopen_reconstructs_state():
    path = _tmp_path()
    try:
        ob = Outbox(path)
        first = ob.put({"n": 1})
        ob.put({"n": 2})
        ob.mark_done(first)

        # 새 인스턴스(=재부팅)로 같은 path 재오픈 → 상태 재구성
        ob2 = Outbox(path)
        pend = ob2.pending()
        assert len(pend) == 1                # 재오픈해도 pending 1
        assert pend[0]["n"] == 2
    finally:
        os.path.exists(path) and os.remove(path)


def test_reopen_all_done_is_empty():
    path = _tmp_path()
    try:
        ob = Outbox(path)
        a = ob.put({"n": 1})
        b = ob.put({"n": 2})
        ob.mark_done(a)
        ob.mark_done(b)
        assert Outbox(path).pending() == []  # 전부 done → 재오픈 시 0
    finally:
        os.path.exists(path) and os.remove(path)


# ---- replay ----
def test_replay_processes_remaining():
    path = _tmp_path()
    try:
        ob = Outbox(path)
        first = ob.put({"n": 1})
        ob.put({"n": 2})
        ob.mark_done(first)

        seen = []
        ok, fail = ob.replay(lambda op: seen.append(op["n"]))
        assert (ok, fail) == (1, 0)          # 나머지 1건 처리
        assert seen == [2]
        assert ob.pending() == []            # replay 후 pending 0
    finally:
        os.path.exists(path) and os.remove(path)


def test_replay_across_reopen():
    path = _tmp_path()
    try:
        ob = Outbox(path)
        ob.put({"n": 1})
        ob.put({"n": 2})
        # 크래시 시뮬레이션: mark_done 없이 새 인스턴스로 재개
        ob2 = Outbox(path)
        assert len(ob2.pending()) == 2

        seen = []
        ok, fail = ob2.replay(lambda op: seen.append(op["n"]))
        assert (ok, fail) == (2, 0)
        assert sorted(seen) == [1, 2]
        assert ob2.pending() == []
        # 재오픈해도 done 이 유지됨(중복 재처리 없음)
        assert Outbox(path).pending() == []
    finally:
        os.path.exists(path) and os.remove(path)


def test_replay_failure_keeps_pending():
    path = _tmp_path()
    try:
        ob = Outbox(path)
        ob.put({"n": 1})
        ob.put({"n": 2})

        def handler(op):
            if op["n"] == 1:
                raise RuntimeError("boom")   # 첫 건은 실패

        ok, fail = ob.replay(handler)
        assert (ok, fail) == (1, 1)          # 1 성공, 1 실패
        pend = ob.pending()
        assert len(pend) == 1 and pend[0]["n"] == 1  # 실패분은 pending 유지

        # 재시도하면 성공(at-least-once)
        ok2, fail2 = ob.replay(lambda op: None)
        assert (ok2, fail2) == (1, 0)
        assert ob.pending() == []
    finally:
        os.path.exists(path) and os.remove(path)


def test_corrupt_trailing_line_tolerated():
    path = _tmp_path()
    try:
        ob = Outbox(path)
        ob.put({"n": 1})
        # 크래시로 인한 부분 기록(마지막 줄이 깨짐)을 흉내
        with open(path, "a", encoding="utf-8") as f:
            f.write('{"op_id": "x", "op": {"n": 2}, "sta')  # 개행 없이 잘림
        # 재오픈해도 예외 없이, 온전한 1건만 복구
        pend = Outbox(path).pending()
        assert len(pend) == 1 and pend[0]["n"] == 1
    finally:
        os.path.exists(path) and os.remove(path)


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
