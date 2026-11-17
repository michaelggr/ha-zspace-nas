"""控制台链接的签名与校验（不依赖 Home Assistant，方便单测）。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

TOKEN_TTL_HOURS = 12


def sign(secret: bytes, payload: str) -> str:
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()[:32]


def make_token(secret: bytes, entry_id: str, vm: str, hours: int = TOKEN_TTL_HOURS) -> str:
    body = json.dumps(
        {"e": entry_id, "v": vm, "x": int(time.time()) + hours * 3600},
        separators=(",", ":"),
    )
    raw = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return f"{raw}.{sign(secret, raw)}"


def parse_token(secret: bytes, token: str) -> dict[str, Any] | None:
    """验签 + 过期检查；任何异常都当作无效。"""
    try:
        raw, signature = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sign(secret, raw), signature):
        return None
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("x", 0)) < int(time.time()):
        return None
    return payload
