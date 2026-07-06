"""
Windows CLI 명령줄 길이 제한 우회 헬퍼

Windows의 cmd.exe는 명령줄 최대 길이가 8,191자입니다.
claude.cmd를 통해 실행될 때 --system-prompt 인자가 이 제한을 초과할 수 있습니다.

이 모듈은 system_prompt를 임시 디렉토리의 CLAUDE.md 파일로 분리하여
CLI 명령줄 길이 문제를 우회합니다.

Non-Windows에서는 아무 변환 없이 원본 options를 그대로 반환합니다.
"""

import atexit
import logging
import os
import shutil
import sys
import tempfile
from dataclasses import replace

from claude_agent_sdk import ClaudeAgentOptions

logger = logging.getLogger(__name__)

# 임시 디렉토리 추적 (프로세스 종료 시 정리)
_temp_dirs: list[str] = []


def _cleanup_temp_dirs():
    """프로세스 종료 시 임시 디렉토리 정리"""
    for d in _temp_dirs:
        try:
            # Windows junction은 rmtree 전에 먼저 제거 (원본 보호)
            claude_dir = os.path.join(d, ".claude")
            if sys.platform == "win32" and os.path.isdir(claude_dir):
                try:
                    # junction이면 rmdir로 링크만 제거 (원본 보호)
                    os.rmdir(claude_dir)
                except OSError:
                    pass
            shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass


atexit.register(_cleanup_temp_dirs)


def prepare_options(options: ClaudeAgentOptions) -> ClaudeAgentOptions:
    """Windows에서 system_prompt를 CLAUDE.md 파일로 분리하여 CLI 길이 제한을 우회합니다.

    Windows가 아니거나 system_prompt가 짧으면 원본 options를 그대로 반환합니다.

    Args:
        options: 원본 ClaudeAgentOptions

    Returns:
        ClaudeAgentOptions: Windows에서는 system_prompt=None, cwd=temp_dir로 변환된 options
    """
    if sys.platform != "win32":
        return options

    if not options.system_prompt or not isinstance(options.system_prompt, str):
        return options

    # Windows CLI: system_prompt를 항상 CLAUDE.md 파일로 전달
    # CLI 인자 방식은 cmd.exe 8191자 제한 + 초기화 지연 문제 발생
    # 짧은 프롬프트(500자 미만)만 CLI 인자로 허용
    if len(options.system_prompt) < 500:
        return options

    # 임시 디렉토리 생성
    temp_dir = tempfile.mkdtemp(prefix="eco_agent_")
    _temp_dirs.append(temp_dir)

    # system_prompt를 CLAUDE.md로 저장
    claude_md_path = os.path.join(temp_dir, "CLAUDE.md")
    with open(claude_md_path, "w", encoding="utf-8") as f:
        f.write(options.system_prompt)

    # 원본 cwd의 .claude 디렉토리를 임시 디렉토리에 연결 (스킬/설정 접근 유지)
    original_cwd = str(options.cwd) if options.cwd else os.getcwd()
    original_claude_dir = os.path.join(original_cwd, ".claude")
    if os.path.isdir(original_claude_dir):
        temp_claude_dir = os.path.join(temp_dir, ".claude")
        try:
            # Windows: junction (관리자 권한 불필요)
            import subprocess
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", temp_claude_dir, original_claude_dir],
                capture_output=True, timeout=5
            )
            if not os.path.isdir(temp_claude_dir):
                # junction 실패 시 폴백: 디렉토리 복사
                shutil.copytree(original_claude_dir, temp_claude_dir)
        except Exception:
            try:
                shutil.copytree(original_claude_dir, temp_claude_dir)
            except Exception as copy_err:
                logger.warning(f"[PROMPT_HELPER] Failed to link .claude dir: {copy_err}")

    # 원본 cwd를 add_dirs로 보존 (파일 접근 유지)
    add_dirs = list(options.add_dirs or [])
    if original_cwd not in [str(d) for d in add_dirs]:
        add_dirs.append(original_cwd)

    # 'project' setting source 보장 (CLAUDE.md 읽기 위해)
    sources = list(options.setting_sources or [])
    if "project" not in sources:
        sources.append("project")

    logger.info(
        f"[PROMPT_HELPER] System prompt ({len(options.system_prompt)} chars) "
        f"-> CLAUDE.md in {temp_dir}"
    )

    return replace(
        options,
        system_prompt=None,
        cwd=temp_dir,
        add_dirs=add_dirs,
        setting_sources=sources,
    )
