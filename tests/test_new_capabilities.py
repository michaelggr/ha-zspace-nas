"""新增能力测试：GPU / AI、Time Machine、系统事件归一化 + 播报逻辑。

归一化函数不依赖 HA，可直接 import；播报器用假对象 mock HA 依赖，
验证「新事件按级别播报、旧事件与低级别不播、去重、冷却」四条不变量。
"""

from __future__ import annotations

import pathlib
import sys
import types
import unittest

_PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "zspace_nas"
sys.path.insert(0, str(_PKG))

from zspace.normalize import (  # noqa: E402
    EventData,
    EventInfo,
    event_level_rank,
    normalize_events,
    normalize_gpu_info,
    normalize_gpu_tasks,
    normalize_time_machine,
)


class GpuNormalizeTest(unittest.TestCase):
    def test_gpu_bytes_converted_to_mib(self) -> None:
        gpus = normalize_gpu_info({
            "list": [{
                "model": "RTX 4090",
                "memoryTotal": 25769803776,   # 24 GiB 字节
                "memoryUsed": 8589934592,     # 8 GiB 字节
                "utilization": "46%",
                "temperature": 58,
            }]
        })
        self.assertEqual(len(gpus), 1)
        self.assertEqual(gpus[0].memory_total_mib, 24576.0)
        self.assertEqual(gpus[0].memory_used_mib, 8192.0)
        self.assertEqual(gpus[0].memory_percent, 33.3)
        self.assertEqual(gpus[0].utilization, 46.0)

    def test_gpu_mib_values_kept(self) -> None:
        gpus = normalize_gpu_info({"gpus": [{"name": "GPU0", "memoryTotal": 8192, "memoryUsed": 1024}]})
        self.assertEqual(gpus[0].memory_total_mib, 8192.0)

    def test_ai_tasks(self) -> None:
        tasks = normalize_gpu_tasks({
            "taskList": [
                {"name": "zllamas", "processName": "zllamas", "status": "运行中", "memory": 6144},
                {"process_name": "zaispace", "state": "待命", "mem": 2048},
            ]
        })
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0].process_name, "zllamas")
        self.assertEqual(tasks[1].name, "zaispace")
        self.assertEqual(tasks[1].memory_mib, 2048.0)

    def test_empty_structure(self) -> None:
        self.assertEqual(normalize_gpu_info({}), [])
        self.assertEqual(normalize_gpu_tasks(None), [])


class TimeMachineNormalizeTest(unittest.TestCase):
    def test_status_fields(self) -> None:
        tm = normalize_time_machine({
            "status": "已配置", "enabled": True,
            "limit_size": 549755813888,  # 512 GiB
            "size": 137945750528,        # 128.5 GiB
            "dir": "/tmp/zfsv3/pool/TimeMachine/admin",
            "haveBackup": True,
        })
        self.assertEqual(tm.limit_bytes, 549755813888)
        self.assertTrue(tm.enabled)
        self.assertTrue(tm.have_backup)
        self.assertAlmostEqual(tm.usage, 25.1, places=1)

    def test_unlimited_quota_has_no_usage(self) -> None:
        tm = normalize_time_machine({"size": 1024, "limit_size": 0})
        self.assertIsNone(tm.usage)  # 0 配额 = 无限制，不显示百分比


class EventNormalizeTest(unittest.TestCase):
    def test_events_and_new_count(self) -> None:
        data = normalize_events({
            "list": [
                {"id": "1", "eventType": "notify", "level": "error", "title": "磁盘预警",
                 "content": "重映射扇区", "createTime": 1763980800, "isNew": 1},
                {"id": "2", "level": "info", "title": "登录成功", "isNew": 0},
            ]
        })
        self.assertEqual(len(data.events), 2)
        self.assertEqual(data.new_count, 1)
        self.assertEqual(data.events[0].level, "error")
        self.assertIsNotNone(data.events[0].time)  # 时间戳已格式化

    def test_level_rank(self) -> None:
        self.assertEqual(event_level_rank("info"), 0)
        self.assertEqual(event_level_rank("warning"), 1)
        self.assertEqual(event_level_rank("告警"), 1)
        self.assertEqual(event_level_rank("Error"), 2)
        self.assertEqual(event_level_rank("未知"), 0)


class _FakeCoordinator:
    def __init__(self, events: list[EventInfo]) -> None:
        self.data = EventData(events=events)
        self._listeners = []

    def async_add_listener(self, cb):
        self._listeners.append(cb)
        return lambda: None

    def emit(self) -> None:
        for cb in self._listeners:
            cb()


class _FakeServices:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    async def async_call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, data))


class _FakeHass:
    def __init__(self) -> None:
        self.services = _FakeServices()
        self._tasks = []

    def async_create_task(self, coro):
        # 立即执行协程，便于断言；asyncio.run 自带事件循环，无残留 loop 警告
        import asyncio
        self._tasks.append(asyncio.run(coro))


