"""服务注册的静态护栏：防止 async_register 实参错位导致集成加载崩溃。

这类 bug 在真机上表现为「集成装不上」，单元测试若不 mock HA 很难覆盖，
所以用 AST 直接读源码结构，确保每个 async_register 调用的实参符合签名。
"""

from __future__ import annotations

import ast
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVICES = ROOT / "custom_components/zspace_nas/services.py"
sys.path.insert(0, str(ROOT / "custom_components/zspace_nas"))

from const import (  # noqa: E402
    SERVICE_CONTAINER_ACTION,
    SERVICE_CONTAINER_LOGS,
    SERVICE_DUMP_RAW,
    SERVICE_VNC_URL,
)

EXPECTED_SERVICES = {
    SERVICE_CONTAINER_ACTION,
    SERVICE_CONTAINER_LOGS,
    SERVICE_DUMP_RAW,
    SERVICE_VNC_URL,
}


class ServiceRegistrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tree = ast.parse(SERVICES.read_text(encoding="utf-8"))

    def _registrations(self) -> list[ast.Call]:
        """找出所有 hass.services.async_register(...) 调用。"""
        calls: list[ast.Call] = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "async_register"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "services"
            ):
                calls.append(node)
        return calls

    def test_each_register_has_exactly_three_positional_args(self) -> None:
        # 签名为 async_register(domain, service, func, *, schema=None, ...)
        # 多一个位置参数就会 TypeError，导致整个 async_setup_entry 失败
        for call in self._registrations():
            with self.subTest(line=call.lineno):
                self.assertEqual(
                    len(call.args),
                    3,
                    f"第 {call.lineno} 行 async_register 位置参数应为 3 个，"
                    f"实际 {len(call.args)} 个——多余参数会在加载时抛 TypeError",
                )

    def test_all_expected_services_registered(self) -> None:
        # 源码里用常量名（AST 中是 Name），解析回它们指向的字符串值再比对
        name_to_value = {
            "SERVICE_CONTAINER_ACTION": SERVICE_CONTAINER_ACTION,
            "SERVICE_CONTAINER_LOGS": SERVICE_CONTAINER_LOGS,
            "SERVICE_DUMP_RAW": SERVICE_DUMP_RAW,
            "SERVICE_VNC_URL": SERVICE_VNC_URL,
        }
        registered: set[str] = set()
        for call in self._registrations():
            if len(call.args) >= 2 and isinstance(call.args[1], ast.Name):
                const_name = call.args[1].id
                self.assertIn(
                    const_name,
                    name_to_value,
                    f"第 {call.lineno} 行注册的服务名 {const_name} 不是已知常量",
                )
                registered.add(name_to_value[const_name])
        self.assertEqual(
            registered,
            EXPECTED_SERVICES,
            "注册的服务集合与常量定义不一致",
        )

    def test_handler_is_callable_node(self) -> None:
        # 第三个参数必须是函数引用，不能是字符串常量（曾误把 SERVICE_VNC_URL 放这里）
        for call in self._registrations():
            with self.subTest(line=call.lineno):
                self.assertGreaterEqual(len(call.args), 3)
                handler = call.args[2]
                self.assertIsInstance(
                    handler,
                    (ast.Name, ast.Attribute),
                    f"第 {call.lineno} 行第三个参数应是函数，而不是 {ast.dump(handler)}",
                )


if __name__ == "__main__":
    unittest.main()
