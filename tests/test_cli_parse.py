"""CLI 输出解析测试：不启动子进程，只验证 JSON 与错误码处理。

跑法：
    python -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "custom_components" / "zspace_nas"
sys.path.insert(0, str(_PKG))

from zspace.base import (  # noqa: E402
    ZspaceAuthError,
    ZspacePermissionError,
    ZspaceUnavailableError,
)
from zspace.cli import ZspaceCliClient  # noqa: E402


def _client() -> ZspaceCliClient:
    return ZspaceCliClient(binary="x", profile_home="y")


class ParseTest(unittest.TestCase):
    def test_ok_payload_returns_data(self) -> None:
        stdout = b'{"ok":true,"profile":"ha","data":{"cpu":{"usage":12}}}'
        result = _client()._parse(stdout, b"", 0, "system", "info")
        self.assertEqual(result, {"cpu": {"usage": 12}})

    def test_error_payload_maps_auth(self) -> None:
        stderr = b'{"ok":false,"error":{"code":"E_OAUTH","message":"expired"}}'
        with self.assertRaises(ZspaceAuthError):
            _client()._parse(b"", stderr, 1, "system", "info")

    def test_not_logged_in_maps_auth(self) -> None:
        # 实跑 CLI 得到的是这个码，文档里写的是 E_OAUTH，两个都要认
        stderr = b'{"ok":false,"error":{"code":"E_NOT_LOGGED_IN","message":"not logged in"}}'
        with self.assertRaises(ZspaceAuthError):
            _client()._parse(b"", stderr, 1, "system", "info")

    def test_error_payload_maps_permission(self) -> None:
        stderr = b'{"ok":false,"error":{"code":"E_PERMISSION","message":"denied"}}'
        with self.assertRaises(ZspacePermissionError):
            _client()._parse(b"", stderr, 1, "docker", "containers")

    def test_skips_log_lines_before_json(self) -> None:
        stdout = b"warming up\nconnecting\ndone\n{\"ok\":true,\"data\":[]}"
        result = _client()._parse(stdout, b"", 0, "docker", "containers")
        self.assertEqual(result, [])

    def test_garbage_output_raises(self) -> None:
        with self.assertRaises(ZspaceUnavailableError):
            _client()._parse(b"not json at all", b"", 1, "system", "info")


if __name__ == "__main__":
    unittest.main()
