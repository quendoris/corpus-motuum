#!/usr/bin/env python3
"""Experimental, scale-relative figure segmentation probe for scanned book pages.

This is a research implementation, not a production extractor. It is derived
from the current figure-extraction invariants rather than from the previous
ad-hoc review renderer.

Important separation of concerns:

* graph growth establishes ownership/envelopes;
* traversal through a component does not automatically make that component
  renderable support;
* raw seed families are coalesced into assets before crops are emitted;
* every asset gets its own support/alpha mask (never a crop of the union mask);
* closure/interior paper and observed figure ink are separate mask layers;
* source-preserving and cleaned publication previews are emitted separately.

Canonical text/OCR/caption identity is never used to build a mask.
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

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
    cx: float
    cy: float
    line_support: float = 0.0
    text_score: float = 0.0
    seed_score: float = 0.0


@dataclass
class Region:
    component_labels: list[int]
    seed_labels: list[int]
    bbox: list[int]
    area: int
    extent: int
    text_area_fraction: float
    max_seed_score: float


def foreground_mask(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, float]:
    h, w = gray.shape
    sigma = max(4.0, min(h, w) / 45.0)
    paper = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma, sigmaY=sigma)
    residual = np.maximum(paper.astype(np.int16) - gray.astype(np.int16), 0).astype(np.uint8)
    nz = residual[residual > 0]
    if nz.size == 0:
        return np.zeros_like(gray), residual, paper, sigma, 0.0
    hi = max(1.0, float(np.percentile(nz, 99.7)))
    ink = np.clip(residual.astype(np.float32) * (255.0 / hi), 0, 255).astype(np.uint8)
    otsu, mask = cv2.threshold(ink, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return mask, ink, paper, sigma, float(otsu)


def connected_components(mask: np.ndarray) -> tuple[np.ndarray, list[Component]]:
    n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    comps: list[Component] = []
    for label in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[label])
        if area < 2:
            continue
        comps.append(Component(
            label=label, x=x, y=y, w=w, h=h, area=area,
            cx=float(centroids[label, 0]), cy=float(centroids[label, 1]),
        ))
    return labels, comps


def _line_support_for_candidates(cands: list[Component], radius_factor: float = 6.0) -> None:
    """Estimate bilateral horizontal support using y buckets."""
    if not cands:
        return
    median_h = float(np.median([c.h for c in cands]))
    y_step = max(3.0, median_h * 0.6)
    buckets: dict[int, list[Component]] = {}
    for c in cands:
        buckets.setdefault(int(c.cy // y_step), []).append(c)

    for c in cands:
        left = right = 0
        yb = int(c.cy // y_step)
        max_dx = radius_factor * c.h
        for by in range(yb - 2, yb + 3):
            for n in buckets.get(by, []):
                if n.label == c.label:
                    continue
                if abs(n.cy - c.cy) > max(2.0, 0.50 * c.h):
                    continue
                if abs(n.h - c.h) > max(2.0, 0.45 * c.h):
                    continue
                dx = n.cx - c.cx
                if abs(dx) > max_dx:
                    continue
                if dx < 0:
                    left += 1
                else:
                    right += 1
        c.line_support = min(1.0, min(left, right) / 2.0)


def estimate_page_scale(comps: list[Component], shape: tuple[int, int]) -> tuple[float, float, int, int]:
    h_page, w_page = shape
    dust_h = max(4, int(round(h_page * 0.0035)))
    cands = [
        c for c in comps
        if dust_h <= c.h <= 0.05 * h_page
        and c.w <= 0.07 * w_page
        and c.area >= 3
    ]
    if len(cands) < 30:
        raise RuntimeError("not enough text-scale candidates")

    _line_support_for_candidates(cands)
    max_h = max(c.h for c in cands)
    hist = np.zeros(max_h + 1, dtype=np.float64)
    for c in cands:
        hist[c.h] += 1.0 + 3.0 * c.line_support
    kernel = np.asarray([1, 2, 3, 2, 1], dtype=np.float64)
    kernel /= kernel.sum()
    smooth = np.convolve(hist, kernel, mode="same")
    mode_h = int(np.argmax(smooth[dust_h:]) + dust_h)

    band = [
        c for c in cands
        if 0.65 * mode_h <= c.h <= 1.55 * mode_h and c.line_support >= 0.25
    ]
    if len(band) < 20:
        band = [c for c in cands if 0.65 * mode_h <= c.h <= 1.55 * mode_h]
    h_cap = float(np.percentile([c.h for c in band], 97.0))
    return h_cap, float(mode_h), len(cands), len(band)


def assign_text_scores(comps: list[Component], h_cap: float) -> None:
    small = [
        c for c in comps
        if c.h <= 1.9 * h_cap
        and c.w <= 3.0 * h_cap
        and c.area <= 5.0 * h_cap * h_cap
    ]
    small_labels = {c.label for c in small}
    cell = max(6.0, 2.0 * h_cap)
    buckets: dict[tuple[int, int], list[Component]] = {}
    for c in small:
        buckets.setdefault((int(c.cx // cell), int(c.cy // cell)), []).append(c)

    for c in comps:
        if c.label not in small_labels:
            c.text_score = 0.0
            continue
        bx, by = int(c.cx // cell), int(c.cy // cell)
        left = right = loose_left = loose_right = aligned = 0
        for ix in range(bx - 4, bx + 5):
            for iy in range(by - 1, by + 2):
                for n in buckets.get((ix, iy), []):
                    if n.label == c.label:
                        continue
                    dx = n.cx - c.cx
                    if abs(dx) > 8.0 * h_cap:
                        continue
                    if abs(n.cy - c.cy) <= 0.55 * h_cap and abs(n.h - c.h) <= 0.8 * h_cap:
                        aligned += 1
                        if dx < 0:
                            left += 1
                        else:
                            right += 1
                    if abs(n.cy - c.cy) <= 0.45 * h_cap:
                        if dx < 0:
                            loose_left += 1
                        else:
                            loose_right += 1

        bilateral = min(1.0, min(left, right) / 2.0)
        loose = min(1.0, min(loose_left, loose_right) / 3.0)
        local = min(1.0, aligned / 5.0)
        h_ratio, w_ratio = c.h / h_cap, c.w / h_cap
        if 0.35 <= h_ratio <= 1.45 and w_ratio <= 2.2:
            geom = 1.0
        elif 0.22 <= h_ratio <= 1.7 and w_ratio <= 2.8:
            geom = 0.55
        else:
            geom = 0.10
        c.text_score = float(np.clip(
            0.40 * bilateral + 0.30 * loose + 0.15 * local + 0.15 * geom,
            0.0, 1.0,
        ))


def estimate_stroke_width(labels: np.ndarray, comps: list[Component], h_cap: float) -> float:
    text = [c for c in comps if 0.55 * h_cap <= c.h <= 1.35 * h_cap and c.text_score >= 0.5]
    if not text:
        return max(1.0, h_cap / 7.0)
    lut = np.zeros(int(labels.max()) + 1, dtype=np.uint8)
    for c in text:
        lut[c.label] = 255
    text_mask = lut[labels]
    dist = cv2.distanceTransform(text_mask, cv2.DIST_L2, 5)
    local_max = dist >= (cv2.dilate(dist, np.ones((3, 3), np.uint8)) - 1e-6)
    vals = dist[(local_max) & (dist > 0)]
    return float(max(1.0, 2.0 * np.median(vals))) if vals.size else max(1.0, h_cap / 7.0)


def robust_seed_scores(comps: list[Component], h_cap: float, w_stroke: float) -> tuple[list[Component], float]:
    baseline = [
        c for c in comps
        if c.text_score >= 0.55 and c.h <= 1.6 * h_cap and c.w <= 2.5 * h_cap
    ]
    if len(baseline) < 30:
        baseline = [c for c in comps if c.h <= 1.6 * h_cap and max(c.w, c.h) <= 2.5 * h_cap]
    if len(baseline) < 20:
        raise RuntimeError("not enough baseline components for seed model")

    def features(c: Component) -> np.ndarray:
        return np.asarray([
            math.log(max(c.h / h_cap, 1e-6)),
            math.log(max(max(c.w, c.h) / h_cap, 1e-6)),
            math.log(max(c.area / (h_cap * w_stroke), 1e-6)),
        ], dtype=np.float64)

    x = np.stack([features(c) for c in baseline])
    center = np.median(x, axis=0)
    scale = 1.4826 * np.median(np.abs(x - center), axis=0)
    scale = np.maximum(scale, 0.15)
    base_scores = np.max((x - center) / scale, axis=1)
    threshold = max(3.5, float(np.percentile(base_scores, 99.5)) + 0.5)

    seeds: list[Component] = []
    for c in comps:
        c.seed_score = float(np.max((features(c) - center) / scale))
        meaningful = (
            max(c.w, c.h) >= 2.2 * h_cap
            or c.area >= 7.0 * h_cap * w_stroke
            or c.h >= 1.7 * h_cap
        )
        if c.seed_score >= threshold and c.text_score < 0.72 and meaningful:
            seeds.append(c)
    return seeds, threshold


def bbox_gap(a: Component, b: Component) -> tuple[float, float, float]:
    ax1, ay1, ax2, ay2 = a.x, a.y, a.x + a.w, a.y + a.h
    bx1, by1, bx2, by2 = b.x, b.y, b.x + b.w, b.y + b.h
    dx = max(0.0, max(ax1 - bx2, bx1 - ax2))
    dy = max(0.0, max(ay1 - by2, by1 - ay2))
    return math.hypot(dx, dy), dx, dy


def _overlap_1d(a1: int, a2: int, b1: int, b2: int) -> int:
    return max(0, min(a2, b2) - max(a1, b1))


def grow_seed_families(
    comps: list[Component], seeds: list[Component], h_cap: float, w_stroke: float,
    text_penalty: float, growth_budget: float,
) -> list[Region]:
    if not seeds:
        return []
    by_label = {c.label: c for c in comps}
    cell = max(8, int(round(3.0 * h_cap)))
    buckets: dict[tuple[int, int], list[Component]] = {}
    for c in comps:
        buckets.setdefault((int(c.cx // cell), int(c.cy // cell)), []).append(c)

    def neighbors(c: Component):
        bx, by = int(c.cx // cell), int(c.cy // cell)
        for ix in range(bx - 2, bx + 3):
            for iy in range(by - 2, by + 3):
                yield from buckets.get((ix, iy), [])

    dist: dict[int, float] = {}
    owner: dict[int, int] = {}
    heap: list[tuple[float, int]] = []
    for seed_index, seed in enumerate(seeds):
        dist[seed.label] = 0.0
        owner[seed.label] = seed_index
        heapq.heappush(heap, (0.0, seed.label))

    while heap:
        cost, label = heapq.heappop(heap)
        if cost != dist.get(label):
            continue
        c = by_label[label]
        for n in neighbors(c):
            if n.label == c.label:
                continue
            gap, dx, dy = bbox_gap(c, n)
            if gap > 2.2 * h_cap:
                continue
            geometric = gap / max(w_stroke, 1.0)
            if dx == 0.0 or dy == 0.0:
                geometric *= 0.55
            artifact = 0.0
            if n.area < 0.35 * h_cap * w_stroke and max(n.w, n.h) < 1.2 * h_cap:
                artifact = 1.5
            step = 0.38 * geometric + text_penalty * n.text_score + artifact
            new_cost = cost + step
            if new_cost <= growth_budget and new_cost < dist.get(n.label, math.inf):
                dist[n.label] = new_cost
                owner[n.label] = owner[label]
                heapq.heappush(heap, (new_cost, n.label))

    groups: dict[int, list[Component]] = {}
    for label, seed_index in owner.items():
        groups.setdefault(seed_index, []).append(by_label[label])

    regions: list[Region] = []
    for seed_index, members in groups.items():
        x1 = min(c.x for c in members); y1 = min(c.y for c in members)
        x2 = max(c.x + c.w for c in members); y2 = max(c.y + c.h for c in members)
        area = sum(c.area for c in members)
        weighted_text = sum(c.area * c.text_score for c in members) / max(area, 1)
        regions.append(Region(
            component_labels=[c.label for c in members],
            seed_labels=[seeds[seed_index].label],
            bbox=[x1, y1, x2, y2],
            area=area,
            extent=max(x2 - x1, y2 - y1),
            text_area_fraction=float(weighted_text),
            max_seed_score=seeds[seed_index].seed_score,
        ))
    return regions


def coalesce_regions(regions: list[Region], comps_by_label: dict[int, Component], h_cap: float) -> list[Region]:
    current = list(regions)
    changed = True
    while changed:
        changed = False
        out: list[Region] = []
        used = [False] * len(current)
        for i, a in enumerate(current):
            if used[i]:
                continue
            used[i] = True
            merged = a
            for j, b in enumerate(current):
                if used[j]:
                    continue
                ax1, ay1, ax2, ay2 = merged.bbox
                bx1, by1, bx2, by2 = b.bbox
                x_overlap = _overlap_1d(ax1, ax2, bx1, bx2)
                y_overlap = _overlap_1d(ay1, ay2, by1, by2)
                x_ratio = x_overlap / max(1, min(ax2 - ax1, bx2 - bx1))
                y_ratio = y_overlap / max(1, min(ay2 - ay1, by2 - by1))
                dx = max(0, max(ax1 - bx2, bx1 - ax2))
                dy = max(0, max(ay1 - by2, by1 - ay2))
                should_merge = (
                    (x_overlap > 0 and y_overlap > 0)
                    or (dy <= 1.8 * h_cap and x_ratio >= 0.18)
                    or (dx <= 1.8 * h_cap and y_ratio >= 0.18)
                )
                if not should_merge:
                    continue
                labels = sorted(set(merged.component_labels + b.component_labels))
                seeds = sorted(set(merged.seed_labels + b.seed_labels))
                members = [comps_by_label[k] for k in labels]
                area = sum(c.area for c in members)
                x1 = min(c.x for c in members); y1 = min(c.y for c in members)
                x2 = max(c.x + c.w for c in members); y2 = max(c.y + c.h for c in members)
                merged = Region(
                    component_labels=labels,
                    seed_labels=seeds,
                    bbox=[x1, y1, x2, y2],
                    area=area,
                    extent=max(x2 - x1, y2 - y1),
                    text_area_fraction=sum(c.area * c.text_score for c in members) / max(area, 1),
                    max_seed_score=max(merged.max_seed_score, b.max_seed_score),
                )
                used[j] = True
                changed = True
            out.append(merged)
        current = out
    return current


def accept_regions(regions: list[Region], shape: tuple[int, int], h_cap: float, w_stroke: float) -> list[Region]:
    h_page, w_page = shape
    accepted: list[Region] = []
    for r in regions:
        x1, y1, x2, y2 = r.bbox
        rw, rh = x2 - x1, y2 - y1
        touches_edge = x1 <= 2 or y1 <= 2 or x2 >= w_page - 2 or y2 >= h_page - 2
        border_column = touches_edge and min(rw, rh) <= 3.0 * h_cap and max(rw, rh) >= 8.0 * h_cap
        extreme_sliver = min(rw, rh) <= 0.22 * h_cap and max(rw, rh) >= 4.0 * h_cap
        if border_column or (touches_edge and extreme_sliver):
            continue
        if r.text_area_fraction >= 0.52:
            continue
        if not (r.area >= 2.5 * h_cap * h_cap or r.extent >= 5.0 * h_cap):
            continue
        accepted.append(r)
    return sorted(accepted, key=lambda r: r.area, reverse=True)


def renderable_support(
    labels: np.ndarray, region: Region, comps_by_label: dict[int, Component],
    h_cap: float, w_stroke: float,
) -> np.ndarray:
    members = [comps_by_label[k] for k in region.component_labels]
    seeds = [comps_by_label[k] for k in region.seed_labels]
    primary = max(seeds, key=lambda c: c.seed_score)

    core: list[Component] = []
    candidates: list[Component] = []
    for c in members:
        large = max(c.w, c.h) >= 1.8 * h_cap or c.area >= 4.5 * h_cap * w_stroke
        strong = c.seed_score >= 3.5
        if c.label in region.seed_labels or (large and c.text_score <= 0.58) or (strong and c.text_score <= 0.48):
            core.append(c)
        if c.label in region.seed_labels or c.text_score <= 0.34 or (large and c.text_score <= 0.58) or (strong and c.text_score <= 0.48):
            candidates.append(c)
    if not core:
        core = [primary]

    sx1, sy1 = primary.x, primary.y
    sx2, sy2 = sx1 + primary.w, sy1 + primary.h
    keep: list[int] = []
    for c in candidates:
        if c in core:
            keep.append(c.label)
            continue
        gap = min(bbox_gap(c, k)[0] for k in core)
        inside_primary = c.x >= sx1 and c.y >= sy1 and c.x + c.w <= sx2 and c.y + c.h <= sy2
        strong = c.seed_score >= 3.5
        large = max(c.w, c.h) >= 1.8 * h_cap or c.area >= 4.5 * h_cap * w_stroke
        glyph_like = (
            0.55 * h_cap <= c.h <= 1.45 * h_cap
            and c.w <= 1.45 * h_cap
            and c.area <= 2.8 * h_cap * h_cap
        )
        if glyph_like and not inside_primary and not strong:
            continue
        limit = 1.30 * h_cap if (strong or large or inside_primary) else 0.60 * h_cap
        if gap <= limit:
            keep.append(c.label)

    lut = np.zeros(int(labels.max()) + 1, dtype=np.uint8)
    for label in keep:
        lut[label] = 255
    return lut[labels]


def closure_and_alpha(
    support: np.ndarray, w_stroke: float, closure_budget: float,
    margin_scale: float, alpha_scale: float,
) -> tuple[np.ndarray, np.ndarray, float, bool]:
    r_close = max(1, int(round(1.5 * w_stroke)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r_close + 1, 2 * r_close + 1))
    closed = cv2.morphologyEx(support, cv2.MORPH_CLOSE, kernel)
    bridge = cv2.bitwise_and(closed, cv2.bitwise_not(support))
    grad = cv2.morphologyEx(support, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    perimeter_px = max(1, int(np.count_nonzero(grad)))
    bridge_px = int(np.count_nonzero(bridge))
    closure_cost = bridge_px / max(1.0, perimeter_px * r_close)

    hard = support.copy()
    closure_accepted = closure_cost <= closure_budget
    if closure_accepted:
        h, w = closed.shape
        flood = closed.copy()
        ff = np.zeros((h + 2, w + 2), dtype=np.uint8)
        cv2.floodFill(flood, ff, (0, 0), 255)
        holes = cv2.bitwise_not(flood)
        hard = cv2.bitwise_or(closed, holes)

    r_margin = max(1, int(round(margin_scale * w_stroke)))
    kernel_margin = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r_margin + 1, 2 * r_margin + 1))
    hard = cv2.dilate(hard, kernel_margin)

    r_alpha = max(1.0, alpha_scale * w_stroke)
    outside = (hard == 0).astype(np.uint8)
    dist_outside = cv2.distanceTransform(outside, cv2.DIST_L2, 5)
    alpha = np.where(hard > 0, 255.0, np.clip(255.0 * (1.0 - dist_outside / r_alpha), 0.0, 255.0))
    return hard, alpha.astype(np.uint8), float(closure_cost), closure_accepted


def page_domain(gray: np.ndarray) -> tuple[str, dict]:
    stats = {
        "mean_luma": float(gray.mean()),
        "std_luma": float(gray.std()),
        "dark_fraction": float((gray < 180).mean()),
        "very_dark_fraction": float((gray < 120).mean()),
    }
    if stats["dark_fraction"] > 0.75:
        return "unsupported-dark-fulltone", stats
    return "normal-text-candidate", stats


def process_page(path: Path, out_dir: Path, args: argparse.Namespace) -> dict:
    color = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if color is None:
        raise RuntimeError(f"could not read image: {path}")
    gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
    domain, global_stats = page_domain(gray)
    payload: dict = {
        "schema": "corpus-motuum-figure-structure-probe-v1",
        "source": str(path),
        "width": int(color.shape[1]),
        "height": int(color.shape[0]),
        "page_class": domain,
        **global_stats,
        "assets": [],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = path.stem
    if domain != "normal-text-candidate":
        return payload

    fg, ink, paper, bg_sigma, otsu = foreground_mask(gray)
    labels, comps = connected_components(fg)
    h_cap, height_mode, candidate_count, text_band_count = estimate_page_scale(comps, gray.shape)
    assign_text_scores(comps, h_cap)
    w_stroke = estimate_stroke_width(labels, comps, h_cap)
    seeds, seed_threshold = robust_seed_scores(comps, h_cap, w_stroke)
    raw = grow_seed_families(
        comps, seeds, h_cap, w_stroke,
        text_penalty=args.text_penalty, growth_budget=args.growth_budget,
    )
    by_label = {c.label: c for c in comps}
    grouped = coalesce_regions(raw, by_label, h_cap)
    assets = accept_regions(grouped, gray.shape, h_cap, w_stroke)

    overlay = color.copy()
    asset_records = []
    for index, region in enumerate(assets, start=1):
        support = renderable_support(labels, region, by_label, h_cap, w_stroke)
        hard, alpha, closure_cost, closure_ok = closure_and_alpha(
            support, w_stroke,
            closure_budget=args.closure_budget,
            margin_scale=args.margin_scale,
            alpha_scale=args.alpha_scale,
        )
        ys, xs = np.where(alpha > 0)
        if xs.size == 0:
            continue
        x1, y1, x2, y2 = int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)
        cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 0, 255), 2)
        cv2.putText(overlay, str(index), (x1 + 3, max(12, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1, cv2.LINE_AA)

        crop_gray = gray[y1:y2, x1:x2]
        crop_paper = paper[y1:y2, x1:x2]
        crop_support = support[y1:y2, x1:x2]
        crop_alpha = alpha[y1:y2, x1:x2]
        source_rgba = np.dstack([crop_gray, crop_gray, crop_gray, crop_alpha])
        clean_gray = np.where(crop_support > 0, crop_gray, crop_paper).astype(np.uint8)
        clean_rgba = np.dstack([clean_gray, clean_gray, clean_gray, crop_alpha])

        prefix = f"{stem}-asset-{index:02d}"
        cv2.imwrite(str(out_dir / f"{prefix}.support.png"), crop_support)
        cv2.imwrite(str(out_dir / f"{prefix}.alpha.png"), crop_alpha)
        cv2.imwrite(str(out_dir / f"{prefix}.source.png"), source_rgba)
        cv2.imwrite(str(out_dir / f"{prefix}.clean.png"), clean_rgba)

        asset_records.append({
            "asset_index": index,
            "bbox": [x1, y1, x2, y2],
            "support_pixels": int(np.count_nonzero(crop_support)),
            "alpha_pixels": int(np.count_nonzero(crop_alpha)),
            "closure_cost": closure_cost,
            "closure_accepted": closure_ok,
            "region": asdict(region),
            "files": {
                "support": f"{prefix}.support.png",
                "alpha": f"{prefix}.alpha.png",
                "source": f"{prefix}.source.png",
                "clean": f"{prefix}.clean.png",
            },
        })

    cv2.imwrite(str(out_dir / f"{stem}.foreground.png"), fg)
    cv2.imwrite(str(out_dir / f"{stem}.ink-response.png"), ink)
    cv2.imwrite(str(out_dir / f"{stem}.asset-overlay.jpg"), overlay, [cv2.IMWRITE_JPEG_QUALITY, 95])

    payload.update({
        "background_sigma_px": bg_sigma,
        "foreground_otsu": otsu,
        "component_count": len(comps),
        "component_height_mode": height_mode,
        "scale_candidate_count": candidate_count,
        "text_band_count": text_band_count,
        "H_cap": h_cap,
        "W_stroke": w_stroke,
        "seed_threshold": seed_threshold,
        "seed_count": len(seeds),
        "raw_seed_family_count": len(raw),
        "grouped_region_count": len(grouped),
        "accepted_asset_count": len(asset_records),
        "parameters": {
            "text_penalty": args.text_penalty,
            "growth_budget": args.growth_budget,
            "closure_budget": args.closure_budget,
            "margin_scale": args.margin_scale,
            "alpha_scale": args.alpha_scale,
        },
        "assets": asset_records,
    })
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
    ap.add_argument("--out", type=Path, default=Path("work/figure-structure-probe-v1"))
    ap.add_argument("--text-penalty", type=float, default=5.0)
    ap.add_argument("--growth-budget", type=float, default=6.0)
    ap.add_argument("--closure-budget", type=float, default=0.35)
    ap.add_argument("--margin-scale", type=float, default=1.5)
    ap.add_argument("--alpha-scale", type=float, default=2.0)
    args = ap.parse_args()

    images = iter_images(args.inputs)
    if not images:
        raise SystemExit("no input images found")

    pages = []
    failures = []
    for path in images:
        try:
            payload = process_page(path, args.out, args)
            pages.append(payload)
            print(
                f"{path.name}: class={payload['page_class']} "
                f"assets={payload.get('accepted_asset_count', 0)} "
                f"H_cap={payload.get('H_cap')} W_stroke={payload.get('W_stroke')}"
            )
        except Exception as exc:
            failures.append({"source": str(path), "error": str(exc)})
            print(f"{path.name}: ERROR: {exc}")

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "index.json").write_text(
        json.dumps({"schema": "corpus-motuum-figure-structure-index-v1", "pages": pages, "failures": failures}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if failures:
        raise SystemExit(f"{len(failures)} page(s) failed; see {args.out / 'index.json'}")


if __name__ == "__main__":
    main()
