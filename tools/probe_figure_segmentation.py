#!/usr/bin/env python3
"""Architecture-first figure extraction probe for historical scanned pages.

The probe intentionally separates five concepts that previous experiments mixed:

1. component evidence: observations from the page;
2. ownership: bounded graph reachability from strong figure seeds;
3. asset identity: grouping of seed families into output illustrations;
4. renderable support: observed ink allowed to appear in the derivative;
5. territory: opaque paper enclosed by the illustration.

The spatial index is an optimisation only. Every candidate edge is rechecked with
the declared bbox-gap relation, and bbox coverage (not centroid placement) is
indexed so long rails/ropes cannot disappear merely because their centroids are
far apart.

This remains a research tool. Thresholds are dimensionless/scale-relative but
must still be calibrated on manual ground truth before any production claim.
"""
from __future__ import annotations
import argparse
import hashlib
import heapq
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable
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

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

@dataclass
class Region:
    component_labels: list[int]
    seed_labels: list[int]
    bbox: list[int]
    area: int
    extent: int
    text_area_fraction: float
    max_seed_score: float
    grouping_evidence: list[dict] = field(default_factory=list)

@dataclass(frozen=True)
class Edge:
    a: int
    b: int
    gap: float
    dx: float
    dy: float
    x_overlap_ratio: float
    y_overlap_ratio: float

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def checked_imwrite(path: Path, image: np.ndarray, params: list[int] | None=None) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(path), image, [] if params is None else params)
    if not ok or not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f'failed to write image: {path}')
    return sha256_file(path)

def foreground_mask(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, float]:
    h, w = gray.shape
    sigma = max(4.0, min(h, w) / 45.0)
    paper = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma, sigmaY=sigma)
    residual = np.maximum(paper.astype(np.int16) - gray.astype(np.int16), 0).astype(np.uint8)
    nz = residual[residual > 0]
    if nz.size == 0:
        return (np.zeros_like(gray), residual, paper, sigma, 0.0)
    hi = max(1.0, float(np.percentile(nz, 99.7)))
    ink = np.clip(residual.astype(np.float32) * (255.0 / hi), 0, 255).astype(np.uint8)
    otsu, mask = cv2.threshold(ink, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return (mask, ink, paper, sigma, float(otsu))

def connected_components(mask: np.ndarray) -> tuple[np.ndarray, list[Component]]:
    n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    comps: list[Component] = []
    for label in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[label])
        if area < 2:
            continue
        comps.append(Component(label=label, x=x, y=y, w=w, h=h, area=area, cx=float(centroids[label, 0]), cy=float(centroids[label, 1])))
    return (labels, comps)

def _line_support_for_candidates(cands: list[Component], radius_factor: float=6.0) -> None:
    """Estimate row support without requiring a glyph to have neighbours on both sides."""
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
                if abs(n.cy - c.cy) > max(2.0, 0.5 * c.h):
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
        one_sided = min(1.0, max(left, right) / 3.0)
        bilateral = min(1.0, min(left, right) / 2.0)
        c.line_support = min(1.0, 0.65 * one_sided + 0.35 * bilateral)

def estimate_page_scale(comps: list[Component], shape: tuple[int, int]) -> tuple[float, float, int, int]:
    h_page, w_page = shape
    dust_h = max(4, int(round(h_page * 0.0035)))
    cands = [c for c in comps if dust_h <= c.h <= 0.05 * h_page and c.w <= 0.07 * w_page and (c.area >= 3)]
    if len(cands) < 30:
        raise RuntimeError('not enough text-scale candidates')
    _line_support_for_candidates(cands)
    max_h = max((c.h for c in cands))
    hist = np.zeros(max_h + 1, dtype=np.float64)
    for c in cands:
        hist[c.h] += 1.0 + 3.0 * c.line_support
    kernel = np.asarray([1, 2, 3, 2, 1], dtype=np.float64)
    kernel /= kernel.sum()
    smooth = np.convolve(hist, kernel, mode='same')
    mode_h = int(np.argmax(smooth[dust_h:]) + dust_h)
    band = [c for c in cands if 0.65 * mode_h <= c.h <= 1.55 * mode_h and c.line_support >= 0.22]
    if len(band) < 20:
        band = [c for c in cands if 0.65 * mode_h <= c.h <= 1.55 * mode_h]
    h_cap = float(np.percentile([c.h for c in band], 97.0))
    return (h_cap, float(mode_h), len(cands), len(band))

