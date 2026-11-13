"""极空间数据通道。

当前实现是通道 A（子进程 CLI）。未来换成原生 HTTP 时，只需提供同样签名的
`async_call(domain, action, params)`，协调器与实体层不用改。
"""

from .base import (
    ZspaceAuthError,
    ZspaceError,
    ZspaceNotFoundError,
    ZspacePermissionError,
    ZspaceUnavailableError,
)
from .binary import binary_available, resolve_binary
from .cli import ZspaceCliClient
from .login import ZspaceLogin
from .policy import ZspacePolicyError

__all__ = [
    "ZspaceAuthError",
    "ZspaceLogin",
    "binary_available",
    "resolve_binary",
    "ZspaceCliClient",
    "ZspaceError",
    "ZspaceNotFoundError",
    "ZspacePermissionError",
    "ZspacePolicyError",
    "ZspaceUnavailableError",
]
