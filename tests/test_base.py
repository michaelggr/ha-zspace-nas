"""解析工具的单元测试（不依赖 Home Assistant）。

跑法：
    python -m unittest discover -s tests
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_BASE = Path(__file__).resolve().parents[1] / "custom_components" / "zspace_nas" / "zspace" / "base.py"
_spec = importlib.util.spec_from_file_location("zspace_base", _BASE)
assert _spec and _spec.loader
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)


class PickTest(unittest.TestCase):
    def test_nested_alias(self) -> None:
        payload = {"cpu": {"CPUInfo": {"usage": "12.5%"}}}
        self.assertEqual(base.pick(payload, ["usage", "percent"]), "12.5%")

    def test_scope_prefers_given_subtree(self) -> None:
        payload = {"memory": {"used": 2048}, "disk": {"used": 999}}
        self.assertEqual(base.pick(payload["memory"], ["used"]), 2048)

    def test_missing_returns_default(self) -> None:
        self.assertIsNone(base.pick({"a": 1}, ["nope"]))


class ConvertTest(unittest.TestCase):
    def test_bytes_with_units(self) -> None:
        self.assertEqual(base.as_bytes("1.5 TB"), int(1.5 * 1024**4))
        self.assertEqual(base.as_bytes("256 GiB"), 256 * 1024**3)
        self.assertEqual(base.as_bytes(1024), 1024)

    def test_percent_float(self) -> None:
        self.assertEqual(base.as_float("37%"), 37.0)
        self.assertEqual(base.as_float("12.5 ℃"), 12.5)
        # 逗号按千分位处理（极空间返回的是 1,024 这类格式）
        self.assertEqual(base.as_float("1,024 MB"), 1024.0)

    def test_bool_variants(self) -> None:
        self.assertTrue(base.as_bool("running"))
        self.assertFalse(base.as_bool("exited"))
        self.assertIsNone(base.as_bool("weird"))

    def test_percentage_avoids_zero_division(self) -> None:
        self.assertEqual(base.percentage(50, 200), 25.0)
        self.assertIsNone(base.percentage(50, 0))

    def test_container_list_shapes(self) -> None:
        self.assertEqual(len(base.as_list({"containers": [1, 2]})), 2)
        self.assertEqual(len(base.as_list([1])), 1)
        self.assertEqual(base.as_list({"other": "x"}), [])


if __name__ == "__main__":
    unittest.main()
