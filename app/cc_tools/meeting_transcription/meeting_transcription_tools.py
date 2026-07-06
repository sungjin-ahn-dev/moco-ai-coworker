"""
Meeting Tools for Claude Code SDK
회의 음성 파일 관리 및 텍스트 변환 도구
"""

import json
from typing import Any, Dict
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

from app.cc_utils.clova_helper import convert_speech_to_text_with_speakers
from app.config.settings import get_settings


@tool(
    "list_meeting_files",
    "특정 날짜의 회의/미팅 음성 파일 목록을 조회합니다. 회의록 작성을 위해 사용합니다.",
    {
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "조회할 날짜 (YYYYMMDD 형식, 예: 20250128)"
            }
        },
        "required": ["date"]
    }
)
async def list_meeting_files(args: Dict[str, Any]) -> Dict[str, Any]:
    """특정 날짜의 회의 파일 목록 조회"""
    date = args["date"]

    try:
        settings = get_settings()

        # 날짜 폴더 경로
        meetings_dir = Path(settings.FILESYSTEM_BASE_DIR) / "meetings" / date

        if not meetings_dir.exists():
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": True,
                        "date": date,
                        "files": [],
                        "message": f"{date} 날짜의 회의 파일이 없습니다."
                    }, ensure_ascii=False, indent=2)
                }]
            }

        # 모든 파일 리스트 가져오기
        files = []
        for file_path in sorted(meetings_dir.iterdir()):
            if file_path.is_file():
                files.append({
                    "filename": file_path.name,
                    "path": f"meetings/{date}/{file_path.name}",
                    "size_mb": round(file_path.stat().st_size / 1024 / 1024, 2),
                    "extension": file_path.suffix
                })

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "date": date,
                    "folder_path": str(meetings_dir),
                    "total_files": len(files),
                    "files": files,
                    "message": f"{date} 날짜의 회의 파일 {len(files)}개 조회 완료"
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
                    "message": f"파일 목록 조회 중 오류 발생: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


@tool(
    "transcribe_meeting",
    "회의/미팅 음성 파일에서 화자 구분이 포함된 텍스트를 추출합니다. 회의록 작성을 위해 사용합니다.",
    {
        "type": "object",
        "properties": {
            "audio_file_path": {
                "type": "string",
                "description": "변환할 음성 파일의 경로 (절대 경로 또는 상대 경로, 예: meetings/20250128/meeting_20250128_120000.webm)"
            }
        },
        "required": ["audio_file_path"]
    }
)
async def transcribe_meeting(args: Dict[str, Any]) -> Dict[str, Any]:
    """회의 음성 파일을 텍스트로 변환 (화자 구분 포함)"""
    audio_file_path = args["audio_file_path"]

    try:
        settings = get_settings()

        # 파일 경로 처리 (상대 경로면 FILESYSTEM_BASE_DIR 기준으로 변환)
        file_path = Path(audio_file_path)
        if not file_path.is_absolute():
            file_path = Path(settings.FILESYSTEM_BASE_DIR) / audio_file_path

        # 파일 존재 확인
        if not file_path.exists():
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "success": False,
                        "error": True,
                        "message": f"파일을 찾을 수 없습니다: {audio_file_path}"
                    }, ensure_ascii=False, indent=2)
                }],
                "error": True
            }

        # Clova로 음성 변환
        transcript = await convert_speech_to_text_with_speakers(str(file_path))

        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "success": True,
                    "file_path": str(file_path),
                    "transcript": transcript,
                    "message": "회의 음성 변환 완료"
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
                    "message": f"음성 변환 중 오류 발생: {str(e)}"
                }, ensure_ascii=False, indent=2)
            }],
            "error": True
        }


# MCP Server 생성
meetings_tools = [
    list_meeting_files,
    transcribe_meeting,
]


def create_meetings_mcp_server():
    """Claude Code SDK용 회의록 작성 MCP 서버"""
    return create_sdk_mcp_server(
        name="meeting_transcription",
        version="1.0.0",
        tools=meetings_tools
    )
