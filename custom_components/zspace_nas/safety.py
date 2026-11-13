"""高危动作的运行时护栏：受保护容器、状态前置条件、冷却时间、演练模式。

不依赖 Home Assistant，便于单测覆盖。任何一条不满足就不放行，
调用方负责把 reason 直接展示给用户。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

# 名字里带这些片段的容器默认视为系统容器，不参与启停
DEFAULT_PROTECTED_PATTERNS: tuple[str, ...] = (
    "zspace",
    "znas",
    "zido",
    "zvm",
    "zapp",
    "zconnect",
    "zdrive",
)

ACTION_LABELS = {"start": "启动", "stop": "停止", "restart": "重启"}


@dataclass(frozen=True)
class SafetyPolicy:
    allow_control: bool = False
    dry_run: bool = False
    cooldown: int = 15
    protected: tuple[str, ...] = DEFAULT_PROTECTED_PATTERNS
    # 控制白名单：只有写在这里的容器才允许启停；留空表示全部不允许
    allowed: tuple[str, ...] = ()


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""
    simulated: bool = False


@dataclass
class SafetyGuard:
    policy: SafetyPolicy
    _clock: Callable[[], float] = field(default=time.monotonic)
    _last_action: dict[str, float] = field(default_factory=dict)

    def is_protected(self, name: str | None, container_id: str | None = None) -> bool:
        haystack = f"{name or ''} {container_id or ''}".lower()
        if not haystack.strip():
            return True
        return any(pattern.lower() in haystack for pattern in self.policy.protected if pattern)

    def is_allowed(self, name: str | None, container_id: str | None = None) -> bool:
        """白名单匹配：没配置就一个都不许动；`*` 表示全部允许。"""
        if not self.policy.allowed:
            return False
        if "*" in self.policy.allowed:
            return True
        haystack = f"{name or ''} {container_id or ''}".lower()
        return any(pattern.lower() in haystack for pattern in self.policy.allowed if pattern)

    def cooldown_remaining(self, key: str) -> int:
        last = self._last_action.get(key)
        if last is None or self.policy.cooldown <= 0:
            return 0
        remain = self.policy.cooldown - (self._clock() - last)
        return max(int(remain) + (1 if remain % 1 else 0), 0)

    def evaluate(
        self,
        *,
        name: str | None,
        container_id: str | None,
        running: bool | None,
        action: str,
    ) -> Decision:
        """按"开关 → 保护名单 → 状态 → 冷却"的顺序逐条放行或拦截。"""
        label = ACTION_LABELS.get(action, action)

        if not self.policy.allow_control:
            return Decision(False, "容器控制未开启，请到集成选项里允许控制")
        if action not in ACTION_LABELS:
            return Decision(False, f"不支持的动作：{action}")
        if self.is_protected(name, container_id):
            return Decision(False, f"{name or container_id} 属于受保护容器，禁止{label}")
        if not self.is_allowed(name, container_id):
            return Decision(
                False,
                f"{name or container_id} 不在允许控制的名单里（选项 → 允许控制的容器）",
            )

        if running is True and action == "start":
            return Decision(False, f"{name} 已在运行，无需{label}")
        if running is False and action in ("stop", "restart"):
            return Decision(False, f"{name} 已处于停止状态，无法{label}")

        key = container_id or name or ""
        remain = self.cooldown_remaining(key)
        if remain > 0:
            return Decision(False, f"{name} 操作过于频繁，请 {remain} 秒后再试")

        return Decision(True, simulated=self.policy.dry_run)

    def record(self, name: str | None, container_id: str | None) -> None:
        """动作真正下发后调用，用于冷却计时。"""
        key = container_id or name or ""
        if key:
            self._last_action[key] = self._clock()

    def reset(self) -> None:
        self._last_action.clear()


def _split(value: str | tuple[str, ...] | list[str] | None) -> list[str]:
    if isinstance(value, str):
        candidates = value.replace("，", ",").split(",")
    else:
        candidates = list(value or [])
    return [str(item).strip() for item in candidates if str(item).strip()]


def build_policy(
    *,
    allow_control: bool,
    dry_run: bool,
    cooldown: int,
    extra_protected: str | tuple[str, ...] | list[str] | None,
    allowed: str | tuple[str, ...] | list[str] | None = None,
) -> SafetyPolicy:
    """把选项里的逗号分隔名单合并进默认系统容器名单。"""
    patterns = list(DEFAULT_PROTECTED_PATTERNS)
    for name in _split(extra_protected):
        if name not in patterns:
            patterns.append(name)
    return SafetyPolicy(
        allow_control=allow_control,
        dry_run=dry_run,
        cooldown=max(int(cooldown), 0),
        protected=tuple(patterns),
        allowed=tuple(dict.fromkeys(_split(allowed))),
    )
