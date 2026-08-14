"""Session Lane 회귀 테스트. `python -m app.test_session_lane`

유휴-종료 경쟁(TOCTOU)으로 트레일링 메시지가 조용히 유실되던 버그의 회귀 가드.
수정: enqueue 가 put 후 ensure_worker 로 재확인 + 워커가 유휴 타임아웃 시
큐가 비어있지 않으면 종료하지 않고 계속 처리.
"""

import asyncio

from app.queueing_extended import SessionLane


def test_lane_processes_after_idle_exit():
    """워커가 유휴로 종료된 뒤 재-enqueue 하면 재생성돼 처리된다."""
    async def scenario():
        processed = []

        async def pf(job):
            processed.append(job)

        lane = SessionLane("s1", idle_timeout=0.05)
        await lane.enqueue(("job1", pf))
        await asyncio.sleep(0.12)                 # job1 처리 + 워커 유휴-종료
        assert processed == ["job1"], processed
        assert lane._worker_task.done()           # 유휴로 종료됨

        await lane.enqueue(("job2", pf))          # 종료 후 재-enqueue
        await asyncio.sleep(0.05)
        assert processed == ["job1", "job2"], processed

    asyncio.run(scenario())


def test_lane_fifo_ordering():
    """같은 레인은 순서(FIFO)를 보장한다."""
    async def scenario():
        processed = []

        async def pf(job):
            await asyncio.sleep(0.01)
            processed.append(job)

        lane = SessionLane("s2", idle_timeout=5)
        for j in ["a", "b", "c"]:
            await lane.enqueue((j, pf))
        await asyncio.sleep(0.1)
        assert processed == ["a", "b", "c"], processed
        lane._worker_task.cancel()

    asyncio.run(scenario())


def test_no_loss_enqueue_around_idle():
    """유휴 타임아웃 경계에서 반복 enqueue 해도 하나도 유실되지 않는다(경쟁 가드)."""
    async def scenario():
        processed = []

        async def pf(job):
            processed.append(job)

        lane = SessionLane("s3", idle_timeout=0.02)
        for i in range(20):
            await lane.enqueue((i, pf))
            await asyncio.sleep(0.02)             # 유휴 경계 근처에서 재-enqueue
        await asyncio.sleep(0.1)
        lost = set(range(20)) - set(processed)
        assert not lost, f"유실됨: {sorted(lost)}"
        if lane._worker_task and not lane._worker_task.done():
            lane._worker_task.cancel()

    asyncio.run(scenario())


if __name__ == "__main__":
    import sys

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
