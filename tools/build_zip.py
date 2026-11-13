"""打包成可直接解压进 /config 的 zip（只含 custom_components）。

用法：
    python tools/build_zip.py
产物：
    dist/ha-zspace-nas-<version>.zip
    └── custom_components/zspace_nas/...
"""

from __future__ import annotations

import json
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "zspace_nas"
DIST = ROOT / "dist"
SKIP = {"__pycache__", ".DS_Store"}


def main() -> int:
    version = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))["version"]
    DIST.mkdir(exist_ok=True)
    target = DIST / f"ha-zspace-nas-{version}.zip"

    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(COMPONENT.rglob("*")):
            if path.is_dir() or any(part in SKIP for part in path.parts):
                continue
            if path.suffix == ".pyc":
                continue
            archive.write(path, path.relative_to(ROOT))
            count += 1

    print(f"{target}  ({count} 个文件, {target.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
