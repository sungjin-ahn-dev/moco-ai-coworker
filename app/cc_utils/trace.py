"""
trace — 요청 단위 trace_id 전파 + 구조화 로깅 (관측성). contextvars 기반.

현재 오케스트레이터/서브에이전트 로그는 동시 처리되는 여러 요청이 한 로그
스트림에 뒤섞여, 특정 요청 하나의 흐름(메모리 검색 → orchestrator → 도구 호출
→ 응답)을 사후에 이어붙이기 어렵다. 이 모듈은 요청 진입부에서 짧은 trace_id 를
contextvars 로 바인딩하고, 로깅 필터로 모든 로그 레코드에 그 id 를 주입한다.

왜 opt-in·무회귀인가:
- 순수 유틸이며 import 만으로는 아무 부작용이 없다. install_trace_logging() 을
  명시적으로 호출한 경우에만 로깅 파이프라인에 붙는다.
- contextvars 는 async task/thread 경계에서 격리되므로 요청 간 값이 새지 않는다.
- 바인딩이 없을 때 get_trace_id() 는 '-' 를 돌려주므로, 배선 전 로그 포맷에
  {trace_id} 를 참조하더라도 KeyError 없이 그대로 동작한다.
- install_trace_logging() 은 예외를 삼켜(안전), 로깅 설정이 특이해도 앱을 죽이지
  않는다. 실패 시 단순히 트레이싱만 비활성(회귀 없음).

외부 의존(MCP/LLM/네트워크) 없음 — 표준 라이브러리(contextvars/logging/uuid)만 사용.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator

# 미바인딩 기본값. 로그 포맷의 {trace_id} 가 항상 무언가를 갖도록 '-' 로.
_UNSET = "-"

_trace_id: ContextVar[str] = ContextVar("moco_trace_id", default=_UNSET)


def new_trace_id() -> str:
    """새 짧은 trace_id(8-char hex)를 생성. 요청 하나를 식별할 정도면 충분."""
    return uuid.uuid4().hex[:8]


def get_trace_id() -> str:
    """현재 컨텍스트의 trace_id. 바인딩 전이면 '-'."""
    return _trace_id.get()


def bind_trace_id(tid: str) -> Token:
    """trace_id 를 현재 컨텍스트에 바인딩하고 reset 용 토큰을 반환."""
    return _trace_id.set(tid)


def reset_trace_id(token: Token) -> None:
    """bind_trace_id 가 준 토큰으로 직전 값(중첩 포함)을 복원."""
    _trace_id.reset(token)


@contextmanager
def new_trace(prefix: str = "") -> Iterator[str]:
    """새 trace_id 를 바인딩하는 컨텍스트 매니저.

    진입 시 새 id 를 만들어 바인딩하고 그 id 를 yield 한다. prefix 가 주어지면
    id 앞에 붙여 사람이 흐름을 구분하기 쉽게 한다(예: prefix='orch-').
    종료 시 반드시 이전 값으로 복원하므로 중첩해도 바깥 값이 안전하게 되살아난다.
    """
    tid = f"{prefix}{new_trace_id()}" if prefix else new_trace_id()
    token = bind_trace_id(tid)
    try:
        yield tid
    finally:
        reset_trace_id(token)


class TraceFilter(logging.Filter):
    """로그 레코드에 record.trace_id 를 주입하는 필터(레코드를 막지 않음)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()
        return True


def install_trace_logging(logger: logging.Logger | None = None) -> int:
    """대상 로거(기본: 루트)의 모든 핸들러에 TraceFilter 를 부착.

    - 이미 TraceFilter 가 달린 핸들러는 건너뛰어(멱등) 중복 주입을 막는다.
    - 어떤 이유로든 실패하면 예외를 삼키고 0 을 반환(무회귀·안전).
    - 부착한 핸들러 수를 반환(테스트/진단용).

    핸들러에 부착하는 이유: 필터를 로거에 달면 자식 로거에서 propagate 된
    레코드에는 적용되지 않는다. 핸들러에 달아야 그 핸들러가 처리하는 모든
    레코드(직접·전파 무관)에 trace_id 가 찍힌다.
    """
    try:
        target = logger if logger is not None else logging.getLogger()
        count = 0
        for handler in list(target.handlers):
            if any(isinstance(f, TraceFilter) for f in handler.filters):
                continue
            handler.addFilter(TraceFilter())
            count += 1
        return count
    except Exception:  # 로깅 설정이 특이해도 앱을 죽이지 않음
        return 0
