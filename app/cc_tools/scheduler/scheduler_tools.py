"""
Scheduler Tools for Claude Code SDK
Claude가 직접 스케줄을 관리할 수 있는 도구
"""

import asyncio
import json
import logging
import uuid
from typing import Any, Dict

from claude_agent_sdk import create_sdk_mcp_server, tool

from app import scheduler

# 스케줄 파일 동시 접근 방지를 위한 Lock
_schedule_file_lock = asyncio.Lock()


@tool(
    "add_schedule",
    "새로운 스케줄을 추가합니다. cron 또는 date 타입을 지원합니다.",
    {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "스케줄의 목적을 나타내는 고유한 이름 (예: '매일 아침 리마인더', '주간 보고')"
            },
            "schedule_type": {
                "type": "string",
                "enum": ["cron", "date"],
                "description": "스케줄 타입 - 'cron' (반복) 또는 'date' (일회성)"
            },
            "schedule_value": {
                "type": "string",
                "description": "cron 타입: cron 표현식 (예: '0 9 * * *' = 매일 9시), date 타입: 'YYYY-MM-DD HH:MM:SS' 형식"
            },
            "user_id": {
                "type": "string",
                "description": "스케줄이 실행될 때 메시지를 받을 사용자 ID"
            },
            "text": {
                "type": "string",
                "description": "스케줄 실행 시점에 가상 상주 직원이 받을 완전한 명령문 (반드시 봇 이름으로 시작하는 전체 명령 포함. 예: '원하나님, 1이라고 말해주세요')"
            },
            "channel_id": {
                "type": "string",
                "description": "스케줄이 실행될 채널 ID"
            },
            "is_enabled": {
                "type": "boolean",
                "description": "스케줄 활성화 여부 (기본값: true)"
            }
        },
        "required": ["name", "schedule_type", "schedule_value", "user_id", "text", "channel_id"]
    }
)
async def scheduler_add_schedule(args: Dict[str, Any]) -> Dict[str, Any]:
    """새로운 스케줄 추가"""
    name = args["name"]
    schedule_type = args["schedule_type"]
    schedule_value = args["schedule_value"]
    user_id = args["user_id"]
    text = args["text"]
    channel_id = args["channel_id"]
    is_enabled = args.get("is_enabled", True)

    try:
        if schedule_type not in ["cron", "date"]:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": "잘못된 schedule_type입니다. 'cron' 또는 'date'만 사용할 수 있습니다."
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

        # 날짜/cron 형식 검증
        if schedule_type == "date":
            from datetime import datetime
            try:
                datetime.fromisoformat(schedule_value.replace('Z', '+00:00'))
            except (ValueError, AttributeError) as e:
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": False,
                            "error": True,
                            "message": f"잘못된 날짜 형식입니다: {schedule_value}. 'YYYY-MM-DD HH:MM:SS' 형식을 사용하세요."
                        }, ensure_ascii=False, indent=2)
                    }],
                    "error": True
                }
        elif schedule_type == "cron":
            from apscheduler.triggers.cron import CronTrigger
            try:
                CronTrigger.from_crontab(schedule_value)
            except (ValueError, KeyError) as e:
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": False,
                            "error": True,
                            "message": f"잘못된 cron 표현식입니다: {schedule_value}. 예: '0 9 * * *' (매일 9시)"
                        }, ensure_ascii=False, indent=2)
                    }],
                    "error": True
                }

        # 동시 접근 방지를 위한 Lock 사용
        async with _schedule_file_lock:
            schedules = scheduler.read_schedules_from_file()

            # 중복 이름 체크 (경고만, 차단하지 않음)
            duplicate_names = [s.get("name") for s in schedules if s.get("name") == name and s.get("is_enabled")]
            if duplicate_names:
                logging.warning(f"[SCHEDULER_TOOLS] Duplicate schedule name detected: {name}")
            new_schedule = {
                "id": str(uuid.uuid4()),
                "name": name,
                "schedule_type": schedule_type,
                "schedule_value": schedule_value,
                "user": user_id,
                "text": text,
                "channel": channel_id,
                "is_enabled": is_enabled,
            }
            schedules.append(new_schedule)
            scheduler.write_schedules_to_file(schedules)
            await scheduler.reload_schedules_from_file()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "message": f"성공적으로 스케줄을 추가했습니다: {name}",
                    "schedule_id": new_schedule["id"]
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"스케줄 추가 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "remove_schedule",
    "ID를 사용하여 스케줄을 삭제합니다.",
    {
        "type": "object",
        "properties": {
            "schedule_id": {
                "type": "string",
                "description": "삭제할 스케줄의 ID"
            }
        },
        "required": ["schedule_id"]
    }
)
async def scheduler_remove_schedule(args: Dict[str, Any]) -> Dict[str, Any]:
    """스케줄 삭제"""
    schedule_id = args["schedule_id"]

    try:
        # 동시 접근 방지를 위한 Lock 사용
        async with _schedule_file_lock:
            schedules = scheduler.read_schedules_from_file()
            original_count = len(schedules)
            schedules = [s for s in schedules if s.get("id") != schedule_id]

            if len(schedules) == original_count:
                return {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({
                            "success": False,
                            "error": True,
                            "message": f"ID가 {schedule_id}인 스케줄을 찾을 수 없습니다."
                        }, ensure_ascii=False, indent=2)
                    }],
                    "error": True
                }

            scheduler.write_schedules_to_file(schedules)
            await scheduler.reload_schedules_from_file()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "message": f"ID가 {schedule_id}인 스케줄을 삭제했습니다."
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"스케줄 삭제 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "list_schedules",
    "저장된 활성 스케줄의 목록을 반환합니다. 지난 date 타입 스케줄은 자동으로 제외됩니다. channel_id로 특정 채널의 스케줄만 조회할 수 있습니다.",
    {
        "type": "object",
        "properties": {
            "channel_id": {
                "type": "string",
                "description": "특정 채널의 스케줄만 조회하려면 채널 ID를 지정하세요. 생략하면 모든 채널의 스케줄을 반환합니다."
            }
        }
    }
)
async def scheduler_list_schedules(args: Dict[str, Any]) -> Dict[str, Any]:
    """스케줄 목록 조회 (지난 스케줄 제외, 채널별 필터링 가능)"""
    try:
        from datetime import datetime

        channel_id_filter = args.get("channel_id")
        schedules = scheduler.read_schedules_from_file()

        if not schedules:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "message": "등록된 스케줄이 없습니다.",
                        "schedules": []
                    }, ensure_ascii=False, indent=2)
                }]
            }

        schedule_list = []

        for s in schedules:
            # channel_id 필터링
            if channel_id_filter and s.get("channel") != channel_id_filter:
                continue

            # date 타입이고 이미 지난 스케줄은 제외 (과거 시간인 경우 스키핑)
            if s.get("schedule_type") == "date":
                try:
                    run_date = datetime.fromisoformat(
                        s.get("schedule_value").replace('Z', '+00:00')
                    )
                    if run_date <= datetime.now(run_date.tzinfo):
                        continue  # 지난 스케줄 스킵
                except (ValueError, AttributeError) as e:
                    logging.warning(f"스케줄 ID {s.get('id')} - 잘못된 날짜 형식: {s.get('schedule_value')}, 오류: {e}")
                    continue  # 파싱 실패 시 제외

            schedule_list.append({
                "id": s.get("id"),
                "name": s.get("name"),
                "schedule_type": s.get("schedule_type"),
                "schedule_value": s.get("schedule_value"),
                "user": s.get("user"),
                "channel": s.get("channel"),
                "text": s.get("text"),
                "is_enabled": s.get("is_enabled")
            })

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "message": f"등록된 스케줄: {len(schedule_list)}개",
                    "schedules": schedule_list
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"스케줄 목록 조회 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "update_schedule",
    "기존 스케줄을 업데이트합니다.",
    {
        "type": "object",
        "properties": {
            "schedule_id": {
                "type": "string",
                "description": "업데이트할 스케줄의 ID"
            },
            "name": {
                "type": "string",
                "description": "스케줄의 새 이름 (선택)"
            },
            "schedule_value": {
                "type": "string",
                "description": "새 스케줄 값 (선택)"
            },
            "text": {
                "type": "string",
                "description": "새 메시지 내용 (선택)"
            },
            "is_enabled": {
                "type": "boolean",
                "description": "스케줄 활성화 여부 (선택)"
            }
        },
        "required": ["schedule_id"]
    }
)
async def scheduler_update_schedule(args: Dict[str, Any]) -> Dict[str, Any]:
    """스케줄 업데이트"""
    schedule_id = args["schedule_id"]

    try:
        schedules = scheduler.read_schedules_from_file()
        schedule_found = False

        for s in schedules:
            if s.get("id") == schedule_id:
                schedule_found = True
                # 업데이트할 필드만 변경
                if "name" in args:
                    s["name"] = args["name"]
                if "schedule_value" in args:
                    s["schedule_value"] = args["schedule_value"]
                if "text" in args:
                    s["text"] = args["text"]
                if "is_enabled" in args:
                    s["is_enabled"] = args["is_enabled"]
                break

        if not schedule_found:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": f"ID가 {schedule_id}인 스케줄을 찾을 수 없습니다."
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

        scheduler.write_schedules_to_file(schedules)
        await scheduler.reload_schedules_from_file()

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "message": f"ID가 {schedule_id}인 스케줄을 업데이트했습니다."
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": True,
                    "message": f"스케줄 업데이트 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


# MCP Server 생성
scheduler_tools = [
    scheduler_add_schedule,
    scheduler_remove_schedule,
    scheduler_list_schedules,
    scheduler_update_schedule,
]


def create_scheduler_mcp_server():
    """Claude Code SDK용 Scheduler MCP 서버"""
    return create_sdk_mcp_server(
        name="scheduler",
        version="1.0.0",
        tools=scheduler_tools
    )
