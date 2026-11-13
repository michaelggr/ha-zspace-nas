"""运行时对象：一条 config entry 对应一个 NAS。"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.helpers.device_registry import DeviceInfo

from .coordinator import (
    DiskCoordinator,
    DockerCoordinator,
    StorageCoordinator,
    SystemCoordinator,
    TaskCoordinator,
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
    guard: SafetyGuard
    allow_control: bool

    @property
    def coordinators(self) -> tuple:
        return (self.system, self.storage, self.docker, self.disk, self.tasks)
