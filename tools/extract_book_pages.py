#!/usr/bin/env python3
"""Extract the complete book into 600 physical-page images with provenance.

Ordinary PDF spreads contain two embedded 750x1200 JPEG page images. Those are
copied bit-for-bit. Any spread that does not match that structure is rendered as
a full spread and split at the vertical midpoint instead of being guessed from
its internal image objects.

Generated images are working data and belong under work/. The manifest is small
and is intentionally tracked so every OCR/transcription page can be traced back
to its PDF sheet and extraction method.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import cv2


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
        seg_len = int.from_bytes(data[i:i + 2], "big")
        if seg_len < 2 or i + seg_len > len(data):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height = int.from_bytes(data[i + 3:i + 5], "big")
            width = int.from_bytes(data[i + 5:i + 7], "big")
            return width, height
        i += seg_len
    raise ValueError(f"could not read JPEG dimensions: {path}")


def pdf_page_count(pdf: Path) -> int:
    p = subprocess.run(["pdfinfo", str(pdf)], check=True, capture_output=True, text=True)
    m = re.search(r"^Pages:\s+(\d+)\s*$", p.stdout, flags=re.MULTILINE)
    if not m:
        raise SystemExit("could not determine PDF page count from pdfinfo")
    return int(m.group(1))


def load_benchmark_ids(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(x["id"]) for x in data.get("samples", [])}


def render_and_split(pdf: Path, sheet: int, temp: Path, dpi: int,
                     left_dst: Path, right_dst: Path) -> tuple[tuple[int, int], tuple[int, int]]:
    prefix = temp / f"render-{sheet:03d}"
    subprocess.run([
        "pdftoppm", "-f", str(sheet), "-l", str(sheet),
        "-r", str(dpi), "-jpeg", "-singlefile", str(pdf), str(prefix)
    ], check=True)
    rendered = prefix.with_suffix(".jpg")
    img = cv2.imread(str(rendered), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"sheet {sheet}: could not read rendered spread {rendered}")
    height, width = img.shape[:2]
    if width < 2:
        raise SystemExit(f"sheet {sheet}: rendered spread is too narrow")
    mid = width // 2
    left = img[:, :mid]
    right = img[:, mid:]
    params = [cv2.IMWRITE_JPEG_QUALITY, 95]
    if not cv2.imwrite(str(left_dst), left, params):
        raise SystemExit(f"failed to write {left_dst}")
    if not cv2.imwrite(str(right_dst), right, params):
        raise SystemExit(f"failed to write {right_dst}")
    return (left.shape[1], left.shape[0]), (right.shape[1], right.shape[0])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--out", type=Path, default=Path("work/book-v1/pages"))
    ap.add_argument("--manifest-out", type=Path, default=Path("corpus/source/page-manifest.json"))
    ap.add_argument("--benchmark-manifest", type=Path, default=Path("benchmark/v1/manifest.json"))
    ap.add_argument("--fallback-dpi", type=int, default=300)
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
    held_out = load_benchmark_ids(args.benchmark_manifest)
    total_sheets = pdf_page_count(args.pdf)
    records: list[dict] = []
    fallback_sheets: list[int] = []

    with tempfile.TemporaryDirectory(prefix="corpus-motuum-book-extract-") as td:
        temp = Path(td)
        for sheet in range(1, total_sheets + 1):
            page_ids = {
                "left": f"sheet-{sheet:03d}-left",
                "right": f"sheet-{sheet:03d}-right",
            }
            dst = {
                side: args.out / f"{page_id}.jpg"
                for side, page_id in page_ids.items()
            }

            prefix = temp / f"objects-{sheet:03d}"
            subprocess.run([
                "pdfimages", "-f", str(sheet), "-l", str(sheet), "-j",
                str(args.pdf), str(prefix)
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            extracted = sorted(temp.glob(f"{prefix.name}-*.jpg"))
            candidates = []
            for p in extracted:
                try:
                    if jpeg_dimensions(p) == (750, 1200):
                        candidates.append(p)
                except ValueError:
                    pass

            if len(candidates) == 2:
                dims = {}
                for side, src in zip(("left", "right"), candidates):
                    shutil.copyfile(src, dst[side])
                    dims[side] = (750, 1200)
                method = "embedded-jpeg"
                method_detail = "pdfimages -j; embedded JPEG bitstream copied without rerendering"
            else:
                fallback_sheets.append(sheet)
                left_dims, right_dims = render_and_split(
                    args.pdf, sheet, temp, args.fallback_dpi, dst["left"], dst["right"]
                )
                dims = {"left": left_dims, "right": right_dims}
                method = "rendered-spread-split"
                method_detail = (
                    f"pdftoppm {args.fallback_dpi} dpi JPEG render; full spread split at vertical midpoint; "
                    f"used because embedded objects were not exactly two 750x1200 JPEG pages"
                )

            for side in ("left", "right"):
                page_id = page_ids[side]
                width, height = dims[side]
                records.append({
                    "id": page_id,
                    "physical_index": len(records) + 1,
                    "source_pdf_sheet": sheet,
                    "spread_side": side,
                    "printed_page": None,
                    "path": str(dst[side]),
                    "width": int(width),
                    "height": int(height),
                    "sha256": sha256(dst[side]),
                    "extraction_method": method,
                    "extraction_detail": method_detail,
                    "held_out_benchmark_v1": page_id in held_out,
                })

            if sheet % 25 == 0 or sheet == total_sheets:
                print(f"Extracted PDF sheets: {sheet}/{total_sheets}")

    payload = {
        "schema": "corpus-motuum-page-manifest-v1",
        "source_pdf": str(args.pdf),
        "source_pdf_sha256": sha256(args.pdf),
        "source_pdf_sheets": total_sheets,
        "physical_pages": len(records),
        "ordinary_method": "embedded-jpeg",
        "fallback_method": "rendered-spread-split",
        "fallback_dpi": args.fallback_dpi,
        "fallback_sheets": fallback_sheets,
        "benchmark_manifest": str(args.benchmark_manifest) if args.benchmark_manifest else None,
        "records": records,
    }
    args.manifest_out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Physical pages: {len(records)}")
    print(f"Fallback sheets: {fallback_sheets}")
    print(f"Manifest: {args.manifest_out}")


if __name__ == "__main__":
    main()