def assign_text_scores(comps: list[Component], h_cap: float) -> None:
    """Score text-likeness while treating endpoints of text rows as text too."""
    small = [c for c in comps if c.h <= 1.9 * h_cap and c.w <= 3.0 * h_cap and (c.area <= 5.0 * h_cap * h_cap)]
    small_labels = {c.label for c in small}
    cell = max(6.0, 2.0 * h_cap)
    buckets: dict[tuple[int, int], list[Component]] = {}
    for c in small:
        buckets.setdefault((int(c.cx // cell), int(c.cy // cell)), []).append(c)
    for c in comps:
        if c.label not in small_labels:
            c.text_score = 0.0
            continue
        bx, by = (int(c.cx // cell), int(c.cy // cell))
        left = right = loose_left = loose_right = aligned = 0
        for ix in range(bx - 4, bx + 5):
            for iy in range(by - 1, by + 2):
                for n in buckets.get((ix, iy), []):
                    if n.label == c.label:
                        continue
                    dx = n.cx - c.cx
                    if abs(dx) > 8.0 * h_cap:
                        continue
                    same_height = abs(n.h - c.h) <= 0.8 * h_cap
                    same_row = abs(n.cy - c.cy) <= 0.55 * h_cap
                    if same_row and same_height:
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
        one_sided = min(1.0, max(left, right) / 3.0)
        loose_one_sided = min(1.0, max(loose_left, loose_right) / 4.0)
        local = min(1.0, aligned / 5.0)
        h_ratio, w_ratio = (c.h / h_cap, c.w / h_cap)
        if 0.35 <= h_ratio <= 1.45 and w_ratio <= 2.2:
            geom = 1.0
        elif 0.22 <= h_ratio <= 1.7 and w_ratio <= 2.8:
            geom = 0.55
        else:
            geom = 0.1
        c.text_score = float(np.clip(0.28 * bilateral + 0.3 * one_sided + 0.17 * loose_one_sided + 0.1 * local + 0.15 * geom, 0.0, 1.0))

def estimate_stroke_width(labels: np.ndarray, comps: list[Component], h_cap: float) -> float:
    text = [c for c in comps if 0.55 * h_cap <= c.h <= 1.35 * h_cap and c.text_score >= 0.5]
    if not text:
        return max(1.0, h_cap / 7.0)
    lut = np.zeros(int(labels.max()) + 1, dtype=np.uint8)
    for c in text:
        lut[c.label] = 255
    text_mask = lut[labels]
    dist = cv2.distanceTransform(text_mask, cv2.DIST_L2, 5)
    local_max = dist >= cv2.dilate(dist, np.ones((3, 3), np.uint8)) - 1e-06
    vals = dist[local_max & (dist > 0)]
    return float(max(1.0, 2.0 * np.median(vals))) if vals.size else max(1.0, h_cap / 7.0)

def robust_seed_scores(comps: list[Component], h_cap: float, w_stroke: float) -> tuple[list[Component], float]:
    baseline = [c for c in comps if c.text_score >= 0.55 and c.h <= 1.6 * h_cap and (c.w <= 2.5 * h_cap)]
    if len(baseline) < 30:
        baseline = [c for c in comps if c.h <= 1.6 * h_cap and max(c.w, c.h) <= 2.5 * h_cap]
    if len(baseline) < 20:
        raise RuntimeError('not enough baseline components for seed model')

    def features(c: Component) -> np.ndarray:
        return np.asarray([math.log(max(c.h / h_cap, 1e-06)), math.log(max(max(c.w, c.h) / h_cap, 1e-06)), math.log(max(c.area / (h_cap * w_stroke), 1e-06))], dtype=np.float64)
    x = np.stack([features(c) for c in baseline])
    center = np.median(x, axis=0)
    scale = 1.4826 * np.median(np.abs(x - center), axis=0)
    scale = np.maximum(scale, 0.15)
    base_scores = np.max((x - center) / scale, axis=1)
    threshold = max(3.5, float(np.percentile(base_scores, 99.5)) + 0.5)
    seeds: list[Component] = []
    for c in comps:
        c.seed_score = float(np.max((features(c) - center) / scale))
        meaningful = max(c.w, c.h) >= 2.2 * h_cap or c.area >= 7.0 * h_cap * w_stroke or c.h >= 1.7 * h_cap
        if c.seed_score >= threshold and c.text_score < 0.72 and meaningful:
            seeds.append(c)
    return (seeds, threshold)

def _overlap_1d(a1: int, a2: int, b1: int, b2: int) -> int:
    return max(0, min(a2, b2) - max(a1, b1))

def bbox_gap(a: Component, b: Component) -> tuple[float, float, float]:
    dx = max(0.0, max(a.x - b.x2, b.x - a.x2))
    dy = max(0.0, max(a.y - b.y2, b.y - a.y2))
    return (math.hypot(dx, dy), dx, dy)

def bbox_overlap_ratios(a: Component, b: Component) -> tuple[float, float]:
    xo = _overlap_1d(a.x, a.x2, b.x, b.x2)
    yo = _overlap_1d(a.y, a.y2, b.y, b.y2)
    return (xo / max(1, min(a.w, b.w)), yo / max(1, min(a.h, b.h)))

def _cells_for_box(x1: float, y1: float, x2: float, y2: float, cell: float) -> Iterable[tuple[int, int]]:
    ix1, iy1 = (int(math.floor(x1 / cell)), int(math.floor(y1 / cell)))
    ix2, iy2 = (int(math.floor(max(x1, x2 - 1e-06) / cell)), int(math.floor(max(y1, y2 - 1e-06) / cell)))
    for ix in range(ix1, ix2 + 1):
        for iy in range(iy1, iy2 + 1):
            yield (ix, iy)

def build_bbox_neighbor_graph(comps: list[Component], max_gap: float) -> dict[int, list[Edge]]:
    """Build an exact bbox-gap graph using an expanded-box grid only as a candidate index."""
    if not comps:
        return {}
    cell = max(8.0, max_gap)
    buckets: dict[tuple[int, int], set[int]] = {}
    by_label = {c.label: c for c in comps}
    for c in comps:
        for key in _cells_for_box(c.x - max_gap, c.y - max_gap, c.x2 + max_gap, c.y2 + max_gap, cell):
            buckets.setdefault(key, set()).add(c.label)
    pairs: set[tuple[int, int]] = set()
    for c in comps:
        candidates: set[int] = set()
        for key in _cells_for_box(c.x, c.y, c.x2, c.y2, cell):
            candidates.update(buckets.get(key, ()))
        for other in candidates:
            if other == c.label:
                continue
            a, b = sorted((c.label, other))
            pairs.add((a, b))
    graph: dict[int, list[Edge]] = {c.label: [] for c in comps}
    for a_label, b_label in pairs:
        a, b = (by_label[a_label], by_label[b_label])
        gap, dx, dy = bbox_gap(a, b)
        if gap > max_gap:
            continue
        xr, yr = bbox_overlap_ratios(a, b)
        edge = Edge(a_label, b_label, gap, dx, dy, xr, yr)
        graph[a_label].append(edge)
        graph[b_label].append(edge)
    return graph

def _edge_other(edge: Edge, label: int) -> int:
    return edge.b if edge.a == label else edge.a

def growth_edge_cost(current: Component, nxt: Component, edge: Edge, h_cap: float, w_stroke: float, text_penalty: float) -> float:
    geometric = edge.gap / max(w_stroke, 1.0)
    axis_continuity = max(edge.x_overlap_ratio, edge.y_overlap_ratio)
    if edge.dx == 0.0 or edge.dy == 0.0:
        geometric *= 0.62
    geometric *= 1.0 - 0.3 * min(axis_continuity, 1.0)
    artifact = 0.0
    if nxt.area < 0.35 * h_cap * w_stroke and max(nxt.w, nxt.h) < 1.2 * h_cap:
        artifact = 1.25
    return 0.38 * geometric + text_penalty * nxt.text_score + artifact

def grow_seed_families(comps: list[Component], seeds: list[Component], graph: dict[int, list[Edge]], h_cap: float, w_stroke: float, text_penalty: float, growth_budget: float) -> list[Region]:
    if not seeds:
        return []
    by_label = {c.label: c for c in comps}
    dist: dict[int, float] = {}
    owner: dict[int, int] = {}
    heap: list[tuple[float, int, int]] = []
    for seed_index, seed in enumerate(seeds):
        prior = dist.get(seed.label, math.inf)
        if 0.0 < prior:
            dist[seed.label] = 0.0
            owner[seed.label] = seed_index
            heapq.heappush(heap, (0.0, seed_index, seed.label))
    while heap:
        cost, seed_index, label = heapq.heappop(heap)
        if cost != dist.get(label) or owner.get(label) != seed_index:
            continue
        current = by_label[label]
        for edge in graph.get(label, ()):
            other = _edge_other(edge, label)
            nxt = by_label[other]
            step = growth_edge_cost(current, nxt, edge, h_cap, w_stroke, text_penalty)
            new_cost = cost + step
            old_cost = dist.get(other, math.inf)
            old_owner = owner.get(other, math.inf)
            if new_cost <= growth_budget and (new_cost < old_cost - 1e-09 or (abs(new_cost - old_cost) <= 1e-09 and seed_index < old_owner)):
                dist[other] = new_cost
                owner[other] = seed_index
                heapq.heappush(heap, (new_cost, seed_index, other))
    groups: dict[int, list[Component]] = {}
    for label, seed_index in owner.items():
        groups.setdefault(seed_index, []).append(by_label[label])
    regions: list[Region] = []
    for seed_index, members in groups.items():
        x1 = min((c.x for c in members))
        y1 = min((c.y for c in members))
        x2 = max((c.x2 for c in members))
        y2 = max((c.y2 for c in members))
        area = sum((c.area for c in members))
        weighted_text = sum((c.area * c.text_score for c in members)) / max(area, 1)
        regions.append(Region(component_labels=sorted((c.label for c in members)), seed_labels=[seeds[seed_index].label], bbox=[x1, y1, x2, y2], area=area, extent=max(x2 - x1, y2 - y1), text_area_fraction=float(weighted_text), max_seed_score=seeds[seed_index].seed_score))
    return regions

def _region_pair_evidence(a: Region, b: Region, by_label: dict[int, Component], graph: dict[int, list[Edge]], h_cap: float) -> dict:
    b_labels = set(b.component_labels)
    cross: list[Edge] = []
    seen: set[tuple[int, int]] = set()
    for label in a.component_labels:
        for edge in graph.get(label, ()):
            other = _edge_other(edge, label)
            if other not in b_labels:
                continue
            key = tuple(sorted((edge.a, edge.b)))
            if key in seen:
                continue
            seen.add(key)
            cross.append(edge)
    if cross:
        min_gap = min((e.gap for e in cross))
        best_axis_overlap = max((max(e.x_overlap_ratio, e.y_overlap_ratio) for e in cross))
        close_edges = sum((1 for e in cross if e.gap <= 0.75 * h_cap))
    else:
        min_gap = math.inf
        best_axis_overlap = 0.0
        close_edges = 0
    area_ratio = min(a.area, b.area) / max(a.area, b.area, 1)
    ax1, ay1, ax2, ay2 = a.bbox
    bx1, by1, bx2, by2 = b.bbox
    bbox_x_overlap = _overlap_1d(ax1, ax2, bx1, bx2) / max(1, min(ax2 - ax1, bx2 - bx1))
    bbox_y_overlap = _overlap_1d(ay1, ay2, by1, by2) / max(1, min(ay2 - ay1, by2 - by1))
    return {'min_support_gap': float(min_gap), 'best_axis_overlap': float(best_axis_overlap), 'close_cross_edges': int(close_edges), 'cross_edge_count': len(cross), 'area_ratio': float(area_ratio), 'bbox_x_overlap': float(bbox_x_overlap), 'bbox_y_overlap': float(bbox_y_overlap)}

def _should_group(ev: dict, h_cap: float) -> bool:
    """Conservative grouping: actual support proximity is required; bbox overlap alone is never sufficient."""
    gap = ev['min_support_gap']
    if not math.isfinite(gap):
        return False
    area_ratio = ev['area_ratio']
    axis = ev['best_axis_overlap']
    close_edges = ev['close_cross_edges']
    if area_ratio <= 0.3 and gap <= 1.35 * h_cap and (axis >= 0.08 or close_edges >= 1):
        return True
    if gap <= 0.35 * h_cap and (axis >= 0.18 or close_edges >= 2):
        return True
    if close_edges >= 3 and gap <= 0.75 * h_cap:
        return True
    return False

def _merge_regions(a: Region, b: Region, by_label: dict[int, Component], evidence: dict) -> Region:
    labels = sorted(set(a.component_labels + b.component_labels))
    seeds = sorted(set(a.seed_labels + b.seed_labels))
    members = [by_label[k] for k in labels]
    area = sum((c.area for c in members))
    x1 = min((c.x for c in members))
    y1 = min((c.y for c in members))
    x2 = max((c.x2 for c in members))
    y2 = max((c.y2 for c in members))
    return Region(component_labels=labels, seed_labels=seeds, bbox=[x1, y1, x2, y2], area=area, extent=max(x2 - x1, y2 - y1), text_area_fraction=sum((c.area * c.text_score for c in members)) / max(area, 1), max_seed_score=max(a.max_seed_score, b.max_seed_score), grouping_evidence=a.grouping_evidence + b.grouping_evidence + [evidence])

def coalesce_regions(regions: list[Region], by_label: dict[int, Component], graph: dict[int, list[Edge]], h_cap: float) -> list[Region]:
    current = list(regions)
    while True:
        best: tuple[float, int, int, dict] | None = None
        for i in range(len(current)):
            for j in range(i + 1, len(current)):
                ev = _region_pair_evidence(current[i], current[j], by_label, graph, h_cap)
                if not _should_group(ev, h_cap):
                    continue
                score = ev['min_support_gap'] / max(h_cap, 1.0) - 0.1 * ev['close_cross_edges']
                if best is None or score < best[0]:
                    best = (score, i, j, ev)
        if best is None:
            break
        _, i, j, ev = best
        merged = _merge_regions(current[i], current[j], by_label, ev)
        current = [r for k, r in enumerate(current) if k not in (i, j)] + [merged]
    return current

def accept_regions(regions: list[Region], shape: tuple[int, int], h_cap: float) -> list[Region]:
    h_page, w_page = shape
    accepted: list[Region] = []
    for r in regions:
        x1, y1, x2, y2 = r.bbox
        rw, rh = (x2 - x1, y2 - y1)
        touches_edge = x1 <= 2 or y1 <= 2 or x2 >= w_page - 2 or (y2 >= h_page - 2)
        border_column = touches_edge and min(rw, rh) <= 3.0 * h_cap and (max(rw, rh) >= 8.0 * h_cap)
        extreme_sliver = min(rw, rh) <= 0.22 * h_cap and max(rw, rh) >= 4.0 * h_cap
        if border_column or (touches_edge and extreme_sliver):
            continue
        if r.text_area_fraction >= 0.52:
            continue
        if not (r.area >= 2.5 * h_cap * h_cap or r.extent >= 5.0 * h_cap):
            continue
        accepted.append(r)
    return sorted(accepted, key=lambda r: (r.bbox[1], r.bbox[0]))

def renderable_support(labels: np.ndarray, region: Region, by_label: dict[int, Component], graph: dict[int, list[Edge]], h_cap: float, w_stroke: float, support_budget: float, support_text_penalty: float) -> tuple[np.ndarray, dict]:
    """Recover support via a second bounded graph distinct from ownership growth."""
    members = set(region.component_labels)
    seeds = [by_label[k] for k in region.seed_labels if k in by_label]
    if not seeds:
        raise RuntimeError('region has no valid seed component')
    core: set[int] = set()
    for label in members:
        c = by_label[label]
        large = max(c.w, c.h) >= 1.8 * h_cap or c.area >= 4.5 * h_cap * w_stroke
        strong = c.seed_score >= 3.5
        if label in region.seed_labels or (large and c.text_score <= 0.58) or (strong and c.text_score <= 0.48):
            core.add(label)
    if not core:
        core.add(max(seeds, key=lambda c: c.seed_score).label)
    dist: dict[int, float] = {}
    heap: list[tuple[float, int]] = []
    for label in core:
        dist[label] = 0.0
        heapq.heappush(heap, (0.0, label))
    while heap:
        cost, label = heapq.heappop(heap)
        if cost != dist.get(label):
            continue
        for edge in graph.get(label, ()):
            other = _edge_other(edge, label)
            if other not in members:
                continue
            c = by_label[other]
            geometric = edge.gap / max(w_stroke, 1.0)
            axis = max(edge.x_overlap_ratio, edge.y_overlap_ratio)
            geometric *= 1.0 - 0.35 * min(axis, 1.0)
            text_cost = support_text_penalty * c.text_score
            tiny_cost = 0.6 if c.area < 0.25 * h_cap * w_stroke and max(c.w, c.h) < h_cap else 0.0
            step = 0.42 * geometric + text_cost + tiny_cost
            new = cost + step
            if new <= support_budget and new < dist.get(other, math.inf):
                dist[other] = new
                heapq.heappush(heap, (new, other))
    keep: list[int] = []
    rejected_text: list[int] = []
    for label, cost in dist.items():
        c = by_label[label]
        large = max(c.w, c.h) >= 1.8 * h_cap or c.area >= 4.5 * h_cap * w_stroke
        strong = c.seed_score >= 3.5
        glyph_like = 0.5 * h_cap <= c.h <= 1.5 * h_cap and c.w <= 1.55 * h_cap and (c.area <= 2.8 * h_cap * h_cap)
        if label not in core and glyph_like and (c.text_score >= 0.48) and (not strong):
            rejected_text.append(label)
            continue
        if label in core or c.text_score <= 0.42 or large or strong:
            keep.append(label)
    lut = np.zeros(int(labels.max()) + 1, dtype=np.uint8)
    for label in keep:
        lut[label] = 255
    support = lut[labels]
    return (support, {'core_labels': sorted(core), 'reachable_labels': sorted(dist), 'support_labels': sorted(keep), 'rejected_text_labels': sorted(rejected_text)})

def _holes(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    padded = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    flood = padded.copy()
    ff = np.zeros((h + 4, w + 4), dtype=np.uint8)
    cv2.floodFill(flood, ff, (0, 0), 255)
    outside = flood[1:-1, 1:-1]
    return cv2.bitwise_not(outside)

def closure_and_alpha(support: np.ndarray, w_stroke: float, closure_budget: float, closure_boundary_min: float, max_bridge_width: float, margin_scale: float, alpha_scale: float) -> tuple[np.ndarray, np.ndarray, dict]:
    """Choose closure from a small scale family using topology diagnostics."""
    if np.count_nonzero(support) == 0:
        raise RuntimeError('cannot build territory from empty support')
    grad = cv2.morphologyEx(support, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    perimeter_px = max(1, int(np.count_nonzero(grad)))
    trials: list[dict] = []
    best_payload: tuple[np.ndarray, np.ndarray, dict] | None = None
    for scale in (0.75, 1.25, 1.75, 2.5):
        r_close = max(1, int(round(scale * w_stroke)))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r_close + 1, 2 * r_close + 1))
        closed = cv2.morphologyEx(support, cv2.MORPH_CLOSE, kernel)
        bridge = cv2.bitwise_and(closed, cv2.bitwise_not(support))
        holes = _holes(closed)
        bridge_px = int(np.count_nonzero(bridge))
        hole_px = int(np.count_nonzero(holes))
        closure_cost = bridge_px / max(1.0, perimeter_px * r_close)
        if bridge_px:
            bridge_dist = cv2.distanceTransform((bridge > 0).astype(np.uint8), cv2.DIST_L2, 5)
            max_bridge = float(2.0 * bridge_dist.max() / max(w_stroke, 1.0))
        else:
            max_bridge = 0.0
        if hole_px:
            boundary = cv2.morphologyEx(holes, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
            support_near = cv2.dilate(support, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * max(1, int(round(w_stroke))) + 1, 2 * max(1, int(round(w_stroke))) + 1)))
            boundary_px = max(1, int(np.count_nonzero(boundary)))
            boundary_support = float(np.count_nonzero(cv2.bitwise_and(boundary, support_near))) / boundary_px
        else:
            boundary_support = 0.0
        accepted = hole_px > 0 and closure_cost <= closure_budget and (boundary_support >= closure_boundary_min) and (max_bridge <= max_bridge_width)
        trial = {'scale': scale, 'r_close_px': r_close, 'bridge_pixels': bridge_px, 'hole_pixels': hole_px, 'closure_cost': float(closure_cost), 'boundary_support': float(boundary_support), 'max_bridge_width_wstroke': float(max_bridge), 'accepted': bool(accepted)}
        trials.append(trial)
        if accepted:
            territory = cv2.bitwise_or(closed, holes)
            rank = (closure_cost, -boundary_support, max_bridge, scale)
            if best_payload is None or rank < best_payload[2]['rank']:
                best_payload = (territory, bridge, {'rank': rank, **trial})
    if best_payload is None:
        territory = support.copy()
        chosen = None
    else:
        territory = best_payload[0]
        chosen = {k: v for k, v in best_payload[2].items() if k != 'rank'}
    r_margin = max(1, int(round(margin_scale * w_stroke)))
    kernel_margin = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r_margin + 1, 2 * r_margin + 1))
    hard = cv2.dilate(territory, kernel_margin)
    r_alpha = max(1.0, alpha_scale * w_stroke)
    outside = (hard == 0).astype(np.uint8)
    dist_outside = cv2.distanceTransform(outside, cv2.DIST_L2, 5)
    alpha = np.where(hard > 0, 255.0, np.clip(255.0 * (1.0 - dist_outside / r_alpha), 0.0, 255.0)).astype(np.uint8)
    return (hard, alpha, {'closure_accepted': chosen is not None, 'chosen': chosen, 'trials': trials, 'hard_pixels': int(np.count_nonzero(hard))})

def clean_publication_gray(gray: np.ndarray, paper: np.ndarray, support: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Flatten paper to white while preserving local ink contrast only on support."""
    residual = np.maximum(paper.astype(np.int16) - gray.astype(np.int16), 0).astype(np.float32)
    support_vals = residual[support > 0]
    if support_vals.size:
        hi = max(8.0, float(np.percentile(support_vals, 99.5)))
    else:
        hi = 32.0
    normalized_ink = np.clip(residual * (220.0 / hi), 0.0, 235.0)
    clean = np.full_like(gray, 255, dtype=np.uint8)
    ink_gray = np.clip(255.0 - normalized_ink, 0.0, 255.0).astype(np.uint8)
    clean[support > 0] = ink_gray[support > 0]
    clean[alpha == 0] = 255
    return clean

def page_domain(gray: np.ndarray) -> tuple[str, dict]:
    stats = {'mean_luma': float(gray.mean()), 'std_luma': float(gray.std()), 'dark_fraction': float((gray < 180).mean()), 'very_dark_fraction': float((gray < 120).mean()), 'near_white_fraction': float((gray > 245).mean())}
    if stats['dark_fraction'] > 0.75:
        return ('unsupported-dark-fulltone', stats)
    if stats['std_luma'] < 4.0 and stats['near_white_fraction'] > 0.98:
        return ('unsupported-near-blank', stats)
    return ('normal-text-candidate', stats)

def validate_asset_geometry(source_shape: tuple[int, int], support: np.ndarray, hard: np.ndarray, alpha: np.ndarray, bbox: tuple[int, int, int, int]) -> None:
    h, w = source_shape
    x1, y1, x2, y2 = bbox
    if not (0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h):
        raise RuntimeError(f'asset bbox outside source: {bbox} vs {(w, h)}')
    if np.count_nonzero(support) == 0:
        raise RuntimeError('asset support is empty')
    if np.count_nonzero(alpha) == 0:
        raise RuntimeError('asset alpha is empty')
    if np.any((support > 0) & (hard == 0)):
        raise RuntimeError('hard territory does not contain all support')
    if np.any((hard > 0) & (alpha == 0)):
        raise RuntimeError('alpha does not contain all hard territory')

def process_page(path: Path, out_dir: Path, args: argparse.Namespace) -> dict:
    if not path.is_file():
        raise RuntimeError(f'source image does not exist: {path}')
    source_sha = sha256_file(path)
    color = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if color is None:
        raise RuntimeError(f'could not read image: {path}')
    gray = cv2.cvtColor(color, cv2.COLOR_BGR2GRAY)
    domain, global_stats = page_domain(gray)
    payload: dict = {'schema': 'corpus-motuum-figure-structure-probe-v2', 'source': str(path), 'source_sha256': source_sha, 'width': int(color.shape[1]), 'height': int(color.shape[0]), 'page_class': domain, **global_stats, 'assets': []}
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = path.stem
    if domain != 'normal-text-candidate':
        (out_dir / f'{stem}.metrics.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return payload
    fg, ink, paper, bg_sigma, otsu = foreground_mask(gray)
    labels, comps = connected_components(fg)
    h_cap, height_mode, candidate_count, text_band_count = estimate_page_scale(comps, gray.shape)
    assign_text_scores(comps, h_cap)
    w_stroke = estimate_stroke_width(labels, comps, h_cap)
    seeds, seed_threshold = robust_seed_scores(comps, h_cap, w_stroke)
    max_graph_gap = args.graph_gap_scale * h_cap
    graph = build_bbox_neighbor_graph(comps, max_graph_gap)
    raw = grow_seed_families(comps, seeds, graph, h_cap, w_stroke, text_penalty=args.text_penalty, growth_budget=args.growth_budget)
    by_label = {c.label: c for c in comps}
    grouped = coalesce_regions(raw, by_label, graph, h_cap)
    assets = accept_regions(grouped, gray.shape, h_cap)
    overlay = color.copy()
    asset_records = []
    for index, region in enumerate(assets, start=1):
        support, support_diag = renderable_support(labels, region, by_label, graph, h_cap, w_stroke, support_budget=args.support_budget, support_text_penalty=args.support_text_penalty)
        hard, alpha, closure_diag = closure_and_alpha(support, w_stroke, closure_budget=args.closure_budget, closure_boundary_min=args.closure_boundary_min, max_bridge_width=args.max_bridge_width, margin_scale=args.margin_scale, alpha_scale=args.alpha_scale)
        ys, xs = np.where(alpha > 0)
        if xs.size == 0:
            raise RuntimeError(f'{stem} asset {index}: empty alpha after accepted region')
        bbox = (int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1))
        validate_asset_geometry(gray.shape, support, hard, alpha, bbox)
        x1, y1, x2, y2 = bbox
        cv2.rectangle(overlay, (x1, y1), (x2 - 1, y2 - 1), (0, 0, 255), 2)
        cv2.putText(overlay, str(index), (x1 + 3, max(12, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1, cv2.LINE_AA)
        crop_color = color[y1:y2, x1:x2]
        crop_gray = gray[y1:y2, x1:x2]
        crop_paper = paper[y1:y2, x1:x2]
        crop_support = support[y1:y2, x1:x2]
        crop_hard = hard[y1:y2, x1:x2]
        crop_alpha = alpha[y1:y2, x1:x2]
        source_rgba = np.dstack([crop_color, crop_alpha])
        clean_gray = clean_publication_gray(crop_gray, crop_paper, crop_support, crop_alpha)
        clean_rgba = np.dstack([clean_gray, clean_gray, clean_gray, crop_alpha])
        prefix = f'{stem}-asset-{index:02d}'
        files = {'support': out_dir / f'{prefix}.support.png', 'territory': out_dir / f'{prefix}.territory.png', 'alpha': out_dir / f'{prefix}.alpha.png', 'source': out_dir / f'{prefix}.source.png', 'clean': out_dir / f'{prefix}.clean.png'}
        shas = {'support': checked_imwrite(files['support'], crop_support), 'territory': checked_imwrite(files['territory'], crop_hard), 'alpha': checked_imwrite(files['alpha'], crop_alpha), 'source': checked_imwrite(files['source'], source_rgba), 'clean': checked_imwrite(files['clean'], clean_rgba)}
        asset_records.append({'asset_index': index, 'bbox': [x1, y1, x2, y2], 'support_pixels': int(np.count_nonzero(crop_support)), 'territory_pixels': int(np.count_nonzero(crop_hard)), 'alpha_pixels': int(np.count_nonzero(crop_alpha)), 'region': asdict(region), 'support_diagnostics': support_diag, 'closure_diagnostics': closure_diag, 'files': {k: p.name for k, p in files.items()}, 'sha256': shas})
    page_files = {'foreground': out_dir / f'{stem}.foreground.png', 'ink_response': out_dir / f'{stem}.ink-response.png', 'asset_overlay': out_dir / f'{stem}.asset-overlay.jpg'}
    page_shas = {'foreground': checked_imwrite(page_files['foreground'], fg), 'ink_response': checked_imwrite(page_files['ink_response'], ink), 'asset_overlay': checked_imwrite(page_files['asset_overlay'], overlay, [cv2.IMWRITE_JPEG_QUALITY, 95])}
    edge_count = sum((len(v) for v in graph.values())) // 2
    payload.update({'background_sigma_px': bg_sigma, 'foreground_otsu': otsu, 'component_count': len(comps), 'component_height_mode': height_mode, 'scale_candidate_count': candidate_count, 'text_band_count': text_band_count, 'H_cap': h_cap, 'W_stroke': w_stroke, 'seed_threshold': seed_threshold, 'seed_count': len(seeds), 'graph_edge_count': edge_count, 'raw_seed_family_count': len(raw), 'grouped_region_count': len(grouped), 'accepted_asset_count': len(asset_records), 'parameters': {'graph_gap_scale': args.graph_gap_scale, 'text_penalty': args.text_penalty, 'growth_budget': args.growth_budget, 'support_budget': args.support_budget, 'support_text_penalty': args.support_text_penalty, 'closure_budget': args.closure_budget, 'closure_boundary_min': args.closure_boundary_min, 'max_bridge_width': args.max_bridge_width, 'margin_scale': args.margin_scale, 'alpha_scale': args.alpha_scale}, 'page_files': {k: p.name for k, p in page_files.items()}, 'page_file_sha256': page_shas, 'assets': asset_records})
    metrics_path = out_dir / f'{stem}.metrics.json'
    metrics_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if not metrics_path.is_file() or metrics_path.stat().st_size <= 0:
        raise RuntimeError(f'failed to write metrics: {metrics_path}')
    return payload

def iter_images(inputs: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for p in inputs:
        if p.is_dir():
            for suffix in ('*.jpg', '*.jpeg', '*.png', '*.tif', '*.tiff'):
                paths.extend(p.glob(suffix))
        else:
            paths.append(p)
    return sorted(set(paths))

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('inputs', nargs='+', type=Path)
    ap.add_argument('--out', type=Path, default=Path('work/figure-structure-probe-v2'))
    ap.add_argument('--graph-gap-scale', type=float, default=2.2)
    ap.add_argument('--text-penalty', type=float, default=5.0)
    ap.add_argument('--growth-budget', type=float, default=6.0)
    ap.add_argument('--support-budget', type=float, default=4.6)
    ap.add_argument('--support-text-penalty', type=float, default=5.8)
    ap.add_argument('--closure-budget', type=float, default=0.35)
    ap.add_argument('--closure-boundary-min', type=float, default=0.72)
    ap.add_argument('--max-bridge-width', type=float, default=3.5)
    ap.add_argument('--margin-scale', type=float, default=1.5)
    ap.add_argument('--alpha-scale', type=float, default=2.0)
    args = ap.parse_args()
    images = iter_images(args.inputs)
    if not images:
        raise SystemExit('no input images found')
    pages = []
    failures = []
    for path in images:
        try:
            payload = process_page(path, args.out, args)
            pages.append(payload)
            print(f"{path.name}: class={payload['page_class']} assets={payload.get('accepted_asset_count', 0)} H_cap={payload.get('H_cap')} W_stroke={payload.get('W_stroke')}")
        except Exception as exc:
            failures.append({'source': str(path), 'error': str(exc)})
            print(f'{path.name}: ERROR: {exc}')
    args.out.mkdir(parents=True, exist_ok=True)
    index_path = args.out / 'index.json'
    index_path.write_text(json.dumps({'schema': 'corpus-motuum-figure-structure-index-v2', 'pages': pages, 'failures': failures}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if failures:
        raise SystemExit(f'{len(failures)} page(s) failed; see {index_path}')
if __name__ == '__main__':
    main()
