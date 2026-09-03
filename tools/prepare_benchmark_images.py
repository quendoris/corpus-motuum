#!/usr/bin/env python3
"""Generate deterministic OCR-preprocessing variants for exact benchmark pages."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def percentile_stretch(gray: np.ndarray, low_p=0.4, high_p=99.7) -> np.ndarray:
    lo, hi = np.percentile(gray, (low_p, high_p))
    if hi <= lo:
        return gray.copy()
    x = (gray.astype(np.float32) - lo) * (255.0 / (hi - lo))
    return np.clip(x, 0, 255).astype(np.uint8)


def local_gray(gray: np.ndarray) -> np.ndarray:
    background = cv2.GaussianBlur(gray, (0, 0), 31)
    normalized = cv2.divide(gray, background, scale=255)
    normalized = percentile_stretch(normalized, 0.2, 99.8)
    clahe = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(10, 10))
    normalized = clahe.apply(normalized)
    blur = cv2.GaussianBlur(normalized, (0, 0), 0.7)
    return cv2.addWeighted(normalized, 1.15, blur, -0.15, 0)


def generated_variants(src: np.ndarray) -> dict[str, np.ndarray]:
    gray = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)
    local = local_gray(gray)
    upscaled = cv2.resize(local, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    _, otsu = cv2.threshold(upscaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    adaptive = cv2.adaptiveThreshold(
        upscaled, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 51, 13
    )
    return {
        "raw-gray": gray,
        "local-gray": local,
        "local-gray-150": upscaled,
        "otsu-150": otsu,
        "adaptive-150": adaptive,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exact", type=Path, default=Path("work/benchmark-v1/exact"))
    ap.add_argument("--out", type=Path, default=Path("work/benchmark-v1/inputs"))
    args = ap.parse_args()

    exact_manifest = json.loads((args.exact / "manifest.json").read_text(encoding="utf-8"))
    records = []
    for rec in exact_manifest["records"]:
        src_path = Path(rec["path"])
        src = cv2.imread(str(src_path), cv2.IMREAD_COLOR)
        if src is None:
            raise SystemExit(f"cannot read {src_path}")

        records.append({
            "sample_id": rec["id"],
            "preset": "exact-jpeg",
            "path": str(src_path),
            "shape_px": [int(src.shape[1]), int(src.shape[0])],
        })

        for preset, image in generated_variants(src).items():
            d = args.out / preset
            d.mkdir(parents=True, exist_ok=True)
            out = d / f"{rec['id']}.png"
            if not cv2.imwrite(str(out), image):
                raise SystemExit(f"failed to write {out}")
            records.append({
                "sample_id": rec["id"],
                "preset": preset,
                "path": str(out),
                "shape_px": [int(image.shape[1]), int(image.shape[0])],
            })

    presets = ["exact-jpeg","raw-gray","local-gray","local-gray-150","otsu-150","adaptive-150"]
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "manifest.json").write_text(
        json.dumps({
            "exact_manifest": str(args.exact / "manifest.json"),
            "presets": presets,
            "records": records,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Prepared {len(records)} OCR inputs ({len(presets)} presets) in {args.out}")


if __name__ == "__main__":
    main()
