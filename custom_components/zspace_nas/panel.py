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
    """从集成目录提供面板 JS，浏览器作为 ES module 直接加载。

    必须免鉴权：frontend 把 extra_module_url 渲染成 <script type="module">，
    浏览器发这类请求时不会带 Authorization 头，requires_auth=True 会导致 401，
    面板元素永远定义不出来。JS 里只有渲染逻辑，没有任何凭据或 NAS 信息。
    """

    url = PANEL_URL
    name = "api:zspace_nas:panel_asset"
    requires_auth = False

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


def _frontend_api() -> tuple[Any, Any, Any, Any]:
    """延迟导入前端 API，兼容不同 HA 小版本。

    HA 2026 起注册内置面板的签名改成 sidebar_title/sidebar_icon 等，且不再有
    frontend_url 参数；自托管 JS 改用 add_extra_js_url 让前端加载。
    """
    from homeassistant.components import frontend

    return (
        frontend.async_register_built_in_panel,
        frontend.async_remove_panel,
        frontend.add_extra_js_url,
        frontend.remove_extra_js_url,
    )


async def async_setup_panel(hass: HomeAssistant) -> None:
    """注册面板资源与侧边栏入口，全局只做一次。"""
    store = hass.data.setdefault(DOMAIN, {})
    if store.get(_REGISTERED_KEY):
        return

    hass.http.register_view(PanelAssetView())
    register, _, add_js, _ = _frontend_api()
    try:
        # 先让前端把自托管 JS 作为 module 注入；再注册面板引用自定义元素
        # ha-panel-zspace（component_name=zspace 对应 <ha-panel-zspace>）。
        add_js(hass, PANEL_URL)
        register(
            hass,
            PANEL_NAME,
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            frontend_url_path=PANEL_NAME,
        )
    except (TypeError, ValueError) as err:
        # 老 HA 没有 add_extra_js_url 或签名不同：不让集成整体挂掉，仅跳过面板。
        _LOGGER.warning("当前 HA 版本无法注册极空间侧边栏面板：%s", err)
        return
    store[_REGISTERED_KEY] = True
    _LOGGER.info("极空间侧边栏面板已注册：component=%s js=%s", PANEL_NAME, PANEL_URL)


async def async_remove_panel_if_last(hass: HomeAssistant) -> None:
    """最后一个配置实例卸载时，移除侧边栏面板与注入的 JS。"""
    store = hass.data.get(DOMAIN, {})
    # 还有其它实例在用（key 为 entry_id，非下划线开头的内部标记），保留面板。
    if any(not key.startswith("_") for key in store):
        return
    if not store.pop(_REGISTERED_KEY, False):
        return
    _, remove, _, remove_js = _frontend_api()
    remove(hass, PANEL_NAME)
    remove_js(hass, PANEL_URL)
    _LOGGER.info("极空间侧边栏面板已移除")
