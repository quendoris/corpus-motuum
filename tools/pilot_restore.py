#!/usr/bin/env python3
"""Generate representative physical-page restoration/OCR candidates.

Pilot v0.1.1 deliberately separates the two goals:
  * archival: conservative human-facing restoration candidate
  * ocr: machine-facing grayscale normalization candidate

The immutable source is never overwritten. Six ordinary PDF spreads are split into
12 physical-page samples. Full-resolution outputs are kept as CI artifacts while
compact contact sheets and provenance metadata are committed for review.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Six ordinary spreads -> twelve physical-page samples. Sheet 169 maps to the
# printed 297/298 spread shown during pilot review and is intentionally retained as
# a difficult reference case.
DEFAULT_SHEETS = [2, 20, 60, 100, 169, 260]


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


def split_spread(src: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Split an ordinary landscape spread into left and right physical pages."""
    width = src.shape[1]
    mid = width // 2
    return src[:, :mid].copy(), src[:, mid:].copy()


def percentile_stretch(gray: np.ndarray, low_p: float = 0.5, high_p: float = 99.5) -> np.ndarray:
    lo, hi = np.percentile(gray, (low_p, high_p))
    if hi <= lo:
        return gray.copy()
    stretched = (gray.astype(np.float32) - lo) * (255.0 / (hi - lo))
    return np.clip(stretched, 0, 255).astype(np.uint8)


def archival_candidate(src: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)
    stretched = percentile_stretch(gray, 0.4, 99.7)

    # Keep this branch conservative: mild local contrast and sharpening only.
    clahe = cv2.createCLAHE(clipLimit=1.20, tileGridSize=(12, 12))
    enhanced = clahe.apply(stretched)
    blur = cv2.GaussianBlur(enhanced, (0, 0), 0.8)
    return cv2.addWeighted(enhanced, 1.16, blur, -0.16, 0)


def crop_ocr_border(gray: np.ndarray) -> np.ndarray:
    """Remove a thin scan border before OCR and restore a clean white margin."""
    h, w = gray.shape
    x = max(4, round(w * 0.012))
    y = max(4, round(h * 0.010))
    cropped = gray[y : h - y, x : w - x]
    return cv2.copyMakeBorder(cropped, 36, 36, 36, 36, cv2.BORDER_CONSTANT, value=255)


def ocr_candidate(src: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)
    gray = crop_ocr_border(gray)

    # Correct uneven paper illumination while retaining grayscale stroke detail.
    # Pilot v0.1.0 showed that immediate hard binarization fragmented historical
    # glyphs and did not improve OCR enough to justify the information loss.
    background = cv2.GaussianBlur(gray, (0, 0), 29)
    normalized = cv2.divide(gray, background, scale=255)
    normalized = percentile_stretch(normalized, 0.25, 99.8)

    clahe = cv2.createCLAHE(clipLimit=1.75, tileGridSize=(10, 10))
    enhanced = clahe.apply(normalized)
    blur = cv2.GaussianBlur(enhanced, (0, 0), 0.65)
    sharp = cv2.addWeighted(enhanced, 1.22, blur, -0.22, 0)

    # Enlarging aids segmentation but is explicitly a machine-working transform,
    # not a claim of additional source resolution.
    return cv2.resize(sharp, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)


def make_contact_sheet(paths: list[Path], labels: list[str], output: Path) -> None:
    thumbs: list[Image.Image] = []
    target_w = 520
    label_h = 42

    for path, label in zip(paths, labels):
        img = Image.open(path).convert("L")
        h = round(img.height * target_w / img.width)
        img = img.resize((target_w, h), Image.Resampling.LANCZOS)
        canvas = Image.new("L", (target_w, h + label_h), 255)
        canvas.paste(img, (0, label_h))
        draw = ImageDraw.Draw(canvas)
        draw.text((12, 12), label, fill=0, font=ImageFont.load_default())
        thumbs.append(canvas)

    cols = 4
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
    parser.add_argument("--out", type=Path, default=Path("pilot/v0.1.1"))
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--sheets", type=int, nargs="*", default=DEFAULT_SHEETS)
    args = parser.parse_args()

    full = args.out / "full"
    preview = args.out / "preview"
    work = args.out / "_work"
    full.mkdir(parents=True, exist_ok=True)
    preview.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    refs: list[Path] = []
    archives: list[Path] = []
    ocrs: list[Path] = []
    labels: list[str] = []
    records = []

    for sheet in args.sheets:
        spread_path = work / f"sheet-{sheet:03d}.png"
        render_sheet(args.pdf, sheet, spread_path, args.dpi)
        spread = cv2.imread(str(spread_path), cv2.IMREAD_COLOR)
        if spread is None:
            raise RuntimeError(f"Could not read rendered sheet {sheet}: {spread_path}")

        pages = zip(("left", "right"), split_spread(spread))
        for side, src in pages:
            stem = f"sheet-{sheet:03d}-{side}"
            ref_path = full / f"{stem}-reference.png"
            archive_path = full / f"{stem}-archival.png"
            ocr_path = full / f"{stem}-ocr.png"

            cv2.imwrite(str(ref_path), src)
            archive = archival_candidate(src)
            ocr = ocr_candidate(src)
            cv2.imwrite(str(archive_path), archive)
            cv2.imwrite(str(ocr_path), ocr)

            refs.append(ref_path)
            archives.append(archive_path)
            ocrs.append(ocr_path)
            labels.append(f"PDF {sheet} / {side}")
            records.append({
                "source_pdf_sheet": sheet,
                "spread_side": side,
                "render_dpi": args.dpi,
                "reference": str(ref_path),
                "archival_candidate": str(archive_path),
                "ocr_candidate": str(ocr_path),
                "reference_shape_px": [int(src.shape[1]), int(src.shape[0])],
                "ocr_shape_px": [int(ocr.shape[1]), int(ocr.shape[0])],
                "printed_page_hint": (
                    297 if sheet == 169 and side == "left" else
                    298 if sheet == 169 and side == "right" else
                    None
                ),
            })

    make_contact_sheet(refs, labels, preview / "contact-reference.jpg")
    make_contact_sheet(archives, labels, preview / "contact-archival.jpg")
    make_contact_sheet(ocrs, labels, preview / "contact-ocr.jpg")

    manifest = {
        "status": "pilot-only",
        "pilot_version": "0.1.1",
        "source": str(args.pdf),
        "source_sha256": "bc8dbd965d242f8dbdefb3fbc28a9e24c9d68084a536d388bf96e376b68ff72e",
        "selection_unit": "physical page cropped from an ordinary source PDF spread",
        "selected_sheets": args.sheets,
        "physical_page_samples": len(records),
        "notes": [
            "Exactly twelve physical-page samples are generated from six ordinary spreads.",
            "PDF sheet 169 includes the known printed pages 297/298 difficult-reference spread.",
            "OCR candidate remains grayscale; hard binarization from pilot v0.1.0 was rejected as the default.",
            "These center-split crops are pilot material. Canonical page extraction will later use embedded-image provenance where possible.",
            "Structurally unusual PDF sheets 1 and 297-300 remain separate extraction/provenance tests and are not counted among the twelve text-page samples.",
        ],
        "records": records,
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Do not retain intermediary rendered spreads in output artifacts.
    for path in work.glob("*.png"):
        path.unlink()
    work.rmdir()


if __name__ == "__main__":
    main()
