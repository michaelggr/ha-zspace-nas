"""实体基类：统一设备归属、可用性与唯一 ID。"""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ZspaceBaseCoordinator
from .naming import ForcedEntityIdMixin, token_or_hash
from .runtime import ZspaceRuntime

_LOGGER = logging.getLogger(__name__)

# 从内部 key 反推实体 ID 段；容器由子类覆盖（要带容器名）
_POOL_KEY = re.compile(r"^pool_(.+)_(usage|used|free)$")
_DISK_KEY = re.compile(r"^disk_(.+)_(temperature|power_on|reallocated|capacity)$")
_CONTAINER_KEY = re.compile(r"^container_(.{12})_(running|power|restart)$")


class ZspaceEntity(ForcedEntityIdMixin, CoordinatorEntity[ZspaceBaseCoordinator]):
    """所有实体共享的骨架。"""

    _attr_has_entity_name = True

    def __init__(
        self,
        runtime: ZspaceRuntime,
        coordinator: ZspaceBaseCoordinator,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self.runtime = runtime
        self._key = key
        self._attr_unique_id = f"{runtime.identifier}_{key}"
        # 选项目开启时才用短英文 ID，默认保持旧的拼音 ID 不动
        self._setup_forced_entity_id(self._platform_domain, *self._entity_id_parts())

    def _short_ids_enabled(self) -> bool:
        return bool(getattr(self.runtime, "short_ids", False))

    def _entity_id_parts(self) -> tuple[str, ...]:
        """默认从内部 key 推导英文段：cpu_usage / pool_x_usage / disk_x_temp。"""
        key = self._key
        match = _POOL_KEY.match(key)
        if match:
            return ("pool", match.group(1), match.group(2))
        match = _DISK_KEY.match(key)
        if match:
            return ("disk", match.group(1), match.group(2))
        match = _CONTAINER_KEY.match(key)
        if match:
            # 拿不到容器名时的兜底：用容器短 ID
            return ("container", match.group(1), match.group(2))
        return (key,)

    @property
    def device_info(self) -> DeviceInfo:
        return self.runtime.device

    @property
    def available(self) -> bool:
        # 某个协调器失败只影响它自己的实体
        return self.coordinator.last_update_success


async def async_forget_entity(hass: HomeAssistant, entity: Any) -> None:
    """把一个已经消失的容器实体彻底摘掉。

    ``async_remove()`` 只让它退出状态机，注册表条目和设备会一直留着
    （重启后变成一堆「不可用」的幽灵实体）。这里连同注册表条目一起删，
    设备上没实体了再把设备删掉。
    """
    entity_id = getattr(entity, "entity_id", None)
    if not entity_id:
        return

    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    device_id = entry.device_id if entry is not None else None

    try:
        await entity.async_remove()
    except Exception:  # pylint: disable=broad-except
        pass

    if entry is not None:
        registry.async_remove(entity_id)

    if not device_id:
        return
    # 设备上还有别的实体（比如开关还在）就先留着
    if er.async_entries_for_device(registry, device_id):
        return
    devices = dr.async_get(hass)
    if devices.async_get(device_id) is not None:
        devices.async_remove_device(device_id)
        _LOGGER.debug("极空间清理空设备：%s", device_id)


def purge_orphan_containers(
    hass: HomeAssistant, entry: ConfigEntry, runtime: ZspaceRuntime
) -> int:
    """启动时清掉「容器已删除、实体还留在注册表」的孤儿。

    运行期由各平台的跟随逻辑处理；但 HA 停机期间删掉的容器没有对象可跟随，
    只能在启动时扫一遍注册表。数据没拿到时直接返回，避免误删。
    """
    data = runtime.docker.data
    if data is None or not runtime.docker.last_update_success:
        return 0

    live = {(container.id or container.name)[:12] for container in data.containers}
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    prefix = f"{runtime.identifier}_"
    removed = 0

    for item in list(er.async_entries_for_config_entry(registry, entry.entry_id)):
        if not item.device_id:
            continue
        device = devices.async_get(item.device_id)
        if device is None:
            continue
        identifier = next(
            (ident[1] for ident in device.identifiers if ident[0] == DOMAIN), None
        )
        # NAS 设备本身没有后缀，容器设备是 <nas>_<id12>
        if not identifier or not identifier.startswith(prefix):
            continue
        if identifier[len(prefix):] in live:
            continue
        registry.async_remove(item.entity_id)
        removed += 1
        if not er.async_entries_for_device(registry, item.device_id):
            devices.async_remove_device(item.device_id)

    if removed:
        _LOGGER.info("极空间清理了 %s 个已删除容器的残留实体", removed)
    return removed


def container_device_info(
    runtime: ZspaceRuntime, container_id: str, name: str, image: str | None
) -> DeviceInfo:
    """容器挂到 NAS 下面，形成 设备 → 容器 的层级。"""
    nas_identifier = runtime.identifier
    return DeviceInfo(
        identifiers={(DOMAIN, f"{nas_identifier}_{container_id[:12]}")},
        name=name,
        manufacturer="极空间",
        model=image,
        via_device=(DOMAIN, nas_identifier),
    )


def entry_identifier(entry_id: str, serial: str | None) -> str:
    return f"nas_{serial}" if serial else f"nas_{entry_id}"


def build_device_info(
    entry_id: str,
    title: str,
    model: str | None,
    serial: str | None,
    version: str | None,
    web_url: str | None = None,
) -> DeviceInfo:
    """把探活拿到的型号/序列号变成 HA 设备。"""
    return DeviceInfo(
        identifiers={(DOMAIN, entry_identifier(entry_id, serial))},
        name=title,
        manufacturer="极空间",
        model=model,
        serial_number=serial,
        sw_version=version,
        # 设备页右上角出现「访问」，直接跳到极空间自己的 Web 页面
        configuration_url=web_url or None,
    )


def get_value(data: Any, path: str, default: Any = None) -> Any:
    """按 `a.b` 取嵌套值，取不到给默认值。"""
    current = data
    for part in path.split("."):
        if not hasattr(current, part):
            return default
        current = getattr(current, part)
        if current is None:
            return default
    return current


def find_container(data: Any, container_id: str) -> Any:
    """在 DockerData 快照里按完整 ID / 短 ID / 名称找容器。"""
    for container in getattr(data, "containers", []) or []:
        if container_id in (container.id, container.short_id, container.name):
            return container
    return None
