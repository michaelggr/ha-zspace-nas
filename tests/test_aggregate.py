"""聚合视图数据定型测试（纯函数，无需 HA）。"""

from __future__ import annotations

import pathlib
import sys
import unittest

PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components/zspace_nas"
sys.path.insert(0, str(PKG))

import aggregate  # noqa: E402


class BuildViewTest(unittest.TestCase):
    def test_host_kpis_short_ids(self) -> None:
        states = {
            "sensor.zspace_cpu_usage": "31.2",
            "sensor.zspace_memory_usage": "58",
            "sensor.zspace_cpu_temperature": "42",
            "sensor.zspace_fan_speed": "1200",
            "sensor.zspace_network_rx": "2097152",  # 2 MB/s
        }
        view = aggregate.build_view(states)
        host = view["host"]
        self.assertEqual(host["cpu"]["value"], 31.2)
        self.assertEqual(host["cpu"]["unit"], "%")
        self.assertEqual(host["temp"]["value"], 42)
        self.assertEqual(host["temp"]["unit"], "°C")
        # 网络自适应单位
        self.assertEqual(host["net_rx"]["unit"], "MB/s")
        self.assertEqual(host["net_rx"]["value"], 2.0)
        self.assertEqual(host["net_rx"]["raw"], 2097152)
        self.assertTrue(host["net_rx"]["icon"].startswith("mdi:"))

    def test_host_kpis_pinyin_ids(self) -> None:
        """拼音 ID 也能识别（迁移前 / 回退场景）。"""
        states = {
            "sensor.ji_kong_jian_z4pro_cpu_shi_yong_lu": "12.5",
            "sensor.ji_kong_jian_z4pro_nei_cun_shi_yong_lu": "40",
        }
        view = aggregate.build_view(states)
        self.assertIn("cpu", view["host"])
        self.assertIn("memory", view["host"])
        self.assertEqual(view["host"]["cpu"]["value"], 12.5)

    def test_speed_units(self) -> None:
        for value, unit, shown in (
            (512, "B/s", 512),
            (2048, "KB/s", 2.0),
            (5 * 1024**3, "GB/s", 5.0),
        ):
            with self.subTest(value=value):
                view = aggregate.build_view({"sensor.zspace_network_tx": str(value)})
                self.assertEqual(view["host"]["net_tx"]["unit"], unit)
                self.assertEqual(view["host"]["net_tx"]["value"], shown)

    def test_overview_counts(self) -> None:
        extra = {
            "containers": [
                {"name": "a", "running": True},
                {"name": "b", "running": False},
                {"name": "c", "state": "running"},
            ],
            "vms": [
                {"name": "win", "running": True},
                {"name": "nas", "running": False},
            ],
            "pools": [
                {"name": "p1", "disks": [
                    {"health": "ok"}, {"health": "bad"},
                ]},
            ],
        }
        view = aggregate.build_view({}, extra=extra)
        ov = view["overview"]
        self.assertEqual(ov["containers_total"], 3)
        self.assertEqual(ov["containers_running"], 2)
        self.assertEqual(ov["vms_total"], 2)
        self.assertEqual(ov["vms_running"], 1)
        self.assertEqual(ov["disks"], 2)
        self.assertEqual(ov["disks_bad"], 1)

    def test_empty_is_safe(self) -> None:
        view = aggregate.build_view({})
        self.assertEqual(view["host"], {})
        self.assertEqual(view["containers"], [])
        self.assertEqual(view["overview"]["containers_total"], 0)

    def test_invalid_numbers_are_none(self) -> None:
        view = aggregate.build_view({
            "sensor.zspace_cpu_usage": "unknown",
            "sensor.zspace_memory_usage": "",
        })
        self.assertIsNone(view["host"]["cpu"]["value"])
        self.assertIsNone(view["host"]["memory"]["value"])


class ClassifyDeviceTest(unittest.TestCase):
    def test_classify(self) -> None:
        nas = "nas_ABC123"
        cases = {
            "nas_ABC123": ("host", ""),
            "nas_ABC123_1a2b3c4d5e6f": ("container", "1a2b3c4d5e6f"),
            "nas_ABC123_vm_win10": ("vm", "win10"),
            "other_device": ("other", ""),
        }
        for identifier, expected in cases.items():
            with self.subTest(identifier=identifier):
                self.assertEqual(aggregate.classify_device(identifier, nas), expected)


class VmDisplayNameTest(unittest.TestCase):
    def test_display_name(self) -> None:
        cases = {
            "zspace_dgoxt5d60y5n8y3t": "dgoxt5d60y5n",  # 去掉前缀并截断
            "win10": "win10",          # 已是正常名，原样
            "": "",                    # 空值不崩
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(aggregate.display_vm_name(raw), expected)


if __name__ == "__main__":
    unittest.main()
