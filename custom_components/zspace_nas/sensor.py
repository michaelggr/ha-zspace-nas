"""传感器：系统、存储池、Docker 概况。

一个协调器一次拉取、多个实体共享同一份快照，避免每个实体各发一条命令。
"""

from __future__ import annotations

from dataclasses import asdict
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

from .all_entities_sensor import ZspaceAllEntitiesSensor
from .const import DOMAIN
from .coordinator import StorageData, ZspaceBaseCoordinator
from .entity import ZspaceEntity
from .runtime import ZspaceRuntime

GIB = 1024**3

ValueFn = Callable[[Any], Any]


class ZspaceSensor(ZspaceEntity, SensorEntity):
    """通用只读传感器。"""

    _platform_domain = "sensor"
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
        state_class: SensorStateClass | None = SensorStateClass.MEASUREMENT,
    ) -> None:
        super().__init__(runtime, coordinator, key)
        self._attr_name = name
        # 文本型数值（如「8天 20小时」）不能带 state_class，否则 HA 判定为不可用
        self._attr_state_class = state_class
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


class PoolSensor(ZspaceSensor):
    """存储池传感器：额外把成员盘（槽位/型号/容量/温度/健康）挂到属性上。

    前端不用再逐盘查实体，一张卡就能画出「真机前面板」的盘位格。
    """

    # 成员盘列表只在前端/模板用，不写 recorder（省历史库体积）
    _unrecorded_attributes = frozenset({"disks"})

    def __init__(
        self,
        runtime: ZspaceRuntime,
        coordinator: ZspaceBaseCoordinator,
        key: str,
        name: str,
        value_fn: ValueFn,
        pool_key: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(runtime, coordinator, key, name, value_fn, **kwargs)
        self._pool_key = pool_key

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pool = _pool(self.coordinator.data, self._pool_key)
        if pool is None:
            return {}
        return {
            "pool_type": pool.pool_type,
            "status": pool.status,
            "disk_count": len(pool.disks),
            "disks": [asdict(disk) for disk in pool.disks],
        }


def _pool(data: StorageData, key: str) -> Any:
    for pool in getattr(data, "pools", []):
        if pool.key == key:
            return pool
    return None


def _pool_attr(data: StorageData, key: str, attr: str) -> Any:
    for pool in getattr(data, "pools", []):
        if pool.key == key:
            return getattr(pool, attr, None)
    return None


def _vm_attr(data: Any, name: str, attr: str) -> Any:
    for vm in getattr(data, "vms", []):
        if vm.name == name:
            return getattr(vm, attr, None)
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
    entities: list = [
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
            state_class=None,
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
                    PoolSensor(
                        runtime,
                        runtime.storage,
                        f"pool_{key}_usage",
                        f"存储池 {pool.name} 使用率",
                        lambda d, k=key: _pool_attr(d, k, "usage"),
                        unit=PERCENTAGE,
                        icon="mdi:harddisk",
                        precision=1,
                        pool_key=key,
                    ),
                    PoolSensor(
                        runtime,
                        runtime.storage,
                        f"pool_{key}_used",
                        f"存储池 {pool.name} 已用",
                        lambda d, k=key: _gib(_pool_attr(d, k, "used")),
                        unit="GiB",
                        device_class=SensorDeviceClass.DATA_SIZE,
                        icon="mdi:harddisk",
                        precision=2,
                        pool_key=key,
                    ),
                    PoolSensor(
                        runtime,
                        runtime.storage,
                        f"pool_{key}_free",
                        f"存储池 {pool.name} 可用",
                        lambda d, k=key: _gib(_pool_attr(d, k, "free")),
                        unit="GiB",
                        device_class=SensorDeviceClass.DATA_SIZE,
                        icon="mdi:harddisk",
                        precision=2,
                        pool_key=key,
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

    vm_data = runtime.vm.data
    if vm_data:
        for vm in vm_data.vms:
            vm_name = vm.name
            entities.extend(
                [
                    ZspaceSensor(
                        runtime,
                        runtime.vm,
                        f"vm_{vm_name}_vcpu",
                        "虚拟 CPU",
                        lambda d, n=vm_name: _vm_attr(d, n, "cpu_count"),
                        icon="mdi:cpu-64-bit",
                        precision=0,
                    ),
                    ZspaceSensor(
                        runtime,
                        runtime.vm,
                        f"vm_{vm_name}_cpu_percent",
                        "CPU 使用率",
                        lambda d, n=vm_name: _vm_attr(d, n, "cpu_percent"),
                        unit=PERCENTAGE,
                        icon="mdi:speedometer",
                        precision=1,
                    ),
                    ZspaceSensor(
                        runtime,
                        runtime.vm,
                        f"vm_{vm_name}_memory_percent",
                        "内存使用率",
                        lambda d, n=vm_name: _vm_attr(d, n, "memory_percent"),
                        unit=PERCENTAGE,
                        icon="mdi:memory",
                        precision=1,
                    ),
                    ZspaceSensor(
                        runtime,
                        runtime.vm,
                        f"vm_{vm_name}_memory",
                        "内存",
                        lambda d, n=vm_name: _vm_attr(d, n, "memory_gib"),
                        unit="GiB",
                        device_class=SensorDeviceClass.DATA_SIZE,
                        icon="mdi:memory",
                        precision=2,
                    ),
                    ZspaceSensor(
                        runtime,
                        runtime.vm,
                        f"vm_{vm_name}_disk",
                        "磁盘容量",
                        lambda d, n=vm_name: _vm_attr(d, n, "disk_gib"),
                        unit="GiB",
                        device_class=SensorDeviceClass.DATA_SIZE,
                        icon="mdi:harddisk",
                        precision=2,
                    ),
                    ZspaceSensor(
                        runtime,
                        runtime.vm,
                        f"vm_{vm_name}_ip",
                        "IP 地址",
                        lambda d, n=vm_name: _vm_attr(d, n, "ip"),
                        icon="mdi:ip-network",
                        state_class=None,
                    ),
                    ZspaceSensor(
                        runtime,
                        runtime.vm,
                        f"vm_{vm_name}_vnc",
                        "VNC 端口",
                        lambda d, n=vm_name: _vm_attr(d, n, "vnc_port"),
                        icon="mdi:monitor-dashboard",
                        precision=0,
                    ),
                ]
            )

    # GPU / AI、Time Machine、系统事件概况
    entities.extend(
        [
            ZspaceSensor(
                runtime, runtime.gpu, "gpu_count", "外接 GPU 数量",
                lambda d: len(d.gpus) if d else None, icon="mdi:expansion-card", precision=0,
            ),
            ZspaceSensor(
                runtime, runtime.gpu, "ai_tasks", "AI 任务数",
                lambda d: len(d.tasks) if d else None, icon="mdi:brain", precision=0,
            ),
            ZspaceSensor(
                runtime, runtime.tm, "time_machine_usage", "Time Machine 用量",
                lambda d: getattr(d, "usage", None), unit=PERCENTAGE,
                icon="mdi:backup-restore", precision=1,
            ),
            ZspaceSensor(
                runtime, runtime.events, "system_events", "系统事件数",
                lambda d: len(d.events) if d else None, icon="mdi:bell-outline", precision=0,
            ),
        ]
    )

    # 聚合实体：懒加载 payload，首次被读取时其它实体已在注册表里
    entities.append(ZspaceAllEntitiesSensor(hass, entry, runtime))

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
