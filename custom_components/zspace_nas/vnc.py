"""虚拟机控制台：签名链接 + WebSocket → TCP 桥 + noVNC 前端。

为什么需要桥
------------
极空间给的 VNC 是**裸 TCP**（`ops vm` 详情里的 `vnc` 是 `["192.168.1.10:15566", …]`），
浏览器不能直接连 TCP。所以这里在 HA 里开一个 WebSocket 端点，把二进制帧原样
转发到 NAS 的 VNC 端口，前端用打包进来的 noVNC。

安全
----
* 只有选项 `vnc_console` 打开时才签发链接；
* 链接带 HMAC 签名和过期时间，改一个字节就失效；
* 链接里只有令牌，不含密码；VNC 密码由用户在控制台页面输入，不落盘、不进日志。
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from typing import Any

from aiohttp import WSMsgType, web
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.http import KEY_HASS, HomeAssistantView

from .const import DOMAIN
from .vnc_token import TOKEN_TTL_HOURS, make_token, parse_token

_LOGGER = logging.getLogger(__name__)

STATIC_URL = "/zspace_nas_static"
CONSOLE_PATH = "/zspace_nas/vnc"
WS_PATH = "/api/zspace_nas/vncws"

_SECRET_FILE = "vnc.secret"
_INTERNAL_PREFIX = "10.222."  # 极空间内部桥接网段，浏览器/HA 都到不了


# ── 令牌 ────────────────────────────────────────────────
def _secret_path(hass: HomeAssistant) -> str:
    return hass.config.path("zspace_nas", _SECRET_FILE)


def load_secret(hass: HomeAssistant) -> bytes:
    """密钥落在 /config/zspace_nas/vnc.secret，重启后链接仍然有效。"""
    path = _secret_path(hass)
    try:
        with open(path, "rb") as handle:
            data = handle.read().strip()
        if len(data) >= 32:
            return data
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = secrets.token_hex(32).encode()
    with open(path, "wb") as handle:
        handle.write(data)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return data


# ── 地址选择 ────────────────────────────────────────────
def pick_endpoint(runtime: Any, vm: Any) -> tuple[str, int] | None:
    """挑一个能用 VNC 地址：优先非内部网段，端口兜底用实体上的 vnc_port。"""
    preferred = getattr(runtime, "vnc_host", None)
    port = getattr(vm, "vnc_port", None)
    if preferred and port:
        return preferred, int(port)
    for endpoint in getattr(vm, "vnc_endpoints", []) or []:
        if ":" not in endpoint:
            continue
        host, _, raw_port = endpoint.rpartition(":")
        if host.startswith(_INTERNAL_PREFIX):
            continue
        try:
            return host, int(raw_port)
        except ValueError:
            continue
    return None


def console_url(
    hass: HomeAssistant, runtime: Any, vm_name: str, hours: int = TOKEN_TTL_HOURS
) -> str | None:
    secret = getattr(runtime, "vnc_secret", b"")
    if not secret:
        return None
    token = make_token(secret, runtime.entry_id, vm_name, hours)
    base = (hass.config.internal_url or hass.config.external_url or "").rstrip("/")
    return f"{base}{CONSOLE_PATH}/{token}" if base else f"{CONSOLE_PATH}/{token}"


def _resolve(hass: HomeAssistant, token: str) -> tuple[Any, Any] | tuple[None, None]:
    """令牌 → (runtime, vm)。任何一步不满足都返回 (None, None)。"""
    runtimes = hass.data.get(DOMAIN) or {}
    # 逐个 runtime 试签名：多台 NAS 时各自的密钥相同，直接遍历最简单
    for runtime in runtimes.values():
        if not getattr(runtime, "vnc_enabled", False):
            continue
        secret = getattr(runtime, "vnc_secret", None)
        if not secret:
            continue
        payload = parse_token(secret, token)
        if not payload:
            continue
        if payload.get("e") != runtime.entry_id:
            continue
        data = runtime.vm.data
        for vm in getattr(data, "vms", []) or []:
            if vm.name == payload.get("v"):
                return runtime, vm
    return None, None


# ── HTTP 视图 ───────────────────────────────────────────
def hass_of(request: web.Request) -> HomeAssistant:
    """从请求取 HA 实例。

    HA 2026.x 起 HomeAssistantView 不再注入 self.hass，改从 app 的 KEY_HASS 取。
    这里同时兼容旧版（app 里是裸字符串键 "hass"）与新版（AppKey）。
    """
    app = request.app
    try:
        return app[KEY_HASS]
    except KeyError:
        return app["hass"]


class ConsoleView(HomeAssistantView):
    """把带令牌的地址重定向到静态控制台页面。"""

    url = CONSOLE_PATH + "/{token}"
    name = "zspace_nas:console"
    requires_auth = False

    async def get(self, request: web.Request, token: str) -> web.StreamResponse:
        runtime, vm = _resolve(hass_of(request), token)
        if runtime is None:
            return web.Response(status=403, text="链接无效或已过期，请从设备页重新打开。")
        raise web.HTTPFound(f"{STATIC_URL}/console.html?t={token}")


class VncBridgeView(HomeAssistantView):
    """WebSocket ↔ TCP 透传：浏览器这头是 WS，NAS 那头是裸 VNC 端口。"""

    url = WS_PATH + "/{token}"
    name = "api:zspace_nas:vncws"
    requires_auth = False

    async def get(self, request: web.Request, token: str) -> web.StreamResponse:
        runtime, vm = _resolve(hass_of(request), token)
        if runtime is None:
            return web.Response(status=403, text="链接无效或已过期")

        endpoint = pick_endpoint(runtime, vm)
        if endpoint is None:
            return web.Response(status=503, text="这台虚拟机没有可用的 VNC 地址（是否未启用 VNC？）")
        host, port = endpoint

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=10
            )
        except (OSError, asyncio.TimeoutError) as err:
            _LOGGER.warning("连不上虚拟机 %s 的 VNC %s:%s：%s", vm.name, host, port, err)
            return web.Response(status=502, text=f"无法连接 VNC 服务：{err}")

        response = web.WebSocketResponse(protocols=("binary",), max_msg_size=0, heartbeat=None)
        await response.prepare(request)

        async def ws_to_tcp() -> None:
            async for message in response:
                if message.type == WSMsgType.BINARY:
                    writer.write(message.data)
                    await writer.drain()
                elif message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                    break

        async def tcp_to_ws() -> None:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                await response.send_bytes(chunk)

        tasks = [asyncio.create_task(ws_to_tcp()), asyncio.create_task(tcp_to_ws())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, AttributeError):
                pass
            await response.close()
        return response


# ── 设备上的「访问」链接 ─────────────────────────────────
def async_update_device_urls(hass: HomeAssistant, entry_id: str, runtime: Any) -> None:
    """把每台虚拟机的 configuration_url 刷成新的签名链接（令牌会过期）。"""
    if not getattr(runtime, "vnc_enabled", False):
        return
    devices = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(devices, entry_id):
        identifier = next(
            (ident[1] for ident in device.identifiers if ident[0] == DOMAIN), None
        )
        if not identifier or "_vm_" not in identifier:
            continue
        vm_name = identifier.split("_vm_", 1)[1]
        url = console_url(hass, runtime, vm_name)
        if url and device.configuration_url != url:
            devices.async_update_device(device.id, configuration_url=url)


async def async_register(hass: HomeAssistant) -> None:
    """静态目录与两个视图全局只注册一次。"""
    store = hass.data.setdefault(DOMAIN, {})
    if store.get("_vnc_registered"):
        return
    frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
    await hass.http.async_register_static_paths(
        [get_static_config(STATIC_URL, frontend_dir)]
    )
    hass.http.register_view(ConsoleView())
    hass.http.register_view(VncBridgeView())
    store["_vnc_registered"] = True
    _LOGGER.debug("极空间控制台静态目录与视图已注册：%s", frontend_dir)


def get_static_config(url_path: str, directory: str):
    """延迟导入，兼容 HA 不同小版本的静态路径 API。"""
    from homeassistant.components.http import StaticPathConfig

    return StaticPathConfig(url_path, directory, False)
