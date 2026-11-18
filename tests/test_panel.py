"""侧边栏面板的静态护栏。

面板是「零手动配置」的核心：JS 由集成托管、面板由官方 API 注册。
这些约束在单元测试里没有 HA 运行时可验证，所以用 AST + 文本检查守住。
"""

from __future__ import annotations

import ast
import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "custom_components/zspace_nas"
PANEL_PY = PKG / "panel.py"
PANEL_JS = PKG / "panel.js"
CARD_JS = ROOT / "www/zspace-card.js"
INIT_PY = PKG / "__init__.py"
MANIFEST = PKG / "manifest.json"

MARKER = "if (!customElements.get('zspace-card'))"


class PanelRegistrationTest(unittest.TestCase):
    def test_manifest_declares_frontend_dependency(self) -> None:
        # panel.py 依赖 homeassistant.components.frontend，manifest 必须声明，
        # 否则 HA 会在集成加载前尚未初始化 frontend 时报错。
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertIn("frontend", data.get("dependencies", []))
        self.assertIn("http", data.get("dependencies", []))

    def test_init_wires_panel_setup_and_unload(self) -> None:
        tree = ast.parse(INIT_PY.read_text(encoding="utf-8"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("async_setup_panel", called)
        self.assertIn("async_remove_panel_if_last", called)

    def test_register_uses_frontend_url(self) -> None:
        # frontend_url 是让 HA 动态加载我们自托管 JS 的关键参数。
        tree = ast.parse(PANEL_PY.read_text(encoding="utf-8"))
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name != "register":
                continue
            kwargs = {kw.arg for kw in node.keywords}
            self.assertIn("frontend_url", kwargs, "面板注册缺少 frontend_url")
            self.assertIn("sidebar", kwargs, "面板注册未设置 sidebar")
            found = True
        self.assertTrue(found, "未找到 async_register_built_in_panel 调用")

    def test_panel_view_does_not_use_self_hass(self) -> None:
        # 与控制台视图同样的 HA 2026 兼容约束。
        tree = ast.parse(PANEL_PY.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
            if "HomeAssistantView" not in bases:
                continue
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for sub in ast.walk(item):
                    if (
                        isinstance(sub, ast.Attribute)
                        and sub.attr == "hass"
                        and isinstance(sub.value, ast.Name)
                        and sub.value.id == "self"
                    ):
                        self.fail(
                            f"{node.name}.{item.name} 第 {sub.lineno} 行使用了 "
                            "self.hass，HA 2026.x 已移除"
                        )

    def test_asset_url_is_namespaced(self) -> None:
        # 避免和控制台视图路径冲突：常量须带命名空间，视图须引用该常量。
        tree = ast.parse(PANEL_PY.read_text(encoding="utf-8"))
        constants: dict[str, str] = {}
        url_refs: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if not isinstance(target, ast.Name):
                        continue
                    if target.id == "PANEL_URL" and isinstance(node.value, ast.Constant):
                        constants[target.id] = node.value.value
                    if target.id == "url":
                        if isinstance(node.value, ast.Constant):
                            url_refs.add(node.value.value)
                        elif isinstance(node.value, ast.Name):
                            url_refs.add(f"ref:{node.value.id}")
        self.assertEqual(constants.get("PANEL_URL"), "/api/zspace_nas/panel.js")
        self.assertIn("ref:PANEL_URL", url_refs)


class PanelAssetTest(unittest.TestCase):
    def test_js_defines_card_and_panel(self) -> None:
        source = PANEL_JS.read_text(encoding="utf-8")
        self.assertIn("customElements.define('zspace-card'", source)
        self.assertIn("customElements.define('ha-panel-zspace'", source)
        # component_name = zspace，HA 前端会查找 ha-panel-zspace 元素。
        self.assertIn("set hass(hass)", source)

    def test_panel_js_keeps_card_in_sync(self) -> None:
        # panel.js = zspace-card.js 全文 + 面板入口。卡片逻辑改动后必须同步，
        # 否则面板和仪表盘卡片会显示不一致。这里守住 marker 之前的公共部分。
        card = CARD_JS.read_text(encoding="utf-8")
        panel = PANEL_JS.read_text(encoding="utf-8")
        self.assertIn(MARKER, card)
        self.assertIn(MARKER, panel)
        self.assertEqual(card.split(MARKER)[0], panel.split(MARKER)[0])


if __name__ == "__main__":
    unittest.main()
