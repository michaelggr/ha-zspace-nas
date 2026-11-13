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
from .entity import ZspaceEntity, container_device_info, find_container
from .runtime import ZspaceRuntime
from .zspace import ZspaceError

_LOGGER = logging.getLogger(__name__)


class ContainerRestartButton(ZspaceEntity, ButtonEntity):
    _attr_icon = "mdi:restart"

    def __init__(self, runtime: ZspaceRuntime, container: ContainerInfo) -> None:
        super().__init__(
            runtime, runtime.docker, f"container_{container.id[:12] or container.name}_restart"
        )
        self._container_id = container.id or container.name
        self._attr_name = f"{container.name} 重启"
        self._container_device = container_device_info(
            runtime, self._container_id, container.name, container.image
        )

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

    data = runtime.docker.data
    if not data:
        return

    targets = [
        container
        for container in data.containers
        if runtime.guard.is_allowed(container.name, container.id)
    ]
    if not targets:
        return

    async_add_entities(
        (ContainerRestartButton(runtime, container) for container in targets),
        update_before_add=False,
    )
