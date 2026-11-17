"""命名规则单测：纯函数部分用假的 homeassistant 模块跑，不需要真的 HA。"""

from __future__ import annotations

import pathlib
import sys
import types
import unittest

# naming.py 顶部会 import homeassistant，本机没装 HA，这里塞几个空壳
if "homeassistant" not in sys.modules:
    ha = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    core.callback = lambda func: func
    helpers = types.ModuleType("homeassistant.helpers")
    registry = types.ModuleType("homeassistant.helpers.entity_registry")
    registry.async_get = lambda hass: None
    sys.modules.update(
        {
            "homeassistant": ha,
            "homeassistant.core": core,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.entity_registry": registry,
        }
    )

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "custom_components/zspace_nas"))

from naming import (  # noqa: E402
    ENTITY_ID_PREFIX,
    entity_id_slug,
    object_id,
    short_hash,
    token_or_hash,
)


class SlugTest(unittest.TestCase):
    def test_basic(self) -> None:
        self.assertEqual(entity_id_slug("CPU Usage"), "cpu_usage")
        self.assertEqual(entity_id_slug("qbittorrent"), "qbittorrent")
        self.assertEqual(entity_id_slug("Node-RED"), "node_red")

    def test_chinese_dropped(self) -> None:
        self.assertEqual(entity_id_slug("极空间 Z4Pro CPU 使用率"), "z4pro_cpu")
        self.assertEqual(entity_id_slug("数据库"), "")

    def test_length_capped(self) -> None:
        self.assertLessEqual(len(entity_id_slug("x" * 80)), 28)


class ObjectIdTest(unittest.TestCase):
    def test_skip_empty_parts(self) -> None:
        self.assertEqual(object_id("pool", "", "sata11", None, "usage"), "pool_sata11_usage")

    def test_prefix_constant(self) -> None:
        self.assertEqual(ENTITY_ID_PREFIX, "zspace")


class TokenTest(unittest.TestCase):
    def test_ascii_kept(self) -> None:
        self.assertEqual(token_or_hash("qbittorrent"), "qbittorrent")

    def test_chinese_falls_back_to_hash(self) -> None:
        token = token_or_hash("我的容器")
        self.assertEqual(len(token), 6)
        self.assertTrue(all(ch in "0123456789abcdef" for ch in token))
        # 同一个名字必须得到同一段，否则每次重启都会换 ID
        self.assertEqual(token, token_or_hash("我的容器"))

    def test_hash_is_stable(self) -> None:
        self.assertEqual(short_hash("abc", 4), short_hash("abc", 4))
        self.assertNotEqual(short_hash("abc", 4), short_hash("abd", 4))


if __name__ == "__main__":
    unittest.main()
