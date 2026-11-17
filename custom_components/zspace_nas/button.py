"""容器重启按钮：同样走安全护栏，演练模式下只记录不执行。"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ContainerInfo
from .entity import (
    ZspaceEntity,
    async_forget_entity,
    container_device_info,
    find_container,
)
from .naming import token_or_hash
from .runtime import ZspaceRuntime
from .zspace import ZspaceError

_LOGGER = logging.getLogger(__name__)


class ContainerRestartButton(ZspaceEntity, ButtonEntity):
    _platform_domain = "button"
    _attr_icon = "mdi:restart"

    def __init__(self, runtime: ZspaceRuntime, container: ContainerInfo) -> None:
        # 先就位：基类构造时会用它推 entity_id
        self._container_id = container.id or container.name
        self._container_name = container.name
        super().__init__(
            runtime, runtime.docker, f"container_{container.id[:12] or container.name}_restart"
        )
        self._attr_name = f"{container.name} 重启"
        self._container_device = container_device_info(
            runtime, self._container_id, container.name, container.image
        )

    def _entity_id_parts(self) -> tuple[str, ...]:
        name = getattr(self, "_container_name", "") or self._container_id
        return (token_or_hash(name), "restart")

    @property
    def container_key(self) -> str:
        return self._container_id

    @property
    def device_info(self) -> DeviceInfo:
        return self._container_device

    @property
    def available(self) -> bool:
        container = find_container(self.coordinator.data, self._container_id)
        # 只有运行中的容器才给重启按钮：已停止的点了也只会被护栏拒
        return (
            self.coordinator.last_update_success
            and container is not None
            and container.running
        )

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        container = find_container(self.coordinator.data, self._container_id)
        return {
            "protected": self.runtime.guard.is_protected(
                container.name if container else None, self._container_id
            ),
            "dry_run": self.runtime.guard.policy.dry_run,
        }

    async def async_press(self) -> None:
        container = find_container(self.coordinator.data, self._container_id)
        decision = self.runtime.guard.evaluate(
            name=container.name if container else self._container_id,
            container_id=self._container_id,
            running=container.running if container else None,
            action="restart",
        )
        if not decision.allowed:
            raise HomeAssistantError(f"已拦截：{decision.reason}")
        if decision.simulated:
            _LOGGER.warning("演练模式：跳过 restart %s", self._container_id)
            return

        try:
            await self.runtime.client.async_call(
                "docker",
                "containers",
                {"action": "restart", "container_id": self._container_id},
                write=True,
            )
        except ZspaceError as err:
            raise HomeAssistantError(f"{err.code}: {err.message}") from err

        self.runtime.guard.record(container.name if container else None, self._container_id)
        await self.coordinator.async_request_refresh()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime: ZspaceRuntime = hass.data[DOMAIN][entry.entry_id]
    if not runtime.allow_control:
        return

    def _targets() -> dict[str, ContainerInfo]:
        data = runtime.docker.data
        if not data:
            return {}
        return {
            container.id or container.name: container
            for container in data.containers
            if runtime.guard.is_allowed(container.name, container.id)
        }

    tracked: dict[str, ContainerRestartButton] = {}
    initial: list[ContainerRestartButton] = []
    for key, container in _targets().items():
        entity = ContainerRestartButton(runtime, container)
        tracked[key] = entity
        initial.append(entity)

    if initial:
        async_add_entities(initial, update_before_add=False)

    def _sync_buttons() -> None:
        """容器增删跟着走：新容器补按钮，消失的连注册表一起清掉。"""
        current = _targets()
        if runtime.docker.data is None:
            return

        for key in [key for key in tracked if key not in current]:
            hass.async_create_task(async_forget_entity(hass, tracked.pop(key)))

        fresh = [
            ContainerRestartButton(runtime, container)
            for key, container in current.items()
            if key not in tracked
        ]
        if not fresh:
            return
        for entity in fresh:
            tracked[entity.container_key] = entity
        async_add_entities(fresh, update_before_add=False)

    entry.async_on_unload(runtime.docker.async_add_listener(_sync_buttons))
