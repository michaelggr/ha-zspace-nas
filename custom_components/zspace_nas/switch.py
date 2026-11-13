"""容器开关：只做 start / stop，且在动作下发前过一遍安全护栏。

三层防护：
1. 没开「允许容器控制」时平台不生成实体；
2. 护栏拦截受保护容器、状态不对、冷却期内的重复操作；
3. 客户端在启动子进程前还会再校验一次写操作白名单。
"""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ContainerInfo
from .entity import ZspaceEntity, container_device_info, find_container
from .runtime import ZspaceRuntime
from .zspace import ZspaceError

_LOGGER = logging.getLogger(__name__)


class ContainerSwitch(ZspaceEntity, SwitchEntity):
    """一个容器一个开关，带乐观状态避免点击后短暂回弹。"""

    _attr_icon = "mdi:docker"

    def __init__(self, runtime: ZspaceRuntime, container: ContainerInfo) -> None:
        super().__init__(
            runtime, runtime.docker, f"container_{container.id[:12] or container.name}_power"
        )
        self._container_id = container.id or container.name
        # 容器本身就是设备，开关作为主实体，不再重复容器名
        self._attr_name = None
        self._container_device = container_device_info(
            runtime, self._container_id, container.name, container.image
        )
        self._pending: bool | None = None

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
    def extra_state_attributes(self) -> dict[str, object]:
        container = self._container
        protected = self.runtime.guard.is_protected(
            container.name if container else None, self._container_id
        )
        return {
            "protected": protected,
            "dry_run": self.runtime.guard.policy.dry_run,
            "cooldown_remaining": self.runtime.guard.cooldown_remaining(self._container_id),
        }

    @property
    def is_on(self) -> bool | None:
        if self._pending is not None:
            return self._pending
        container = self._container
        return container.running if container else None

    async def async_turn_on(self, **kwargs: object) -> None:
        await self._async_act("start", True)

    async def async_turn_off(self, **kwargs: object) -> None:
        await self._async_act("stop", False)

    async def _async_act(self, action: str, optimistic: bool) -> None:
        container = self._container
        decision = self.runtime.guard.evaluate(
            name=container.name if container else self._container_id,
            container_id=self._container_id,
            running=container.running if container else None,
            action=action,
        )
        if not decision.allowed:
            raise HomeAssistantError(f"已拦截：{decision.reason}")
        if decision.simulated:
            _LOGGER.warning(
                "演练模式：跳过 %s %s（真正执行请到选项里关闭演练模式）",
                action,
                self._container_id,
            )
            return

        self._pending = optimistic
        self.async_write_ha_state()
        try:
            await self.runtime.client.async_call(
                "docker",
                "containers",
                {"action": action, "container_id": self._container_id},
                write=True,
            )
        except ZspaceError as err:
            self._pending = None
            self.async_write_ha_state()
            raise HomeAssistantError(f"{err.code}: {err.message}") from err

        self.runtime.guard.record(
            container.name if container else None, self._container_id
        )
        await self.coordinator.async_request_refresh()

    def _handle_coordinator_update(self) -> None:
        # 真实状态回来了，撤掉乐观值
        self._pending = None
        super()._handle_coordinator_update()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime: ZspaceRuntime = hass.data[DOMAIN][entry.entry_id]
    if not runtime.allow_control:
        # 未在选项里开启控制时不生成实体，避免误触
        return

    data = runtime.docker.data
    if not data:
        return

    # 只给白名单里的容器建实体：默认名单为空时，一个开关都不会出现
    targets = [
        container
        for container in data.containers
        if runtime.guard.is_allowed(container.name, container.id)
    ]
    if not targets:
        return

    async_add_entities(
        (ContainerSwitch(runtime, container) for container in targets),
        update_before_add=False,
    )
