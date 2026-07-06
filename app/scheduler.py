import os
import json
import uuid
import logging
from datetime import datetime
from typing import List, Dict, Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger  # CronTrigger 임포트 추가
from apscheduler.executors.asyncio import AsyncIOExecutor

from app.config.settings import get_settings
from app.queueing_extended import enqueue_message

# 스케줄러 인스턴스 및 설정
# =================================================================
# AsyncIOExecutor로 동시 실행 가능하도록 설정
executors = {
    'default': AsyncIOExecutor()
}
job_defaults = {
    'coalesce': False,  # 누적된 작업을 합치지 않음
    'max_instances': 3,  # 동일 job이 동시에 3개까지 실행 가능
    'misfire_grace_time': 600  # 10분 이내 지연은 허용
}
scheduler = AsyncIOScheduler(executors=executors, job_defaults=job_defaults)
settings = get_settings()
SCHEDULE_DIR = os.path.join(settings.FILESYSTEM_BASE_DIR, "schedule_data")
SCHEDULE_FILE = os.path.join(SCHEDULE_DIR, "schedules.json")


# 내부 파일 I/O 및 스케줄 관리 로직
# =================================================================
def _ensure_dir_and_file():
    os.makedirs(SCHEDULE_DIR, exist_ok=True)
    if not os.path.exists(SCHEDULE_FILE):
        with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)


def read_schedules_from_file() -> List[Dict[str, Any]]:
    _ensure_dir_and_file()
    try:
        with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return []


def write_schedules_to_file(schedules: List[Dict[str, Any]]):
    _ensure_dir_and_file()
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(schedules, f, indent=2, ensure_ascii=False)


async def scheduled_message_wrapper(message: dict, schedule_id: str, schedule_name: str):
    """스케줄된 메시지를 큐에 넣는 래퍼 (로깅 및 오류 처리)."""
    try:
        logging.info(f"[SCHEDULER] 🔔 Executing schedule: [{schedule_name}] (ID: {schedule_id})")
        logging.info(f"[SCHEDULER]   └─ Channel: {message.get('channel')}, User: {message.get('user')}")
        logging.info(f"[SCHEDULER]   └─ Text preview: {message.get('text', '')[:50]}...")

        await enqueue_message(message)

        logging.info(f"[SCHEDULER] ✅ Schedule executed successfully: [{schedule_name}] (ID: {schedule_id})")
    except Exception as e:
        logging.error(f"[SCHEDULER] ❌ Schedule execution failed: [{schedule_name}] (ID: {schedule_id})")
        logging.error(f"[SCHEDULER]   └─ Error: {type(e).__name__}: {e}")


async def reload_schedules_from_file():
    """파일에서 스케줄을 읽어 스케줄러에 다시 로드합니다."""
    try:
        # scheduled_message_wrapper로 등록된 job만 삭제 (체커/suggester는 유지)
        jobs = scheduler.get_jobs()
        for job in jobs:
            if job.func == scheduled_message_wrapper:
                scheduler.remove_job(job.id)
                logging.debug(f"[SCHEDULER] Removed existing schedule job: {job.name} (ID: {job.id})")
    except Exception as e:
        logging.warning(f"기존 스케줄 삭제 중 오류 발생 (첫 실행 시 정상): {e}")

    schedules = read_schedules_from_file()
    count = 0
    for schedule in schedules:
        if not schedule.get("is_enabled"):
            continue

        try:
            # Add user_id to the message payload
            message = {
                "user": schedule.get("user"),
                "text": schedule.get("text"),
                "channel": schedule.get("channel"),
            }
            schedule_type = schedule.get("schedule_type")
            schedule_value = schedule.get("schedule_value")
            schedule_id = schedule.get("id")
            schedule_name = schedule.get("name")

            job_args = {
                "id": schedule_id,
                "name": schedule_name,
                "args": [message, schedule_id, schedule_name],  # 래퍼에 ID와 이름 전달
            }

            if schedule_type == "cron":
                scheduler.add_job(
                    scheduled_message_wrapper,  # 래퍼 함수 사용
                    trigger=CronTrigger.from_crontab(schedule_value),
                    replace_existing=True,
                    **job_args,
                )
                logging.info(f"[SCHEDULER] 📅 Registered cron schedule: [{schedule_name}] (ID: {schedule_id}), pattern: {schedule_value}")
            elif schedule_type == "date":
                # 과거 시간인 경우 스키핑
                try:
                    run_date = datetime.fromisoformat(schedule_value.replace('Z', '+00:00'))
                    if run_date <= datetime.now(run_date.tzinfo):
                        logging.info(f"[SCHEDULER] ⏭️  Skipping past schedule: [{schedule_name}] (ID: {schedule_id}), time: {schedule_value}")
                        continue
                except (ValueError, AttributeError) as e:
                    logging.error(f"[SCHEDULER] ❌ Invalid date format: [{schedule_name}] (ID: {schedule_id}), value: {schedule_value}, error: {e}")
                    continue

                scheduler.add_job(
                    scheduled_message_wrapper,  # 래퍼 함수 사용
                    trigger="date",
                    run_date=schedule_value,
                    replace_existing=True,
                    **job_args
                )
                logging.info(f"[SCHEDULER] 📅 Registered one-time schedule: [{schedule_name}] (ID: {schedule_id}), time: {schedule_value}")

            count += 1
        except Exception as e:
            logging.error(f"[SCHEDULER] ❌ Failed to register schedule: [{schedule.get('name')}] (ID: {schedule.get('id')}), error: {e}")
    logging.info(f"총 {count}개의 스케줄을 성공적으로 리로드했습니다.")
