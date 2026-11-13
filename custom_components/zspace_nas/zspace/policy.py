"""写操作白名单：在启动子进程之前就把危险动作拦住。

这一层不依赖 Home Assistant，可以被单元测试直接覆盖。
原则是白名单而不是黑名单——没被显式允许的写动作一律拒绝，宁可误拒也不误放。
"""

from __future__ import annotations

from typing import Any

from .base import ZspaceError


class ZspacePolicyError(ZspaceError):
    """被策略拦截（不是 NAS 返回的错误）。"""


# 唯一允许的写操作：容器 启动/停止/重启。删除、强制停止、引擎开关一律不在其中。
WRITE_ACTIONS: dict[tuple[str, str], frozenset[str]] = {
    ("docker", "containers"): frozenset({"start", "stop", "restart"}),
}

# 只读子动作（用于 dump_raw 与混合能力）
READ_ACTIONS: frozenset[str] = frozenset(
    {
        "list",
        "info",
        "stat",
        "stats",
        "top",
        "logs",
        "status",
        "engine_info",
        "storage",
        "disks",
        "usage",
        "version",
        "tasks",
        "common",
        "pull_tasks",
        "download_tasks",
        "proxy",
        "registry",
        "private_hub",
        "env",
        "cron",
        "ipv6",
        "path",
        "check_name",
        "check_path",
    }
)

# dump_raw 单独收紧：只有这些能力允许被导出，且带 action 时必须命中 READ_ACTIONS
DUMP_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        ("system", "info"),
        ("system", "disk"),
        ("system", "device"),
        ("system", "services"),
        ("system", "network"),
        ("system", "ddns"),
        ("storage", "info"),
        ("storage", "smart"),
        ("docker", "containers"),
        ("docker", "images"),
        ("docker", "networks"),
        ("docker", "compose"),
        ("docker", "system"),
        ("download", "tasks"),
        ("backup", "status"),
        ("app", "list"),
        ("app", "status"),
        ("vm", "vm"),
        ("iscsi", "iscsi"),
        ("ops", "security"),
    }
)


# 只读路径上的兜底黑名单：即使某处代码写错，也不允许这些子动作借"读"通道下发
DESTRUCTIVE_SUBACTIONS: frozenset[str] = frozenset(
    {
        "remove",
        "force_remove",
        "force_stop",
        "delete",
        "format",
        "reset",
        "rebuild",
        "prune",
        "wipe",
        "restore",
        "restore_all",
        "empty",
        "shutdown",
        "reboot",
        "power",
        "upgrade",
        "switch",
    }
)


def assert_not_destructive(params: dict[str, Any] | None) -> None:
    sub_action = str((params or {}).get("action", "")).lower()
    if sub_action in DESTRUCTIVE_SUBACTIONS:
        raise ZspacePolicyError(
            "E_POLICY", f"只读通道拒绝高危子动作：{sub_action}"
        )


def assert_write_allowed(
    domain: str,
    action: str,
    params: dict[str, Any] | None,
    *,
    enabled: bool,
) -> None:
    """写操作三重校验：总开关、域+动作、子动作。"""
    if not enabled:
        raise ZspacePolicyError("E_POLICY", "集成未开启写操作（选项里未允许容器控制）")

    allowed = WRITE_ACTIONS.get((domain, action))
    if allowed is None:
        raise ZspacePolicyError("E_POLICY", f"不允许的写操作：{domain} {action}")

    sub_action = str((params or {}).get("action", "")).lower()
    if sub_action not in allowed:
        raise ZspacePolicyError(
            "E_POLICY", f"不允许的容器动作：{sub_action or '(空)'}，仅支持 {sorted(allowed)}"
        )


def assert_dump_allowed(
    domain: str, action: str, params: dict[str, Any] | None
) -> None:
    """dump_raw 只放行已知只读能力，避免被当成万能命令通道。"""
    if (domain, action) not in DUMP_ALLOWLIST:
        raise ZspacePolicyError("E_POLICY", f"{domain} {action} 不在只读导出白名单内")

    sub_action = (params or {}).get("action")
    if sub_action is None:
        return
    if str(sub_action).lower() not in READ_ACTIONS:
        raise ZspacePolicyError("E_POLICY", f"{domain} {action} 的 {sub_action} 不是只读动作")
