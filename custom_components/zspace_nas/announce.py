"""系统事件音箱播报。

设计要点（避免把音箱变成噪音源）：
- 只播报「新」事件：首次订阅把现有事件 id 全标记为已见，之后只播新出现的 id；
- 级别门槛：默认 warning 及以上（用 normalize.event_level_rank 归一），info 不播；
- 去重 + 冷却：同一事件只播一次，两次播报之间至少隔 60 秒；
- 一次最多拼 3 条，防止一条条排队念半天。

播报走 HA 标准 TTS 服务 `tts.speak`，只传 media_player_entity_id 与 message，
让 HA 用媒体播放器绑定的默认语音引擎，不在这里写死具体引擎。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_ANNOUNCE,
    CONF_ANNOUNCE_LEVEL,
    CONF_ANNOUNCE_MEDIA,
    DEFAULT_ANNOUNCE_LEVEL,
)
from .coordinator import EventCoordinator
from .zspace.normalize import EventInfo, event_level_rank

_LOGGER = logging.getLogger(__name__)

_MAX_PER_BATCH = 3
_COOLDOWN_SECONDS = 60


class EventAnnouncer:
    """把新事件送到音箱。一个 config entry 一个实例。"""

    def __init__(
        self,
        hass: HomeAssistant,
        media_player: str,
        min_level: str,
        coordinator: EventCoordinator,
    ) -> None:
        self.hass = hass
        self.media_player = media_player
        self.min_level_rank = event_level_rank(min_level)
        self.coordinator = coordinator
        self._seen: set[str] = set()
        self._initialized = False
        self._last_spoken = 0.0
        self._unsub = None

    def start(self) -> None:
        """订阅协调器更新；首次更新只建立基线不播报。"""
        self._unsub = self.coordinator.async_add_listener(self._on_update)
        # 已有数据立即建立基线，避免重启后把历史全念一遍
        if self.coordinator.data is not None:
            self._mark_baseline(self.coordinator.data.events)

    def stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    def _mark_baseline(self, events: list[EventInfo]) -> None:
        self._seen.update(event.id for event in events)
        self._initialized = True

    @callback
    def _on_update(self) -> None:
        data = self.coordinator.data
        if data is None:
            return
        if not self._initialized:
            self._mark_baseline(data.events)
            return

        fresh: list[EventInfo] = []
        for event in data.events:
            if event.id in self._seen:
                continue
            self._seen.add(event.id)
            if event_level_rank(event.level) >= self.min_level_rank:
                fresh.append(event)
        if not fresh:
            return
        # 越靠前通常是越新的事件
        fresh.reverse()
        self.hass.async_create_task(self.async_speak_events(fresh))

    async def async_speak_events(self, events: list[EventInfo]) -> None:
        """把若干事件拼成一句话播报；带冷却，超量截断。"""
        now = time.monotonic()
        if now - self._last_spoken < _COOLDOWN_SECONDS:
            return
        picked = events[:_MAX_PER_BATCH]
        parts = [self._describe(event) for event in picked]
        message = "极空间提醒：" + "；".join(part for part in parts if part)
        if message == "极空间提醒：":
            return
        extra = f"，另有 {len(events) - len(picked)} 条" if len(events) > len(picked) else ""
        await self.async_speak(message + extra)

    async def async_speak(self, message: str) -> None:
        """调用 HA 的 TTS 服务；失败只记日志，绝不影响集成本体。"""
        self._last_spoken = time.monotonic()
        try:
            await self.hass.services.async_call(
                "tts",
                "speak",
                {
                    "media_player_entity_id": self.media_player,
                    "message": message,
                },
                blocking=False,
            )
        except Exception as err:  # pylint: disable=broad-except
            _LOGGER.warning("极空间事件播报失败：%s", err)

    @staticmethod
    def _describe(event: EventInfo) -> str:
        text = event.title or event.content or event.type or "系统事件"
        if event.content and event.content != text:
            text = f"{text} {event.content}"
        # TTS 念太长没意义，截到 60 字
        return text[:60]


def setup_announcements(hass: HomeAssistant, entry: ConfigEntry, runtime: Any) -> EventAnnouncer | None:
    """按选项装配播报器；未开启或没填媒体播放器时返回 None。"""
    options = entry.options
    if not options.get(CONF_ANNOUNCE, False):
        return None
    media = str(options.get(CONF_ANNOUNCE_MEDIA, "") or "").strip()
    if not media:
        _LOGGER.info("已开启事件播报但未填媒体播放器，跳过")
        return None
    level = str(options.get(CONF_ANNOUNCE_LEVEL, DEFAULT_ANNOUNCE_LEVEL))
    announcer = EventAnnouncer(hass, media, level, runtime.events)
    announcer.start()
    entry.async_on_unload(announcer.stop)
    return announcer
