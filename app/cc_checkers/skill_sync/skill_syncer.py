import logging
import re
from app.config.settings import get_settings
from app.cc_utils.skill_parser import parse_skill_md
from app.cc_utils.skill_registry import SkillRegistry


def _extract_folder_id(value: str) -> str:
    """Google Drive URL 또는 폴더 ID에서 폴더 ID만 추출."""
    value = value.strip()
    # URL 형태: https://drive.google.com/drive/folders/FOLDER_ID 또는 FOLDER_ID?usp=sharing
    match = re.search(r"/folders/([a-zA-Z0-9_-]+)", value)
    if match:
        return match.group(1)
    # 이미 ID만 있는 경우 그대로 반환
    return value


async def sync_community_skills():
    """
    Google Drive 스킬 폴더에서 skill.md 파일 동기화.
    60분 간격으로 스케줄러에서 호출됨.
    """
    settings = get_settings()

    if not settings.SKILL_MARKETPLACE_ENABLED:
        return

    if not settings.SKILL_MARKETPLACE_FOLDER_ID:
        logging.warning("[SKILL_SYNC] SKILL_MARKETPLACE_FOLDER_ID not configured")
        return

    folder_id = _extract_folder_id(settings.SKILL_MARKETPLACE_FOLDER_ID)
    if not folder_id:
        logging.warning("[SKILL_SYNC] Could not extract folder ID from SKILL_MARKETPLACE_FOLDER_ID")
        return

    if not settings.GOOGLE_DRIVE_ENABLED:
        logging.warning("[SKILL_SYNC] Google Drive not enabled, skipping skill sync")
        return

    logging.info("[SKILL_SYNC] Starting community skill sync...")
    registry = SkillRegistry()
    synced = 0
    errors = 0

    try:
        # Google Drive API로 폴더 내 skill.md 파일 목록 조회
        from app.cc_tools.google_drive.google_drive_tools import get_drive_service
        service = get_drive_service()

        logging.info(f"[SKILL_SYNC] Using folder ID: {folder_id}")
        results = service.files().list(
            q=f"'{folder_id}' in parents and name contains 'skill' and mimeType='text/plain' and trashed=false",
            fields="files(id, name, modifiedTime)",
            pageSize=100,
        ).execute()

        files = results.get("files", [])
        logging.info(f"[SKILL_SYNC] Found {len(files)} skill files in Drive")

        for file in files:
            file_id = file["id"]
            modified_time = file["modifiedTime"]

            # 업데이트 필요 여부 확인
            if not registry.needs_update(file_id, modified_time):
                continue

            try:
                # 파일 내용 다운로드
                content = service.files().get_media(fileId=file_id).execute()
                if isinstance(content, bytes):
                    content = content.decode("utf-8")

                # 파싱 + 저장
                skill = parse_skill_md(content)
                registry.upsert(skill, source="community", drive_file_id=file_id)
                logging.info(f"[SKILL_SYNC] Synced: {skill.name} v{skill.version} by {skill.author}")
                synced += 1

            except Exception as e:
                logging.error(f"[SKILL_SYNC] Failed to sync {file.get('name', file_id)}: {e}")
                errors += 1

    except Exception as e:
        logging.error(f"[SKILL_SYNC] Drive access failed: {e}")
        return

    logging.info(f"[SKILL_SYNC] Sync complete: {synced} synced, {errors} errors")
