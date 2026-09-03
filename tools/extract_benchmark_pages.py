#!/usr/bin/env python3
"""Extract benchmark physical pages from embedded PDF JPEGs without rerendering."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def jpeg_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if not data.startswith(b"\xff\xd8"):
        raise ValueError(f"not a JPEG: {path}")
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        while i < len(data) and data[i] == 0xFF:
            i += 1
        marker = data[i]
        i += 1
        if marker in {0xD8, 0xD9}:
            continue
        if i + 2 > len(data):
            break
        seg_len = int.from_bytes(data[i:i+2], "big")
        if seg_len < 2 or i + seg_len > len(data):
            break
        if marker in {0xC0,0xC1,0xC2,0xC3,0xC5,0xC6,0xC7,0xC9,0xCA,0xCB,0xCD,0xCE,0xCF}:
            h = int.from_bytes(data[i+3:i+5], "big")
            w = int.from_bytes(data[i+5:i+7], "big")
            return w, h
        i += seg_len
    raise ValueError(f"could not read JPEG dimensions: {path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--manifest", type=Path, default=Path("benchmark/v1/manifest.json"))
    ap.add_argument("--out", type=Path, default=Path("work/benchmark-v1/exact"))
    args = ap.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    by_sheet: dict[int, list[dict]] = {}
    for sample in manifest["samples"]:
        by_sheet.setdefault(int(sample["source_pdf_sheet"]), []).append(sample)

    args.out.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="corpus-motuum-pdfimages-") as td:
        temp = Path(td)
        for sheet, sheet_samples in sorted(by_sheet.items()):
            if {x["spread_side"] for x in sheet_samples} != {"left", "right"}:
                raise SystemExit(f"sheet {sheet}: manifest must contain left and right samples")

            prefix = temp / f"sheet-{sheet:03d}"
            subprocess.run([
                "pdfimages", "-f", str(sheet), "-l", str(sheet), "-j",
                str(args.pdf), str(prefix)
            ], check=True)

            extracted = sorted(temp.glob(f"{prefix.name}-*.jpg"))
            candidates = [p for p in extracted if jpeg_dimensions(p) == (750, 1200)]
            if len(candidates) != 2:
                raise SystemExit(
                    f"sheet {sheet}: expected exactly two embedded 750x1200 JPEGs; "
                    f"found {[(p.name, jpeg_dimensions(p)) for p in extracted]}"
                )

            for side, src in zip(("left", "right"), candidates):
                sample = next(x for x in sheet_samples if x["spread_side"] == side)
                dst = args.out / f"{sample['id']}.jpg"
                shutil.copyfile(src, dst)
                records.append({
                    "id": sample["id"],
                    "source_pdf_sheet": sheet,
                    "spread_side": side,
                    "printed_page_hint": sample.get("printed_page_hint"),
                    "path": str(dst),
                    "width": 750,
                    "height": 1200,
                    "sha256": sha256(dst),
                })

    (args.out / "manifest.json").write_text(
        json.dumps({
            "source_pdf": str(args.pdf),
            "benchmark_manifest": str(args.manifest),
            "method": "pdfimages -j; embedded JPEG bitstream copied without rerendering",
            "records": records,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Extracted {len(records)} exact benchmark pages to {args.out}")


if __name__ == "__main__":
    main()
