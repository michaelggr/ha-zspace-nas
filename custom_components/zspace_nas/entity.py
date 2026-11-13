"""实体基类：统一设备归属、可用性与唯一 ID。"""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ZspaceBaseCoordinator
from .runtime import ZspaceRuntime


class ZspaceEntity(CoordinatorEntity[ZspaceBaseCoordinator]):
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

    @property
    def device_info(self) -> DeviceInfo:
        return self.runtime.device

    @property
    def available(self) -> bool:
        # 某个协调器失败只影响它自己的实体
        return self.coordinator.last_update_success


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
) -> DeviceInfo:
    """把探活拿到的型号/序列号变成 HA 设备。"""
    return DeviceInfo(
        identifiers={(DOMAIN, entry_identifier(entry_id, serial))},
        name=title,
        manufacturer="极空间",
        model=model,
        serial_number=serial,
        sw_version=version,
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
