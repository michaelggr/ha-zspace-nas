"""安全护栏测试：总开关、保护名单、控制白名单、状态前置、冷却、演练、写白名单。

跑法：
    python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "custom_components" / "zspace_nas"
sys.path.insert(0, str(_PKG))

from safety import SafetyGuard, SafetyPolicy, build_policy  # noqa: E402
from zspace.policy import (  # noqa: E402
    ZspacePolicyError,
    assert_dump_allowed,
    assert_not_destructive,
    assert_write_allowed,
)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class GuardTest(unittest.TestCase):
    def _guard(self, **kwargs) -> SafetyGuard:
        self.clock = Clock()
        return SafetyGuard(policy=SafetyPolicy(**kwargs), _clock=self.clock)

    def test_control_off_denies_everything(self) -> None:
        guard = self._guard(allow_control=False, allowed=("app",))
        decision = guard.evaluate(name="app", container_id="abc", running=True, action="stop")
        self.assertFalse(decision.allowed)
        self.assertIn("未开启", decision.reason)

    def test_whitelist_empty_denies_everything(self) -> None:
        # 默认名单为空：装了集成也不会出现任何能动的开关
        guard = self._guard(allow_control=True)
        decision = guard.evaluate(name="qbittorrent", container_id="abc", running=True, action="stop")
        self.assertFalse(decision.allowed)
        self.assertIn("不在允许控制", decision.reason)
        self.assertFalse(guard.is_allowed("qbittorrent", "abc"))

    def test_whitelist_grants_named_container_only(self) -> None:
        guard = self._guard(allow_control=True, allowed=("qbittorrent",))
        ok = guard.evaluate(name="qbittorrent", container_id="abc", running=True, action="stop")
        self.assertTrue(ok.allowed)
        other = guard.evaluate(name="mysql", container_id="def", running=True, action="stop")
        self.assertFalse(other.allowed)

    def test_wildcard_allows_all_non_protected(self) -> None:
        guard = self._guard(allow_control=True, allowed=("*",))
        self.assertTrue(guard.is_allowed("qbittorrent", "abc"))
        decision = guard.evaluate(name="qbittorrent", container_id="abc", running=True, action="stop")
        self.assertTrue(decision.allowed)
        # 通配不越过受保护名单
        blocked = guard.evaluate(name="zspace-video", container_id="def", running=True, action="stop")
        self.assertFalse(blocked.allowed)
        self.assertIn("受保护", blocked.reason)

    def test_protected_wins_over_whitelist(self) -> None:
        # 即使写进白名单，系统容器依然被拒
        guard = self._guard(allow_control=True, allowed=("zspace-video",))
        decision = guard.evaluate(name="zspace-video", container_id="abc", running=True, action="stop")
        self.assertFalse(decision.allowed)
        self.assertIn("受保护", decision.reason)

    def test_user_extra_protection(self) -> None:
        guard = self._guard(allow_control=True, allowed=("jellyfin",), protected=("jellyfin",))
        decision = guard.evaluate(name="jellyfin", container_id="abc", running=True, action="restart")
        self.assertFalse(decision.allowed)

    def test_state_precondition(self) -> None:
        guard = self._guard(allow_control=True, allowed=("app",))
        stopped = guard.evaluate(name="app", container_id="a", running=False, action="restart")
        self.assertFalse(stopped.allowed)
        already = guard.evaluate(name="app", container_id="a", running=True, action="start")
        self.assertFalse(already.allowed)

    def test_unknown_state_is_not_blocked(self) -> None:
        guard = self._guard(allow_control=True, allowed=("app",))
        decision = guard.evaluate(name="app", container_id="a", running=None, action="restart")
        self.assertTrue(decision.allowed)

    def test_cooldown_blocks_second_action(self) -> None:
        guard = self._guard(allow_control=True, allowed=("app",), cooldown=30)
        self.assertTrue(guard.evaluate(name="app", container_id="a", running=True, action="restart").allowed)
        guard.record("app", "a")

        self.clock.now += 10
        blocked = guard.evaluate(name="app", container_id="a", running=True, action="restart")
        self.assertFalse(blocked.allowed)
        self.assertIn("频繁", blocked.reason)

        self.clock.now += 25
        self.assertTrue(
            guard.evaluate(name="app", container_id="a", running=True, action="restart").allowed
        )

    def test_dry_run_marks_simulated(self) -> None:
        guard = self._guard(allow_control=True, allowed=("app",), dry_run=True)
        decision = guard.evaluate(name="app", container_id="a", running=False, action="start")
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.simulated)

    def test_build_policy_merges_lists(self) -> None:
        policy = build_policy(
            allow_control=True,
            dry_run=False,
            cooldown=5,
            extra_protected="nginx， redis, nginx",
            allowed="qbittorrent, xiaoya, qbittorrent",
        )
        self.assertEqual(policy.protected.count("nginx"), 1)
        self.assertIn("redis", policy.protected)
        self.assertEqual(policy.allowed, ("qbittorrent", "xiaoya"))


class WriteAllowlistTest(unittest.TestCase):
    def test_requires_enable(self) -> None:
        with self.assertRaises(ZspacePolicyError):
            assert_write_allowed("docker", "containers", {"action": "start"}, enabled=False)

    def test_only_three_actions(self) -> None:
        for action in ("start", "stop", "restart"):
            assert_write_allowed("docker", "containers", {"action": action}, enabled=True)

        for action in ("remove", "force_remove", "force_stop", "pause", "rename"):
            with self.assertRaises(ZspacePolicyError):
                assert_write_allowed("docker", "containers", {"action": action}, enabled=True)

    def test_other_domains_blocked(self) -> None:
        for domain, action in (("system", "power"), ("storage", "maintenance"), ("file", "delete")):
            with self.assertRaises(ZspacePolicyError):
                assert_write_allowed(domain, action, {}, enabled=True)

    def test_dump_allowlist(self) -> None:
        assert_dump_allowed("system", "info", {})
        assert_dump_allowed("docker", "containers", {"action": "list"})

        with self.assertRaises(ZspacePolicyError):
            assert_dump_allowed("file", "delete", {})
        with self.assertRaises(ZspacePolicyError):
            assert_dump_allowed("docker", "containers", {"action": "remove"})
        with self.assertRaises(ZspacePolicyError):
            assert_dump_allowed("raw", "GET", {})

    def test_read_path_rejects_destructive(self) -> None:
        assert_not_destructive({"action": "list"})
        with self.assertRaises(ZspacePolicyError):
            assert_not_destructive({"action": "reboot"})


if __name__ == "__main__":
    unittest.main()
