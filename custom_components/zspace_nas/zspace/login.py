"""在 Home Assistant 里完成极空间 OAuth 授权。

CLI 的登录流程需要 stdin 常驻：它会先打印授权链接，再等待一行一次性授权码。
这里把子进程的生命周期交给配置流程持有，用户点链接、授权、把码贴回表单即可。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from .base import ZspaceAuthError

_LOGGER = logging.getLogger(__name__)

_START_TIMEOUT = 30  # 等授权链接的超时
_SUBMIT_TIMEOUT = 60  # 提交授权码后等结果


class ZspaceLogin:
    """一次 login 事务：一个子进程 + 一个授权链接。"""

    def __init__(
        self,
        process: asyncio.subprocess.Process,
        url: str,
        expires_in: int,
        stderr_lines: list[str],
    ) -> None:
        self.process = process
        self.url = url
        self.expires_in = expires_in
        self._stderr = stderr_lines

    @classmethod
    async def async_start(
        cls,
        binary: str,
        profile_home: str,
        profile: str = "ha",
    ) -> "ZspaceLogin":
        os.makedirs(profile_home, exist_ok=True)
        env = dict(os.environ)
        env["ZSPACE_CLI_HOME"] = profile_home
        env.setdefault("NO_COLOR", "1")

        try:
            process = await asyncio.create_subprocess_exec(
                binary,
                "--profile",
                profile,
                "login",
                "--no-open",
                "--auth-code-stdin",
                "--credential-store",
                "file",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError as err:
            raise ZspaceAuthError("E_INSTALL", f"无法启动 CLI 登录：{err}") from err

        loop = asyncio.get_running_loop()
        url_future: asyncio.Future[tuple[str, int]] = loop.create_future()
        stderr_lines: list[str] = []

        async def pump_stdout() -> None:
            assert process.stdout is not None
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if payload.get("event") == "oauth_authorization" and not url_future.done():
                    url_future.set_result(
                        (str(payload.get("url") or ""), int(payload.get("expires_in") or 180))
                    )

        async def pump_stderr() -> None:
            assert process.stderr is not None
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                stderr_lines.append(line.decode("utf-8", errors="replace").strip())
                del stderr_lines[:-20]

        asyncio.create_task(pump_stdout())
        asyncio.create_task(pump_stderr())

        try:
            url, expires_in = await asyncio.wait_for(url_future, timeout=_START_TIMEOUT)
        except asyncio.TimeoutError as err:
            process.kill()
            raise ZspaceAuthError(
                "E_OAUTH", "没有拿到授权链接，请确认登录态目录可写"
            ) from err

        if not url:
            process.kill()
            raise ZspaceAuthError("E_OAUTH", "授权链接为空")

        _LOGGER.debug("已生成授权链接，有效期 %s 秒", expires_in)
        return cls(process, url, expires_in, stderr_lines)

    async def async_submit(self, code: str) -> None:
        """把一次性授权码写进 stdin，并等待 CLI 判定结果。"""
        cleaned = (code or "").strip()
        if not cleaned:
            raise ZspaceAuthError("E_OAUTH", "授权码不能为空")
        if self.process.returncode is not None:
            raise ZspaceAuthError("E_OAUTH", "授权已过期，请重新获取链接")

        try:
            assert self.process.stdin is not None
            self.process.stdin.write((cleaned + "\n").encode())
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as err:
            raise ZspaceAuthError("E_OAUTH", "授权已过期，请重新获取链接") from err

        try:
            returncode = await asyncio.wait_for(self.process.wait(), timeout=_SUBMIT_TIMEOUT)
        except asyncio.TimeoutError as err:
            self.close()
            raise ZspaceAuthError("E_OAUTH", "等待授权结果超时，请重试") from err

        if returncode != 0:
            raise ZspaceAuthError("E_OAUTH", f"授权失败：{self._error_detail(returncode)}")

    def _error_detail(self, returncode: int) -> str:
        """从 stderr 的 JSON 里取可读信息，取不到再退回原文。"""
        for line in reversed(self._stderr):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
            break
        return self._stderr[-1] if self._stderr else f"退出码 {returncode}"

    def close(self) -> None:
        if self.process.returncode is None:
            try:
                self.process.kill()
            except ProcessLookupError:
                pass
