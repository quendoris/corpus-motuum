#!/usr/bin/env python3
"""Build side-by-side visual QA sheets for a reconstructed release PDF."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageDraw, ImageFont

THUMB_W = 280
THUMB_H = 448
LABEL_H = 24
GAP = 12
PAIR_W = THUMB_W * 2 + GAP
PAIR_H = THUMB_H + LABEL_H + GAP
PAIRS_PER_ROW = 2
ROWS_PER_SHEET = 5
PAIRS_PER_SHEET = PAIRS_PER_ROW * ROWS_PER_SHEET


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return value


def fit_page(image: Image.Image) -> Image.Image:
    image = image.convert("RGB")
    image.thumbnail((THUMB_W, THUMB_H), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (THUMB_W, THUMB_H), "white")
    canvas.paste(image, ((THUMB_W - image.width) // 2, (THUMB_H - image.height) // 2))
    return canvas


def render_pdf_page(page: fitz.Page) -> Image.Image:
    scale = THUMB_H / max(1.0, page.rect.height)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def sheet_for_pairs(rows: list[tuple[dict[str, Any], Image.Image, Image.Image]]) -> Image.Image:
    width = PAIRS_PER_ROW * PAIR_W + (PAIRS_PER_ROW + 1) * GAP
    height = ROWS_PER_SHEET * PAIR_H + (ROWS_PER_SHEET + 1) * GAP
    sheet = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for local, (page, original, release) in enumerate(rows):
        col = local % PAIRS_PER_ROW
        row = local // PAIRS_PER_ROW
        x = GAP + col * (PAIR_W + GAP)
        y = GAP + row * (PAIR_H + GAP)
        draw.text(
            (x, y),
            f"{page['physical_index']:03d}  {page['id']}   ORIGINAL | RELEASE",
            fill="black",
            font=font,
        )
        y0 = y + LABEL_H
        sheet.paste(fit_page(original), (x, y0))
        sheet.paste(fit_page(release), (x + THUMB_W + GAP, y0))
    return sheet


def selected_pages(book: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    pages = list(book["pages"])
    if mode == "all":
        return pages
    if mode == "figures":
        return [page for page in pages if page.get("figures")]
    if mode == "hotspots":
        selected: list[dict[str, Any]] = []
        for page in pages:
            for asset in page.get("figures", []):
                classes = set(asset.get("classes") or [])
                if classes.intersection({"manual", "restored"}) or asset.get(
                    "requires_visual_placement_review"
                ):
                    selected.append(page)
                    break
        return selected
    raise SystemExit(f"unknown visual review mode: {mode}")


def build_mode(
    book: dict[str, Any],
    source_pages: Path,
    pdf: fitz.Document,
    out_root: Path,
    mode: str,
) -> dict[str, Any]:
    pages = selected_pages(book, mode)
    mode_root = out_root / mode
    mode_root.mkdir(parents=True, exist_ok=True)
    sheet_paths: list[str] = []
    buffer: list[tuple[dict[str, Any], Image.Image, Image.Image]] = []
    sheet_number = 0
    for page in pages:
        physical = int(page["physical_index"])
        source = source_pages / f"{page['id']}.jpg"
        if not source.is_file():
            raise SystemExit(f"visual review source page missing: {source}")
        with Image.open(source) as raw:
            original = raw.convert("RGB")
        release = render_pdf_page(pdf[physical - 1])
        buffer.append((page, original, release))
        if len(buffer) == PAIRS_PER_SHEET:
            sheet_number += 1
            path = mode_root / f"review-{sheet_number:03d}.jpg"
            sheet_for_pairs(buffer).save(path, "JPEG", quality=90, optimize=True)
            sheet_paths.append(path.relative_to(out_root).as_posix())
            buffer = []
    if buffer:
        sheet_number += 1
        path = mode_root / f"review-{sheet_number:03d}.jpg"
        sheet_for_pairs(buffer).save(path, "JPEG", quality=90, optimize=True)
        sheet_paths.append(path.relative_to(out_root).as_posix())
    return {
        "mode": mode,
        "pages": len(pages),
        "page_ids": [str(page["id"]) for page in pages],
        "sheets": sheet_paths,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--book", type=Path, required=True)
    ap.add_argument("--source-pages", type=Path, required=True)
    ap.add_argument("--pdf", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    book = load_json(args.book)
    if len(book.get("pages", [])) != 600:
        raise SystemExit("visual review expects 600 canonical pages")
    pdf = fitz.open(args.pdf)
    if len(pdf) != 600:
        raise SystemExit("visual review expects a 600-page PDF")
    args.out.mkdir(parents=True, exist_ok=True)
    modes = [
        build_mode(book, args.source_pages, pdf, args.out, "all"),
        build_mode(book, args.source_pages, pdf, args.out, "figures"),
        build_mode(book, args.source_pages, pdf, args.out, "hotspots"),
    ]
    pdf.close()
    payload = {
        "schema": "corpus-motuum-release-visual-review-v1",
        "book": args.book.as_posix(),
        "pdf": args.pdf.as_posix(),
        "modes": modes,
    }
    (args.out / "review.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                m["mode"]: {"pages": m["pages"], "sheets": len(m["sheets"])}
                for m in modes
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
