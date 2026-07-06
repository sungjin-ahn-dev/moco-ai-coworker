"""
생성된 에이전트 .py 파일의 안전성 검증.

3단계:
1. py_compile — 구문 오류 차단 (사실상 템플릿이라 통과 보장)
2. import_isolated — 격리 subprocess 에서 동적 import 시도
3. dry_run — 실제 stream_for_web 짧게 호출 (5초 타임아웃)

각 단계는 독립적으로 실패 가능. 실패 시 명확한 ValidationError 반환.
"""

import asyncio
import logging
import py_compile
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    ok: bool
    stage: str  # "compile" | "import" | "dry_run" | "all"
    message: str
    output: str = ""


def stage_compile(agent_py_path: Path) -> ValidationResult:
    """py_compile 로 구문 검사."""
    try:
        py_compile.compile(str(agent_py_path), doraise=True)
        return ValidationResult(True, "compile", "구문 OK")
    except py_compile.PyCompileError as e:
        return ValidationResult(False, "compile", f"구문 오류: {e.msg}", str(e))


def stage_import_isolated(agent_dir: Path, timeout: float = 15.0) -> ValidationResult:
    """별도 Python subprocess 에서 import 시도. 사이드이펙트로부터 운영 프로세스 보호."""
    # agent_dir 의 부모를 sys.path 에 추가하고 모듈 import 시도
    script = (
        "import sys, importlib.util, traceback\n"
        f"spec = importlib.util.spec_from_file_location('test_agent', {str(agent_dir / 'agent.py')!r})\n"
        "if spec is None or spec.loader is None:\n"
        "    print('SPEC_NONE'); sys.exit(2)\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "try:\n"
        "    spec.loader.exec_module(mod)\n"
        "except Exception as e:\n"
        "    print('IMPORT_FAIL:', repr(e))\n"
        "    traceback.print_exc()\n"
        "    sys.exit(3)\n"
        "if not hasattr(mod, 'stream_for_web'):\n"
        "    print('NO_STREAM_FN'); sys.exit(4)\n"
        "print('IMPORT_OK', getattr(mod, 'AGENT_ID', '?'))\n"
    )
    try:
        # MOCO 가상환경 Python 사용 (claude_agent_sdk 등 의존성 보장)
        venv_py = Path("/home/user/MOCO-main/.venv/bin/python")
        py = str(venv_py) if venv_py.exists() else sys.executable
        result = subprocess.run(
            [py, "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd="/home/user/MOCO-main",
            env={"PYTHONPATH": "/home/user/MOCO-main", "HOME": "/home/acme"},
        )
        if result.returncode == 0 and "IMPORT_OK" in result.stdout:
            return ValidationResult(True, "import", "import OK", result.stdout.strip())
        return ValidationResult(
            False,
            "import",
            f"import 실패 (rc={result.returncode}): {result.stdout[:300]} {result.stderr[:300]}",
            (result.stdout + result.stderr)[:2000],
        )
    except subprocess.TimeoutExpired:
        return ValidationResult(False, "import", f"import {timeout}초 타임아웃")
    except Exception as e:
        return ValidationResult(False, "import", f"import 검사 예외: {e!r}")


async def stage_dry_run(
    streamer_callable,
    user_query: str = "안녕? 짧게 인사만 해줘.",
    user_name: str = "테스터",
    timeout: float = 30.0,
) -> ValidationResult:
    """실제 stream_for_web 호출 — 응답 한 줄 받고 종료. 30초 타임아웃."""
    message_data = {
        "user_name": user_name,
        "user_email": "test@example.com",
        "user_id": "dry_run",
        "channel_id": "DRY_RUN",
        "source": "agent_factory_dry_run",
    }
    chunks = 0
    has_text = False
    last_event = None
    try:
        async def runner():
            nonlocal chunks, has_text, last_event
            async for ev in streamer_callable(user_query, message_data, ""):
                chunks += 1
                last_event = ev
                if ev.get("type") == "text" and ev.get("delta"):
                    has_text = True
                if ev.get("type") in ("done", "error"):
                    break
                # 조기 종료 — 짧게만 확인
                if chunks > 30:
                    break

        await asyncio.wait_for(runner(), timeout=timeout)
        if has_text or (last_event and last_event.get("type") == "done"):
            return ValidationResult(True, "dry_run", f"dry-run OK ({chunks} chunks)")
        return ValidationResult(
            False,
            "dry_run",
            f"응답이 비었음 ({chunks} chunks, last={last_event})",
        )
    except asyncio.TimeoutError:
        return ValidationResult(False, "dry_run", f"dry-run {timeout}초 타임아웃")
    except Exception as e:
        logger.exception("[AGENT_FACTORY] dry_run 예외")
        return ValidationResult(False, "dry_run", f"dry-run 예외: {e!r}")


async def validate_agent_dir(agent_dir: Path, *, skip_dry_run: bool = False) -> ValidationResult:
    """
    한 번에 3단계 검증. dry_run 은 옵션 (개발 환경에선 Claude 호출 비용 발생).

    skip_dry_run=True 면 compile + import 만 (오프라인 친화).
    """
    agent_py = agent_dir / "agent.py"
    if not agent_py.exists():
        return ValidationResult(False, "compile", f"agent.py 없음: {agent_py}")

    r1 = stage_compile(agent_py)
    if not r1.ok:
        return r1

    r2 = stage_import_isolated(agent_dir)
    if not r2.ok:
        return r2

    if skip_dry_run:
        return ValidationResult(True, "all", "compile + import OK (dry-run skipped)")

    # dry_run 은 부모 프로세스에서 — import_isolated 가 이미 격리 검증함
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"agent_factory_dryrun_{agent_dir.name}", str(agent_py))
    if spec is None or spec.loader is None:
        return ValidationResult(False, "dry_run", "spec 로딩 실패")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        return ValidationResult(False, "dry_run", f"dry_run import 실패: {e!r}")

    r3 = await stage_dry_run(mod.stream_for_web)
    if not r3.ok:
        return r3

    return ValidationResult(True, "all", "전체 검증 통과")
