"""
태스크 실행기 (Task Executor)

Sub-agent 병렬/순차 실행, 재계획(replan), 진행 상황 보고 기능을 제공합니다.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.cc_agents.sub_agents.base import make_result

MAX_REPLAN = 2


async def get_agent_function(agent_name: str):
    """agent_name으로 실제 call_XXX_agent 함수를 반환합니다.

    Args:
        agent_name: sub-agent 이름 (research, communication, code, pm, document, data, web)

    Returns:
        callable 또는 None (존재하지 않는 경우)
    """
    from app.cc_agents.sub_agents.research.agent import call_research_agent
    from app.cc_agents.sub_agents.communication.agent import call_communication_agent
    from app.cc_agents.sub_agents.code.agent import call_code_agent
    from app.cc_agents.sub_agents.pm.agent import call_pm_agent
    from app.cc_agents.sub_agents.document.agent import call_document_agent
    from app.cc_agents.sub_agents.data.agent import call_data_agent
    from app.cc_agents.sub_agents.web.agent import call_web_agent

    registry = {
        "research": call_research_agent,
        "communication": call_communication_agent,
        "code": call_code_agent,
        "pm": call_pm_agent,
        "document": call_document_agent,
        "data": call_data_agent,
        "web": call_web_agent,
    }
    return registry.get(agent_name)


class TaskExecutor:
    """Sub-agent 병렬/순차 실행 + 재계획 관리 클래스"""

    def __init__(self, slack_client, message_data: dict):
        """
        Args:
            slack_client: Slack AsyncWebClient 인스턴스
            message_data: 현재 메시지 정보 (channel_id, thread_ts, message_ts 등)
        """
        self.slack_client = slack_client
        self.message_data = message_data
        self.channel_id = message_data.get("channel_id")
        self.thread_ts = message_data.get("thread_ts") or message_data.get("message_ts")

    async def report_progress(self, text: str) -> None:
        """Slack 스레드에 진행 상황을 보고합니다.

        Args:
            text: 보고할 텍스트
        """
        try:
            await self.slack_client.chat_postMessage(
                channel=self.channel_id,
                thread_ts=self.thread_ts,
                text=text,
            )
        except Exception as e:
            logging.warning(f"[TASK_EXECUTOR] Progress report failed: {e}")

    async def run_sub_agent(
        self,
        task: dict,
        workspace_data: dict,
        context: str,
    ) -> dict:
        """단일 sub-agent를 실행합니다. 실패 시 MAX_REPLAN 횟수만큼 재시도합니다.

        Args:
            task: 실행할 태스크 정보
                  {"agent": "research", "query": "...", "description": "..."}
            workspace_data: 공유 작업 공간 데이터 (이전 sub-agent 결과 포함)
            context: 대화 맥락 및 사용자 정보

        Returns:
            dict: RESULT_SCHEMA 형태의 결과 딕셔너리
        """
        agent_name = task.get("agent")
        query = task.get("query", "")

        agent_func = await get_agent_function(agent_name)
        if not agent_func:
            return make_result(
                "failed",
                f"알 수 없는 agent: {agent_name}",
                error=f"unknown_agent:{agent_name}",
            )

        for attempt in range(MAX_REPLAN + 1):
            try:
                result = await agent_func(
                    query=query,
                    context=context,
                    workspace_data=workspace_data,
                )
                if result.get("status") == "failed" and attempt < MAX_REPLAN:
                    logging.warning(
                        f"[TASK_EXECUTOR] {agent_name} failed "
                        f"(attempt {attempt + 1}), retrying..."
                    )
                    await asyncio.sleep(1)
                    continue
                return result
            except Exception as e:
                if attempt == MAX_REPLAN:
                    return make_result(
                        "failed",
                        f"{agent_name} 실패: {str(e)}",
                        error=str(e),
                    )
                await asyncio.sleep(1)

        return make_result("failed", f"{agent_name} 최대 재시도 초과")

    async def execute_parallel(
        self,
        tasks: List[dict],
        context: str,
    ) -> Dict[str, dict]:
        """여러 sub-agent를 병렬로 실행합니다.

        Args:
            tasks: 실행할 태스크 목록
                   [{"agent": "research", "query": "...", "description": "..."}, ...]
            context: 대화 맥락 및 사용자 정보

        Returns:
            dict: {agent_name: result_dict} 형태의 결과 딕셔너리
        """
        workspace_data: Dict[str, Any] = {}

        if tasks:
            await self.report_progress(
                f"작업을 시작합니다. {len(tasks)}개 처리 예정"
            )

        async def run_one(task: dict):
            result = await self.run_sub_agent(task, workspace_data.copy(), context)
            agent_name = task.get("agent", "unknown")
            workspace_data[agent_name] = result.get("data", {})

            status_emoji = "✓" if result.get("status") == "success" else "⚠"
            await self.report_progress(
                f"{status_emoji} {task.get('description', agent_name)} 완료: "
                f"{result.get('summary', '')}"
            )
            return agent_name, result

        results_list = await asyncio.gather(
            *[run_one(t) for t in tasks],
            return_exceptions=True,
        )

        results: Dict[str, dict] = {}
        for item in results_list:
            if isinstance(item, Exception):
                logging.error(f"[TASK_EXECUTOR] Parallel task exception: {item}")
            else:
                agent_name, result = item
                results[agent_name] = result

        return results

    async def execute_sequential(
        self,
        tasks: List[dict],
        context: str,
    ) -> Dict[str, dict]:
        """여러 sub-agent를 순차적으로 실행합니다.

        이전 sub-agent의 결과 데이터가 다음 sub-agent의 workspace_data로 전달됩니다.

        Args:
            tasks: 실행할 태스크 목록 (순서대로 처리)
                   [{"agent": "research", "query": "...", "description": "..."}, ...]
            context: 대화 맥락 및 사용자 정보

        Returns:
            dict: {agent_name: result_dict} 형태의 결과 딕셔너리
        """
        workspace_data: Dict[str, Any] = {}
        results: Dict[str, dict] = {}

        if tasks:
            await self.report_progress(
                f"작업을 시작합니다. {len(tasks)}개 순차 처리 예정"
            )

        for task in tasks:
            result = await self.run_sub_agent(task, workspace_data.copy(), context)
            agent_name = task.get("agent", "unknown")
            results[agent_name] = result
            workspace_data[agent_name] = result.get("data", {})

            status_emoji = "✓" if result.get("status") == "success" else "⚠"
            await self.report_progress(
                f"{status_emoji} {task.get('description', agent_name)} 완료: "
                f"{result.get('summary', '')}"
            )

        return results
