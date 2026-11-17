"""跨模块导入的静态护栏：防止 HA 平台模块引用不存在的名字。

背景：测试环境没有 homeassistant，无法 import 平台模块（sensor/binary_sensor 等），
所以真实的 ImportError 只有在 HA 里加载集成时才暴露（如 VmInfo 未从 coordinator 转出）。
这里用 AST 静态解析每个 `from .X import a, b`，确认 a/b 确实在 X 里定义或转出。
"""

from __future__ import annotations

import ast
import pathlib
import unittest

PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components/zspace_nas"


def exported_names(path: pathlib.Path) -> set[str]:
    """收集一个模块对外可见的名字：import 进来的、定义的、__all__ 里的。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, ast.Tuple):
                    for elt in target.elts:
                        if isinstance(elt, ast.Name):
                            names.add(elt.id)
    return names


class CrossModuleImportTest(unittest.TestCase):
    def test_relative_imports_resolve(self) -> None:
        """每个 `from .mod import name` 的 name 必须在 mod 里真实存在。"""
        cache: dict[str, set[str]] = {}
        problems: list[str] = []
        for src in sorted(PKG.glob("*.py")):
            tree = ast.parse(src.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or not node.level:
                    continue
                module = node.module
                if not module:  # from . import x 形式，跳过
                    continue
                target = PKG / f"{module}.py"
                if not target.exists():
                    # 子包用 __init__.py（如 .zspace）
                    target = PKG / module / "__init__.py"
                if not target.exists():
                    continue
                if module not in cache:
                    cache[module] = exported_names(target)
                available = cache[module]
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    if alias.name not in available:
                        problems.append(
                            f"{src.name}:{node.lineno} 从 .{module} 导入了不存在的 "
                            f"{alias.name}"
                        )
        self.assertEqual(problems, [], "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
