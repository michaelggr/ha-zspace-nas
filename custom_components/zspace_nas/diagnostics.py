"""诊断导出：把状态给出来，但绝不给凭证。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_SENSITIVE = ("token", "authorization", "cookie", "password", "secret", "auth_code", "nas_id", "nasid")


def _redact(value: Any, depth: int = 0) -> Any:
    """递归遮蔽敏感字段，深层结构做深度保护。"""
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if any(marker in str(key).lower() for marker in _SENSITIVE):
                result[key] = "**REDACTED**"
            else:
                result[key] = _redact(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_redact(item, depth + 1) for item in value[:50]]
    if is_dataclass(value):
        return _redact(asdict(value), depth + 1)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    runtime = (hass.data.get(DOMAIN) or {}).get(entry.entry_id)
    if runtime is None:
        return {"error": "runtime not loaded"}

    return {
        "entry": {
            "data": _redact(dict(entry.data)),
            "options": _redact(dict(entry.options)),
            "version": entry.version,
        },
        "device": _redact(dict(runtime.device)),
        "coordinators": {
            "system": {
                "ok": runtime.system.last_update_success,
                "data": _redact(runtime.system.data),
            },
            "storage": {
                "ok": runtime.storage.last_update_success,
                "data": _redact(runtime.storage.data),
            },
            "docker": {
                "ok": runtime.docker.last_update_success,
                "data": _redact(runtime.docker.data),
            },
            "disk": {
                "ok": runtime.disk.last_update_success,
                "data": _redact(runtime.disk.data),
            },
            "tasks": {
                "ok": runtime.tasks.last_update_success,
                "data": _redact(runtime.tasks.data),
            },
        },
    }
