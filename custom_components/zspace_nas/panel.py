"""极空间侧边栏面板：集成自托管卡片 JS 并自动注册为 HA 内置面板。

背景：Lovelace 不允许集成修改用户的仪表盘，所以「往仪表盘手动加卡片」这一步
无法自动化。但 HA 提供 async_register_built_in_panel，可以让集成注册一个
侧边栏页面，用户升级集成后重启即自动出现「极空间」入口，零手动配置。

JS 也由本模块通过 HTTP view 从集成目录直接提供，不需要用户复制文件到 /config/www。
"""

from __future__ import annotations

import logging
import os
from typing import Any

from aiohttp import web

from homeassistant.core import HomeAssistant
from homeassistant.helpers.http import KEY_HASS, HomeAssistantView

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PANEL_NAME = "zspace"
PANEL_TITLE = "极空间"
PANEL_ICON = "mdi:nas"
PANEL_URL = "/api/zspace_nas/panel.js"
JS_FILENAME = "panel.js"
_REGISTERED_KEY = "_panel_registered"


def _read_js(path: str) -> str:
    """在线程池里读 JS，避免阻塞事件循环。"""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class PanelAssetView(HomeAssistantView):
    """从集成目录提供面板 JS，浏览器通过 frontend_url 动态 import。"""

    url = PANEL_URL
    name = "api:zspace_nas:panel_asset"
    # 面板 JS 本身不含隐私数据；保持鉴权，避免外部站点探测集成是否在装。
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        # HA 2026.x 起 HomeAssistantView 不再注入 self.hass，从 app 取。
        hass: HomeAssistant = request.app[KEY_HASS]
        path = os.path.join(os.path.dirname(__file__), JS_FILENAME)
        try:
            source = await hass.async_add_executor_job(_read_js, path)
        except OSError:
            _LOGGER.error("极空间面板资源读取失败：%s", path)
            return web.Response(status=404, text="panel asset not found")
        return web.Response(
            text=source,
            content_type="application/javascript",
            charset="utf-8",
            headers={"Cache-Control": "public, max-age=3600"},
        )


def _frontend_api() -> tuple[Any, Any]:
    """延迟导入前端 API，兼容不同 HA 小版本。"""
    from homeassistant.components import frontend

    return frontend.async_register_built_in_panel, frontend.async_remove_panel


async def async_setup_panel(hass: HomeAssistant) -> None:
    """注册面板资源与侧边栏入口，全局只做一次。"""
    store = hass.data.setdefault(DOMAIN, {})
    if store.get(_REGISTERED_KEY):
        return

    hass.http.register_view(PanelAssetView())
    register, _ = _frontend_api()
    try:
        register(
            hass,
            PANEL_NAME,
            title=PANEL_TITLE,
            icon=PANEL_ICON,
            frontend_url=PANEL_URL,
            sidebar=True,
        )
    except TypeError:
        # 极老的 HA 没有 frontend_url 参数，退化为不注册面板但不影响集成本体。
        _LOGGER.warning("当前 HA 版本不支持 frontend_url，跳过侧边栏面板注册")
        return
    store[_REGISTERED_KEY] = True
    _LOGGER.info("极空间侧边栏面板已注册：%s", PANEL_URL)


async def async_remove_panel_if_last(hass: HomeAssistant) -> None:
    """最后一个配置实例卸载时，移除侧边栏面板。"""
    store = hass.data.get(DOMAIN, {})
    # 还有其它实例在用（key 为 entry_id，非下划线开头的内部标记），保留面板。
    if any(not key.startswith("_") for key in store):
        return
    if not store.pop(_REGISTERED_KEY, False):
        return
    _, remove = _frontend_api()
    remove(hass, PANEL_NAME)
    _LOGGER.info("极空间侧边栏面板已移除")
