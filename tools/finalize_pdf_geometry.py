#!/usr/bin/env python3
"""Finalize fixed-layout PDF geometry for the 1.x book releases.

The ordinary book body is reconstructed on the nominal 450x720 pt page used by
the reader renderer.  The six terminal atlas plates are different: their
canonical page-sticker assets preserve a 750x510 landscape plate frame.  The
source-page manifest records the raster geometry of fallback half-spread crops
for these leaves (1754x2481), not the intended orientation of the plate
content.  Mapping the 750x510 atlas bbox through that raster geometry therefore
shrinks a complete plate into the upper-left corner of a portrait page.

This finalization step replaces only pages carrying canonical ``atlas`` assets
with deterministic landscape pages at the atlas frame aspect ratio, preserves
all other generated PDF pages byte-for-byte at the page-object level, and then
runs a full 600-page geometry/content gate.  It is intentionally data-driven by
asset kind rather than hard-coded physical page numbers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any

import fitz
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

ATLAS_FRAME_WIDTH = 750.0
ATLAS_FRAME_HEIGHT = 510.0
ATLAS_PAGE_WIDTH = 720.0
ATLAS_PAGE_HEIGHT = ATLAS_PAGE_WIDTH * ATLAS_FRAME_HEIGHT / ATLAS_FRAME_WIDTH
ATLAS_TEXT_SIZE = 8.0
GEOMETRY_TOLERANCE = 0.2
MIN_ATLAS_IMAGE_COVERAGE = 0.98


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def find_font() -> str:
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
    ):
        if Path(candidate).is_file():
            return candidate
    raise SystemExit("no Cyrillic-capable system serif font found")


def register_font() -> None:
    if "CMAtlasSerif" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("CMAtlasSerif", find_font()))


def atlas_pages(book: dict[str, Any]) -> dict[int, tuple[dict[str, Any], dict[str, Any]]]:
    result: dict[int, tuple[dict[str, Any], dict[str, Any]]] = {}
    pages = book.get("pages")
    if not isinstance(pages, list) or len(pages) != 600:
        raise SystemExit("PDF geometry finalization requires exactly 600 pages")

    for zero_index, page in enumerate(pages):
        if not isinstance(page, dict):
            raise SystemExit(f"invalid canonical page record at index {zero_index + 1}")
        figures = [row for row in page.get("figures", []) if isinstance(row, dict)]
        atlas = [row for row in figures if row.get("kind") == "atlas"]
        if not atlas:
            continue
        if len(atlas) != 1 or len(figures) != 1:
            raise SystemExit(
                f"{page.get('id')}: atlas leaf must contain exactly one canonical atlas asset"
            )
        asset = atlas[0]
        width = int(asset.get("width") or 0)
        height = int(asset.get("height") or 0)
        if width < 740 or height != 510:
            raise SystemExit(
                f"{page.get('id')}: unexpected canonical atlas geometry {width}x{height}"
            )
        result[zero_index] = (page, asset)

    if len(result) != 6:
        raise SystemExit(f"release expects exactly six atlas leaves, got {len(result)}")
    return result


def build_atlas_replacements(
    atlas: dict[int, tuple[dict[str, Any], dict[str, Any]]],
    root: Path,
    output: Path,
) -> None:
    register_font()
    c = canvas.Canvas(
        str(output),
        pagesize=(ATLAS_PAGE_WIDTH, ATLAS_PAGE_HEIGHT),
        pageCompression=1,
        invariant=1,
    )
    for _, (page, asset) in atlas.items():
        image_path = root / str(asset.get("file") or "")
        if not image_path.is_file():
            raise SystemExit(f"atlas asset missing: {image_path}")
        expected = str(asset.get("sha256") or "")
        if len(expected) != 64 or sha256_file(image_path) != expected:
            raise SystemExit(f"atlas asset hash mismatch: {image_path}")

        c.setPageSize((ATLAS_PAGE_WIDTH, ATLAS_PAGE_HEIGHT))
        c.bookmarkPage(str(page.get("id") or ""))
        c.drawImage(
            ImageReader(str(image_path)),
            0,
            0,
            width=ATLAS_PAGE_WIDTH,
            height=ATLAS_PAGE_HEIGHT,
            preserveAspectRatio=True,
            anchor="c",
            mask="auto",
        )

        # Keep canonical page text selectable/searchable without sacrificing the
        # full plate area.  The label sits in the plate's white upper-right edge.
        text = str(page.get("text") or "")
        if text:
            c.setFont("CMAtlasSerif", ATLAS_TEXT_SIZE)
            text_width = pdfmetrics.stringWidth(
                text, "CMAtlasSerif", ATLAS_TEXT_SIZE
            )
            x = max(6.0, ATLAS_PAGE_WIDTH - 10.0 - text_width)
            c.drawString(x, ATLAS_PAGE_HEIGHT - 12.0, text)
        c.showPage()
    c.save()


def copy_metadata(source: fitz.Document, target: fitz.Document) -> None:
    metadata = {
        key: value
        for key, value in source.metadata.items()
        if isinstance(value, str) and value
    }
    if metadata:
        target.set_metadata(metadata)


def rewrite_pdf(
    pdf_path: Path,
    atlas_pdf: Path,
    atlas: dict[int, tuple[dict[str, Any], dict[str, Any]]],
) -> None:
    source = fitz.open(pdf_path)
    replacements = fitz.open(atlas_pdf)
    if len(source) != 600:
        source.close()
        replacements.close()
        raise SystemExit(f"input PDF has {len(source)} pages, expected 600")
    if len(replacements) != len(atlas):
        source.close()
        replacements.close()
        raise SystemExit("atlas replacement PDF page count mismatch")

    output = fitz.open()
    replacement_index = 0
    for page_index in range(len(source)):
        if page_index in atlas:
            output.insert_pdf(
                replacements,
                from_page=replacement_index,
                to_page=replacement_index,
                links=True,
                annots=True,
            )
            replacement_index += 1
        else:
            output.insert_pdf(
                source,
                from_page=page_index,
                to_page=page_index,
                links=True,
                annots=True,
            )
    copy_metadata(source, output)

    temp_path = pdf_path.with_suffix(".geometry.tmp.pdf")
    output.save(
        temp_path,
        garbage=4,
        deflate=True,
        no_new_id=True,
        preserve_metadata=True,
    )
    output.close()
    source.close()
    replacements.close()
    temp_path.replace(pdf_path)


def validate_final_pdf(
    book: dict[str, Any],
    pdf_path: Path,
    atlas: dict[int, tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    doc = fitz.open(pdf_path)
    if len(doc) != 600:
        doc.close()
        raise SystemExit(f"final PDF has {len(doc)} pages, expected 600")

    total_images = 0
    landscape_pages: list[int] = []
    atlas_coverage: dict[str, list[float]] = {}
    for zero_index, (pdf_page, canonical) in enumerate(zip(doc, book["pages"])):
        physical = zero_index + 1
        page_id = str(canonical.get("id") or "")
        if norm(pdf_page.get_text("text")) != norm(str(canonical.get("text") or "")):
            doc.close()
            raise SystemExit(f"final PDF text mismatch on {page_id}")

        images = pdf_page.get_image_info(xrefs=True)
        expected_count = len(canonical.get("figures", []))
        if len(images) != expected_count:
            doc.close()
            raise SystemExit(
                f"final PDF image count mismatch on {page_id}: "
                f"expected {expected_count}, got {len(images)}"
            )
        total_images += len(images)

        is_landscape = pdf_page.rect.width > pdf_page.rect.height
        if is_landscape:
            landscape_pages.append(physical)

        if zero_index not in atlas:
            if is_landscape:
                doc.close()
                raise SystemExit(f"non-atlas page unexpectedly landscape: {page_id}")
            continue

        if not is_landscape:
            doc.close()
            raise SystemExit(f"atlas page is not landscape: {page_id}")
        if (
            abs(pdf_page.rect.width - ATLAS_PAGE_WIDTH) > GEOMETRY_TOLERANCE
            or abs(pdf_page.rect.height - ATLAS_PAGE_HEIGHT) > GEOMETRY_TOLERANCE
        ):
            doc.close()
            raise SystemExit(
                f"atlas page geometry mismatch on {page_id}: "
                f"{pdf_page.rect.width:.3f}x{pdf_page.rect.height:.3f}"
            )
        if len(images) != 1:
            doc.close()
            raise SystemExit(f"atlas page must contain exactly one image: {page_id}")
        bbox = fitz.Rect(images[0]["bbox"])
        width_coverage = bbox.width / pdf_page.rect.width
        height_coverage = bbox.height / pdf_page.rect.height
        if (
            width_coverage < MIN_ATLAS_IMAGE_COVERAGE
            or height_coverage < MIN_ATLAS_IMAGE_COVERAGE
        ):
            doc.close()
            raise SystemExit(
                f"atlas image does not fill page on {page_id}: "
                f"{width_coverage:.3f}x{height_coverage:.3f}"
            )
        atlas_coverage[page_id] = [width_coverage, height_coverage]

    doc.close()
    expected_landscape = [index + 1 for index in atlas]
    if landscape_pages != expected_landscape:
        raise SystemExit(
            f"landscape page set mismatch: expected {expected_landscape}, got {landscape_pages}"
        )
    if total_images != 168:
        raise SystemExit(f"final PDF contains {total_images} images, expected 168")

    return {
        "mode": "mixed",
        "policy": "portrait body with canonical 750:510 landscape atlas leaves",
        "landscape_pages": landscape_pages,
        "atlas_page_size_points": [ATLAS_PAGE_WIDTH, ATLAS_PAGE_HEIGHT],
        "atlas_minimum_image_coverage": MIN_ATLAS_IMAGE_COVERAGE,
        "atlas_actual_image_coverage": atlas_coverage,
        "validated_pages": 600,
        "validated_images": total_images,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--book", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True)
    args = ap.parse_args()

    book = load_json(args.book)
    pdf_path = args.root / "book.pdf"
    format_manifest_path = args.root / "format-manifest.json"
    if not pdf_path.is_file():
        raise SystemExit(f"PDF missing: {pdf_path}")
    if not format_manifest_path.is_file():
        raise SystemExit(f"format manifest missing: {format_manifest_path}")

    atlas = atlas_pages(book)
    with tempfile.TemporaryDirectory(prefix="corpus-motuum-atlas-") as temp_dir:
        atlas_pdf = Path(temp_dir) / "atlas-pages.pdf"
        build_atlas_replacements(atlas, args.root, atlas_pdf)
        rewrite_pdf(pdf_path, atlas_pdf, atlas)

    geometry = validate_final_pdf(book, pdf_path, atlas)
    manifest = load_json(format_manifest_path)
    pdf_meta = manifest.get("formats", {}).get("pdf")
    if not isinstance(pdf_meta, dict):
        raise SystemExit("format manifest has no PDF metadata")
    pdf_meta["sha256"] = sha256_file(pdf_path)
    pdf_meta["mixed_page_geometry"] = True
    pdf_meta["geometry"] = geometry
    format_manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "pdf": pdf_path.as_posix(),
                "sha256": pdf_meta["sha256"],
                **geometry,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
