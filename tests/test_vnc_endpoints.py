"""VNC 候选地址选择的单元测试。

candidate_endpoints 是纯函数但 vnc.py 顶层依赖 HA/aiohttp，测试环境装不起，
所以用 AST 把函数源码抽出来单独 exec（只依赖模块级常量 _INTERNAL_PREFIX）。
真机一台 VM 常有多个 VNC 地址，第一个不可达时必须能尝试后面的。
"""

from __future__ import annotations

import ast
import pathlib
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
VNC = ROOT / "custom_components/zspace_nas/vnc.py"


def _load_candidate_endpoints():
    tree = ast.parse(VNC.read_text(encoding="utf-8"))
    func = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "candidate_endpoints"
    )
    source = ast.get_source_segment(VNC.read_text(encoding="utf-8"), func)
    ns = {"_INTERNAL_PREFIX": "10.222."}
    exec(source, ns)  # noqa: S102 - 源码来自本仓库，受控
    return ns["candidate_endpoints"]


class FakeVm:
    def __init__(self, endpoints: list[str], port: int | None) -> None:
        self.vnc_endpoints = endpoints
        self.vnc_port = port


class FakeRuntime:
    def __init__(self, host: str | None) -> None:
        self.vnc_host = host


candidate_endpoints = _load_candidate_endpoints()


class CandidateEndpointsTest(unittest.TestCase):
    def test_lan_endpoints_before_internal(self) -> None:
        # 真机顺序：内部桥接在前，局域网在后 → 局域网地址必须排前面
        vm = FakeVm(
            ["10.222.222.1:15566", "192.168.0.170:15566", "192.168.0.14:15566"],
            15566,
        )
        got = candidate_endpoints(FakeRuntime(None), vm)
        self.assertEqual(
            got,
            [("192.168.0.170", 15566), ("192.168.0.14", 15566), ("10.222.222.1", 15566)],
        )

    def test_preferred_host_first(self) -> None:
        vm = FakeVm(["10.222.222.1:15566", "192.168.0.170:15566"], 15566)
        got = candidate_endpoints(FakeRuntime("192.168.0.99"), vm)
        self.assertEqual(got[0], ("192.168.0.99", 15566))
        self.assertIn(("192.168.0.170", 15566), got)

    def test_dedup(self) -> None:
        # vnc_host 与端点里同一地址只保留一次
        vm = FakeVm(["192.168.0.170:15566", "192.168.0.170:15566"], 15566)
        got = candidate_endpoints(FakeRuntime("192.168.0.170"), vm)
        self.assertEqual(got, [("192.168.0.170", 15566)])

    def test_bad_endpoint_ignored(self) -> None:
        vm = FakeVm(["garbage", "192.168.0.170:abc", "192.168.0.14:12345"], 12345)
        got = candidate_endpoints(FakeRuntime(None), vm)
        self.assertEqual(got, [("192.168.0.14", 12345)])

    def test_empty(self) -> None:
        self.assertEqual(candidate_endpoints(FakeRuntime(None), FakeVm([], None)), [])


if __name__ == "__main__":
    unittest.main()
