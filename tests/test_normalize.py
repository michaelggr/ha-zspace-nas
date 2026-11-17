"""归一化测试：夹具结构取自真机（Z4Pro / ZOS 2026-11-14），数值已脱敏。

跑法：
    python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

_PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "zspace_nas"
sys.path.insert(0, str(_PKG))

from zspace.normalize import (  # noqa: E402
    normalize_containers,
    normalize_disks,
    normalize_logs,
    normalize_downloads,
    normalize_engine,
    normalize_smart,
    normalize_storage,
    normalize_vms,
    normalize_system,
)

_FIX = pathlib.Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((_FIX / name).read_text(encoding="utf-8"))


class SystemTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = normalize_system(_load("system-info.json"))

    def test_percentages_are_not_bytes(self) -> None:
        # 真机 memory.used=0.64 是百分比；曾经被当成字节，导致内存传感器读 0
        self.assertEqual(self.data.memory_usage, 0.64)
        self.assertEqual(self.data.memory_used, 219902325)
        self.assertEqual(self.data.memory_total, 34359738368)

    def test_cpu_and_thermal(self) -> None:
        self.assertEqual(self.data.cpu_usage, 0.29)
        self.assertEqual(self.data.cpu_temperature, 52.0)
        self.assertEqual(self.data.fan_speed, 1972)

    def test_network_and_uptime(self) -> None:
        self.assertEqual(self.data.rx_bytes_per_s, 823944)
        self.assertEqual(self.data.tx_bytes_per_s, 3499957)
        self.assertEqual(self.data.uptime, "8天 20小时")

    def test_device_identity(self) -> None:
        self.assertEqual(self.data.model, "Z4Pro")
        self.assertEqual(self.data.serial, "DEMO00000001")

    def test_missing_fields_do_not_raise(self) -> None:
        empty = normalize_system({})
        self.assertIsNone(empty.cpu_usage)
        self.assertIsNone(empty.memory_usage)


class StorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = normalize_storage(_load("storage-info.json"))

    def test_pools(self) -> None:
        self.assertEqual(len(self.data.pools), 2)
        first = self.data.pools[0]
        self.assertEqual(first.key, "sata11")
        self.assertEqual(first.total, 8001574060032)
        self.assertEqual(first.used, 5555311095808)
        self.assertEqual(first.free, 2446262964224)
        self.assertEqual(first.usage, 69.4)
        self.assertEqual(first.pool_type, "zdr")

    def test_second_pool_percentage(self) -> None:
        self.assertEqual(self.data.pools[1].usage, 34.1)


class DockerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = normalize_containers(_load("docker-list.json"), _load("docker-stats.json"))

    def test_counts(self) -> None:
        self.assertEqual(len(self.data.containers), 3)
        self.assertEqual(self.data.running_count, 2)

    def test_names_strip_leading_slash(self) -> None:
        self.assertEqual(self.data.containers[0].name, "music-tagger")
        self.assertEqual(self.data.containers[2].name, "media-stack")

    def test_stats_merged_by_id(self) -> None:
        first = self.data.containers[0]
        self.assertEqual(first.cpu_usage, 0.32)
        self.assertEqual(first.memory_bytes, 699170816)
        self.assertEqual(first.memory_limit, 2147483648)

    def test_digest_image_is_dropped(self) -> None:
        self.assertIsNone(self.data.containers[0].image)
        self.assertEqual(self.data.containers[1].image, "ghcr.io/example/tgbot-demo:latest")

    def test_compose_and_state(self) -> None:
        self.assertEqual(self.data.containers[1].compose, "tgbot-demo")
        self.assertFalse(self.data.containers[2].running)
        self.assertEqual(self.data.containers[2].state, "exited")

    def test_engine(self) -> None:
        self.assertTrue(normalize_engine(_load("docker-status.json")))
        self.assertFalse(normalize_engine({"status": "off"}))
        self.assertIsNone(normalize_engine({}))

    def test_stats_failure_still_lists_containers(self) -> None:
        data = normalize_containers(_load("docker-list.json"), None)
        self.assertEqual(len(data.containers), 3)
        self.assertIsNone(data.containers[0].cpu_usage)


class PoolDiskTest(unittest.TestCase):
    """存储池成员盘：真机 disk_list 里已有健康/温度/通电，不需要额外问 SMART。"""

    def setUp(self) -> None:
        self.data = normalize_storage(_load("storage-info-pools.json"))

    def test_pool_has_disks(self) -> None:
        self.assertEqual(len(self.data.pools), 2)
        self.assertEqual(len(self.data.pools[0].disks), 2)
        self.assertEqual(len(self.data.pools[1].disks), 1)

    def test_disk_fields(self) -> None:
        disk = self.data.pools[0].disks[0]
        self.assertEqual(disk.pos, "1")
        self.assertEqual(disk.model, "ST4000VN006-3CW104")
        self.assertEqual(disk.dev_type, "SATA")
        self.assertEqual(disk.temperature, 29)
        self.assertEqual(disk.power_on_hours, 24191)
        self.assertEqual(disk.reallocated, 0)
        self.assertTrue(disk.healthy)
        self.assertEqual(disk.total, 4000787030016)

    def test_disks_sorted_by_slot(self) -> None:
        self.assertEqual([d.pos for d in self.data.pools[0].disks], ["1", "3"])

    def test_missing_reallocated(self) -> None:
        # NVMe / 部分固件不带重映射计数，应为 None 而不是 0
        self.assertIsNone(self.data.pools[1].disks[0].reallocated)


class VmTest(unittest.TestCase):
    """虚拟机列表：真机字段名不统一，靠别名容错。"""

    def setUp(self) -> None:
        self.data = normalize_vms(_load("ops-vm.json"))

    def test_count_and_titles(self) -> None:
        self.assertEqual(len(self.data.vms), 2)
        self.assertEqual(self.data.vms[0].title, "Windows 10")
        self.assertEqual(self.data.vms[1].name, "zspace_ubuntu")

    def test_power_state(self) -> None:
        self.assertTrue(self.data.vms[0].running)   # isStart: true
        self.assertFalse(self.data.vms[1].running)  # state: 2 = 关闭
        self.assertEqual(self.data.running_count, 1)

    def test_memory_unit_fallback(self) -> None:
        self.assertEqual(self.data.vms[0].memory_gib, 8)
        # 4096 明显是 MiB，折算成 4 GiB
        self.assertEqual(self.data.vms[1].memory_gib, 4.0)

    def test_disk_sum(self) -> None:
        self.assertEqual(self.data.vms[0].disk_gib, 192)
        self.assertEqual(self.data.vms[1].disk_gib, 32)

    def test_vnc_and_ip(self) -> None:
        self.assertEqual(self.data.vms[0].vnc_port, 5901)
        self.assertEqual(self.data.vms[0].ip, "192.168.1.50")
        self.assertIsNone(self.data.vms[1].vnc_port)

    def test_empty_payload(self) -> None:
        for payload in ({}, [], {"domainList": []}, None):
            self.assertEqual(len(normalize_vms(payload).vms), 0)

    def test_single_detail_object(self) -> None:
        data = normalize_vms({"domainDetail": {"name": "zspace_a", "isStart": False}})
        self.assertEqual(len(data.vms), 1)
        self.assertFalse(data.vms[0].running)


class DiskTest(unittest.TestCase):
    def setUp(self) -> None:
        self.smart = normalize_smart(_load("smart-disk.json"))
        self.data = normalize_disks(_load("system-disk.json"), {"TESTDISK01": self.smart})

    def test_smart_uses_str_value_not_now_value(self) -> None:
        # now_value 是厂商归一化值（温度会读成 176），必须用 str_value 的首位数字
        self.assertEqual(self.smart["temperature"], 26)
        self.assertEqual(self.smart["power_on_hours"], 3907)
        self.assertEqual(self.smart["reallocated"], 8)
        self.assertEqual(self.smart["health"], "ok")

    def test_disks_merged(self) -> None:
        self.assertEqual(len(self.data.disks), 2)
        first = self.data.disks[0]
        self.assertEqual(first.dev, "sda")
        self.assertEqual(first.model, "ST4000VN006-3CW104")
        self.assertEqual(first.total, 4000787030016)
        self.assertEqual(first.temperature, 26)
        self.assertEqual(first.power_on_hours, 3907)

    def test_disk_without_smart(self) -> None:
        second = self.data.disks[1]
        self.assertIsNone(second.temperature)
        self.assertEqual(second.total, 2048408248320)


class DownloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = normalize_downloads(_load("download-tasks.json"))

    def test_counts(self) -> None:
        self.assertEqual(self.data.active, 1)
        self.assertEqual(self.data.total, 2)
        self.assertEqual(self.data.seeding, 1)

    def test_rates(self) -> None:
        self.assertEqual(self.data.rate_down, 1048576)
        self.assertEqual(self.data.rate_up, 2048)

    def test_active_names(self) -> None:
        self.assertEqual(self.data.names, ["ubuntu.iso"])


class LogsTest(unittest.TestCase):
    def test_plain_string(self) -> None:
        self.assertEqual(normalize_logs("a\nb\n"), ["a", "b"])

    def test_dict_with_string(self) -> None:
        self.assertEqual(normalize_logs({"logs": "x\ny"}), ["x", "y"])

    def test_dict_with_list(self) -> None:
        self.assertEqual(normalize_logs({"logs": ["p", "q"]}), ["p", "q"])

    def test_list_of_objects(self) -> None:
        raw = [{"message": "m1"}, {"line": "l1"}, {"unknown": 1}]
        lines = normalize_logs(raw)
        self.assertEqual(lines[:2], ["m1", "l1"])
        self.assertIn("unknown", lines[2])

    def test_empty_shapes(self) -> None:
        self.assertEqual(normalize_logs({}), [])
        self.assertEqual(normalize_logs([]), [])
        self.assertEqual(normalize_logs(None), [])


if __name__ == "__main__":
    unittest.main()