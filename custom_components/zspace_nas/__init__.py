"""极空间 NAS 集成入口。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .const import (
    CONF_ALLOW_CONTROL,
    CONF_ALLOWED,
    CONF_ALLOW_INSECURE,
    CONF_BINARY,
    CONF_COOLDOWN,
    CONF_DISK_INTERVAL,
    CONF_DOWNLOAD_INTERVAL,
    CONF_DRY_RUN,
    CONF_PROTECTED,
    CONF_DOCKER_INTERVAL,
    CONF_HOST,
    CONF_PROFILE,
    CONF_PROFILE_HOME,
    CONF_STORAGE_INTERVAL,
    CONF_SYSTEM_INTERVAL,
    DEFAULT_BINARY,
    DEFAULT_COOLDOWN,
    DEFAULT_DISK_INTERVAL,
    DEFAULT_DOCKER_INTERVAL,
    DEFAULT_DOWNLOAD_INTERVAL,
    DEFAULT_PROFILE,
    DEFAULT_PROFILE_HOME,
    DEFAULT_STORAGE_INTERVAL,
    DEFAULT_SYSTEM_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import (
    DiskCoordinator,
    DockerCoordinator,
    StorageCoordinator,
    SystemCoordinator,
    TaskCoordinator,
)
from .entity import build_device_info, entry_identifier
from .runtime import ZspaceRuntime
from .safety import SafetyGuard, build_policy
from .services import async_setup_services
from .zspace import ZspaceAuthError, ZspaceCliClient, ZspaceError, ZspaceUnavailableError
from .zspace.base import pick
from .zspace.normalize import normalize_system

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    allow_control = bool(entry.options.get(CONF_ALLOW_CONTROL, False))
    client = ZspaceCliClient(
        binary=entry.data.get(CONF_BINARY, DEFAULT_BINARY),
        profile_home=entry.data.get(CONF_PROFILE_HOME, DEFAULT_PROFILE_HOME),
        profile=entry.data.get(CONF_PROFILE) or DEFAULT_PROFILE,
        host=entry.data.get(CONF_HOST) or None,
        allow_insecure_http=bool(entry.data.get(CONF_ALLOW_INSECURE)),
        # 写操作总闸门：没开容器控制时，客户端直接拒绝任何写调用
        allow_write=allow_control,
    )

    try:
        probe = await client.async_probe()
    except ZspaceAuthError as err:
        # 触发重新授权流程，而不是让整个集成就地失败
        raise ConfigEntryAuthFailed(err.message) from err
    except (ZspaceUnavailableError, ZspaceError) as err:
        raise ConfigEntryNotReady(str(err)) from err

    # 真机把型号藏在 detail.list[0].basic.name（如 Z4Pro-XXXX），序列号是 nasid
    sys_info = normalize_system(probe)
    model = sys_info.model or pick(probe, ["model", "product", "device_model"])
    serial = sys_info.serial or pick(probe, ["sn", "serial", "serial_number", "device_sn"])
    version = pick(probe, ["version", "firmware", "system_version", "os_version"])
    identifier = entry_identifier(entry.entry_id, str(serial) if serial else None)
    device = build_device_info(
        entry.entry_id,
        entry.title,
        str(model) if model else None,
        str(serial) if serial else None,
        str(version) if version else None,
    )

    options = entry.options
    system = SystemCoordinator(
        hass, entry, client, "system", options.get(CONF_SYSTEM_INTERVAL, DEFAULT_SYSTEM_INTERVAL)
    )
    storage = StorageCoordinator(
        hass, entry, client, "storage", options.get(CONF_STORAGE_INTERVAL, DEFAULT_STORAGE_INTERVAL)
    )
    docker = DockerCoordinator(
        hass, entry, client, "docker", options.get(CONF_DOCKER_INTERVAL, DEFAULT_DOCKER_INTERVAL)
    )
    disk = DiskCoordinator(
        hass, entry, client, "disk", options.get(CONF_DISK_INTERVAL, DEFAULT_DISK_INTERVAL)
    )
    tasks = TaskCoordinator(
        hass,
        entry,
        client,
        "tasks",
        options.get(CONF_DOWNLOAD_INTERVAL, DEFAULT_DOWNLOAD_INTERVAL),
    )

    # 系统信息是实体存在的前提，失败直接让 entry 进入重试
    await system.async_config_entry_first_refresh()
    # 存储与 Docker 允许单独失败（权限不足的设备很常见），不阻塞整体启动
    results = await asyncio.gather(
        storage.async_refresh(),
        docker.async_refresh(),
        disk.async_refresh(),
        tasks.async_refresh(),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, Exception):
            _LOGGER.warning("极空间子协调器首次刷新失败：%s", result)

    guard = SafetyGuard(
        build_policy(
            allow_control=allow_control,
            dry_run=bool(options.get(CONF_DRY_RUN, False)),
            cooldown=int(options.get(CONF_COOLDOWN, DEFAULT_COOLDOWN)),
            extra_protected=options.get(CONF_PROTECTED, ""),
            allowed=options.get(CONF_ALLOWED, ""),
        )
    )

    runtime = ZspaceRuntime(
        client=client,
        identifier=identifier,
        device=device,
        system=system,
        storage=storage,
        docker=docker,
        disk=disk,
        tasks=tasks,
        guard=guard,
        allow_control=allow_control,
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_setup_services(hass)

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def runtime_for(hass: HomeAssistant, entry: ConfigEntry) -> Any:
    return hass.data.get(DOMAIN, {}).get(entry.entry_id)
