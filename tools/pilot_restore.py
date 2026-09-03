#!/usr/bin/env python3
"""Generate representative restoration/OCR candidates from selected PDF sheets.

This is a pilot, not a canonical restoration pipeline. It deliberately produces two
branches from the immutable source:
  * archival: conservative human-facing candidate
  * ocr: aggressive machine-facing candidate

Full-resolution outputs are intended for CI artifacts; compact contact sheets and
metadata may be committed for review.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

DEFAULT_SHEETS = [1, 2, 20, 60, 100, 150, 180, 220, 260, 296, 297, 300]


def render_sheet(pdf: Path, sheet: int, out_png: Path, dpi: int) -> None:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    prefix = out_png.with_suffix("")
    subprocess.run(
        [
            "pdftoppm",
            "-f", str(sheet),
            "-l", str(sheet),
            "-singlefile",
            "-r", str(dpi),
            "-png",
            str(pdf),
            str(prefix),
        ],
        check=True,
    )


def percentile_stretch(gray: np.ndarray, low_p: float = 0.5, high_p: float = 99.5) -> np.ndarray:
    lo, hi = np.percentile(gray, (low_p, high_p))
    if hi <= lo:
        return gray.copy()
    stretched = (gray.astype(np.float32) - lo) * (255.0 / (hi - lo))
    return np.clip(stretched, 0, 255).astype(np.uint8)


def archival_candidate(src: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)
    stretched = percentile_stretch(gray, 0.4, 99.7)

    # Conservative local contrast enhancement. Avoid thresholding: this branch is
    # meant for humans and should preserve stroke shape and gray detail.
    clahe = cv2.createCLAHE(clipLimit=1.25, tileGridSize=(12, 12))
    enhanced = clahe.apply(stretched)

    # Mild unsharp mask; enough to improve old letter edges without inventing them.
    blur = cv2.GaussianBlur(enhanced, (0, 0), 0.8)
    sharp = cv2.addWeighted(enhanced, 1.18, blur, -0.18, 0)
    return sharp


def ocr_candidate(src: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)

    # Estimate uneven paper/background illumination and divide it out.
    background = cv2.GaussianBlur(gray, (0, 0), 31)
    normalized = cv2.divide(gray, background, scale=255)

    clahe = cv2.createCLAHE(clipLimit=2.4, tileGridSize=(10, 10))
    normalized = clahe.apply(normalized)

    # Aggressive binarization is intentionally confined to the OCR branch.
    binary = cv2.adaptiveThreshold(
        normalized,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        51,
        13,
    )

    # Upscale after thresholding to make narrow historical glyphs easier for OCR
    # engines to segment. This does not claim to add source detail.
    return cv2.resize(binary, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_NEAREST)


def make_contact_sheet(paths: list[Path], labels: list[str], output: Path) -> None:
    thumbs: list[Image.Image] = []
    target_w = 700
    label_h = 44

    for path, label in zip(paths, labels):
        img = Image.open(path).convert("L")
        h = round(img.height * target_w / img.width)
        img = img.resize((target_w, h), Image.Resampling.LANCZOS)
        canvas = Image.new("L", (target_w, h + label_h), 255)
        canvas.paste(img, (0, label_h))
        draw = ImageDraw.Draw(canvas)
        draw.text((12, 12), label, fill=0, font=ImageFont.load_default())
        thumbs.append(canvas)

    cols = 3
    rows = (len(thumbs) + cols - 1) // cols
    cell_w = max(t.width for t in thumbs)
    cell_h = max(t.height for t in thumbs)
    sheet = Image.new("L", (cell_w * cols, cell_h * rows), 255)

    for idx, thumb in enumerate(thumbs):
        x = (idx % cols) * cell_w
        y = (idx // cols) * cell_h
        sheet.paste(thumb, (x, y))

    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=90, optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--out", type=Path, default=Path("pilot/v0.1.0"))
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--sheets", type=int, nargs="*", default=DEFAULT_SHEETS)
    args = parser.parse_args()

    full = args.out / "full"
    preview = args.out / "preview"
    full.mkdir(parents=True, exist_ok=True)
    preview.mkdir(parents=True, exist_ok=True)

    refs: list[Path] = []
    archives: list[Path] = []
    ocrs: list[Path] = []
    records = []

    for sheet in args.sheets:
        stem = f"sheet-{sheet:03d}"
        ref_path = full / f"{stem}-reference.png"
        archive_path = full / f"{stem}-archival.png"
        ocr_path = full / f"{stem}-ocr.png"

        render_sheet(args.pdf, sheet, ref_path, args.dpi)
        src = cv2.imread(str(ref_path), cv2.IMREAD_COLOR)
        if src is None:
            raise RuntimeError(f"Could not read rendered sheet {sheet}: {ref_path}")

        archive = archival_candidate(src)
        ocr = ocr_candidate(src)
        cv2.imwrite(str(archive_path), archive)
        cv2.imwrite(str(ocr_path), ocr)

        refs.append(ref_path)
        archives.append(archive_path)
        ocrs.append(ocr_path)
        records.append({
            "source_pdf_sheet": sheet,
            "render_dpi": args.dpi,
            "reference": str(ref_path),
            "archival_candidate": str(archive_path),
            "ocr_candidate": str(ocr_path),
            "reference_shape_px": [int(src.shape[1]), int(src.shape[0])],
            "ocr_shape_px": [int(ocr.shape[1]), int(ocr.shape[0])],
        })

    labels = [f"PDF sheet {n}" for n in args.sheets]
    make_contact_sheet(refs, labels, preview / "contact-reference.jpg")
    make_contact_sheet(archives, labels, preview / "contact-archival.jpg")
    make_contact_sheet(ocrs, labels, preview / "contact-ocr.jpg")

    manifest = {
        "status": "pilot-only",
        "source": str(args.pdf),
        "source_sha256": "bc8dbd965d242f8dbdefb3fbc28a9e24c9d68084a536d388bf96e376b68ff72e",
        "selection_unit": "source PDF sheet (not printed book page)",
        "selected_sheets": args.sheets,
        "notes": [
            "Archival and OCR outputs are experimental candidates, not canonical restorations.",
            "The selection deliberately spans the volume and includes structurally unusual tail sheets.",
            "Printed book-page mapping will be added after page extraction/provenance mapping is validated.",
        ],
        "records": records,
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
