"""传感器：系统、存储池、Docker 概况。

一个协调器一次拉取、多个实体共享同一份快照，避免每个实体各发一条命令。
"""

from __future__ import annotations

from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import StorageData, ZspaceBaseCoordinator
from .entity import ZspaceEntity
from .runtime import ZspaceRuntime

GIB = 1024**3

ValueFn = Callable[[Any], Any]


class ZspaceSensor(ZspaceEntity, SensorEntity):
    """通用只读传感器。"""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        runtime: ZspaceRuntime,
        coordinator: ZspaceBaseCoordinator,
        key: str,
        name: str,
        value_fn: ValueFn,
        *,
        unit: str | None = None,
        device_class: SensorDeviceClass | None = None,
        icon: str | None = None,
        precision: int | None = None,
    ) -> None:
        super().__init__(runtime, coordinator, key)
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_icon = icon
        self._attr_suggested_display_precision = precision
        self._value_fn = value_fn

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data
        if data is None:
            return None
        try:
            return self._value_fn(data)
        except (AttributeError, TypeError):
            return None


def _pool_attr(data: StorageData, key: str, attr: str) -> Any:
    for pool in getattr(data, "pools", []):
        if pool.key == key:
            return getattr(pool, attr, None)
    return None


def _disk_attr(data: Any, sn: str, attr: str) -> Any:
    for disk in getattr(data, "disks", []):
        if disk.sn == sn:
            return getattr(disk, attr, None)
    return None


