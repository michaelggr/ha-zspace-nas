"""控制台视图的静态护栏：防止用已移除的 self.hass 导致页面 500。

HA 2026.x 起 HomeAssistantView 不再注入 self.hass，视图方法访问它会抛
AttributeError（真机表现为控制台页面 500）。单元测试没有 HA 运行时，
所以用 AST 直接检查 View 子类方法体，确保改用 hass_of(request)。
"""

from __future__ import annotations

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
VNC = ROOT / "custom_components/zspace_nas/vnc.py"


class VncViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tree = ast.parse(VNC.read_text(encoding="utf-8"))

    def test_view_methods_do_not_use_self_hass(self) -> None:
        # 找出继承 HomeAssistantView 的类，检查其方法体里有没有 self.hass
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {
                b.id
                for b in node.bases
                if isinstance(b, ast.Name)
            }
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
                            f"{node.name}.{item.name} 第 {sub.lineno} 行使用了 self.hass；"
                            "HA 2026.x 已移除该属性，请改用 hass_of(request)"
                        )

    def test_hass_helper_reads_key_hass(self) -> None:
        # hass_of 必须用 KEY_HASS 取实例（旧键兜底可保留）
        helper = None
        for node in self.tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "hass_of":
                helper = node
                break
        self.assertIsNotNone(helper, "缺少 hass_of(request) 辅助函数")
        src = ast.get_source_segment(VNC.read_text(encoding="utf-8"), helper) or ""
        self.assertIn("KEY_HASS", src, "hass_of 必须通过 KEY_HASS 取 HA 实例")


if __name__ == "__main__":
    unittest.main()
