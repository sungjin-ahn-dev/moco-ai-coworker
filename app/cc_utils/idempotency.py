"""
idempotency — 부작용 도구(메일 전송 등)의 중복 실행 방지(멱등성).

왜 필요한가:
    오케스트레이터/재시도 루프(예: reflexion 재시도, 데몬 재처리)나 사용자의
    중복 요청으로 같은 메일이 두 번 발송되는 사고를 막아야 한다. 메일 전송처럼
    외부에 관측 가능한 부작용을 내는 도구는 "같은 입력 → 한 번만 실행"이 보장돼야
    안전하다.

설계:
    - (도구이름 + 정규화된 인자)로 안정적인 키를 만든다(make_key). 인자 dict 의
      순서가 달라도 같은 키가 나오도록 sort_keys=True 로 직렬화한다.
    - 파일 백엔드 append-only JSONL 로그에 (key, result) 를 기록한다. 프로세스가
      재시작해도 로그를 다시 읽어(load) 이전 실행 결과를 캐시로 복원한다.
    - threading.RLock 으로 다중 스레드 접근을 직렬화한다.

무회귀·opt-in:
    이 모듈은 순수 라이브러리라 import 만으로는 아무 동작도 바꾸지 않는다. 외부
    의존(MCP/LLM/네트워크)이 전혀 없고, 실제 도구 실행은 호출자가 넘긴
    coro_factory(콜백)를 통해서만 일어난다. 기존 도구에는 settings 플래그가 켜졌을
    때만(getattr(settings, 'IDEMPOTENCY_ENABLED', False)) once() 로 감싸 배선한다.
    플래그가 꺼져 있으면 도구는 종전과 100% 동일하게 동작한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Any, Awaitable, Callable, Dict, Optional


def make_key(tool_name: str, args: Dict[str, Any]) -> str:
    """도구이름 + 정규화 인자로 안정적인 멱등 키(sha1 hexdigest)를 만든다.

    args 의 키 순서에 무관하게 동일한 키가 나오도록 sort_keys=True 로 직렬화한다.
    한글 등 비 ASCII 값도 그대로 반영하려고 ensure_ascii=False 를 쓴다.
    """
    payload = tool_name + json.dumps(
        args, sort_keys=True, ensure_ascii=False, default=str
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


class IdempotencyStore:
    """파일(JSONL) 백엔드 멱등 저장소.

    각 줄은 {"key": <str>, "result": <json>} 형식의 append-only 로그다.
    같은 key 가 여러 번 기록되면 나중 값이 이긴다(last-write-wins).
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._cache: Dict[str, Any] = {}
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._load()

    # -- 내부 --------------------------------------------------------------
    def _load(self) -> None:
        """기존 로그 파일을 읽어 캐시를 복원한다(재오픈 시 이전 결과 유지)."""
        if not os.path.exists(self.path):
            return
        with self._lock:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except (ValueError, json.JSONDecodeError):
                        # 손상된/부분 기록 줄은 건너뛴다(무회귀).
                        continue
                    key = rec.get("key")
                    if key is not None:
                        self._cache[key] = rec.get("result")

    # -- 공개 API ----------------------------------------------------------
    def seen(self, key: str) -> Optional[Any]:
        """key 가 기록돼 있으면 저장된 result 를, 없으면 None 을 돌려준다.

        result 자체가 None 인 경우와 '미기록'을 구분하기 위해 캐시 멤버십으로
        판단한다(포함돼 있으면 저장된 값을 그대로 반환).
        """
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            return None

    def record(self, key: str, result: Any) -> None:
        """(key, result) 를 로그에 append 하고 캐시를 갱신한다."""
        with self._lock:
            line = json.dumps(
                {"key": key, "result": result},
                ensure_ascii=False,
                default=str,
            )
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            self._cache[key] = result

    async def once(
        self, key: str, coro_factory: Callable[[], Awaitable[Any]]
    ) -> Any:
        """멱등 실행: 이미 본 key 면 캐시를, 아니면 실행 후 기록하고 반환.

        - 이미 기록된 key: coro_factory 를 절대 호출하지 않고 캐시 결과 반환.
        - 처음 보는 key: await coro_factory() 로 실제 부작용을 1회 실행하고,
          그 결과를 record 한 뒤 반환한다.

        coro_factory 는 인자 없이 호출되어 awaitable 을 돌려주는 콜백이다
        (지연 실행). 외부 의존은 전부 이 콜백 안에 캡슐화된다.
        """
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        result = await coro_factory()
        self.record(key, result)
        return result
