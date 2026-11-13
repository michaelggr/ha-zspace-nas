"""配置流程：填参数 → 未登录时当场授权 → 建条目。

登录在 HA 内完成：集成起一个常驻 stdin 的 `zspace-cli login`，把授权链接交给用户，
用户把网页上的一次性授权码贴回表单，CLI 自己落盘登录态。
"""

from __future__ import annotations

import logging
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
    CONF_HOST,
    CONF_PROFILE,
    CONF_PROFILE_HOME,
    CONF_PROTECTED,
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
from .zspace import (
    ZspaceAuthError,
    ZspaceCliClient,
    ZspaceError,
    ZspaceLogin,
    ZspaceUnavailableError,
    resolve_binary,
)
from .zspace.base import pick
from .zspace.normalize import normalize_system

_LOGGER = logging.getLogger(__name__)


def _build_client(data: dict[str, Any]) -> ZspaceCliClient:
    return ZspaceCliClient(
        binary=data.get(CONF_BINARY, DEFAULT_BINARY),
        profile_home=data.get(CONF_PROFILE_HOME) or DEFAULT_PROFILE_HOME,
        profile=data.get(CONF_PROFILE) or DEFAULT_PROFILE,
        host=data.get(CONF_HOST) or None,
        allow_insecure_http=bool(data.get(CONF_ALLOW_INSECURE)),
    )


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Optional(CONF_BINARY, default=defaults.get(CONF_BINARY, DEFAULT_BINARY)): selector.TextSelector(),
            vol.Optional(
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
    """首次添加与重新授权共用一套授权流程。"""

    VERSION = 1

    def __init__(self) -> None:
        self._login: ZspaceLogin | None = None
        self._pending: dict[str, Any] | None = None
        self._reauth_entry: ConfigEntry | None = None

    # ── 第一步：参数 ────────────────────────────────────
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._pending = dict(user_input)
            client = _build_client(user_input)
            try:
                probe = await client.async_probe()
            except ZspaceAuthError:
                # 还没登录：直接进授权步骤，不报错
                return await self.async_step_auth()
            except ZspaceUnavailableError as err:
                errors["base"] = "binary_missing" if err.code == "E_INSTALL" else "cannot_connect"
            except ZspaceError:
                errors["base"] = "unknown"
            else:
                return await self._async_create_entry(user_input, probe)

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input),
            errors=errors,
            description_placeholders={
                "profile_dir": (user_input or {}).get(CONF_PROFILE_HOME, DEFAULT_PROFILE_HOME)
            },
        )

    # ── 第二步：网页授权 ────────────────────────────────
    async def async_step_auth(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        data = self._reauth_entry.data if self._reauth_entry else (self._pending or {})

        if user_input is not None:
            if self._login is None:
                errors["base"] = "expired"
            else:
                try:
                    await self._login.async_submit(user_input.get("code", ""))
                except ZspaceAuthError as err:
                    _LOGGER.warning("授权失败：%s", err.message)
                    errors["base"] = "invalid_code"
                    self._close_login()
                else:
                    self._close_login()
                    client = _build_client(data)
                    try:
                        probe = await client.async_probe()
                    except ZspaceError:
                        errors["base"] = "cannot_connect"
                    else:
                        if self._reauth_entry is not None:
                            return self.async_update_reload_and_abort(self._reauth_entry)
                        return await self._async_create_entry(data, probe)

        if self._login is None:
            try:
                self._login = await ZspaceLogin.async_start(
                    resolve_binary(data.get(CONF_BINARY)),
                    data.get(CONF_PROFILE_HOME) or DEFAULT_PROFILE_HOME,
                    data.get(CONF_PROFILE) or DEFAULT_PROFILE,
                )
            except ZspaceAuthError as err:
                _LOGGER.error("无法启动授权：%s", err.message)
                return self.async_show_form(
                    step_id="user",
                    data_schema=_user_schema(data),
                    errors={"base": "login_failed"},
                    description_placeholders={
                        "profile_dir": data.get(CONF_PROFILE_HOME, DEFAULT_PROFILE_HOME)
                    },
                )

        return self.async_show_form(
            step_id="auth",
            data_schema=vol.Schema({vol.Required("code"): selector.TextSelector()}),
            errors=errors,
            description_placeholders={
                "url": self._login.url,
                "expires_in": str(self._login.expires_in),
                "profile_dir": data.get(CONF_PROFILE_HOME, DEFAULT_PROFILE_HOME),
            },
        )

    # ── 重新授权 ────────────────────────────────────────
    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        self._pending = dict(entry_data)
        return await self.async_step_auth()

    # ── 收尾 ────────────────────────────────────────────
    async def _async_create_entry(self, data: dict[str, Any], probe: Any) -> FlowResult:
        info = normalize_system(probe)
        serial = info.serial or pick(probe, ["sn", "serial", "device_sn"])
        await self.async_set_unique_id(str(serial) if serial else self._fallback_id(data))
        self._abort_if_unique_id_configured()

        title = f"极空间 {info.model}" if info.model else "极空间 NAS"
        return self.async_create_entry(title=title, data=data)

    def _close_login(self) -> None:
        if self._login is not None:
            self._login.close()
            self._login = None

    @staticmethod
    def _fallback_id(user_input: dict[str, Any]) -> str:
        return str(user_input.get(CONF_HOST) or user_input.get(CONF_PROFILE_HOME) or "zspace")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ZspaceNasOptionsFlow(config_entry)


class ZspaceNasOptionsFlow(OptionsFlow):
    """控制开关、白名单与轮询周期。"""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self._entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ALLOW_CONTROL, default=options.get(CONF_ALLOW_CONTROL, False)
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_ALLOWED, default=options.get(CONF_ALLOWED, "")
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_DRY_RUN, default=options.get(CONF_DRY_RUN, False)
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_PROTECTED, default=options.get(CONF_PROTECTED, "")
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_COOLDOWN, default=options.get(CONF_COOLDOWN, DEFAULT_COOLDOWN)
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
                    selector.NumberSelectorConfig(min=60, max=86400, step=60, unit_of_measurement="s")
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
