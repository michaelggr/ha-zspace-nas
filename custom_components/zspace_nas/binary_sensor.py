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
from .coordinator import ContainerInfo, DiskInfo, VmInfo
from .entity import (
    ZspaceEntity,
    async_forget_entity,
    container_device_info,
    find_container,
    find_vm,
    vm_device_info,
)
from .naming import token_or_hash
from .runtime import ZspaceRuntime


class ContainerRunningSensor(ZspaceEntity, BinarySensorEntity):
    """每个容器一个运行态传感器。"""

    _platform_domain = "binary_sensor"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, runtime: ZspaceRuntime, container: ContainerInfo) -> None:
        # 先就位：基类构造时会用它推 entity_id
        self._container_id = container.id or container.name
        self._container_name = container.name
        super().__init__(
            runtime, runtime.docker, f"container_{container.id[:12] or container.name}_running"
        )
        self._attr_name = "运行中"
        self._container_device = container_device_info(
            runtime, self._container_id, container.name, container.image
        )

    def _entity_id_parts(self) -> tuple[str, ...]:
        # 容器名优先，纯中文名退回稳定哈希
        name = getattr(self, "_container_name", "") or self._container_id
        return (token_or_hash(name), "running")

    @property
    def container_key(self) -> str:
        return self._container_id

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


class VmRunningSensor(ZspaceEntity, BinarySensorEntity):
    """虚拟机电源状态。`ops vm` 是只读接口，不会碰虚拟机本身。"""

    _platform_domain = "binary_sensor"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, runtime: ZspaceRuntime, vm: VmInfo) -> None:
        self._vm_name = vm.name
        self._vm_title = vm.title
        super().__init__(runtime, runtime.vm, f"vm_{vm.name}_running")
        self._attr_name = "运行中"
        self._vm_device = vm_device_info(runtime, vm.name, vm.title, vm.os_type)

    def _entity_id_parts(self) -> tuple[str, ...]:
        return ("vm", token_or_hash(getattr(self, "_vm_name", "")), "running")

    @property
    def device_info(self) -> DeviceInfo:
        return self._vm_device

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self._vm is not None

    @property
    def _vm(self) -> VmInfo | None:
        return find_vm(self.coordinator.data, self._vm_name)

    @property
    def is_on(self) -> bool | None:
        vm = self._vm
        return vm.running if vm else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        vm = self._vm
        if vm is None:
            return {}
        return {
            "vm_name": vm.name,
            "state": vm.raw_state,
            "os_type": vm.os_type,
            "ip": vm.ip,
            "vnc_port": vm.vnc_port,
            "cpu_count": vm.cpu_count,
            "memory_gib": vm.memory_gib,
            "disk_gib": vm.disk_gib,
        }


class DockerEngineSensor(ZspaceEntity, BinarySensorEntity):
    """Docker 引擎是否在运行。"""

    _platform_domain = "binary_sensor"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_name = "Docker 引擎"

    def __init__(self, runtime: ZspaceRuntime) -> None:
        super().__init__(runtime, runtime.docker, "docker_engine")

    @property
    def is_on(self) -> bool | None:
        return getattr(self.coordinator.data, "engine_running", None)


class DiskHealthSensor(ZspaceEntity, BinarySensorEntity):
    """SMART 健康状态：非 ok 即告警。"""

    _platform_domain = "binary_sensor"
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

    vm_data = runtime.vm.data
    if vm_data:
        entities.extend(
            VmRunningSensor(runtime, vm) for vm in vm_data.vms
        )

    data = runtime.docker.data
    if data:
        entities.append(DockerEngineSensor(runtime))

    # 容器实体登记表：新增容器自动补齐，消失的容器自动移除
    tracked: dict[str, ContainerRunningSensor] = {}
    for container in (data.containers if data else []):
        entity = ContainerRunningSensor(runtime, container)
        tracked[entity.container_key] = entity
        entities.append(entity)

    async_add_entities(entities, update_before_add=False)

    def _sync_containers() -> None:
        """每次 Docker 刷新后对齐实体：新容器建实体，消失的容器删实体。"""
        latest = runtime.docker.data
        if latest is None:
            return
        current = {
            container.id or container.name: container for container in latest.containers
        }

        for key in [key for key in tracked if key not in current]:
            hass.async_create_task(async_forget_entity(hass, tracked.pop(key)))

        fresh = [
            ContainerRunningSensor(runtime, container)
            for key, container in current.items()
            if key not in tracked
        ]
        if not fresh:
            return
        for entity in fresh:
            tracked[entity.container_key] = entity
        async_add_entities(fresh, update_before_add=False)

    entry.async_on_unload(runtime.docker.async_add_listener(_sync_containers))