def _load_announcer():
    """注入最小 HA / 包桩模块后加载 announce.py（含相对导入）。

    announce.py 用 ``from .const`` / ``from .coordinator``，测试环境没有真实
    包上下文，这里造一个名为 ``zspace_nas`` 的假包，把真实 const 与必要的
    coordinator 符号塞进去，再用 importlib 按包内模块加载。
    """
    import importlib.util

    for name in (
        "homeassistant", "homeassistant.config_entries", "homeassistant.core",
    ):
        sys.modules.setdefault(name, types.ModuleType(name))
    ce = sys.modules["homeassistant.config_entries"]
    core = sys.modules["homeassistant.core"]
    if not hasattr(ce, "ConfigEntry"):
        ce.ConfigEntry = object
    if not hasattr(core, "HomeAssistant"):
        core.HomeAssistant = object
    if not hasattr(core, "callback"):
        core.callback = lambda fn: fn

    import const as real_const
    pkg = types.ModuleType("zspace_nas")
    pkg.__path__ = [str(_PKG)]
    sys.modules["zspace_nas"] = pkg
    const_mod = types.ModuleType("zspace_nas.const")
    for key in dir(real_const):
        setattr(const_mod, key, getattr(real_const, key))
    sys.modules["zspace_nas.const"] = const_mod
    coord_mod = types.ModuleType("zspace_nas.coordinator")
    coord_mod.EventCoordinator = object
    sys.modules["zspace_nas.coordinator"] = coord_mod
    # announce 从 zspace.normalize 取 event_level_rank；用真实实现
    import zspace.normalize as real_norm
    zpkg = types.ModuleType("zspace_nas.zspace")
    zpkg.__path__ = [str(_PKG / "zspace")]
    sys.modules["zspace_nas.zspace"] = zpkg
    znorm = types.ModuleType("zspace_nas.zspace.normalize")
    znorm.event_level_rank = real_norm.event_level_rank
    znorm.EventInfo = real_norm.EventInfo
    sys.modules["zspace_nas.zspace.normalize"] = znorm

    spec = importlib.util.spec_from_file_location(
        "zspace_nas.announce", _PKG / "announce.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["zspace_nas.announce"] = mod
    spec.loader.exec_module(mod)
    return mod.EventAnnouncer


class AnnouncerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.EventAnnouncer = _load_announcer()

    def _make(self, events, min_level="warning"):
        hass = _FakeHass()
        coord = _FakeCoordinator(events)
        ann = self.EventAnnouncer(hass, "media_player.living_room", min_level, coord)
        ann.start()  # 建立基线：现有事件不播报
        return hass, coord, ann

    def test_baseline_events_not_spoken(self) -> None:
        hass, coord, ann = self._make([
            EventInfo(id="old", level="error", title="旧错误"),
        ])
        ann._last_spoken = 0  # 绕过冷却
        coord.emit()
        self.assertEqual(len(hass.services.calls), 0)

    def test_new_warning_spoken(self) -> None:
        hass, coord, ann = self._make([])
        ann._last_spoken = 0
        coord.data.events = [
            EventInfo(id="new1", level="warning", title="存储告警"),
            EventInfo(id="new2", level="error", title="磁盘故障"),
        ]
        coord.emit()
        self.assertEqual(len(hass.services.calls), 1)
        domain, service, data = hass.services.calls[0]
        self.assertEqual((domain, service), ("tts", "speak"))
        self.assertEqual(data["media_player_entity_id"], "media_player.living_room")
        self.assertIn("存储告警", data["message"])
        self.assertIn("磁盘故障", data["message"])

    def test_new_info_not_spoken(self) -> None:
        hass, coord, ann = self._make([])
        ann._last_spoken = 0
        coord.data.events = [EventInfo(id="n", level="info", title="登录")]
        coord.emit()
        self.assertEqual(len(hass.services.calls), 0)

    def test_dedup_same_id_not_repeated(self) -> None:
        hass, coord, ann = self._make([])
        ann._last_spoken = 0
        coord.data.events = [EventInfo(id="x", level="error", title="故障")]
        coord.emit()
        ann._last_spoken = 0  # 再绕过冷却，模拟短时间内第二次更新
        coord.emit()
        self.assertEqual(len(hass.services.calls), 1)

    def test_max_three_per_batch(self) -> None:
        hass, coord, ann = self._make([])
        ann._last_spoken = 0
        coord.data.events = [
            EventInfo(id=str(i), level="error", title=f"事件{i}") for i in range(6)
        ]
        coord.emit()
        message = hass.services.calls[0][2]["message"]
        self.assertIn("另有 3 条", message)


if __name__ == "__main__":
    unittest.main()
