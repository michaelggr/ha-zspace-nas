"""对外服务：容器动作 + 原始数据导出（调试用）。"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    ALLOWED_CONTAINER_ACTIONS,
    ATTR_ACTION,
    ATTR_CLI_ACTION,
    ATTR_CONTAINER_ID,
    ATTR_DOMAIN,
    ATTR_PARAMS,
    DOMAIN,
    SERVICE_CONTAINER_ACTION,
    SERVICE_DUMP_RAW,
)
from .zspace import ZspaceError

_LOGGER = logging.getLogger(__name__)

CONTAINER_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONTAINER_ID): cv.string,
        vol.Required(ATTR_ACTION): vol.In(ALLOWED_CONTAINER_ACTIONS),
        vol.Optional("entry_id"): cv.string,
    }
)

DUMP_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DOMAIN): cv.string,
        vol.Required(ATTR_CLI_ACTION): cv.string,
        vol.Optional(ATTR_PARAMS, default={}): dict,
        vol.Optional("entry_id"): cv.string,
    }
)


def _find_runtime(hass: HomeAssistant, entry_id: str | None, container_id: str) -> Any:
    """按容器 ID / 名称反查所属 NAS，支持多台设备。"""
    store = hass.data.get(DOMAIN) or {}
    if entry_id and entry_id in store:
        return store[entry_id]

    runtimes = list(store.values())
    for runtime in runtimes:
        data = runtime.docker.data
        if not data:
            continue
        for container in data.containers:
            if container_id in (container.id, container.short_id, container.name):
                return runtime
    return None


async def async_setup_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_CONTAINER_ACTION):
        return

    async def _handle_container_action(call: ServiceCall) -> None:
        action = call.data[ATTR_ACTION]
        container_id = call.data[ATTR_CONTAINER_ID]
        runtime = _find_runtime(hass, call.data.get("entry_id"), container_id)

        if runtime is None:
            raise ServiceValidationError(f"没找到容器 {container_id} 所属的极空间设备")
        if not runtime.allow_control:
            raise ServiceValidationError(
                "容器控制未开启，请到集成选项里打开「允许容器控制」"
            )

        target = container_id
        container = None
        data = runtime.docker.data
        for candidate in (data.containers if data else []):
            if container_id in (candidate.id, candidate.short_id, candidate.name):
                container = candidate
                target = candidate.id or container_id
                break

        # 与开关、按钮共用同一套护栏：受保护容器、状态前置、冷却时间
        decision = runtime.guard.evaluate(
            name=container.name if container else container_id,
            container_id=target,
            running=container.running if container else None,
            action=action,
        )
        if not decision.allowed:
            raise ServiceValidationError(f"已拦截：{decision.reason}")
        if decision.simulated:
            _LOGGER.warning("演练模式：跳过 %s %s", action, target)
            return

        try:
            await runtime.client.async_call(
                "docker",
                "containers",
                {"action": action, "container_id": target},
                write=True,
            )
        except ZspaceError as err:
            raise ServiceValidationError(f"{err.code}: {err.message}") from err

        runtime.guard.record(container.name if container else None, target)
        await runtime.docker.async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        SERVICE_CONTAINER_ACTION,
        _handle_container_action,
        schema=CONTAINER_SCHEMA,
        supports_response=SupportsResponse.NONE,
    )

    async def _handle_dump_raw(call: ServiceCall) -> dict[str, Any]:
        """把某个能力调用的原始 JSON 落盘，用于核对字段结构。"""
        runtime = _first_runtime(hass, call.data.get("entry_id"))
        if runtime is None:
            raise ServiceValidationError("还没有配置任何极空间设备")

        domain = call.data[ATTR_DOMAIN]
        cli_action = call.data[ATTR_CLI_ACTION]
        try:
            data = await runtime.client.async_call(
                domain, cli_action, call.data.get(ATTR_PARAMS) or {}, dump=True
            )
        except ZspaceError as err:
            raise ServiceValidationError(f"{err.code}: {err.message}") from err

        def _write() -> str:
            directory = hass.config.path("zspace_nas")
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, f"dump-{domain}-{cli_action}.json".replace("/", "_"))
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
            return path

        path = await hass.async_add_executor_job(_write)
        _LOGGER.info("原始数据已写入 %s", path)
        return {"path": path}

    hass.services.async_register(
        DOMAIN,
        SERVICE_DUMP_RAW,
        _handle_dump_raw,
        schema=DUMP_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


def _first_runtime(hass: HomeAssistant, entry_id: str | None) -> Any:
    store = hass.data.get(DOMAIN) or {}
    if entry_id and entry_id in store:
        return store[entry_id]
    return next(iter(store.values()), None)
