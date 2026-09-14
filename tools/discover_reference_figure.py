#!/usr/bin/env python3
"""Locate a damaged translated figure in a public-domain reference edition.

The tool ranks PDF pages by OCR text evidence, renders compact candidate
contact sheets, and records the exact downloaded-file digest.  It deliberately
does not choose or copy an illustration: that remains an explicit, reviewable
editorial decision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import unicodedata
from pathlib import Path

import fitz
from PIL import Image, ImageDraw, ImageFont


QUERY_GROUPS = [
    (9, ("fig 74", "figure 74", "74 fig", "74 figure")),
    (5, ("poignee", "corde", "main droite", "main gauche")),
    (4, ("courir a gauche", "courir a droite", "cinquieme exercice")),
    (3, ("quatre ou cinq pas", "sans toucher la terre", "pieds reunis")),
    (2, ("cercle", "bras", "poitrine")),
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("\u0153", "oe")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def score_text(text: str) -> tuple[int, list[str]]:
    normal = normalize(text)
    score = 0
    hits: list[str] = []
    for weight, terms in QUERY_GROUPS:
        for term in terms:
            if term in normal:
                score += weight
                hits.append(term)
    return score, hits


def render_page(page: fitz.Page, max_width: int = 500) -> Image.Image:
    zoom = max_width / max(1.0, page.rect.width)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def make_sheet(items: list[tuple[dict, Image.Image]], path: Path) -> None:
    columns = 4
    tile_w, tile_h, image_h = 520, 790, 730
    rows = math.ceil(len(items) / columns)
    canvas = Image.new("RGB", (columns * tile_w, rows * tile_h), (232, 232, 232))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    for index, (record, image) in enumerate(items):
        col, row = index % columns, index // columns
        x, y = col * tile_w, row * tile_h
        draw.rectangle((x + 2, y + 2, x + tile_w - 3, y + tile_h - 3), fill="white")
        shown = image.copy()
        shown.thumbnail((tile_w - 16, image_h - 8), Image.Resampling.LANCZOS)
        canvas.paste(shown, (x + (tile_w - shown.width) // 2, y + 4))
        label = f"PDF {record['pdf_page']} | score={record['score']} | {','.join(record['hits'])[:55]}"
        draw.text((x + 8, y + image_h + 8), label, fill=(15, 15, 15), font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path, "JPEG", quality=90, optimize=True, progressive=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--work-url", required=True)
    parser.add_argument("--top", type=int, default=32)
    parser.add_argument("--fallback-start", type=int, default=None)
    parser.add_argument("--fallback-end", type=int, default=None)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    document = fitz.open(args.pdf)
    records: list[dict] = []
    text_pages = 0
    for index, page in enumerate(document):
        text = page.get_text("text")
        if text.strip():
            text_pages += 1
        score, hits = score_text(text)
        excerpt = normalize(text)
        records.append({
            "pdf_page": index + 1,
            "printed_labels": [label for label in page.get_label().split(",") if label],
            "score": score,
            "hits": hits,
            "excerpt": excerpt[:500],
        })

    ranked = sorted(records, key=lambda row: (-row["score"], row["pdf_page"]))
    selected = [row for row in ranked if row["score"] > 0][: args.top]
    selection_mode = "ocr-ranking"
    if len(selected) < 12:
        # A deterministic visual fallback for image-only PDFs.  The translated
        # p.215 occurs in the middle third of the 700-page French source.
        selection_mode = "deterministic-middle-third-fallback"
        if args.fallback_start is not None or args.fallback_end is not None:
            start = max(1, int(args.fallback_start or 1))
            end = min(len(document), int(args.fallback_end or len(document)))
            if start > end:
                raise SystemExit("fallback start must not exceed fallback end")
            candidates = list(range(start, end + 1))
            selection_mode = "explicit-dense-fallback"
        else:
            candidates = list(range(max(1, len(document) // 3), min(len(document), 2 * len(document) // 3), 10))
        selected = [
            {**records[page - 1], "fallback": True}
            for page in candidates[: args.top]
        ]

    rendered: list[tuple[dict, Image.Image]] = []
    for row in selected:
        rendered.append((row, render_page(document[row["pdf_page"] - 1])))
    sheet_paths: list[str] = []
    for offset in range(0, len(rendered), 8):
        path = args.out / f"candidates-{offset // 8 + 1:02d}.jpg"
        make_sheet(rendered[offset : offset + 8], path)
        sheet_paths.append(str(path))

    payload = {
        "schema": "corpus-motuum-reference-figure-discovery-v1",
        "purpose": "Locate the clean source analogue for Russian figure 74 without generative redrawing.",
        "reference": {
            "work_url": args.work_url,
            "pdf_url": args.source_url,
            "license": "Public Domain Mark",
            "pdf_sha256": sha256_file(args.pdf),
            "pdf_bytes": args.pdf.stat().st_size,
            "pdf_pages": len(document),
            "pages_with_extractable_text": text_pages,
        },
        "selection_mode": selection_mode,
        "queries": [{"weight": weight, "terms": list(terms)} for weight, terms in QUERY_GROUPS],
        "candidate_count": len(selected),
        "candidates": selected,
        "contact_sheets": sheet_paths,
    }
    (args.out / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "pdf_pages": len(document),
        "text_pages": text_pages,
        "selection_mode": selection_mode,
        "top_candidates": selected[:10],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
