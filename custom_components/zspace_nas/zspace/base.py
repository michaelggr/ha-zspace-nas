"""极空间 CLI 的返回值全是 JSON，但字段命名与嵌套层级随固件变化。

这里不硬编码结构，改成"按别名递归查找"，再用 dump_raw 服务核对真实样本。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

_UNIT_FACTORS = {
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "kib": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "mib": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "gib": 1024**3,
    "t": 1024**4,
    "tb": 1024**4,
    "tib": 1024**4,
    "p": 1024**5,
    "pb": 1024**5,
}


class ZspaceError(Exception):
    """CLI 返回的业务错误或调用失败。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ZspaceAuthError(ZspaceError):
    """登录态失效，需要重新授权（E_OAUTH / E_CREDENTIAL）。"""


class ZspacePermissionError(ZspaceError):
    """账号权限不足（E_PERMISSION / E_POLICY）。"""


class ZspaceNotFoundError(ZspaceError):
    """对象不存在（E_NOT_FOUND）。"""


class ZspaceUnavailableError(ZspaceError):
    """网络、超时或二进制不可用。"""


def normalize_key(value: str) -> str:
    """抹平大小写、下划线与连字符，便于别名匹配。"""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def pick(
    obj: Any,
    aliases: Iterable[str],
    *,
    default: Any = None,
    max_depth: int = 6,
) -> Any:
    """在任意嵌套结构里按别名广度优先查找第一个非空值。

    容器列表、系统信息这类返回结构的字段名不固定（`cpu` / `cpu_info` / `CPUInfo`），
    逐个 get 会写成一堆 if；这里统一交给别名匹配，取不到再退化。
    """
    targets = {normalize_key(a) for a in aliases}
    queue: list[tuple[Any, int]] = [(obj, 0)]
    while queue:
        node, depth = queue.pop(0)
        if depth > max_depth:
            continue
        if isinstance(node, dict):
            for key, value in node.items():
                if normalize_key(key) in targets and value not in (None, "", [], {}):
                    return value
            queue.extend((v, depth + 1) for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            queue.extend((v, depth + 1) for v in node if isinstance(v, (dict, list)))
    return default


def as_list(value: Any, *, keys: Iterable[str] = ("list", "items", "data")) -> list[Any]:
    """把 `[...]` 或 `{"list": [...]}` 统一成列表。"""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, list):
                return candidate
        # 退化：取第一个列表值
        for candidate in value.values():
            if isinstance(candidate, list):
                return candidate
    return []


def as_float(value: Any) -> float | None:
    """解析 `12.5%` / `12,5` / `12` 这类值。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "").replace("℃", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def as_int(value: Any) -> int | None:
    number = as_float(value)
    return int(number) if number is not None else None


def as_bool(value: Any) -> bool | None:
    """兼容 true/1/"on"/"running" 这类布尔表示。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on", "running", "up", "ok", "healthy"):
        return True
    if text in ("false", "0", "no", "off", "stopped", "exited", "down", "error"):
        return False
    return None


def as_bytes(value: Any) -> int | None:
    """解析 `1.2 TB` / `5368709120` / `12 GiB` 这类容量。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().lower().replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*([a-z]*)", text)
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2) or "b"
    return int(amount * _UNIT_FACTORS.get(unit, 1))


def percentage(used: float | int | None, total: float | int | None) -> float | None:
    """已用/总量算百分比，缺数据时返回 None 而不是 0。"""
    if used is None or not total:
        return None
    return round(float(used) / float(total) * 100, 1)
