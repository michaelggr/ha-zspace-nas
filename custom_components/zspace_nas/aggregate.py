"""聚合实体：把整台 NAS 的实体定型成一份「前端可直接渲染」的数据。

设计（对比 fnOS 的 sensor.fnos_all_entities）：
- fnOS 只给原始 groups[]，前端要自己遍历、自己认角色、自己算 KPI；
- 这里额外给 `view`——一份已经分好主次、归一好单位/图标/语义的定型结构，
  前端卡片读 `view` 一个属性就能整屏渲染，不写死任何实体 ID，且新增容器/
  虚拟机自动跟随。

结构（属性 `view`）：
    {
      "host": {                      # 重点：主机核心指标，一张卡直接铺开
         "cpu": {"value":31.2,"unit":"%","icon":"mdi:cpu-64-bit","entity_id":...},
         "memory": {...}, "temp": {...}, "fan": {...}, "uptime": "...",
         "net_rx": {...}, "net_tx": {...}, "download": {...},
      },
      "storage": {                   # 详：存储池 + 成员盘
         "pools": [{"name","usage","used","free","health","disks":[...]}],
      },
      "containers": [                # 详：每个容器一行，带可控实体
         {"name","state","running","cpu","memory","entity_id","switch_id",
          "action_button_id","icon"},
      ],
      "vms": [                       # 详：每台虚拟机一行，带电源/控制台实体
         {"name","title","state","running","cpu","memory","ip","vnc_port",
          "entity_id","switch_id","reboot_button_id","console_url"},
      ],
      "overview": {"containers_total","containers_running","vms_total",
                   "vms_running","disks","disks_bad","pools"},
    }

state = 纳入的实体总数；属性 `groups` 保留 fnOS 风格的原始分组，便于房间精灵
之类的通用卡片也能消费。大属性一律不写 recorder（绕开 16384 字节上限）。
"""

from __future__ import annotations

from typing import Any, Callable

# 聚合视图刷新信号：VNC 链接续签、设备改名等外部事件发此信号强制重建 view。
# 放在本模块避免 vnc.py ↔ all_entities_sensor.py 循环导入。
VNC_RENEW_SIGNAL = "zspace_nas_renew_console_urls"

# 主机关键实体：英文 key → (显示名, 单位后缀, 图标)。前端按此定型展示。
_HOST_KEYS: dict[str, tuple[str, str, str]] = {
    "zspace_cpu_usage": ("CPU 使用率", "%", "mdi:cpu-64-bit"),
    "zspace_memory_usage": ("内存使用率", "%", "mdi:memory"),
    "zspace_cpu_temperature": ("CPU 温度", "°C", "mdi:thermometer"),
    "zspace_fan_speed": ("风扇转速", "rpm", "mdi:fan"),
    "zspace_network_rx": ("网络下载", "B/s", "mdi:download-network"),
    "zspace_network_tx": ("网络上传", "B/s", "mdi:upload-network"),
    "zspace_downloads_active": ("下载任务", "", "mdi:download"),
    "zspace_downloads_rate": ("下载速度", "B/s", "mdi:speedometer"),
    "zspace_downloads_seeding": ("做种任务", "", "mdi:seed"),
    "zspace_uptime": ("运行时长", "", "mdi:clock-outline"),
}

# 主键别名：不同命名方式（拼音 / 短 ID）都能认出来
_HOST_ALIASES: dict[str, str] = {
    "cpu_usage": "cpu", "cpu_shi_yong_lu": "cpu",
    "memory_usage": "memory", "nei_cun_shi_yong_lu": "memory",
    "cpu_temperature": "temp", "cpu_wen_du": "temp",
    "fan_speed": "fan", "feng_shan_zhuan_su": "fan",
    "network_rx": "net_rx", "wang_luo_jie_shou": "net_rx",
    "network_tx": "net_tx", "wang_luo_fa_song": "net_tx",
    "downloads_active": "download", "xia_zai_ren_wu": "download",
    "downloads_rate": "download_rate", "xia_zai_su_du": "download_rate",
    "downloads_seeding": "seeding", "zuo_chong_ren_wu": "seeding",
    "uptime": "uptime", "yun_xing_shi_chang": "uptime",
}

_ROLE_UNITS = {
    "cpu": "%", "memory": "%", "temp": "°C", "fan": "rpm",
    "net_rx": "B/s", "net_tx": "B/s", "download_rate": "B/s",
}

_ROLE_ICONS = {
    "cpu": "mdi:cpu-64-bit", "memory": "mdi:memory",
    "temp": "mdi:thermometer", "fan": "mdi:fan",
    "net_rx": "mdi:download-network", "net_tx": "mdi:upload-network",
    "download": "mdi:download", "download_rate": "mdi:speedometer",
    "seeding": "mdi:seed", "uptime": "mdi:clock-outline",
}

_ROLE_NAMES = {
    "cpu": "CPU 使用率", "memory": "内存使用率", "temp": "CPU 温度",
    "fan": "风扇转速", "net_rx": "网络下载", "net_tx": "网络上传",
    "download": "下载任务", "download_rate": "下载速度",
    "seeding": "做种任务", "uptime": "运行时长",
}


