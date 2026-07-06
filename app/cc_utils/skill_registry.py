import sqlite3
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from app.config.settings import get_settings
from app.cc_utils.skill_parser import Skill

def get_db_path() -> Path:
    settings = get_settings()
    base_dir = settings.FILESYSTEM_BASE_DIR or os.getcwd()
    db_dir = Path(base_dir) / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "skill_registry.db"

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            id              TEXT PRIMARY KEY,
            name            TEXT,
            version         TEXT,
            author          TEXT,
            description     TEXT,
            required_mcps   TEXT,   -- JSON array
            optional_mcps   TEXT,   -- JSON array
            tags            TEXT,   -- JSON array
            trigger_keywords TEXT,  -- JSON array
            model           TEXT,
            system_prompt   TEXT,
            source          TEXT,   -- "local" | "community"
            drive_file_id   TEXT,
            last_synced     DATETIME,
            is_active       INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    conn.close()

class SkillRegistry:
    """스킬 저장소 CRUD"""

    def upsert(self, skill: Skill, source: str = "community", drive_file_id: str = "") -> None:
        conn = get_connection()
        conn.execute("""
            INSERT OR REPLACE INTO skills
            (id, name, version, author, description, required_mcps, optional_mcps,
             tags, trigger_keywords, model, system_prompt, source, drive_file_id, last_synced, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            skill.name, skill.name, skill.version, skill.author, skill.description,
            json.dumps(skill.required_mcps, ensure_ascii=False),
            json.dumps(skill.optional_mcps, ensure_ascii=False),
            json.dumps(skill.tags, ensure_ascii=False),
            json.dumps(skill.trigger_keywords, ensure_ascii=False),
            skill.model, skill.system_prompt, source, drive_file_id,
            datetime.now().isoformat()
        ))
        conn.commit()
        conn.close()

    def get(self, skill_id: str) -> Optional[Dict[str, Any]]:
        conn = get_connection()
        row = conn.execute("SELECT * FROM skills WHERE id=? AND is_active=1", (skill_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_all_active(self) -> List[Dict[str, Any]]:
        conn = get_connection()
        rows = conn.execute("SELECT * FROM skills WHERE is_active=1 ORDER BY name").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def needs_update(self, drive_file_id: str, modified_time: str) -> bool:
        """Google Drive 파일이 업데이트가 필요한지 확인"""
        conn = get_connection()
        row = conn.execute(
            "SELECT last_synced FROM skills WHERE drive_file_id=?", (drive_file_id,)
        ).fetchone()
        conn.close()
        if not row:
            return True
        last_synced = row["last_synced"]
        return modified_time > last_synced

    def get_all_as_prompt(self) -> str:
        """Orchestrator 시스템 프롬프트용 스킬 목록 문자열 생성"""
        skills = self.get_all_active()
        if not skills:
            return "등록된 커뮤니티 스킬이 없습니다."
        lines = []
        for s in skills:
            keywords = json.loads(s.get("trigger_keywords", "[]"))
            lines.append(f"- **{s['id']}**: {s['description']} (키워드: {', '.join(keywords)})")
        return "\n".join(lines)

    def deactivate(self, skill_id: str) -> None:
        conn = get_connection()
        conn.execute("UPDATE skills SET is_active=0 WHERE id=?", (skill_id,))
        conn.commit()
        conn.close()


def load_local_skills() -> int:
    """
    .claude/skills/ 디렉토리의 로컬 스킬을 레지스트리에 등록합니다.
    서버 시작 시 호출됩니다.

    Returns:
        int: 등록된 스킬 수
    """
    from app.cc_utils.skill_parser import parse_skill_md

    skills_dir = Path(os.getcwd()) / ".claude" / "skills"
    if not skills_dir.exists():
        logging.info("[SKILL_LOADER] No .claude/skills/ directory found")
        return 0

    registry = SkillRegistry()
    count = 0

    for skill_dir in skills_dir.iterdir():
        if not skill_dir.is_dir():
            continue

        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue

        try:
            content = skill_file.read_text(encoding="utf-8")
            skill = parse_skill_md(content)
            registry.upsert(skill, source="local")
            count += 1
            logging.debug(f"[SKILL_LOADER] Loaded local skill: {skill.name}")
        except Exception as e:
            logging.warning(f"[SKILL_LOADER] Failed to load {skill_file}: {e}")

    logging.info(f"[SKILL_LOADER] Loaded {count} local skills from .claude/skills/")
    return count
