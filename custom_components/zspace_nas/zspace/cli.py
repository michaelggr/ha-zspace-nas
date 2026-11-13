"""通道 A：把官方 zspace-cli 当作数据源，用子进程调用并解析 JSON。

为什么不在集成里重写 HTTP 协议：CLI 已经封装了 OAuth、代理路由与 90 项能力，
先复用它把实体跑通；v1.0 再换原生 HTTP，transport 接口保持不变。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from .base import (
    ZspaceAuthError,
    ZspaceError,
    ZspaceNotFoundError,
    ZspacePermissionError,
    ZspaceUnavailableError,
)
from .binary import resolve_binary
from .policy import assert_dump_allowed, assert_not_destructive, assert_write_allowed

_LOGGER = logging.getLogger(__name__)


class ZspaceCliClient:
    """一条命令一次 exec，输出稳定 JSON。"""

    def __init__(
        self,
        binary: str,
        profile_home: str,
        *,
        profile: str = "ha",
        host: str | None = None,
        allow_insecure_http: bool = False,
        timeout: int = 40,
        max_concurrency: int = 2,
        allow_write: bool = False,
    ) -> None:
        self._binary = resolve_binary(binary)
        self._profile_home = profile_home
        self._profile = profile
        self._host = host or None
        self._allow_insecure_http = allow_insecure_http
        self._timeout = timeout
        self._allow_write = allow_write
        # 同一个 profile 上有登录态与刷新锁，限制并发可减少无谓的锁等待
        self._semaphore = asyncio.Semaphore(max_concurrency)

    @property
    def binary(self) -> str:
        return self._binary

    def _build_argv(self, domain: str, action: str, params: dict[str, Any]) -> list[str]:
        argv = [self._binary, "--profile", self._profile]
        if self._host:
            argv += ["--host", self._host]
            # 明文 HTTP 需要每次调用显式降级，不落盘
            if self._allow_insecure_http:
                argv.append("--allow-insecure-http")
        argv += [domain, action, "--params", json.dumps(params, ensure_ascii=False)]
        return argv

    def _build_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["ZSPACE_CLI_HOME"] = self._profile_home
        env.setdefault("NO_COLOR", "1")
        env.setdefault("LANG", "C.UTF-8")
        return env

    def _ensure_executable(self) -> None:
        """HACS/Samba 拷进来的二进制常丢执行位，这里自动补一次。"""
        if not os.path.isfile(self._binary):
            raise ZspaceUnavailableError("E_INSTALL", f"找不到可执行文件 {self._binary}")
        if os.access(self._binary, os.X_OK):
            return
        try:
            os.chmod(self._binary, 0o755)
        except OSError as err:
            raise ZspaceUnavailableError(
                "E_INSTALL", f"{self._binary} 不可执行且自动修复失败：{err}"
            ) from err
        if not os.access(self._binary, os.X_OK):
            raise ZspaceUnavailableError("E_INSTALL", f"{self._binary} 仍然不可执行")

    async def async_call(
        self,
        domain: str,
        action: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: int | None = None,
        write: bool = False,
        dump: bool = False,
    ) -> Any:
        """执行一条 CLI 命令，返回 `data` 字段。

        任何写操作必须在调用点显式声明 `write=True`，否则走到子进程之前就被拦下；
        这是最后一道闸门，实体与服务层的校验是前两道。
        """
        params = params or {}
        if write:
            assert_write_allowed(domain, action, params, enabled=self._allow_write)
        elif dump:
            assert_dump_allowed(domain, action, params)
        else:
            assert_not_destructive(params)

        self._ensure_executable()

        argv = self._build_argv(domain, action, params)
        limit = timeout or self._timeout

        async with self._semaphore:
            try:
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._build_env(),
                )
            except OSError as err:
                raise ZspaceUnavailableError("E_EXEC", f"无法启动 CLI：{err}") from err

            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    process.communicate(), timeout=limit
                )
            except asyncio.TimeoutError as err:
                process.kill()
                await process.wait()
                raise ZspaceUnavailableError(
                    "E_TIMEOUT", f"{domain} {action} 超过 {limit}s 未返回"
                ) from err

        return self._parse(stdout_b, stderr_b, process.returncode, domain, action)

    async def async_probe(self) -> dict[str, Any]:
        """探活：拿系统基本信息，同时用于配置流程校验登录态。"""
        data = await self.async_call("system", "info", {"fields": ["basic", "cpu", "memory"]})
        return data if isinstance(data, dict) else {"raw": data}

    # ── 解析 ────────────────────────────────────────────
    def _parse(
        self,
        stdout_b: bytes,
        stderr_b: bytes,
        returncode: int | None,
        domain: str,
        action: str,
    ) -> Any:
        stdout = stdout_b.decode("utf-8", errors="replace").strip()
        stderr = stderr_b.decode("utf-8", errors="replace").strip()

        payload = self._last_json(stdout) or self._last_json(stderr)
        if payload is None:
            raise ZspaceUnavailableError(
                "E_OUTPUT",
                f"{domain} {action} 未返回 JSON（exit={returncode}）：{(stderr or stdout)[:200]}",
            )

        if payload.get("ok") is False:
            error = payload.get("error") or {}
            code = str(error.get("code") or "E_UNKNOWN")
            message = str(error.get("message") or "")
            raise self._map_error(code, message)

        if returncode not in (0, None):
            raise ZspaceUnavailableError("E_EXIT", f"{domain} {action} 退出码 {returncode}")

        return payload.get("data", payload)

    @staticmethod
    def _last_json(text: str) -> dict[str, Any] | None:
        """CLI 可能先打日志再输出结果，取最后一行能解析的 JSON 对象。"""
        if not text:
            return None
        try:
            whole = json.loads(text)
            if isinstance(whole, dict):
                return whole
        except json.JSONDecodeError:
            pass
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    @staticmethod
    def _map_error(code: str, message: str) -> ZspaceError:
        """错误码映射成 HA 侧可区分的异常，供 reauth 与实体可用性判断。

        实际跑出来的登录失效码是 `E_NOT_LOGGED_IN`（不是文档里的 E_OAUTH），两个都要接。
        """
        _LOGGER.debug("zspace-cli 错误 %s: %s", code, message)
        if code in (
            "E_NOT_LOGGED_IN",
            "E_CLOUD_AUTH_REQUIRED",
            "E_OAUTH",
            "E_CREDENTIAL",
            "E_AUTH",
            "E_PROFILE_BUSY",
        ):
            return ZspaceAuthError(code, message)
        if code in ("E_PERMISSION", "E_POLICY"):
            return ZspacePermissionError(code, message)
        if code == "E_NOT_FOUND":
            return ZspaceNotFoundError(code, message)
        if code in (
            "E_INSTALL",
            "E_UNSUPPORTED_PLATFORM",
            "E_TIMEOUT",
            "E_STORAGE_UNAVAILABLE",
            "E_CLI_HOME",
            "E_NET",
            "E_HTTP",
        ):
            return ZspaceUnavailableError(code, message)
        return ZspaceError(code, message)