def _gib(value: int | None) -> float | None:
    return round(value / GIB, 2) if isinstance(value, int) else None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime: ZspaceRuntime = hass.data[DOMAIN][entry.entry_id]
    entities: list[ZspaceSensor] = [
        ZspaceSensor(
            runtime,
            runtime.system,
            "cpu_usage",
            "CPU 使用率",
            lambda d: d.cpu_usage,
            unit=PERCENTAGE,
            icon="mdi:cpu-64-bit",
            precision=1,
        ),
        ZspaceSensor(
            runtime,
            runtime.system,
            "cpu_temperature",
            "CPU 温度",
            lambda d: d.cpu_temperature,
            unit="°C",
            device_class=SensorDeviceClass.TEMPERATURE,
            icon="mdi:thermometer",
            precision=0,
        ),
        ZspaceSensor(
            runtime,
            runtime.system,
            "fan_speed",
            "风扇转速",
            lambda d: d.fan_speed,
            unit="rpm",
            icon="mdi:fan",
            precision=0,
        ),
        ZspaceSensor(
            runtime,
            runtime.system,
            "uptime",
            "运行时长",
            lambda d: d.uptime,
            icon="mdi:clock-outline",
        ),
        ZspaceSensor(
            runtime,
            runtime.system,
            "memory_usage",
            "内存使用率",
            lambda d: d.memory_usage,
            unit=PERCENTAGE,
            icon="mdi:memory",
            precision=1,
        ),
        ZspaceSensor(
            runtime,
            runtime.system,
            "memory_used",
            "内存已用",
            lambda d: _gib(d.memory_used),
            unit="GiB",
            device_class=SensorDeviceClass.DATA_SIZE,
            icon="mdi:memory",
            precision=2,
        ),
        ZspaceSensor(
            runtime,
            runtime.system,
            "network_rx",
            "网络接收",
            lambda d: d.rx_bytes_per_s,
            unit="B/s",
            device_class=SensorDeviceClass.DATA_RATE,
            icon="mdi:download-network",
        ),
        ZspaceSensor(
            runtime,
            runtime.system,
            "network_tx",
            "网络发送",
            lambda d: d.tx_bytes_per_s,
            unit="B/s",
            device_class=SensorDeviceClass.DATA_RATE,
            icon="mdi:upload-network",
        ),
        ZspaceSensor(
            runtime,
            runtime.docker,
            "docker_containers_total",
            "容器总数",
            lambda d: len(getattr(d, "containers", [])),
            icon="mdi:docker",
        ),
        ZspaceSensor(
            runtime,
            runtime.docker,
            "docker_containers_running",
            "运行中容器",
            lambda d: getattr(d, "running_count", None),
            icon="mdi:docker",
        ),
    ]

    storage_data: StorageData | None = runtime.storage.data
    if storage_data:
        for pool in storage_data.pools:
            key = pool.key
            entities.extend(
                [
                    ZspaceSensor(
                        runtime,
                        runtime.storage,
                        f"pool_{key}_usage",
                        f"存储池 {pool.name} 使用率",
                        lambda d, k=key: _pool_attr(d, k, "usage"),
                        unit=PERCENTAGE,
                        icon="mdi:harddisk",
                        precision=1,
                    ),
                    ZspaceSensor(
                        runtime,
                        runtime.storage,
                        f"pool_{key}_used",
                        f"存储池 {pool.name} 已用",
                        lambda d, k=key: _gib(_pool_attr(d, k, "used")),
                        unit="GiB",
                        device_class=SensorDeviceClass.DATA_SIZE,
                        icon="mdi:harddisk",
                        precision=2,
                    ),
                    ZspaceSensor(
                        runtime,
                        runtime.storage,
                        f"pool_{key}_free",
                        f"存储池 {pool.name} 可用",
                        lambda d, k=key: _gib(_pool_attr(d, k, "free")),
                        unit="GiB",
                        device_class=SensorDeviceClass.DATA_SIZE,
                        icon="mdi:harddisk",
                        precision=2,
                    ),
                ]
            )

    disk_data = runtime.disk.data
    if disk_data:
        for disk in disk_data.disks:
            sn = disk.sn
            label = disk.dev or disk.position or sn[-4:]
            entities.extend(
                [
                    ZspaceSensor(
                        runtime,
                        runtime.disk,
                        f"disk_{sn}_temperature",
                        f"磁盘 {label} 温度",
                        lambda d, s=sn: _disk_attr(d, s, "temperature"),
                        unit="°C",
                        device_class=SensorDeviceClass.TEMPERATURE,
                        precision=0,
                    ),
                    ZspaceSensor(
                        runtime,
                        runtime.disk,
                        f"disk_{sn}_power_on",
                        f"磁盘 {label} 通电时间",
                        lambda d, s=sn: _disk_attr(d, s, "power_on_hours"),
                        unit="h",
                        icon="mdi:timer-outline",
                        precision=0,
                    ),
                    ZspaceSensor(
                        runtime,
                        runtime.disk,
                        f"disk_{sn}_reallocated",
                        f"磁盘 {label} 重映射扇区",
                        lambda d, s=sn: _disk_attr(d, s, "reallocated"),
                        icon="mdi:alert-circle-outline",
                        precision=0,
                    ),
                    ZspaceSensor(
                        runtime,
                        runtime.disk,
                        f"disk_{sn}_capacity",
                        f"磁盘 {label} 容量",
                        lambda d, s=sn: _gib(_disk_attr(d, s, "total")),
                        unit="GiB",
                        device_class=SensorDeviceClass.DATA_SIZE,
                        icon="mdi:harddisk",
                        precision=0,
                    ),
                ]
            )

    entities.extend(
        [
            ZspaceSensor(
                runtime,
                runtime.tasks,
                "downloads_active",
                "下载任务",
                lambda d: getattr(d, "active", None),
                icon="mdi:download",
            ),
            ZspaceSensor(
                runtime,
                runtime.tasks,
                "downloads_rate",
                "下载速度",
                lambda d: getattr(d, "rate_down", None),
                unit="B/s",
                device_class=SensorDeviceClass.DATA_RATE,
                icon="mdi:speedometer",
            ),
            ZspaceSensor(
                runtime,
                runtime.tasks,
                "downloads_seeding",
                "做种任务",
                lambda d: getattr(d, "seeding", None),
                icon="mdi:upload-network",
            ),
        ]
    )

    async_add_entities(entities, update_before_add=False)
