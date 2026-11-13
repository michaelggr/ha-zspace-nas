"""二进制传感器：容器运行态、Docker 引擎。"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ContainerInfo, DiskInfo
from .entity import ZspaceEntity, container_device_info, find_container
from .runtime import ZspaceRuntime


class ContainerRunningSensor(ZspaceEntity, BinarySensorEntity):
    """每个容器一个运行态传感器。"""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, runtime: ZspaceRuntime, container: ContainerInfo) -> None:
        super().__init__(
            runtime, runtime.docker, f"container_{container.id[:12] or container.name}_running"
        )
        self._container_id = container.id or container.name
        self._attr_name = "运行中"
        self._container_device = container_device_info(
            runtime, self._container_id, container.name, container.image
        )

    @property
    def device_info(self) -> DeviceInfo:
        return self._container_device

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self._container is not None

    @property
    def _container(self) -> ContainerInfo | None:
        return find_container(self.coordinator.data, self._container_id)

    @property
    def is_on(self) -> bool | None:
        container = self._container
        return container.running if container else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        container = self._container
        if container is None:
            return {}
        return {
            "container_id": container.id,
            "image": container.image,
            "state": container.state,
            "cpu_usage": container.cpu_usage,
            "memory_mb": (
                round(container.memory_bytes / 1024**2, 1)
                if container.memory_bytes
                else None
            ),
        }


class DockerEngineSensor(ZspaceEntity, BinarySensorEntity):
    """Docker 引擎是否在运行。"""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_name = "Docker 引擎"

    def __init__(self, runtime: ZspaceRuntime) -> None:
        super().__init__(runtime, runtime.docker, "docker_engine")

    @property
    def is_on(self) -> bool | None:
        return getattr(self.coordinator.data, "engine_running", None)


class DiskHealthSensor(ZspaceEntity, BinarySensorEntity):
    """SMART 健康状态：非 ok 即告警。"""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, runtime: ZspaceRuntime, disk: DiskInfo) -> None:
        super().__init__(runtime, runtime.disk, f"disk_{disk.sn}_health")
        self._sn = disk.sn
        self._attr_name = f"磁盘 {disk.dev or disk.position or disk.sn[-4:]} 健康"
        self._disk = disk

    @property
    def _current(self) -> DiskInfo | None:
        for disk in getattr(self.coordinator.data, "disks", []) or []:
            if disk.sn == self._sn:
                return disk
        return None

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self._current is not None

    @property
    def is_on(self) -> bool | None:
        disk = self._current
        if disk is None or disk.health is None:
            return None
        return str(disk.health).lower() not in ("ok", "good", "passed")

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        disk = self._current or self._disk
        return {
            "model": disk.model,
            "serial": disk.sn,
            "temperature": disk.temperature,
            "power_on_hours": disk.power_on_hours,
            "reallocated_sectors": disk.reallocated,
            "is_ssd": disk.is_ssd,
        }


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime: ZspaceRuntime = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []

    disk_data = runtime.disk.data
    if disk_data:
        entities.extend(DiskHealthSensor(runtime, disk) for disk in disk_data.disks)

    data = runtime.docker.data
    if data:
        entities.append(DockerEngineSensor(runtime))
        entities.extend(ContainerRunningSensor(runtime, c) for c in data.containers)

    async_add_entities(entities, update_before_add=False)
