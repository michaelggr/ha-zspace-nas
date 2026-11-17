"""控制台链接令牌：签名、过期、篡改。"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "custom_components/zspace_nas"))

from vnc_token import make_token, parse_token, sign  # noqa: E402

SECRET = b"0123456789abcdef" * 4


class TokenTest(unittest.TestCase):
    def test_roundtrip(self) -> None:
        token = make_token(SECRET, "entry1", "zspace_abc", 1)
        payload = parse_token(SECRET, token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["e"], "entry1")
        self.assertEqual(payload["v"], "zspace_abc")

    def test_expired(self) -> None:
        token = make_token(SECRET, "entry1", "vm", hours=-1)
        self.assertIsNone(parse_token(SECRET, token))

    def test_tampered_payload(self) -> None:
        token = make_token(SECRET, "entry1", "vm", 1)
        raw, sig = token.split(".", 1)
        # 改一位内容，签名对不上
        broken = ("A" if raw[0] != "A" else "B") + raw[1:] + "." + sig
        self.assertIsNone(parse_token(SECRET, broken))

    def test_wrong_secret(self) -> None:
        token = make_token(SECRET, "entry1", "vm", 1)
        self.assertIsNone(parse_token(b"x" * 32, token))

    def test_garbage(self) -> None:
        for value in ("", "abc", "abc.def", "....", "a.b.c"):
            self.assertIsNone(parse_token(SECRET, value))

    def test_signature_is_stable(self) -> None:
        self.assertEqual(sign(SECRET, "abc"), sign(SECRET, "abc"))
        self.assertNotEqual(sign(SECRET, "abc"), sign(SECRET, "abd"))


if __name__ == "__main__":
    unittest.main()
