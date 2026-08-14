"""
공유 작업 공간 (Shared Workspace)

단일 사용자 요청 처리 중 Sub-agent들이 공유하는 임시 in-memory 공간입니다.
각 요청은 고유한 task_id를 가지며, 요청 완료 후 destroy_workspace()로 정리합니다.
"""

import uuid
from typing import Any, Dict, Optional


class TaskWorkspace:
    """단일 사용자 요청 처리 중 Sub-agent들이 공유하는 임시 공간"""

    def __init__(self, task_id: Optional[str] = None):
        """
        Args:
            task_id: 작업 고유 ID. None이면 UUID v4로 자동 생성됩니다.
        """
        self.task_id = task_id or str(uuid.uuid4())
        self.store: Dict[str, Any] = {}

    def write(self, agent_name: str, key: str, value: Any) -> None:
        """Sub-agent가 생성한 데이터를 공유 공간에 저장합니다.

        저장 키는 "{agent_name}.{key}" 형태로 네임스페이스가 구분됩니다.

        Args:
            agent_name: 데이터를 생성한 sub-agent 이름
            key: 저장할 데이터의 키
            value: 저장할 데이터
        """
        self.store[f"{agent_name}.{key}"] = value

    def read(self, key: str) -> Optional[Any]:
        """네임스페이스 포함 전체 키로 읽기 (예: "research.search_results")."""
        return self.store.get(key)

    def read_by_agent(self, agent_name: str) -> Dict[str, Any]:
        """특정 sub-agent가 저장한 모든 데이터를 반환합니다.

        Args:
            agent_name: 데이터를 조회할 sub-agent 이름

        Returns:
            dict: {key: value} 형태 (네임스페이스 접두사 제외)
        """
        prefix = f"{agent_name}."
        return {
            k[len(prefix):]: v
            for k, v in self.store.items()
            if k.startswith(prefix)
        }

    def read_all(self) -> Dict[str, Any]:
        """저장소 전체의 복사본을 반환."""
        return self.store.copy()

    def clear(self) -> None:
        """공유 공간의 모든 데이터를 초기화합니다."""
        self.store.clear()

    def __repr__(self) -> str:
        return f"TaskWorkspace(task_id={self.task_id}, keys={list(self.store.keys())})"


# ---------------------------------------------------------------------------
# 활성 작업 공간 레지스트리 (task_id → TaskWorkspace)
# ---------------------------------------------------------------------------

_active_workspaces: Dict[str, TaskWorkspace] = {}


def create_workspace(task_id: Optional[str] = None) -> TaskWorkspace:
    """새 TaskWorkspace를 생성하고 레지스트리에 등록합니다.

    Args:
        task_id: 작업 고유 ID. None이면 UUID v4로 자동 생성됩니다.

    Returns:
        TaskWorkspace: 생성된 작업 공간 인스턴스
    """
    ws = TaskWorkspace(task_id)
    _active_workspaces[ws.task_id] = ws
    return ws


def get_workspace(task_id: str) -> Optional[TaskWorkspace]:
    """task_id로 활성 작업 공간 조회. 없으면 None."""
    return _active_workspaces.get(task_id)


def destroy_workspace(task_id: str) -> None:
    """작업 공간을 레지스트리에서 제거 (메모리 해제)."""
    _active_workspaces.pop(task_id, None)
