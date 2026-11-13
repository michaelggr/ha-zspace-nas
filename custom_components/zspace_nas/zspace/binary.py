"""定位 zspace-cli：优先用户填的路径，其次用集成内置的二进制。

内置二进制随 HACS 安装包一起下发，HAOS 上不需要再手动放文件。
"""

from __future__ import annotations

import os
import platform
import pathlib

_ARCH_FILES = {
    "x86_64": "zspace-cli-x64",
    "amd64": "zspace-cli-x64",
    "aarch64": "zspace-cli-arm64",
    "arm64": "zspace-cli-arm64",
}


def bundled_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "bin"


def bundled_name() -> str:
    return _ARCH_FILES.get(platform.machine().lower(), "zspace-cli-x64")


def resolve_binary(configured: str | None) -> str:
    """配置留空时回落到内置文件，兼容旧配置里的绝对路径。"""
    if configured:
        candidate = os.path.expanduser(str(configured).strip())
        if candidate and os.path.isfile(candidate):
            return candidate
    return str(bundled_dir() / bundled_name())


def binary_available(configured: str | None) -> bool:
    return os.path.isfile(resolve_binary(configured))
