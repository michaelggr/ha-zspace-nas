"""配置流程：填路径 → 探活 → 建条目。

登录态不在这里做（CLI 的 login 需要一个吊着 180 秒的交互进程），
改为在电脑上授权一次，把 profile 目录整目录搬到 NAS 旁边的 /config 里。
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_ALLOW_CONTROL,
    CONF_ALLOWED,
    CONF_ALLOW_INSECURE,
    CONF_BINARY,
    CONF_COOLDOWN,
    CONF_DISK_INTERVAL,
    CONF_DOCKER_INTERVAL,
    CONF_DOWNLOAD_INTERVAL,
    CONF_DRY_RUN,
    CONF_PROTECTED,
    CONF_HOST,
    CONF_PROFILE,
    CONF_PROFILE_HOME,
    CONF_STORAGE_INTERVAL,
    CONF_SYSTEM_INTERVAL,
    DEFAULT_BINARY,
    DEFAULT_COOLDOWN,
    DEFAULT_DISK_INTERVAL,
    DEFAULT_DOCKER_INTERVAL,
    DEFAULT_DOWNLOAD_INTERVAL,
    DEFAULT_PROFILE,
    DEFAULT_PROFILE_HOME,
    DEFAULT_STORAGE_INTERVAL,
    DEFAULT_SYSTEM_INTERVAL,
    DOMAIN,
    MAX_COOLDOWN,
    MAX_INTERVAL,
    MIN_COOLDOWN,
    MIN_INTERVAL,
)
from .zspace import ZspaceAuthError, ZspaceCliClient, ZspaceError, ZspaceUnavailableError
from .zspace.base import pick


def _build_client(data: dict[str, Any]) -> ZspaceCliClient:
    return ZspaceCliClient(
        binary=data[CONF_BINARY],
        profile_home=data[CONF_PROFILE_HOME],
        profile=data.get(CONF_PROFILE) or DEFAULT_PROFILE,
        host=data.get(CONF_HOST) or None,
        allow_insecure_http=bool(data.get(CONF_ALLOW_INSECURE)),
    )


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_BINARY, default=defaults.get(CONF_BINARY, DEFAULT_BINARY)): selector.TextSelector(),
            vol.Required(
                CONF_PROFILE_HOME, default=defaults.get(CONF_PROFILE_HOME, DEFAULT_PROFILE_HOME)
            ): selector.TextSelector(),
            vol.Optional(CONF_PROFILE, default=defaults.get(CONF_PROFILE, DEFAULT_PROFILE)): selector.TextSelector(),
            vol.Optional(CONF_HOST, default=defaults.get(CONF_HOST, "")): selector.TextSelector(),
            vol.Optional(
                CONF_ALLOW_INSECURE, default=defaults.get(CONF_ALLOW_INSECURE, False)
            ): selector.BooleanSelector(),
        }
    )


class ZspaceNasConfigFlow(ConfigFlow, domain=DOMAIN):
    """首次添加与重新授权共用一套校验。"""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            client = _build_client(user_input)
            try:
                probe = await client.async_probe()
            except ZspaceAuthError:
                errors["base"] = "auth"
            except ZspaceUnavailableError as err:
                # E_INSTALL 说明二进制没放对，其它按连不上处理
                errors["base"] = "binary_missing" if err.code == "E_INSTALL" else "cannot_connect"
            except ZspaceError:
                errors["base"] = "unknown"
            else:
                serial = pick(probe, ["sn", "serial", "serial_number", "device_sn"])
                await self.async_set_unique_id(str(serial) if serial else self._fallback_id(user_input))
                self._abort_if_unique_id_configured()

                model = pick(probe, ["model", "product", "device_model"])
                title = f"极空间 {model}" if model else "极空间 NAS"
                return self.async_create_entry(title=str(title), data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input),
            errors=errors,
            description_placeholders={
                "profile_dir": (user_input or {}).get(CONF_PROFILE_HOME, DEFAULT_PROFILE_HOME)
            },
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        self._reauth_entry: ConfigEntry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = _build_client(self._reauth_entry.data)
            try:
                await client.async_probe()
            except ZspaceAuthError:
                errors["base"] = "auth"
            except ZspaceError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(self._reauth_entry)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={
                "profile_dir": self._reauth_entry.data.get(CONF_PROFILE_HOME, DEFAULT_PROFILE_HOME)
            },
        )

    @staticmethod
    def _fallback_id(user_input: dict[str, Any]) -> str:
        """没有序列号时用地址/路径兜底，保证唯一。"""
        return str(user_input.get(CONF_HOST) or user_input.get(CONF_PROFILE_HOME) or "zspace")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ZspaceNasOptionsFlow(config_entry)


class ZspaceNasOptionsFlow(OptionsFlow):
    """控制开关与轮询周期。"""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self._entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ALLOW_CONTROL,
                    default=options.get(CONF_ALLOW_CONTROL, False),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_DRY_RUN,
                    default=options.get(CONF_DRY_RUN, False),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_ALLOWED,
                    default=options.get(CONF_ALLOWED, ""),
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_PROTECTED,
                    default=options.get(CONF_PROTECTED, ""),
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_COOLDOWN,
                    default=options.get(CONF_COOLDOWN, DEFAULT_COOLDOWN),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_COOLDOWN, max=MAX_COOLDOWN, step=5, unit_of_measurement="s"
                    )
                ),
                vol.Optional(
                    CONF_DOCKER_INTERVAL,
                    default=options.get(CONF_DOCKER_INTERVAL, DEFAULT_DOCKER_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_INTERVAL, max=MAX_INTERVAL, step=5, unit_of_measurement="s"
                    )
                ),
                vol.Optional(
                    CONF_SYSTEM_INTERVAL,
                    default=options.get(CONF_SYSTEM_INTERVAL, DEFAULT_SYSTEM_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_INTERVAL, max=MAX_INTERVAL, step=5, unit_of_measurement="s"
                    )
                ),
                vol.Optional(
                    CONF_STORAGE_INTERVAL,
                    default=options.get(CONF_STORAGE_INTERVAL, DEFAULT_STORAGE_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_INTERVAL, max=MAX_INTERVAL, step=30, unit_of_measurement="s"
                    )
                ),
                vol.Optional(
                    CONF_DOWNLOAD_INTERVAL,
                    default=options.get(CONF_DOWNLOAD_INTERVAL, DEFAULT_DOWNLOAD_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_INTERVAL, max=MAX_INTERVAL, step=30, unit_of_measurement="s"
                    )
                ),
                vol.Optional(
                    CONF_DISK_INTERVAL,
                    default=options.get(CONF_DISK_INTERVAL, DEFAULT_DISK_INTERVAL),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=60, max=86400, step=60, unit_of_measurement="s"
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
