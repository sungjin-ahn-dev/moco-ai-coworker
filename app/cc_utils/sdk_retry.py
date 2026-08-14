"""
ClaudeSDKClient 재시도 래퍼

Windows에서 CLI 프로세스 초기화 타임아웃(Control request timeout: initialize)이
간헐적으로 발생합니다. 이 모듈은 자동 재시도를 통해 안정성을 확보합니다.
"""

import asyncio
import logging

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

logger = logging.getLogger(__name__)


class RetryableSDKClient:
    """재시도 기능이 포함된 ClaudeSDKClient 래퍼."""

    def __init__(
        self,
        options: ClaudeAgentOptions,
        max_retries: int = 3,
        agent_name: str = "AGENT",
    ):
        self.options = options
        self.max_retries = max_retries
        self.agent_name = agent_name
        self._client = None
        self._attempt = 0

    async def __aenter__(self):
        last_error = None
        for attempt in range(self.max_retries):
            self._attempt = attempt
            try:
                self._client = ClaudeSDKClient(options=self.options)
                await self._client.__aenter__()
                return self._client
            except Exception as e:
                last_error = e
                if self._client:
                    try:
                        await self._client.__aexit__(type(e), e, None)
                    except Exception:
                        pass
                    self._client = None

                if attempt < self.max_retries - 1:
                    wait_sec = (attempt + 1) * 2
                    logger.warning(
                        f"[{self.agent_name}] Init attempt {attempt + 1}/{self.max_retries} "
                        f"failed: {e}. Retrying in {wait_sec}s..."
                    )
                    await asyncio.sleep(wait_sec)
                else:
                    logger.error(
                        f"[{self.agent_name}] All {self.max_retries} init attempts failed: {e}"
                    )

        raise last_error

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            await self._client.__aexit__(exc_type, exc_val, exc_tb)
