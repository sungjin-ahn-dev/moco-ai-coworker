"""
Cloudflare Quick Tunnel (TryCloudflare) 관리.

`cloudflared tunnel --url http://localhost:<port>` 를 서브프로세스로 실행하여
로컬 웹 서버를 외부에 공개. 매 실행마다 임시 https://*.trycloudflare.com URL이
발급되며, Cloudflare 계정/도메인은 불필요하다.

OAuth 콜백 URL은 매 재시작마다 바뀌므로, Google/MS365/Slack OAuth 앱 콘솔에
새 URL을 등록해야 외부 사용자가 로그인할 수 있다.
"""

import asyncio
import logging
import re
import shutil
from typing import Optional

logger = logging.getLogger(__name__)

_TUNNEL_URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


class CloudflareQuickTunnel:
    def __init__(self, local_port: int, use_https: bool = False, binary: str = "cloudflared"):
        self.local_port = local_port
        self.use_https = use_https
        self.binary = binary
        self.process: Optional[asyncio.subprocess.Process] = None
        self.public_url: Optional[str] = None
        self._url_ready = asyncio.Event()
        self._reader_task: Optional[asyncio.Task] = None

    async def start(self, wait_for_url_timeout: float = 30.0) -> Optional[str]:
        if shutil.which(self.binary) is None:
            logger.warning(
                "[CLOUDFLARE_TUNNEL] '%s' not found in PATH. Skipping tunnel setup. "
                "Install: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/",
                self.binary,
            )
            return None

        scheme = "https" if self.use_https else "http"
        local_url = f"{scheme}://localhost:{self.local_port}"
        cmd = [self.binary, "tunnel", "--no-autoupdate", "--url", local_url]
        if self.use_https:
            cmd.append("--no-tls-verify")
        logger.info("[CLOUDFLARE_TUNNEL] Starting quick tunnel: %s", " ".join(cmd))

        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception as e:
            logger.warning("[CLOUDFLARE_TUNNEL] Failed to spawn cloudflared: %s", e)
            return None

        self._reader_task = asyncio.create_task(self._read_output())

        try:
            await asyncio.wait_for(self._url_ready.wait(), timeout=wait_for_url_timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "[CLOUDFLARE_TUNNEL] URL not captured within %ss. Tunnel may come up shortly.",
                wait_for_url_timeout,
            )
        return self.public_url

    async def _read_output(self):
        assert self.process and self.process.stdout
        try:
            async for raw in self.process.stdout:
                text = raw.decode(errors="replace").rstrip()
                if not text:
                    continue
                logger.debug("[CLOUDFLARE_TUNNEL] %s", text)
                if not self._url_ready.is_set():
                    m = _TUNNEL_URL_RE.search(text)
                    if m:
                        self.public_url = m.group(0)
                        self._url_ready.set()
                        logger.info("=" * 70)
                        logger.info("[CLOUDFLARE_TUNNEL] Public URL: %s", self.public_url)
                        logger.info("[CLOUDFLARE_TUNNEL]    Chat: %s/chat", self.public_url)
                        logger.info("[CLOUDFLARE_TUNNEL]    CRM:  %s/crm", self.public_url)
                        logger.info("=" * 70)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[CLOUDFLARE_TUNNEL] Output reader error: %s", e)

    async def stop(self):
        if self.process and self.process.returncode is None:
            logger.info("[CLOUDFLARE_TUNNEL] Stopping tunnel...")
            try:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            except ProcessLookupError:
                pass
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        logger.info("[CLOUDFLARE_TUNNEL] Tunnel stopped")
