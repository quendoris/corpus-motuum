#!/usr/bin/env python3
"""Export benchmark pages into a portable review pack.

The script copies benchmark input images and metadata only; it does not alter
source material.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--manifest",
        default="work/benchmark-v1/inputs/manifest.json",
    )
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pages = out / "pages"
    pages.mkdir(exist_ok=True)

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    exported = []

    for item in manifest.get("pages", []):
        src = Path(item["path"])
        dst = pages / src.name
        shutil.copy2(src, dst)
        exported.append({**item, "exported_path": str(dst)})

    (out / "metadata.json").write_text(
        json.dumps({"pages": exported}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "README.txt").write_text(
        "Corpus Motuum benchmark pack. Source images are extracted benchmark inputs.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
