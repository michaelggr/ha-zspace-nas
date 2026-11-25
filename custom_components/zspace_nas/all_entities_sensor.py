"""聚合传感器实体：sensor.zspace_all_entities。

一台 NAS 的全部能力收敛到一个实体，供专用卡片一屏渲染：
- state = 纳入的实体数；
- 属性 view  = 定型好的前端结构（主机 KPI / 存储 / 容器 / 虚拟机 / 概览）；
- 属性 groups = fnOS 风格原始分组，通用卡片也能消费。

刷新策略（比 fnOS 更省）：
- 状态变化 4 秒合并一次；注册表变化 1 秒防抖；5 分钟兜底比对签名；
- 签名没变就完全不写状态机，避免每轮重写大属性；
- 整包属性不进 recorder，绕开 16384 字节单行上限。
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .aggregate import VNC_RENEW_SIGNAL, build_view, classify_device, display_vm_name
from .const import DOMAIN
from .coordinator import ZspaceBaseCoordinator
from .entity import entry_identifier
from .naming import ForcedEntityIdMixin
from .runtime import ZspaceRuntime

_LOGGER = logging.getLogger(__name__)

_KIND_LABEL = {"host": "主机", "container": "容器", "vm": "虚拟机", "other": "其他"}
_KIND_RANK = {"host": 0, "container": 1, "vm": 2, "other": 3}

# 属性体积上限（不写 recorder，仅防止极端机器把状态机撑爆）
_ATTR_BUDGET = 90000
_FLUSH_DELAY = 4.0


class ZspaceAllEntitiesSensor(ForcedEntityIdMixin, SensorEntity):
    """整台 NAS 的聚合视图。"""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:view-dashboard"
    _unrecorded_attributes = frozenset({"view", "groups", "total", "classes"})

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, runtime: ZspaceRuntime):
        self.hass = hass
        self.entry = entry
        self.runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}-all-entities"
        self._attr_name = "所有实体"
        self._payload: dict[str, Any] | None = None
        self._ids: set[str] = set()
        self._sig: Any = None
        self._pending = None
        self._refresh_handle = None
        self._force_write = True
        self._setup_forced_entity_id("sensor", "all_entities")

    @property
    def device_info(self):
        return self.runtime.device

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self):
        return self._ensure_payload().get("total")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._ensure_payload()

    # ---- 生命周期 ---------------------------------------------------- #
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._handle_state_changed)
        )
        self.async_on_remove(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._handle_registry_event
            )
        )
        self.async_on_remove(
            self.hass.bus.async_listen(
                dr.EVENT_DEVICE_REGISTRY_UPDATED, self._handle_registry_event
            )
        )
        # VNC 链接续签后刷新 view 里的 console_url
        self.async_on_remove(
            async_dispatcher_connect(self.hass, VNC_RENEW_SIGNAL, self._force_refresh)
        )
        # GPU / Time Machine / 事件更新后重建 view（这些未必有独立实体）
        for coord in (
            self.runtime.gpu, self.runtime.tm, self.runtime.events,
        ):
            self.async_on_remove(coord.async_add_listener(self._force_refresh))
        self._payload = self._build_payload()
        self._sig = self._signature()
        self.async_on_remove(async_track_time_interval(self.hass, self._async_poll, timedelta(minutes=5)))
        self.async_on_remove(self._cancel_timers)

    def _cancel_timers(self) -> None:
        for handle in (self._pending, self._refresh_handle):
            if handle is not None:
                handle()
        self._pending = None
        self._refresh_handle = None

    @callback
    def _force_refresh(self, *args: Any) -> None:
        """外部信号（如 VNC 续签）→ 强制重建一次。"""
        self._force_write = True
        if self._refresh_handle is None:
            self._refresh_handle = async_call_later(self.hass, 1.0, self._refresh_now)

    @callback
    def _handle_state_changed(self, event) -> None:
        entity_id = (event.data or {}).get("entity_id")
        if not entity_id or entity_id == self.entity_id or entity_id not in self._ids:
            return
        if self._pending is None:
            self._pending = async_call_later(self.hass, _FLUSH_DELAY, self._flush_soon)

    @callback
    def _handle_registry_event(self, event) -> None:
        # 注册表/设备变化只可能是本集成的实体：1 秒后强制重建即可
        if self._refresh_handle is None:
            self._force_write = True
            self._refresh_handle = async_call_later(self.hass, 1.0, self._refresh_now)

    async def _flush_soon(self, _now=None) -> None:
        self._pending = None
        self._write_if_changed()

    async def _refresh_now(self, _now=None) -> None:
        self._refresh_handle = None
        self._write_if_changed()

    async def _async_poll(self, _now=None) -> None:
        self._write_if_changed()

    # ---- 组装 -------------------------------------------------------- #
    def _signature(self) -> tuple:
        states = self.hass.states
        parts = []
        for eid in sorted(self._ids):
            state = states.get(eid)
            parts.append((eid, state.state if state else None,
                          state.last_changed if state else None))
        # GPU / 事件这类数据未必有对应实体，直接把协调器数据纳入签名，
        # 变化时也能触发 view 重建（事件列表不再需要等实体状态变化）。
        parts.append(self._coordinator_signature())
        return tuple(parts)

    def _coordinator_signature(self) -> str:
        """GPU / Time Machine / 事件数据的轻量签名。"""
        try:
            runtime = self.runtime
            gpu = runtime.gpu.data
            tm = runtime.tm.data
            events = runtime.events.data
            return json.dumps(
                {
                    "gpu": [vars(g) for g in gpu.gpus] + [vars(t) for t in gpu.tasks]
                    if gpu else None,
                    "gpu_ok": [gpu.info_available, gpu.tasks_available] if gpu else None,
                    "tm": vars(tm) if tm else None,
                    "events": [vars(e) for e in events.events] if events else None,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        except Exception:  # pragma: no cover - 防御
            return ""

    def _write_if_changed(self) -> None:
        sig = self._signature()
        if not self._force_write and sig == self._sig:
            return
        self._force_write = False
        self._sig = sig
        self._payload = None
        self.async_write_ha_state()

    def _collect(self) -> tuple[dict[str, Any], dict[str, dict[str, Any]], set[str]]:
        """扫描本集成实体：返回 (entity_id→state, entity_id→registry, ids)。"""
        registry = er.async_get(self.hass)
        states: dict[str, Any] = {}
        entries: dict[str, Any] = {}
        ids: set[str] = set()
        for item in er.async_entries_for_config_entry(registry, self.entry.entry_id):
            if item.disabled_by or item.entity_id == self.entity_id:
                continue
            state = self.hass.states.get(item.entity_id)
            if state is None:
                continue
            states[item.entity_id] = {"state": state.state, "attributes": dict(state.attributes)}
            entries[item.entity_id] = item
            ids.add(item.entity_id)
        return states, entries, ids

    def _build_payload(self) -> dict[str, Any]:
        try:
            states, entries, ids = self._collect()
        except Exception as err:  # pragma: no cover - 防御
            _LOGGER.warning("极空间聚合实体扫描失败：%s", err)
            states, entries, ids = {}, {}, set()
        self._ids = ids

        # 用协调器数据补齐「非实体」的详表（容器性能、存储成员盘、VM 控制台）
        extra = self._build_extra(states, entries)
        view = build_view(
            states,
            friendly=lambda eid: (states.get(eid) or {}).get("attributes", {}).get("friendly_name"),
            extra=extra,
        )
        groups = self._build_groups(entries, states)

        payload = {
            "total": len(ids),
            "classes": len(groups),
            "view": view,
            "groups": groups,
        }
        # 极端情况裁剪 groups（view 已limit在合理体积，优先保 view）
        if self._size(payload) > _ATTR_BUDGET:
            for group in groups:
                group["entities"] = group["entities"][:20]
            payload["groups"] = groups
        return payload

    def _build_extra(self, states, entries) -> dict[str, Any]:
        runtime = self.runtime
        # 存储池 + 成员盘
        pools = []
        storage = runtime.storage.data
        if storage:
            for pool in storage.pools:
                pools.append({
                    "name": pool.name,
                    "key": pool.key,
                    "usage": pool.usage,
                    "used": pool.used,
                    "free": pool.free,
                    "status": pool.status,
                    "type": pool.pool_type,
                    "disks": [
                        {
                            "pos": d.pos, "model": d.model, "sn": d.sn,
                            "health": d.health, "healthy": d.healthy,
                            "total": d.total, "temperature": d.temperature,
                            "power_on_hours": d.power_on_hours,
                            "reallocated": d.reallocated,
                        }
                        for d in pool.disks
                    ],
                })
        # 设备 identifier → {domain: [entity_id]}，按设备精确关联可控实体，
        # 不靠友好名猜（中文名/短 ID/重命名都不会错位）。
        by_device = self._device_index(entries)

        # 容器：设备 identifier = <nas>_<id12>
        containers = []
        docker = runtime.docker.data
        if docker:
            for c in docker.containers:
                ident = f"{runtime.identifier}_{c.short_id}"
                devs = by_device.get(ident, {})
                switch_id = next(iter(devs.get("switch", [])), None)
                # 运行态 sensor/binary_sensor 走设备关联
                state_id = next(iter(devs.get("binary_sensor", [])), None)
                containers.append({
                    "name": c.name,
                    "state": c.state,
                    "running": c.running,
                    "cpu": c.cpu_usage,
                    "memory_mb": round(c.memory_bytes / 1048576, 1) if c.memory_bytes else None,
                    "image": c.image,
                    "entity_id": state_id,
                    "switch_id": switch_id,
                    "action_button_id": next(iter(devs.get("button", [])), None),
                })
        # 虚拟机：设备 identifier = <nas>_vm_<name>
        vms = []
        vm_data = runtime.vm.data
        if vm_data:
            for vm in vm_data.vms:
                ident = f"{runtime.identifier}_vm_{vm.name}"
                devs = by_device.get(ident, {})
                # 真机 VM 列表常缺 title/stateLang：标题会变成 zspace_xxx 机器名，
                # 状态文案也会空。这里只做展示层兜底，raw name/state 原值保留给自动化。
                shown_title = vm.title or display_vm_name(vm.name)
                shown_state = vm.raw_state or ("运行中" if vm.running else "已关闭")
                vms.append({
                    "name": vm.name,
                    "title": shown_title,
                    "state": shown_state,
                    "running": vm.running,
                    "cpu": vm.cpu_percent,
                    "memory": vm.memory_percent,
                    "ip": vm.ip,
                    "vnc_port": vm.vnc_port,
                    "os": vm.os_type,
                    "entity_id": next(iter(devs.get("binary_sensor", [])), None),
                    "switch_id": next(iter(devs.get("switch", [])), None),
                    "reboot_button_id": next(iter(devs.get("button", [])), None),
                    "console_url": self._console_url(vm.name),
                })
        # GPU / AI 任务：接口不支持的机型给明确标记，前端显示「不支持」
        gpu_view: dict[str, Any] = {"available": False, "gpus": [], "tasks": []}
        gpu_data = runtime.gpu.data
        if gpu_data:
            gpu_view = {
                "available": gpu_data.info_available or gpu_data.tasks_available,
                "gpus": [vars(g) for g in gpu_data.gpus],
                "tasks": [vars(t) for t in gpu_data.tasks],
            }
        # Time Machine 备份状态
        tm_view: dict[str, Any] = {}
        tm_data = runtime.tm.data
        if tm_data:
            tm_view = {
                "status": tm_data.status,
                "enabled": tm_data.enabled,
                "limit_gib": round(tm_data.limit_bytes / 1024**3, 1)
                if tm_data.limit_bytes else None,
                "used_gib": round(tm_data.used_bytes / 1024**3, 1)
                if tm_data.used_bytes else None,
                "usage": tm_data.usage,
                "path": tm_data.path,
                "have_backup": tm_data.have_backup,
            }
        # 系统事件：前端只展示最近 20 条，播报器自己按 id 去重
        event_views: list[dict[str, Any]] = []
        event_data = runtime.events.data
        if event_data:
            event_views = [vars(e) for e in event_data.events[:20]]

        # 型号/版本从设备信息取（DeviceInfo 是 Mapping），比 runtime 属性可靠
        dev = runtime.device or {}
        model = dev.get("model") if hasattr(dev, "get") else None
        return {
            "pools": pools,
            "containers": containers,
            "vms": vms,
            "gpu": gpu_view,
            "time_machine": tm_view,
            "events": event_views,
            "host_name": self.entry.title,
            "model": model,
            "version": None,  # 固件版本当前未进聚合视图，保留字段供卡片兼容
        }

    def _device_index(self, entries) -> dict[str, dict[str, list[str]]]:
        """按设备标识把实体分好域：{identifier: {domain: [entity_id, ...]}}。"""
        dev_reg = dr.async_get(self.hass)
        index: dict[str, dict[str, list[str]]] = {}
        for eid, item in entries.items():
            device = dev_reg.async_get(item.device_id) if item.device_id else None
            if not device:
                continue
            ident = next((i for d, i in device.identifiers if d == DOMAIN), None)
            if not ident:
                continue
            domain = eid.split(".", 1)[0]
            index.setdefault(ident, {}).setdefault(domain, []).append(eid)
        return index

    def _console_url(self, vm_name: str) -> str | None:
        """从设备注册表 configuration_url 取该 VM 的 noVNC 链接（已签名）。"""
        identifiers = {(DOMAIN, f"{self.runtime.identifier}_vm_{vm_name}")}
        device = dr.async_get(self.hass).async_get_device(identifiers=identifiers)
        return getattr(device, "configuration_url", None) if device else None

    def _build_groups(self, entries, states) -> list[dict[str, Any]]:
        """fnOS 风格原始分组：一台设备一个节点。"""
        dev_reg = dr.async_get(self.hass)
        groups: dict[str, dict[str, Any]] = {}
        for eid, item in entries.items():
            device = dev_reg.async_get(item.device_id) if item.device_id else None
            identifier = ""
            if device:
                identifier = next(
                    (ident for dom, ident in device.identifiers if dom == DOMAIN), ""
                )
            kind, key = classify_device(identifier, self.runtime.identifier)
            group_key = f"{kind}:{key}"
            group = groups.setdefault(group_key, {
                "key": group_key,
                "name": (device.name if device else self.entry.title) or group_key,
                "kind": kind,
                "label": _KIND_LABEL.get(kind, "其他"),
                "count": 0,
                "entities": [],
            })
            state = states.get(eid, {})
            attrs = state.get("attributes", {})
            group["entities"].append({
                "entity_id": eid,
                "name": item.name or item.original_name or eid.rsplit(".", 1)[-1],
                "state": state.get("state"),
                "icon": attrs.get("icon") or item.icon or item.original_icon,
            })
            group["count"] += 1
        return sorted(
            groups.values(),
            key=lambda g: (_KIND_RANK.get(g["kind"], 9), g["name"]),
        )

    def _ensure_payload(self) -> dict[str, Any]:
        if self._payload is None:
            self._payload = self._build_payload()
        return self._payload

    @staticmethod
    def _size(obj: Any) -> int:
        return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """把聚合实体加入 sensor 平台（放在平台末尾，此时其它实体已就绪）。"""
    runtime: ZspaceRuntime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ZspaceAllEntitiesSensor(hass, entry, runtime)])
