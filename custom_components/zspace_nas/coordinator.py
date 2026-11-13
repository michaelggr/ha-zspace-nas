"""分级轮询：Docker 30s / 系统 60s / 存储 300s 各自独立。

原始结构由 `zspace/normalize.py` 归一化，这里只负责调度与异常映射。
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, seconds
from .zspace import ZspaceAuthError, ZspaceCliClient, ZspaceError
from .zspace.normalize import (
    ContainerInfo,
    DiskData,
    DiskInfo,
    DockerData,
    DownloadData,
    StorageData,
    SystemData,
    normalize_containers,
    normalize_disks,
    normalize_downloads,
    normalize_engine,
    normalize_smart,
    normalize_storage,
    normalize_system,
)

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "ContainerInfo",
    "DiskCoordinator",
    "DiskData",
    "DiskInfo",
    "DockerCoordinator",
    "DockerData",
    "DownloadData",
    "StorageCoordinator",
    "StorageData",
    "SystemCoordinator",
    "SystemData",
    "TaskCoordinator",
    "ZspaceBaseCoordinator",
]


class ZspaceBaseCoordinator(DataUpdateCoordinator[object]):
    """统一处理异常映射：登录失效进 reauth，其余标记不可用。"""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ZspaceCliClient,
        name: str,
        interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}_{name}",
            update_interval=seconds(interval),
            config_entry=entry,
        )
        self.client = client

    async def _async_update_data(self) -> object:
        try:
            return await self._fetch()
        except ZspaceAuthError as err:
            raise ConfigEntryAuthFailed(f"极空间登录态失效：{err.message}") from err
        except ZspaceError as err:
            raise UpdateFailed(f"{err.code}: {err.message}") from err

    async def _fetch(self) -> object:  # pragma: no cover - 子类实现
        raise NotImplementedError


class SystemCoordinator(ZspaceBaseCoordinator):
    """CPU / 内存 / 网络 / 风扇，60s 一次。"""

    async def _fetch(self) -> SystemData:
        raw = await self.client.async_call(
            "system", "info", {"fields": ["basic", "cpu", "memory", "network", "fan"]}
        )
        return normalize_system(raw)


class StorageCoordinator(ZspaceBaseCoordinator):
    """存储池容量，300s 一次。"""

    async def _fetch(self) -> StorageData:
        raw = await self.client.async_call("storage", "info", {})
        return normalize_storage(raw)


class DockerCoordinator(ZspaceBaseCoordinator):
    """容器列表 + 全部容器统计，30s 一次。

    list 与 stats 各一次调用，合并成同一份快照，保证不同实体读到同一时刻的状态；
    统计失败（缓存未就绪）不影响容器列表本身。
    """

    async def _fetch(self) -> DockerData:
        listing = await self.client.async_call("docker", "containers", {"action": "list"})
        stats = None
        try:
            stats = await self.client.async_call("docker", "containers", {"action": "stats"})
        except ZspaceError as err:
            _LOGGER.debug("容器统计不可用：%s", err)

        data = normalize_containers(listing, stats)

        try:
            engine_raw = await self.client.async_call("docker", "system", {"action": "status"})
            data.engine_running = normalize_engine(engine_raw)
        except ZspaceError as err:
            _LOGGER.debug("Docker 引擎状态不可用：%s", err)

        return data


class DiskCoordinator(ZspaceBaseCoordinator):
    """磁盘容量 + 逐盘 SMART，默认 1 小时一次。

    SMART 必须逐盘查询（单盘约 0.4 秒），所以单独放慢周期；
    单盘失败只丢那一块盘的 SMART，不影响其它盘和容量信息。
    """

    async def _fetch(self) -> DiskData:
        raw = await self.client.async_call("system", "disk", {"include": ["mounts", "statics"]})
        base = normalize_disks(raw, None)

        smart_map: dict[str, dict] = {}
        for disk in base.disks:
            try:
                smart_raw = await self.client.async_call(
                    "storage", "smart", {"sn": disk.sn, "lang": "zh"}
                )
            except ZspaceError as err:
                _LOGGER.debug("磁盘 %s 的 SMART 不可用：%s", disk.sn, err)
                continue
            smart_map[disk.sn] = normalize_smart(smart_raw)

        return normalize_disks(raw, smart_map)


class TaskCoordinator(ZspaceBaseCoordinator):
    """下载任务概况，5 分钟一次。"""

    async def _fetch(self) -> DownloadData:
        raw = await self.client.async_call("download", "tasks", {"start": 0, "num": 50})
        return normalize_downloads(raw)
