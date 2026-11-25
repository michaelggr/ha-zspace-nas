"""运行时对象：一条 config entry 对应一个 NAS。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo

from .coordinator import (
    DiskCoordinator,
    DockerCoordinator,
    EventCoordinator,
    GpuCoordinator,
    StorageCoordinator,
    SystemCoordinator,
    TaskCoordinator,
    TimeMachineCoordinator,
    VmCoordinator,
)
from .safety import SafetyGuard
from .zspace import ZspaceCliClient


@dataclass
class ZspaceRuntime:
    client: ZspaceCliClient
    identifier: str  # device registry 里的 NAS 标识，用于拼 unique_id
    device: DeviceInfo
    system: SystemCoordinator
    storage: StorageCoordinator
    docker: DockerCoordinator
    disk: DiskCoordinator
    tasks: TaskCoordinator
    vm: VmCoordinator
    gpu: GpuCoordinator
    tm: TimeMachineCoordinator
    events: EventCoordinator
    guard: SafetyGuard
    allow_control: bool
    # 是否启用短英文 entity_id（选项 short_entity_ids）
    short_ids: bool = False
    # 虚拟机控制台：是否开启、签名密钥、优先使用的 VNC 主机
    entry_id: str = ""
    vnc_enabled: bool = False
    vnc_secret: bytes = b""
    vnc_host: str | None = None
    # 事件播报器（可选），由 announce.setup_announcements 装配
    announcer: Any = None

    @property
    def coordinators(self) -> tuple:
        return (
            self.system, self.storage, self.docker, self.disk,
            self.tasks, self.vm, self.gpu, self.tm, self.events,
        )
