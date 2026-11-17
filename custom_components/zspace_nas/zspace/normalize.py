"""把 CLI 的真实返回结构归一化成固定字段（不依赖 Home Assistant）。

字段名来自真机抓取（Z4Pro / ZOS，2026-11-14）：
- system info -> data.detail.list[0].{cpu,memory,net_speed,fan,cpu_fan}
  cpu.used 与 memory.used 都是**百分比**，不是字节
- storage info -> data.pool_list[].{name,total_size,free_size,usage_size}
- docker containers list -> [{Id,Names[],State,Image,ComposeProject,...}]，Name 带前导 "/"
- docker containers stats -> [{container_id,cpu_usage:"0.32%",memory_usage,...}]
- docker system status -> {"status":"on"}

别名匹配（pick）保留作为兜底，适配其它机型/固件。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .base import as_bytes, as_float, as_int, as_list, percentage, pick


@dataclass
class SystemData:
    cpu_usage: float | None = None
    cpu_temperature: float | None = None
    memory_usage: float | None = None
    memory_used: int | None = None
    memory_total: int | None = None
    rx_bytes_per_s: int | None = None
    tx_bytes_per_s: int | None = None
    uptime: str | None = None
    fan_speed: int | None = None
    model: str | None = None
    serial: str | None = None


@dataclass
class PoolDisk:
    """存储池里的成员盘：真机 storage info 的 disk_list 已带健康、温度、通电、重映射。"""

    pos: str | None = None
    model: str | None = None
    sn: str | None = None
    dev_type: str | None = None
    health: str | None = None
    total: int | None = None
    used: int | None = None
    temperature: float | None = None
    power_on_hours: int | None = None
    reallocated: int | None = None

    @property
    def healthy(self) -> bool:
        return str(self.health or "").lower() in ("ok", "good", "normal", "")


@dataclass
class PoolInfo:
    key: str
    name: str
    total: int | None = None
    used: int | None = None
    free: int | None = None
    usage: float | None = None
    status: str | None = None
    pool_type: str | None = None
    disks: list[PoolDisk] = field(default_factory=list)


@dataclass
class StorageData:
    pools: list[PoolInfo] = field(default_factory=list)


@dataclass
class ContainerInfo:
    id: str
    name: str
    state: str | None = None
    image: str | None = None
    compose: str | None = None
    running: bool = False
    cpu_usage: float | None = None
    memory_bytes: int | None = None
    memory_limit: int | None = None

    @property
    def short_id(self) -> str:
        return self.id[:12] if self.id else self.name


@dataclass
class DockerData:
    engine_running: bool | None = None
    containers: list[ContainerInfo] = field(default_factory=list)

    @property
    def running_count(self) -> int:
        return sum(1 for c in self.containers if c.running)


def _node(raw: object) -> dict:
    """system info 把内容藏在 detail.list[0]，旧固件可能直接平铺。"""
    if not isinstance(raw, dict):
        return {}
    detail = raw.get("detail")
    if isinstance(detail, dict):
        items = detail.get("list")
        if isinstance(items, list) and items and isinstance(items[0], dict):
            return items[0]
    return raw


def _percent(container: object, key: str) -> float | None:
    """`used` 这类字段是百分比（0.64 = 0.64%），转成统一的 float。"""
    value = (container or {}).get(key) if isinstance(container, dict) else None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    return as_float(value)


def normalize_system(raw: object) -> SystemData:
    node = _node(raw)
    cpu = node.get("cpu") if isinstance(node.get("cpu"), dict) else {}
    memory = node.get("memory") if isinstance(node.get("memory"), dict) else {}
    net = node.get("net_speed") if isinstance(node.get("net_speed"), dict) else {}
    fan = node.get("cpu_fan") if isinstance(node.get("cpu_fan"), dict) else node.get("fan") or {}

    total = as_bytes(memory.get("total")) if memory else None
    used_percent = _percent(memory, "used")

    # 只有确认 total 存在且 used 落在 0~100 时才当作百分比，避免把字节数误判成百分比
    if used_percent is not None and used_percent > 100:
        memory_used = int(used_percent)
        memory_usage = percentage(memory_used, total)
    else:
        memory_usage = used_percent
        memory_used = int(total * used_percent / 100) if (total and used_percent is not None) else None

    basic = node.get("basic") if isinstance(node.get("basic"), dict) else {}
    return SystemData(
        cpu_usage=_percent(cpu, "used"),
        cpu_temperature=as_float(cpu.get("temp")) if cpu else None,
        memory_usage=memory_usage,
        memory_used=memory_used,
        memory_total=total,
        rx_bytes_per_s=as_bytes(net.get("download1s")) if net else None,
        tx_bytes_per_s=as_bytes(net.get("upload1s")) if net else None,
        uptime=_humanize_uptime(node.get("uptime")),
        fan_speed=as_float(fan.get("speed")) if isinstance(fan, dict) else None,
        model=_clean_model(basic.get("name")),
        serial=str(basic.get("nasid")) if basic.get("nasid") else None,
    )


def _pool_disks(raw: object) -> list[PoolDisk]:
    """盘位信息：pos 是槽位号，simple_smart 里有通电时长与重映射。"""
    disks: list[PoolDisk] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        smart = item.get("simple_smart") if isinstance(item.get("simple_smart"), dict) else {}
        disks.append(
            PoolDisk(
                pos=str(item.get("pos")) if item.get("pos") is not None else None,
                model=str(item.get("model")) if item.get("model") else None,
                sn=str(item.get("sn")) if item.get("sn") else None,
                dev_type=str(item.get("dev_type")) if item.get("dev_type") else None,
                health=str(item.get("health")) if item.get("health") else None,
                total=as_bytes(item.get("total_size")),
                used=as_bytes(item.get("usage_size")),
                temperature=as_float(item.get("temp")),
                power_on_hours=as_int(smart.get("power_on_hours")),
                reallocated=as_int(smart.get("reallocated_sector_count")),
            )
        )
    return sorted(disks, key=lambda d: int(d.pos) if (d.pos or "").isdigit() else 99)


def normalize_storage(raw: object) -> StorageData:
    pools_raw = as_list(raw, keys=("pool_list", "pools", "list", "items"))
    pools: list[PoolInfo] = []
    for item in pools_raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or pick(item, ["pool_name", "pool"]) or "")
        total = as_bytes(item.get("total_size")) or as_bytes(pick(item, ["total", "capacity"]))
        free = as_bytes(item.get("free_size")) or as_bytes(pick(item, ["free", "available"]))
        used = as_bytes(item.get("usage_size")) or as_bytes(
            pick(item, ["used_size", "used_bytes"])
        )
        if used is None and total is not None and free is not None:
            used = max(total - free, 0)
        pools.append(
            PoolInfo(
                key=name or str(item.get("id", "")),
                name=name or f"pool{item.get('id', '')}",
                total=total,
                used=used,
                free=free,
                usage=percentage(used, total),
                status=str(item.get("status")) if item.get("status") else None,
                pool_type=str(item.get("pool_type")) if item.get("pool_type") else None,
                disks=_pool_disks(item.get("disk_list")),
            )
        )
    return StorageData(pools=pools)


def normalize_containers(listing: object, stats: object = None) -> DockerData:
    items = as_list(listing, keys=("containers", "list", "items"))
    containers: list[ContainerInfo] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        container_id = str(item.get("Id") or pick(item, ["id", "container_id"]) or "")
        raw_name = item.get("Names") or pick(item, ["name", "container_name"])
        if isinstance(raw_name, list):
            raw_name = raw_name[0] if raw_name else None
        name = str(raw_name or container_id[:12] or "container").lstrip("/")
        state = str(item.get("State") or pick(item, ["state", "status"]) or "") or None
        containers.append(
            ContainerInfo(
                id=container_id,
                name=name,
                state=state,
                image=_image_label(item.get("Image")),
                compose=str(item.get("ComposeProject") or "") or None,
                running=(state or "").lower() == "running",
            )
        )

    _apply_stats(containers, stats)
    return DockerData(containers=containers)


def _apply_stats(containers: list[ContainerInfo], stats: object) -> None:
    by_id: dict[str, ContainerInfo] = {}
    for container in containers:
        by_id[container.id] = container
        by_id[container.short_id] = container

    for item in as_list(stats, keys=("stats", "containers", "list", "items")):
        if not isinstance(item, dict):
            continue
        stats_id = str(item.get("container_id") or pick(item, ["id"]) or "")
        target = by_id.get(stats_id) or by_id.get(stats_id[:12])
        if target is None:
            continue
        target.cpu_usage = as_float(item.get("cpu_usage") or item.get("cpu_usage_win"))
        target.memory_bytes = as_bytes(item.get("memory_usage"))
        target.memory_limit = as_bytes(item.get("memory_limit"))


def normalize_engine(raw: object) -> bool | None:
    if not isinstance(raw, dict):
        return None
    status = raw.get("status") or raw.get("state")
    if isinstance(status, bool):
        return status
    if isinstance(status, str):
        lowered = status.strip().lower()
        if lowered in ("on", "running", "enabled", "true", "up"):
            return True
        if lowered in ("off", "stopped", "disabled", "false", "down"):
            return False
    return None


def _image_label(value: object) -> str | None:
    """Image 有时是 sha256 摘要，有时是仓库标签；摘要对用户没意义就丢掉。"""
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("sha256:"):
        return None
    return value


def _clean_model(name: object) -> str | None:
    """`Z4Pro-XXXX` -> `Z4Pro`；拿不到就去掉末尾的短横线后缀。"""
    if not isinstance(name, str) or not name:
        return None
    head, _, tail = name.rpartition("-")
    return head if head and len(tail) <= 6 else name


def _humanize_uptime(value: object) -> str | None:
    """uptime 是秒数字符串，转成 `8天 20小时` 这种可读形式。"""
    seconds = as_float(value)
    if seconds is None or seconds <= 0:
        return None
    total = int(seconds)
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    parts = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if not days and minutes:
        parts.append(f"{minutes}分")
    return " ".join(parts) or "刚刚"

# ── 磁盘与 SMART ────────────────────────────────────────
@dataclass
class DiskInfo:
    sn: str
    model: str | None = None
    dev: str | None = None
    position: str | None = None
    mountpoint: str | None = None
    total: int | None = None
    used: int | None = None
    available: int | None = None
    is_ssd: bool | None = None
    health: str | None = None
    temperature: float | None = None
    power_on_hours: int | None = None
    reallocated: int | None = None


@dataclass
class DiskData:
    disks: list[DiskInfo] = field(default_factory=list)


@dataclass
class DownloadData:
    active: int = 0
    total: int = 0
    seeding: int = 0
    rate_down: int | None = None
    rate_up: int | None = None
    names: list[str] = field(default_factory=list)


# SMART 属性 id：194 温度、9 通电时间、5 重映射扇区
_SMART_TEMP_IDS = (194, 190)
_SMART_POWER_ON_ID = 9
_SMART_REALLOCATED_ID = 5


def _smart_attr(attributes: list, ids: tuple[int, ...]) -> dict:
    """按 ids 的优先级取属性，不按列表顺序（190 常排在 194 前面）。"""
    for wanted in ids:
        for attribute in attributes:
            if isinstance(attribute, dict) and attribute.get("id") == wanted:
                return attribute
    return {}


def _leading_int(value: object) -> int | None:
    """`"34 (Min/Max 0/39)"` -> 34；`"3907 (218 129 0)"` -> 3907。"""
    if value is None:
        return None
    match = re.search(r"-?\d+", str(value))
    return int(match.group()) if match else None


def normalize_smart(raw: object) -> dict:
    """真机 SMART 里 `now_value` 是厂商归一化值（温度会读出 176），不能直接用。

    可靠的是 `str_value` 的首位数字；没有 str_value 时退回较小的 raw_value。
    """
    if not isinstance(raw, dict):
        return {}
    attributes = raw.get("attributes") if isinstance(raw.get("attributes"), list) else []
    if not attributes:
        return {}

    temp_attr = _smart_attr(attributes, _SMART_TEMP_IDS)
    temperature = _leading_int(temp_attr.get("str_value"))
    if temperature is None:
        candidate = as_float(temp_attr.get("now_value"))
        temperature = candidate if candidate is not None and 0 < candidate < 120 else None
    if temperature is not None and not (0 < temperature < 120):
        temperature = None

    power_attr = _smart_attr(attributes, (_SMART_POWER_ON_ID,))
    power_on = _leading_int(power_attr.get("str_value"))
    if power_on is None:
        candidate_int = as_int(power_attr.get("raw_value"))
        power_on = candidate_int if candidate_int is not None and candidate_int < 1_000_000 else None

    reallocated = as_int(_smart_attr(attributes, (_SMART_REALLOCATED_ID,)).get("raw_value"))

    return {
        "is_ssd": bool(raw.get("is_ssd")) if raw.get("is_ssd") is not None else None,
        "health": str(raw.get("health")) if raw.get("health") else None,
        "temperature": temperature,
        "power_on_hours": power_on,
        "reallocated": reallocated,
    }


def normalize_disks(disk_raw: object, smart_map: dict[str, dict] | None = None) -> DiskData:
    """合并 `system disk` 的容量信息与逐盘 SMART 结果。"""
    mounts = {}
    if isinstance(disk_raw, dict):
        mounts = disk_raw.get("mounts") if isinstance(disk_raw.get("mounts"), dict) else {}
    items = mounts.get("disks") if isinstance(mounts.get("disks"), list) else []

    disks: list[DiskInfo] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        sn = str(item.get("sn") or "")
        if not sn:
            continue
        smart_meta = item.get("smart") if isinstance(item.get("smart"), dict) else {}
        smart = (smart_map or {}).get(sn, {})
        disks.append(
            DiskInfo(
                sn=sn,
                model=str(smart_meta.get("device_model") or smart_meta.get("model") or "") or None,
                dev=item.get("dev_name"),
                position=str(item.get("position")) if item.get("position") is not None else None,
                mountpoint=item.get("mountpoint"),
                total=as_bytes(item.get("total")),
                used=as_bytes(item.get("used")),
                available=as_bytes(item.get("available")),
                is_ssd=smart.get("is_ssd"),
                health=smart.get("health"),
                temperature=smart.get("temperature"),
                power_on_hours=smart.get("power_on_hours"),
                reallocated=smart.get("reallocated"),
            )
        )
    return DiskData(disks=disks)


# ── 下载任务 ────────────────────────────────────────────
def normalize_downloads(raw: object) -> DownloadData:
    """`download tasks` 的 list 里每条是一个任务，已完成的 isFinished=true。"""
    if not isinstance(raw, dict):
        return DownloadData()
    tasks = raw.get("list") if isinstance(raw.get("list"), list) else []

    active = 0
    names: list[str] = []
    for task in tasks:
        if not isinstance(task, dict) or task.get("isFinished"):
            continue
        active += 1
        if len(names) < 5 and task.get("name"):
            names.append(str(task["name"]))

    return DownloadData(
        active=active,
        total=as_int(raw.get("total")) or len(tasks),
        seeding=as_int(raw.get("seedingTaskCount")) or 0,
        rate_down=as_bytes(raw.get("totalRateDownload")),
        rate_up=as_bytes(raw.get("totalRateUpload")),
        names=names,
    )


# ── 容器日志 ────────────────────────────────────────────
_LOG_KEYS = ("text", "message", "log", "line", "msg", "content")


def normalize_logs(raw: object) -> list[str]:
    """`docker containers action=logs` 返回最近 100 行快照。

    后端可能是字符串、字符串数组，也可能是 `{"logs": [...]}` 或对象数组，
    这里统一成一行一个字符串的列表。
    """
    items: object = raw
    if isinstance(raw, dict):
        for key in ("logs", "list", "items", "data"):
            if key in raw:
                items = raw[key]
                break
        else:
            items = []

    if isinstance(items, str):
        return [line for line in items.splitlines() if line.strip()]
    if not isinstance(items, list):
        return []

    lines: list[str] = []
    for item in items:
        if isinstance(item, str):
            lines.append(item)
            continue
        if isinstance(item, dict):
            for key in _LOG_KEYS:
                value = item.get(key)
                if value is not None:
                    lines.append(str(value))
                    break
            else:
                lines.append(json.dumps(item, ensure_ascii=False))
    return lines


# ── 虚拟机（ops vm，只读）────────────────────────────────
@dataclass
class VmInfo:
    """一台虚拟机。

    真机字段（`ops vm`，ZOS 4.x）：name / title / osType / cpuNum / memGbNum /
    cpuPercent / memPercent / state 或 domainState（1=运行，5=关闭）/ stateLang /
    vncPort / firmware / uuid。详情接口额外给 sdaList[].virtualsize 与 vnc 地址列。
    """

    name: str
    title: str
    running: bool = False
    raw_state: str | None = None
    cpu_count: int | None = None
    memory_gib: float | None = None
    cpu_percent: float | None = None
    memory_percent: float | None = None
    disk_gib: float | None = None
    ip: str | None = None
    vnc_port: int | None = None
    vnc_endpoints: list[str] = field(default_factory=list)
    os_type: str | None = None
    firmware: str | None = None
    uuid: str | None = None


@dataclass
class VmData:
    vms: list[VmInfo] = field(default_factory=list)

    @property
    def running_count(self) -> int:
        return sum(1 for vm in self.vms if vm.running)


_RUNNING_STATES = ("running", "started", "start", "on", "1", "normal")
_STOPPED_STATES = ("stopped", "shutdown", "halt", "off", "0", "3", "suspend", "paused")


def _vm_running(item: dict) -> tuple[bool, str | None]:
    """电源状态：真机可能是 isStart(bool)、state(数字/字符串) 或 domainState。"""
    raw = pick(item, ["isStart", "running", "state", "domainState", "status"])
    if isinstance(raw, bool):
        return raw, str(int(raw))
    text = str(raw).strip().lower() if raw is not None else ""
    if text in _RUNNING_STATES:
        return True, text
    if text in _STOPPED_STATES:
        return False, text
    # 极空间后端常见 1=运行、2=关闭，认不出来就当停止
    if text.isdigit():
        return text in ("1",), text
    return False, text or None


def _vm_memory_gib(item: dict) -> float | None:
    """内存单位不固定：memoryGib 是 GiB，memory/memoryNum 可能是 MiB。"""
    value = pick(
        item, ["memoryGib", "memory_gib", "memGbNum", "memory", "memoryNum", "mem"]
    )
    number = as_float(value)
    if number is None:
        return None
    if number > 512:  # 明显是 MiB
        return round(number / 1024, 2)
    return round(number, 2)


def _vm_disk_gib(item: dict) -> float | None:
    """磁盘容量：真机详情里 sdaList[].virtualsize 是字节，capacity 是 GB 字符串。"""
    disks = pick(item, ["disks", "diskList", "disk_list", "sdaList", "volumes"])
    if not isinstance(disks, list):
        return None
    total = 0.0
    found = False
    for disk in disks:
        if not isinstance(disk, dict):
            continue
        virtual = as_float(disk.get("virtualsize"))
        if virtual:
            total += virtual / 1024**3
            found = True
            continue
        size = as_float(pick(disk, ["capacityGib", "capacity_gib", "capacity", "size"]))
        if size is None:
            continue
        if size > 1024 * 1024:      # 字节
            size = size / 1024**3
        elif size > 4096:            # MiB
            size = size / 1024
        total += size
        found = True
    return round(total, 2) if found else None


def _vm_endpoints(item: dict) -> list[str]:
    """详情里的 vnc 是 ["192.168.0.170:15566", ...]，纯 TCP 地址。"""
    raw = item.get("vnc")
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(addr) for addr in raw if addr]
    return []


def vm_items(raw: object) -> list[dict]:
    """从 `ops vm` 的返回里取出虚拟机条目列表。"""
    items: object = raw
    if isinstance(raw, dict):
        for key in ("domainList", "list", "items", "data", "domainDetail"):
            if key in raw:
                items = raw[key]
                break
        else:
            items = []
    if isinstance(items, dict):
        items = [items]
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def normalize_vms(raw: object) -> VmData:
    """`ops vm` 返回列表；单台详情是对象，也兼容。"""
    items = vm_items(raw)

    vms: list[VmInfo] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(pick(item, ["name", "vmName", "domainName", "id"]) or "")
        if not name:
            continue
        running, raw_state = _vm_running(item)
        state_lang = str(item.get("stateLang") or "") or None
        vms.append(
            VmInfo(
                name=name,
                title=str(pick(item, ["title", "displayName", "remark", "nickname"]) or name),
                running=running,
                raw_state=state_lang or raw_state,
                cpu_count=as_int(pick(item, ["cpuNum", "cpu_count", "vcpu", "cpu"])),
                memory_gib=_vm_memory_gib(item),
                cpu_percent=as_float(pick(item, ["cpuPercent", "cpu_percent"])),
                memory_percent=as_float(pick(item, ["memPercent", "memoryPercent"])),
                disk_gib=_vm_disk_gib(item),
                ip=str(pick(item, ["ip", "ipAddress", "ipv4", "guestIp"]) or "") or None,
                vnc_port=as_int(pick(item, ["vncPort", "vnc_port"])),
                vnc_endpoints=_vm_endpoints(item),
                os_type=str(pick(item, ["osType", "os_type", "guestType"]) or "") or None,
                firmware=str(item.get("firmware") or "") or None,
                uuid=str(item.get("uuid") or "") or None,
            )
        )
    return VmData(vms=vms)
