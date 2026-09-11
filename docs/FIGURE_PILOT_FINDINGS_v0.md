# Figure pilot findings v0

> **Superseded research note.** This file records observations from exploratory local passes and is retained only as experimental history. Manual review later showed that the review/export path could split one illustration into several assets, duplicate overlapping regions, render traversed bleed-through/text as figure support, leave justified interior paper transparent, and omit assets through manual review-pack sampling. At the time this note was written, several stages described below were not yet reproducible from the committed `probe_figure_segmentation.py`. Do not treat the QA counts or rendering claims in this document as validation. See `FIGURE_REVIEW_AUDIT_v1.md` and the rebuilt probe for the corrective architecture.

Status: superseded research findings from the first 32-page figure-extraction pilot. Numeric parameters and behavioral claims below are **not** production guarantees.

## Pilot composition

The exported pilot contains 32 source pages:

- 24 positive pages selected from verified canonical pages with explicit `N ФИГ.` captions;
- 8 caption-negative controls selected from verified pages without such captions.

Canonical text is selection/QA metadata only. It is not used to construct segmentation masks.

## What the first passes established

### 1. Page-local scale is viable

On ordinary body pages the printed-text scale is extremely stable across the pilot. The first naive estimator, however, failed on a heavy bleed-through page because 2–4 px fragments outnumbered true character components.

A scale-relative dust floor plus an area/line-supported text mode recovered the ordinary character scale on that page. This is now an architectural requirement: the text reference population must itself be robustly identified rather than treating every small connected component as text evidence.

### 2. Dark cover/full-tone pages must be classified before scale estimation

The two dark endpoint pages (`sheet-001-left` and `sheet-300-right`) produce meaningless connected-component statistics if they are forced through a body-text model. A simple luminance-domain preflight already separates them cleanly in the pilot.

The production extractor should therefore fail closed or enter a separately calibrated page mode when a page is outside the ordinary text-page domain.

### 3. Strong seeds work, but size alone is insufficient

Large human silhouettes, ropes, ladders and apparatus lines generate strong non-text outliers. Large headings/caption glyphs and page-edge artifacts can also be outliers.

Adding a local text-likeness score based on component size plus horizontal/baseline neighborhood support removes the most obvious caption/body-text seeds without suppressing the major figure components.

### 4. Naive proximity growth is unsafe

The first proximity-only growth pass reproduced the expected failure: a long figure/apparatus region can pull in nearby body text simply because repeated small gaps form a path into the paragraph.

Replacing that with bounded geodesic growth over the component graph, with cumulative text-likeness penalties, removes the observed paragraph capture on representative pages while still recovering broken figure support.

This confirms that the critical distinction is not "near versus far" but **cheap figure-like continuation versus expensive text-like continuation**.

### 5. Bleed-through requires region-level filtering in addition to seed filtering

On the heavy bleed-through page (`sheet-126-left`), many isolated reverse-side fragments remain statistically unusual. The true drawing is nevertheless much stronger as a region by total normalized area/extent and support continuity.

Therefore an accepted figure cannot be defined by "there exists a seed". Seed families must be grown, grouped and scored as candidate regions. Small isolated seed families remain rejected evidence.

### 6. Region coalescing is a separate problem from foreground recovery

Several real figures are visually composed of multiple disconnected or weakly connected seed families. Examples include nested apparatus pieces and a single numbered illustration laid out as vertically separated subparts.

The exploratory coalescing rule based on rectangle intersection/nesting and small axis-aligned gaps was only a pilot heuristic. Manual review later proved that exporting raw seed families before final asset grouping is unacceptable.

The distinction must remain explicit in evaluation:

- pixel/support segmentation;
- growth ownership;
- region acceptance;
- asset grouping/false split;
- asset separation/false merge;
- renderable support;
- interior alpha/fill.

### 7. Thin typographic rules and scanner borders are important hard negatives

Two particularly useful false-positive families appeared:

- a thin horizontal footnote/typographic separator, whose large extent can resemble apparatus geometry;
- a long page-edge line/crop artifact.

The current diagnostic treatment uses context rather than a global "thin lines are not figures" rule. This matters because genuine ropes, poles and ladder rails can also be long and thin.

## Historical QA result — not validation

The exploratory pass once produced:

- zero accepted regions on all 8 caption-negative control pages;
- at least one accepted region on all 24 caption-positive pages;
- a post-coalescing region count equal to explicit caption count on all 24 positive pilot pages.

Manual visual review later showed that count agreement can coexist with bad assets: split drawings, duplicate overlapping crops, orphan floor strokes, bleed-through text in alpha, transparent interior paper, and omitted review samples. Therefore this count result is retained only as a coarse historical diagnostic and must **not** be reported as segmentation accuracy or asset correctness.

## Closure/fill experiment

A provisional closure experiment suggested normalizing artificial bridge support by observed contour scale rather than total foreground area, e.g.

`J_bridge = |A_bridge| / (P(M) · r_close)`.

This remains an uncalibrated research feature. Manual open/closed reference masks are required before any closure threshold is frozen.

## Rendering experiment

The intended rendering invariants remain:

1. observed historical strokes come from source pixels;
2. source-preserving and publication-clean derivatives are distinct;
3. justified interior paper is opaque rather than transparent;
4. only the outer alpha boundary is feathered;
5. no production claim follows from a visually plausible preview.

## What is still required before production

The next validation stage needs manual source-coordinate reference annotations. At minimum the reference set must score:

- missed weak strokes;
- ordinary-text intrusion;
- reverse-side bleed-through suppression versus weak-stroke loss;
- boundary error;
- false figure on caption-negative pages;
- false merge;
- false split;
- closure false-fill;
- page-domain classification.

Calibration and held-out validation pages must be separated before global dimensionless coefficients are frozen.

Only after the project-local algorithm survives that process should its reusable core be copied into the `snippets` repository. Failed exploratory behavior has no compatibility status and should not constrain the replacement implementation.
