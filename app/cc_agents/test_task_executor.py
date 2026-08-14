"""task_executor 유닛테스트. `python -m app.cc_agents.test_task_executor`

execute_dag 가 선행(deps) 서브태스크의 결과를 공유 TaskWorkspace 를 통해 후행
서브에이전트에 실제로 전달하는지, 독립 서브태스크는 단일 레벨로 병렬 실행하는지,
그리고 워크스페이스 레지스트리가 요청 종료 후 정리되는지를 검증한다.

sub-agent 호출은 get_agent_function 을 가짜 에이전트로 교체(monkeypatch)해 관찰한다 —
실제 LLM/Slack 호출 없이 배선만 검증한다.
"""

import asyncio

from app.cc_agents import task_executor as TE
from app.cc_agents import workspace as WS
from app.cc_agents.sub_agents.base import make_result


class _FakeSlack:
    """report_progress 가 호출하는 chat_postMessage 만 흉내내는 no-op 클라이언트."""

    async def chat_postMessage(self, **kwargs):
        return {"ok": True}


def _fake_get_agent_function(recorder):
    """agent_func 가 받은 workspace_data 를 recorder[agent_name] 에 기록하는 가짜 레지스트리."""

    async def fake_get(agent_name):
        async def agent_func(query, context, workspace_data=None):
            recorder[agent_name] = workspace_data
            return make_result(
                "success", f"{agent_name} 요약",
                data={"by": agent_name, "q": query},
            )
        return agent_func

    return fake_get


def _with_fake_agents(recorder, coro_factory):
    """get_agent_function 을 가짜로 바꾸고 coro 를 실행한 뒤 원복한다."""
    orig = TE.get_agent_function
    TE.get_agent_function = _fake_get_agent_function(recorder)
    try:
        return asyncio.run(coro_factory())
    finally:
        TE.get_agent_function = orig


def test_execute_dag_passes_upstream_to_downstream():
    """후행 서브태스크(document)가 선행(research) 결과를 공유 워크스페이스로 받는다."""
    recorder = {}
    subtasks = [
        {"id": "s1", "agent": "research", "query": "조사", "deps": []},
        {"id": "s2", "agent": "document", "query": "정리", "deps": ["s1"]},
    ]
    ex = TE.TaskExecutor(_FakeSlack(), {})
    results = _with_fake_agents(recorder, lambda: ex.execute_dag(subtasks, context="ctx"))

    assert results["s1"]["status"] == "success"
    assert results["s2"]["status"] == "success"
    # 선행 research 는 upstream 이 비어 있어야
    assert recorder["research"] == {}
    # 후행 document 는 선행 s1 결과를 워크스페이스로 받아야
    seen = recorder["document"]
    assert "s1" in seen
    assert seen["s1"]["agent"] == "research"
    assert seen["s1"]["data"]["by"] == "research"


def test_execute_dag_independent_run_as_single_layer():
    """독립 서브태스크는 서로의 결과를 보지 않고 단일 레벨로 실행된다."""
    recorder = {}
    subtasks = [
        {"id": "s1", "agent": "research", "query": "a", "deps": []},
        {"id": "s2", "agent": "data", "query": "b", "deps": []},
    ]
    ex = TE.TaskExecutor(_FakeSlack(), {})
    results = _with_fake_agents(recorder, lambda: ex.execute_dag(subtasks, context="ctx"))

    assert set(results.keys()) == {"s1", "s2"}
    assert recorder["research"] == {} and recorder["data"] == {}


def test_execute_dag_diamond_terminal_sees_both_parents():
    """다이아몬드 DAG의 종단 노드는 두 선행 결과를 모두 받는다."""
    recorder = {}
    subtasks = [
        {"id": "s1", "agent": "research", "query": "root", "deps": []},
        {"id": "s2", "agent": "code", "query": "left", "deps": ["s1"]},
        {"id": "s3", "agent": "data", "query": "right", "deps": ["s1"]},
        {"id": "s4", "agent": "document", "query": "merge", "deps": ["s2", "s3"]},
    ]
    ex = TE.TaskExecutor(_FakeSlack(), {})
    results = _with_fake_agents(recorder, lambda: ex.execute_dag(subtasks, context="ctx"))

    assert set(results.keys()) == {"s1", "s2", "s3", "s4"}
    seen = recorder["document"]
    assert set(seen.keys()) == {"s2", "s3"}
    assert seen["s2"]["data"]["by"] == "code"
    assert seen["s3"]["data"]["by"] == "data"


def test_execute_dag_cleans_up_workspace_registry():
    """execute_dag 종료 후 활성 워크스페이스 레지스트리가 정리된다(누수 없음)."""
    recorder = {}
    subtasks = [{"id": "s1", "agent": "research", "query": "x", "deps": []}]
    ex = TE.TaskExecutor(_FakeSlack(), {})
    before = len(WS._active_workspaces)
    _with_fake_agents(recorder, lambda: ex.execute_dag(subtasks, context="c"))
    assert len(WS._active_workspaces) == before


def test_taskworkspace_namespacing():
    """TaskWorkspace 의 write/read/read_by_agent 네임스페이싱."""
    ws = WS.TaskWorkspace("t1")
    ws.write("research", "result", {"x": 1})
    assert ws.read("research.result") == {"x": 1}
    assert ws.read_by_agent("research") == {"result": {"x": 1}}
    assert "research.result" in ws.read_all()
    ws.clear()
    assert ws.read_all() == {}


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