def _num(value: Any, digits: int = 1) -> float | int | None:
    """尽力转数字；None/空串/异常一律返回 None，绝不显示 0 误导。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    rounded = round(number, digits)
    return int(rounded) if rounded == int(rounded) else rounded


def _speed(value: Any) -> tuple[Any, str]:
    """B/s → 自适应可读单位（保留原始 B/s 数值给前端做曲线）。"""
    number = _num(value)
    if number is None:
        return None, "B/s"
    for unit, factor in (("GB/s", 1024**3), ("MB/s", 1024**2), ("KB/s", 1024)):
        if abs(number) >= factor:
            return round(number / factor, 1), unit
    return int(number), "B/s"


def _host_role(entity_id: str) -> str | None:
    """从实体 ID 尾部反推主机角色（兼容拼音与短 ID）。"""
    tail = entity_id.rsplit(".", 1)[-1]
    if tail in _HOST_ALIASES:
        return _HOST_ALIASES[tail]
    for suffix, role in _HOST_ALIASES.items():
        if tail.endswith("_" + suffix):
            return role
    return None


def build_view(
    states: dict[str, Any],
    *,
    friendly: Callable[[str], str | None] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把 {entity_id: state} 定型成前端视图。

    ``states`` 的值支持两种形态：
      * 字符串/数字（state.state）；
      * dict（含 state / attributes），便于直接喂 HA 状态对象。

    ``extra`` 用于传入非实体来源的数据（如存储池成员盘、VM 详情），
    键名 ``pools`` / ``vms_detail`` / ``containers_detail``。
    纯函数、不依赖 HA，便于单元测试。
    """
    extra = extra or {}
    name_of = friendly or (lambda eid: None)

    def state_of(entity_id: str) -> tuple[Any, dict[str, Any]]:
        raw = states.get(entity_id)
        if isinstance(raw, dict):
            return raw.get("state"), raw.get("attributes") or {}
        return raw, {}

    # ---- 主机重点指标 ----
    host: dict[str, dict[str, Any]] = {}
    for entity_id in states:
        role = _host_role(entity_id)
        if not role or role in host:
            continue
        value, attributes = state_of(entity_id)
        unit = _ROLE_UNITS.get(role, "")
        if role in {"net_rx", "net_tx", "download_rate"}:
            shown, shown_unit = _speed(value)
        else:
            # 数值型角色统一转成数字；文本角色（uptime/任务数例外）保留原值
            shown = _num(value) if role in {"cpu", "memory", "temp", "fan"} else value
            shown_unit = unit
        host[role] = {
            "name": _ROLE_NAMES.get(role, name_of(entity_id) or role),
            "value": shown,
            "raw": _num(value),
            "unit": shown_unit,
            "icon": _ROLE_ICONS.get(role, "mdi:gauge"),
            "entity_id": entity_id,
        }

    # ---- 从聚合属性里取现成的详表（若有） ----
    pools = list(extra.get("pools") or [])
    containers = list(extra.get("containers") or [])
    vms = list(extra.get("vms") or [])

    # ---- 概览计数 ----
    running_containers = sum(
        1 for c in containers if _truthy(c.get("running", c.get("state")))
    )
    running_vms = sum(
        1 for v in vms if _truthy(v.get("running", v.get("state")))
    )
    _BAD_HEALTH = {"bad", "error", "fail", "failed", "warning", "warn",
                   "degraded", "异常", "告警", "警告", "预警"}
    bad_disks = sum(
        1 for p in pools for d in (p.get("disks") or [])
        if str(d.get("health", "")).lower() in _BAD_HEALTH
        or d.get("healthy") is False
    )
    overview = {
        "containers_total": len(containers),
        "containers_running": running_containers,
        "vms_total": len(vms),
        "vms_running": running_vms,
        "pools": len(pools),
        "disks": sum(len(p.get("disks") or []) for p in pools),
        "disks_bad": bad_disks,
        "host_name": extra.get("host_name"),
        "model": extra.get("model"),
        "version": extra.get("version"),
    }

    return {
        "host": host,
        "storage": {"pools": pools},
        "containers": containers,
        "vms": vms,
        "overview": overview,
    }


def _truthy(value: Any) -> bool:
    """运行态归一：兼容 1/5、"running"/"on"/"运行中"、True。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        # 极空间 VM：1=运行，5=关闭
        return int(value) == 1
    text = str(value or "").strip().lower()
    return text in {"on", "true", "1", "running", "运行中", "运行"}


def classify_device(identifier: str, nas_identifier: str) -> tuple[str, str]:
    """设备标识 → (kind, key)。

    nas_<serial>              → ("host", "")
    nas_<serial>_<id12>       → ("container", "<id12>")
    nas_<serial>_vm_<name>    → ("vm", "<name>")
    """
    if not identifier or not identifier.startswith(nas_identifier):
        return "other", ""
    suffix = identifier[len(nas_identifier):].lstrip("_")
    if not suffix:
        return "host", ""
    if suffix.startswith("vm_"):
        return "vm", suffix[3:]
    return "container", suffix
