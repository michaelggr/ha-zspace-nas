#!/usr/bin/env python3
"""按平台下载官方 zspace-cli 并校验 SHA-256。

用法：
    python tools/fetch_cli.py --dest ./bin
    python tools/fetch_cli.py --dest ./bin --platform linux-arm64

产物来自极空间官方升级域名 update.zenithspace.net，校验值与 Skill 包内
runtime-artifacts.json 一致；对不上就直接失败，不落盘。
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import stat
import sys
import urllib.request
from pathlib import Path

BASE = "https://update.zenithspace.net/AgentToolkit/20261023/"
VERSION = "0.3.11"

ARTIFACTS: dict[str, tuple[str, str]] = {
    "linux-x64": (
        "zspace-cli-v0.3.11-linux-x64",
        "97ed9ea7fdeb88129498838eac3493a546008d2ef4e20778fd4d5538016616a7",
    ),
    "linux-arm64": (
        "zspace-cli-v0.3.11-linux-arm64",
        "200099ea0cbae6e1a4769a68975e628493301a6a19b2912043053f1a29b33fa2",
    ),
    "darwin-x64": (
        "zspace-cli-v0.3.11-darwin-x64",
        "312a08e3b3ed6f5660b949025a379cb107f43efcd684cb25717fba93a7d2f5d",
    ),
    "darwin-arm64": (
        "zspace-cli-v0.3.11-darwin-arm64",
        "f438303c204702e5028751c1c3a26d0c5ff0dc956801a6ef6dd30fbbb4338f7d",
    ),
    "windows-x64": (
        "zspace-cli-v0.3.11-windows-x64.exe",
        "5d5f1468dab6fd6151b03a96d7ddfd0aaf579730b4f37cbcc30ddfcdf54a8031",
    ),
    "windows-arm64": (
        "zspace-cli-v0.3.11-windows-arm64.exe",
        "611baefd7eca61af848ed150f2924c933dce84bc3dd25c0270e22520d415d09c",
    ),
}


def detect_platform() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    if system == "linux":
        return f"linux-{arch}"
    if system == "darwin":
        return f"darwin-{arch}"
    if system == "windows":
        return f"windows-{arch}"
    raise SystemExit(f"不支持的平台：{system}/{machine}")


def main() -> int:
    parser = argparse.ArgumentParser(description="下载并校验 zspace-cli")
    parser.add_argument("--dest", default="./bin", help="输出目录，默认 ./bin")
    parser.add_argument("--platform", default=None, help="指定平台，默认自动识别")
    parser.add_argument("--force", action="store_true", help="已存在时覆盖")
    args = parser.parse_args()

    target = args.platform or detect_platform()
    if target not in ARTIFACTS:
        raise SystemExit(f"未知平台 {target}，可选：{', '.join(ARTIFACTS)}")

    filename, expected = ARTIFACTS[target]
    dest_dir = Path(args.dest)
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if target.startswith("windows") else ""
    out = dest_dir / f"zspace-cli{suffix}"

    if out.exists() and not args.force:
        print(f"{out} 已存在，加 --force 覆盖")
        return 0

    url = BASE + filename
    print(f"下载 {target} → {out}")
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - 固定官方域名
        payload = response.read()

    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected:
        print(f"校验失败：期望 {expected}，实际 {digest}", file=sys.stderr)
        return 1

    out.write_bytes(payload)
    out.chmod(out.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"完成，版本 {VERSION}，{len(payload)} 字节，sha256 校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
