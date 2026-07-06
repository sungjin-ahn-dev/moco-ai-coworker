"""
Files Tools for Claude Code SDK
파일 저장/변환 관련 도구
"""

import base64
import json
import os
from pathlib import Path
from typing import Any, Dict

from claude_agent_sdk import create_sdk_mcp_server, tool

from app.config.settings import get_settings


def get_base_dir() -> Path:
    """파일 저장 기본 디렉토리 반환"""
    settings = get_settings()
    base_dir = settings.FILESYSTEM_BASE_DIR
    if not base_dir:
        base_dir = os.path.expanduser("~/Documents/MOCO")
    return Path(base_dir)


@tool(
    "save_base64_image",
    "base64로 인코딩된 이미지 데이터를 파일로 저장합니다. Tableau 등에서 받은 이미지를 저장할 때 사용하세요.",
    {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "저장할 파일 경로 (예: files/C12345/dashboard.png). FILESYSTEM_BASE_DIR 기준 상대경로 또는 절대경로"
            },
            "base64_data": {
                "type": "string",
                "description": "base64로 인코딩된 이미지 데이터 (data:image/png;base64, 접두사 있어도 됨)"
            }
        },
        "required": ["file_path", "base64_data"]
    }
)
async def save_base64_image(args: Dict[str, Any]) -> Dict[str, Any]:
    """base64 이미지를 파일로 저장"""
    file_path = args["file_path"]
    base64_data = args["base64_data"]

    try:
        # data:image/png;base64, 접두사 제거
        if "," in base64_data:
            base64_data = base64_data.split(",", 1)[1]

        # base64 디코딩
        image_data = base64.b64decode(base64_data)

        # 경로 처리
        if not os.path.isabs(file_path):
            full_path = get_base_dir() / file_path
        else:
            full_path = Path(file_path)

        # 디렉토리 생성
        full_path.parent.mkdir(parents=True, exist_ok=True)

        # 파일 저장
        with open(full_path, "wb") as f:
            f.write(image_data)

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "message": f"이미지 저장 완료",
                    "path": str(full_path),
                    "size_bytes": len(image_data)
                }, ensure_ascii=False, indent=2)
            }]
        }

    except base64.binascii.Error as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": f"base64 디코딩 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "isError": True
        }
    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": f"파일 저장 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "isError": True
        }


@tool(
    "read_file_as_base64",
    "파일을 읽어서 base64로 인코딩합니다. 이미지 파일을 Slack에 업로드하기 전 등에 사용하세요.",
    {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "읽을 파일 경로. FILESYSTEM_BASE_DIR 기준 상대경로 또는 절대경로"
            }
        },
        "required": ["file_path"]
    }
)
async def read_file_as_base64(args: Dict[str, Any]) -> Dict[str, Any]:
    """파일을 base64로 읽기"""
    file_path = args["file_path"]

    try:
        # 경로 처리
        if not os.path.isabs(file_path):
            full_path = get_base_dir() / file_path
        else:
            full_path = Path(file_path)

        if not full_path.exists():
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": f"파일을 찾을 수 없습니다: {full_path}"
                    }, ensure_ascii=False, indent=2)
                }],
                "isError": True
            }

        # 파일 읽기 및 base64 인코딩
        with open(full_path, "rb") as f:
            file_data = f.read()

        base64_data = base64.b64encode(file_data).decode("utf-8")

        # MIME 타입 추정
        suffix = full_path.suffix.lower()
        mime_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".pdf": "application/pdf",
        }
        mime_type = mime_types.get(suffix, "application/octet-stream")

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "path": str(full_path),
                    "size_bytes": len(file_data),
                    "mime_type": mime_type,
                    "base64_data": base64_data
                }, ensure_ascii=False, indent=2)
            }]
        }

    except Exception as e:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": False,
                    "error": f"파일 읽기 실패: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "isError": True
        }


@tool(
    "list_skills",
    "등록된 스킬 목록을 조회합니다. 각 스킬의 이름, 설명, 트리거를 반환합니다.",
    {"type": "object", "properties": {}, "required": []}
)
async def list_skills(args: Dict[str, Any]) -> Dict[str, Any]:
    """스킬 목록 조회"""
    import re
    skills_dir = Path(os.getcwd()) / ".claude" / "skills"
    if not skills_dir.exists():
        return {"content": [{"type": "text", "text": "스킬 디렉토리가 없습니다."}]}

    skills = []
    for skill_dir in sorted(skills_dir.iterdir()):
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            content = skill_md.read_text(encoding="utf-8")
            # frontmatter 파싱
            name = skill_dir.name
            desc = ""
            fm_match = re.search(r"---\s*\n(.*?)\n---", content, re.DOTALL)
            if fm_match:
                fm = fm_match.group(1)
                name_match = re.search(r"name:\s*(.+)", fm)
                desc_match = re.search(r'description:\s*"?(.+?)"?\s*$', fm, re.MULTILINE)
                if name_match:
                    name = name_match.group(1).strip()
                if desc_match:
                    desc = desc_match.group(1).strip()

            skills.append({"name": name, "description": desc, "path": str(skill_dir.name)})

    result = f"등록된 스킬: {len(skills)}개\n\n"
    for s in skills:
        result += f"• **{s['name']}** — {s['description'][:80]}\n"

    return {"content": [{"type": "text", "text": result}]}


@tool(
    "create_skill",
    "새 스킬을 생성합니다. 반복 업무를 SKILL.md로 저장하여 다음에 재사용합니다.",
    {
        "type": "object",
        "properties": {
            "skill_name": {"type": "string", "description": "스킬 이름 (영문 kebab-case, 예: weekly-report)"},
            "skill_content": {"type": "string", "description": "SKILL.md 전체 내용 (frontmatter + 본문)"},
        },
        "required": ["skill_name", "skill_content"]
    }
)
async def create_skill(args: Dict[str, Any]) -> Dict[str, Any]:
    """스킬 생성"""
    import logging
    name = args["skill_name"]
    content = args["skill_content"]

    skills_dir = Path(os.getcwd()) / ".claude" / "skills" / name
    skills_dir.mkdir(parents=True, exist_ok=True)

    skill_file = skills_dir / "SKILL.md"
    skill_file.write_text(content, encoding="utf-8")

    logging.info(f"[SKILL_CREATOR] Skill created: {name} at {skill_file}")

    return {"content": [{"type": "text", "text": f"스킬 '{name}'이 생성되었습니다.\n경로: .claude/skills/{name}/SKILL.md\n\n다음에 관련 요청을 하면 이 스킬이 자동으로 로드됩니다."}]}


# 도구 등록
files_tools = [save_base64_image, read_file_as_base64, list_skills, create_skill]


def create_files_mcp_server():
    """Claude Code SDK Files MCP server"""
    return create_sdk_mcp_server(name="files-tools", version="1.0.0", tools=files_tools)
