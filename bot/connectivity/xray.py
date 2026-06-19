from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from typing import Optional

from bot.connectivity.links import build_xray_config, parse_share_link

logger = logging.getLogger(__name__)


class XrayStartError(Exception):
    """Raised when the local xray-core process cannot be started."""


class XrayConnection:
    """Launches xray-core from a share link and exposes a local SOCKS5 proxy.

    Telegram traffic is then routed through `socks5://127.0.0.1:<port>`.
    """

    def __init__(
        self,
        share_link: str,
        socks_port: int = 10808,
        xray_bin: str = "xray",
    ) -> None:
        self.share_link = share_link
        self.socks_port = socks_port
        self.xray_bin = xray_bin
        self._process: Optional[asyncio.subprocess.Process] = None
        self._config_path: Optional[str] = None

    @property
    def proxy_url(self) -> str:
        return f"socks5://127.0.0.1:{self.socks_port}"

    def _resolve_binary(self) -> str:
        path = shutil.which(self.xray_bin) or (
            self.xray_bin if os.path.isfile(self.xray_bin) else None
        )
        if not path:
            raise XrayStartError(
                f"xray binary not found ('{self.xray_bin}'). Install xray-core and/or "
                "set XRAY_BIN to its full path."
            )
        return path

    async def _wait_until_ready(self, timeout: float = 12.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        last_err: Optional[Exception] = None
        while asyncio.get_event_loop().time() < deadline:
            if self._process and self._process.returncode is not None:
                raise XrayStartError(
                    f"xray exited early with code {self._process.returncode}"
                )
            try:
                reader, writer = await asyncio.open_connection(
                    "127.0.0.1", self.socks_port
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:  # noqa: BLE001
                    pass
                return
            except OSError as exc:
                last_err = exc
                await asyncio.sleep(0.3)
        raise XrayStartError(
            f"xray SOCKS port {self.socks_port} did not become ready: {last_err}"
        )

    async def start(self) -> str:
        """Parse the link, write config, spawn xray, wait for the SOCKS port."""
        binary = self._resolve_binary()
        outbound = parse_share_link(self.share_link)
        config = build_xray_config(outbound, self.socks_port)

        fd, path = tempfile.mkstemp(prefix="xray-tg-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(config, fh)
        self._config_path = path
        logger.info(
            "Starting xray-core (%s) -> SOCKS 127.0.0.1:%s",
            outbound.get("protocol"),
            self.socks_port,
        )

        self._process = await asyncio.create_subprocess_exec(
            binary,
            "run",
            "-c",
            path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await self._wait_until_ready()
        except XrayStartError:
            await self.stop()
            raise
        logger.info("xray-core ready on %s", self.proxy_url)
        return self.proxy_url

    async def stop(self) -> None:
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self._process.kill()
            except ProcessLookupError:
                pass
        self._process = None
        if self._config_path and os.path.exists(self._config_path):
            try:
                os.remove(self._config_path)
            except OSError:
                pass
        self._config_path = None
