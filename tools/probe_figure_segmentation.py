#!/usr/bin/env python3
"""Measure per-page text scale and visualize strong non-text figure seeds.

This is a calibration probe, not the final extractor. It deliberately stops
before region growth/fill so seed behaviour can be validated independently.
All geometric measurements are normalized by page-derived H_cap/W_stroke.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from statistics import NormalDist

import cv2
import numpy as np


@dataclass
class Component:
    label: int
    x: int
    y: int
    w: int
    h: int
    area: int
    z_height: float = 0.0
    z_extent: float = 0.0
    z_area: float = 0.0
    seed_score: float = 0.0


def robust_center_scale(values: np.ndarray) -> tuple[float, float]:
    med = float(np.median(values))
    mad = float(np.median(np.abs(values - med)))
    sigma = 1.4826 * mad
    if not math.isfinite(sigma) or sigma < 1e-6:
        sigma = float(np.std(values))
    if not math.isfinite(sigma) or sigma < 1e-6:
        sigma = 1.0
    return med, sigma


def smooth_hist_mode(values: np.ndarray, lo: int, hi: int) -> float:
    vals = np.clip(np.rint(values).astype(int), lo, hi)
    hist = np.bincount(vals, minlength=hi + 1).astype(np.float64)
    sigma = max(1.0, (hi - lo) / 80.0)
    radius = max(2, int(round(3 * sigma)))
    xs = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-(xs * xs) / (2 * sigma * sigma))
    kernel /= kernel.sum()
    smoothed = np.convolve(hist, kernel, mode="same")
    smoothed[:lo] = 0.0
    return float(np.argmax(smoothed))


def foreground_mask(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:
    h, w = gray.shape
    sigma = max(4.0, min(h, w) / 45.0)
    background = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma, sigmaY=sigma)
    residual = np.maximum(background.astype(np.int16) - gray.astype(np.int16), 0).astype(np.uint8)

    nonzero = residual[residual > 0]
    if nonzero.size == 0:
        return np.zeros_like(gray, dtype=np.uint8), residual, sigma, 0.0
    hi = float(np.percentile(nonzero, 99.7))
    scale = 255.0 / max(hi, 1.0)
    ink8 = np.clip(residual.astype(np.float32) * scale, 0, 255).astype(np.uint8)
    otsu, mask = cv2.threshold(ink8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return mask, ink8, sigma, float(otsu)


def measure_page(mask: np.ndarray, alpha_page: float) -> tuple[list[Component], float, float, float, np.ndarray]:
    h_page, w_page = mask.shape
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

    comps: list[Component] = []
    min_h = max(2, int(round(h_page * 0.0015)))
    max_text_candidate_h = max(min_h + 1, int(round(h_page * 0.06)))
    max_text_candidate_w = max(2, int(round(w_page * 0.08)))

    mode_candidates = []
    for label in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[label])
        if area < 2:
            continue
        c = Component(label=label, x=x, y=y, w=w, h=h, area=area)
        comps.append(c)
        if min_h <= h <= max_text_candidate_h and w <= max_text_candidate_w:
            mode_candidates.append(h)

    if len(mode_candidates) < 20:
        raise RuntimeError("not enough text-like components to estimate page scale")

    heights = np.asarray(mode_candidates, dtype=np.float64)
    mode_h = smooth_hist_mode(heights, min_h, max_text_candidate_h)
    text_band = [
        c for c in comps
        if 0.55 * mode_h <= c.h <= 1.70 * mode_h
        and c.w <= 2.2 * mode_h
    ]
    if len(text_band) < 20:
        raise RuntimeError("text-height mode did not yield a stable component population")

    h_cap = float(np.percentile([c.h for c in text_band], 97.5))

    text_labels = {c.label for c in text_band}
    text_mask = np.zeros_like(mask)
    if text_labels:
        lut = np.zeros(n, dtype=np.uint8)
        for label in text_labels:
            lut[label] = 255
        text_mask = lut[labels]

    dist = cv2.distanceTransform(text_mask, cv2.DIST_L2, 5)
    local_max = dist >= (cv2.dilate(dist, np.ones((3, 3), np.uint8)) - 1e-6)
    maxima = dist[(local_max) & (dist > 0.0)]
    if maxima.size:
        w_stroke = float(2.0 * np.median(maxima))
    else:
        w_stroke = max(1.0, h_cap / 7.0)
    w_stroke = max(w_stroke, 1.0)

    baseline = [
        c for c in comps
        if c.h <= 1.75 * h_cap
        and max(c.w, c.h) <= 2.5 * h_cap
        and c.area <= 3.5 * h_cap * h_cap
    ]
    if len(baseline) < 20:
        baseline = text_band

    def features(c: Component) -> tuple[float, float, float]:
        return (
            math.log(max(c.h / h_cap, 1e-6)),
            math.log(max(max(c.w, c.h) / h_cap, 1e-6)),
            math.log(max(c.area / (h_cap * w_stroke), 1e-6)),
        )

    base_features = np.asarray([features(c) for c in baseline], dtype=np.float64)
    centers = []
    scales = []
    for j in range(3):
        center, scale = robust_center_scale(base_features[:, j])
        centers.append(center)
        scales.append(scale)

    tests = max(1, 3 * len(comps))
    tail = min(0.25, max(1e-12, alpha_page / tests))
    z_threshold = float(NormalDist().inv_cdf(1.0 - tail))

    seed_mask = np.zeros_like(mask)
    for c in comps:
        f = features(c)
        zs = [(f[j] - centers[j]) / scales[j] for j in range(3)]
        c.z_height, c.z_extent, c.z_area = (float(z) for z in zs)
        c.seed_score = max(c.z_height, c.z_extent, c.z_area)
        if c.seed_score >= z_threshold:
            seed_mask[labels == c.label] = 255

    return comps, h_cap, w_stroke, z_threshold, seed_mask


def process(path: Path, out_dir: Path, alpha_page: float) -> dict:
    color = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if color is None:
        raise RuntimeError(f"could not read image: {path}")
    gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
    mask, ink8, bg_sigma, otsu = foreground_mask(gray)
    comps, h_cap, w_stroke, z_threshold, seed_mask = measure_page(mask, alpha_page)

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = path.stem
    cv2.imwrite(str(out_dir / f"{stem}.foreground.png"), mask)
    cv2.imwrite(str(out_dir / f"{stem}.ink-response.png"), ink8)
    cv2.imwrite(str(out_dir / f"{stem}.seeds.png"), seed_mask)

    overlay = color.copy()
    seed_idx = seed_mask > 0
    overlay[seed_idx] = (0.35 * overlay[seed_idx] + 0.65 * np.array([0, 0, 255])).astype(np.uint8)
    cv2.imwrite(str(out_dir / f"{stem}.seed-overlay.jpg"), overlay, [cv2.IMWRITE_JPEG_QUALITY, 95])

    seeds = [c for c in comps if c.seed_score >= z_threshold]
    payload = {
        "schema": "corpus-motuum-figure-seed-probe-v0",
        "source": str(path),
        "width": int(color.shape[1]),
        "height": int(color.shape[0]),
        "background_sigma_px": bg_sigma,
        "foreground_otsu": otsu,
        "component_count": len(comps),
        "H_cap": h_cap,
        "W_stroke": w_stroke,
        "seed_false_positive_budget_per_page": alpha_page,
        "seed_z_threshold": z_threshold,
        "seed_count": len(seeds),
        "seeds": [asdict(c) for c in sorted(seeds, key=lambda x: x.seed_score, reverse=True)],
    }
    (out_dir / f"{stem}.metrics.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def iter_images(inputs: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for p in inputs:
        if p.is_dir():
            for suffix in ("*.jpg", "*.jpeg", "*.png", "*.tif", "*.tiff"):
                paths.extend(p.glob(suffix))
        else:
            paths.append(p)
    return sorted(set(paths))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("work/figure-seed-probe-v0"))
    ap.add_argument(
        "--seed-fp-budget",
        type=float,
        default=0.01,
        help="family-wise false-seed budget per page under the provisional text-tail model",
    )
    args = ap.parse_args()
    if not (0.0 < args.seed_fp_budget < 1.0):
        raise SystemExit("--seed-fp-budget must be between 0 and 1")

    images = iter_images(args.inputs)
    if not images:
        raise SystemExit("no input images found")

    index = []
    failures = []
    for path in images:
        try:
            payload = process(path, args.out, args.seed_fp_budget)
            index.append(payload)
            print(
                f"{path.name}: H_cap={payload['H_cap']:.2f} "
                f"W_stroke={payload['W_stroke']:.2f} seeds={payload['seed_count']}"
            )
        except Exception as exc:
            failures.append({"source": str(path), "error": str(exc)})
            print(f"{path.name}: ERROR: {exc}")

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "index.json").write_text(
        json.dumps({"pages": index, "failures": failures}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if failures:
        raise SystemExit(f"{len(failures)} page(s) failed; see {args.out / 'index.json'}")


if __name__ == "__main__":
    main()
