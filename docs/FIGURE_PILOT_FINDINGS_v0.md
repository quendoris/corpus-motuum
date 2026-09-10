# Figure pilot findings v0

Status: research findings from the first 32-page figure-extraction pilot. These are **not** production guarantees and do not freeze the current numeric parameters.

## Pilot composition

The exported pilot contains 32 source pages:

- 24 positive pages selected from verified canonical pages with explicit `N ФИГ.` captions;
- 8 negative controls selected from verified pages without such captions.

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

An image-only coalescing rule based on rectangle intersection/nesting and small axis-aligned gaps with strong orthogonal overlap was sufficient to collapse the obvious pilot over-splits without using caption identity as inference input.

The distinction must remain explicit in evaluation:

- pixel/support segmentation;
- region acceptance;
- asset grouping/false split;
- asset separation/false merge.

### 7. Thin typographic rules and scanner borders are important hard negatives

Two particularly useful false-positive families appeared:

- a thin horizontal footnote/typographic separator, whose large extent can resemble apparatus geometry;
- a long page-edge line/crop artifact.

The current diagnostic treatment uses context rather than a global "thin lines are not figures" rule:

- page-edge concentration + extreme thinness identifies border artifacts;
- a thin horizontal line flanked by line-supported text on both sides is treated as a typographic rule candidate.

This matters because genuine ropes, poles and ladder rails can also be long and thin.

## Current QA result

After the diagnostic additions above, the current research pass produces:

- zero accepted figure regions on all 8 negative-control pages;
- at least one accepted region on all 24 positive pages;
- after image-only coalescing, accepted-region count agrees with the number of explicit figure captions on all 24 positive pilot pages.

This is encouraging but **must not be reported as segmentation accuracy**. Caption count is only a coarse QA signal. It does not measure whether every weak stroke was preserved, whether a few text pixels leaked into a mask, whether the exact boundary is correct, or whether a composite figure should be grouped according to the eventual archival policy.

## Closure/fill experiment

A provisional closure experiment exposed another useful detail. Measuring morphological bridge pixels relative to total foreground area makes ordinary contour thickening look excessively expensive. A more meaningful first normalization is bridge support relative to observed contour length times closing radius, for example

`J_bridge = |A_bridge| / (P(M) · r_close)`.

On the representative pilot figures this produces comparable dimensionless values despite very different drawing area. The final closure cost should still add gap-span/topology terms and be fitted against manual open/closed reference masks; the present threshold is not frozen.

## Rendering experiment

The current preview path:

1. keeps the accepted source-region luminance;
2. optionally desaturates chroma without whitening;
3. fills only accepted interior regions;
4. adds a small page-relative outer margin;
5. feathers only the outer alpha boundary;
6. composites cleanly on a warm/yellow paper background.

The previews support the original archival goal: figures remain visibly historical rather than becoming redrawn black-on-white clip-art.

## What is still required before production

The next validation stage needs manual source-coordinate reference annotations. At minimum the reference set must score:

- missed weak strokes;
- ordinary-text intrusion;
- boundary error;
- false figure on negative pages;
- false merge;
- false split;
- closure false-fill;
- page-domain classification.

Calibration and held-out validation pages must be separated before the global dimensionless coefficients are frozen.

Only after the project-local algorithm survives that process should its reusable core be copied into the `snippets` repository. The snippet architecture is being documented separately now; implementation publication is intentionally deferred until this validation is complete.
