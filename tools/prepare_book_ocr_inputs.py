#!/usr/bin/env python3
"""Prepare only the two full-book OCR views we actually use.

- local-gray-150: general recognition input for Tesseract rus/orus
- raw-gray: complementary historical-glyph input for Kraken

The transformation is deterministic and reuses the benchmark preprocessing code.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2

from prepare_benchmark_images import local_gray


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, default=Path("corpus/source/page-manifest.json"))
    ap.add_argument("--out", type=Path, default=Path("work/book-v1/inputs"))
    args = ap.parse_args()

    source = json.loads(args.manifest.read_text(encoding="utf-8"))
    raw_dir = args.out / "raw-gray"
    local150_dir = args.out / "local-gray-150"
    raw_dir.mkdir(parents=True, exist_ok=True)
    local150_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    for idx, rec in enumerate(source["records"], start=1):
        src_path = Path(rec["path"])
        img = cv2.imread(str(src_path), cv2.IMREAD_COLOR)
        if img is None:
            raise SystemExit(f"cannot read source page: {src_path}")

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        raw_path = raw_dir / f"{rec['id']}.png"
        if not cv2.imwrite(str(raw_path), gray):
            raise SystemExit(f"failed to write {raw_path}")
        records.append({
            "sample_id": rec["id"],
            "preset": "raw-gray",
            "path": str(raw_path),
            "source_page_sha256": rec["sha256"],
        })

        local = local_gray(gray)
        local150 = cv2.resize(local, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
        local150_path = local150_dir / f"{rec['id']}.png"
        if not cv2.imwrite(str(local150_path), local150):
            raise SystemExit(f"failed to write {local150_path}")
        records.append({
            "sample_id": rec["id"],
            "preset": "local-gray-150",
            "path": str(local150_path),
            "source_page_sha256": rec["sha256"],
        })

        if idx % 50 == 0 or idx == len(source["records"]):
            print(f"Prepared pages: {idx}/{len(source['records'])}")

    manifest = {
        "schema": "corpus-motuum-ocr-input-manifest-v1",
        "page_manifest": str(args.manifest),
        "presets": ["raw-gray", "local-gray-150"],
        "records": records,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Prepared {len(source['records'])} pages / {len(records)} OCR inputs")


if __name__ == "__main__":
    main()
