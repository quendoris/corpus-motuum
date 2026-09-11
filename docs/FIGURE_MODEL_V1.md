# Figure extraction model v1

Status: research implementation on `figures/model-v1`.

`v1` is a structural rewrite of the first probe. It exists because manual review showed that the old implementation mixed seed detection, graph ownership, asset grouping and output masking. The target is not to preserve the old code; the target is one-pass extraction whose errors can be measured and corrected without hidden priors.

## Pipeline

`source page`
→ page-domain check
→ local paper / foreground response
→ text-scale and stroke-scale estimation
→ component evidence
→ robust figure seeds
→ exact bbox-gap neighbour graph
→ bounded ownership growth
→ support-based asset grouping
→ independent bounded renderable-support recovery
→ topology/closure decision
→ territory + alpha
→ source-preserving and clean derivatives
→ hashed audit manifest.

The following identities are intentionally false:

- seed ≠ figure;
- reachable component ≠ renderable ink;
- seed family ≠ output asset;
- bbox ≠ mask;
- observed ink ≠ enclosed paper territory;
- clean derivative ≠ source evidence.

## Corrections relative to model v0

### Neighbour graph

The spatial index now indexes expanded **bounding-box coverage**, not component centroids. The index only proposes candidate pairs; every edge is rechecked by the declared geometric bbox-gap relation. Therefore a long rail, rope or ladder side cannot lose an otherwise valid edge merely because its centroid is far away.

### Text evidence

Text-row endpoints receive one-sided row evidence. A glyph no longer becomes suspiciously non-text merely because it is the first or last glyph in a line.

### Asset grouping

Rectangle overlap by itself is never a grouping reason. Seed families are grouped only when actual component-support edges provide proximity/continuation evidence. Small orphan fragments may attach more permissively than two comparable candidate figures.

### Renderable support

Ownership growth and output support are separate bounded graph passes. Traversing a weak component can help establish ownership without automatically forcing that component into the output mask.

### Closure and interior paper

Closure is evaluated at several stroke-relative radii. A candidate closure records bridge area, estimated maximum bridge width, enclosed-hole area and boundary support. If none pass the topology constraints, the drawing remains open and no interior is invented.

### Output derivatives

Each asset emits:

- `*.support.png` — observed renderable ink;
- `*.territory.png` — accepted opaque territory after closure/margin;
- `*.alpha.png` — final feathered alpha;
- `*.source.png` — original source colour under the asset alpha;
- `*.clean.png` — publication derivative with non-support paper flattened to white.

The source derivative and clean derivative are deliberately different products.

### Integrity

Image writes fail closed. Asset bboxes and masks are checked for non-emptiness and containment. Source pages and all emitted asset/page images receive SHA-256 records in the metrics manifest.

## Regression tests

`tests/test_figure_probe_geometry.py` covers the failure classes that triggered this rewrite:

1. long component + nearby fragment must remain neighbours even with distant centroids;
2. overlapping bboxes without support evidence must not merge;
3. a small contour gap may close, while a truly open U-shape must not be filled;
4. an end-to-end synthetic page with text, uneven paper and two nearby drawings must produce two separate assets and hash-valid outputs.

Run:

```bash
python -m unittest -v tests/test_figure_probe_geometry.py
```

## Pilot command

After extracting `figure-pilot-v0.tar.gz`:

```bash
python tools/probe_figure_segmentation.py \
  figure-pilot-v0/images \
  --out work/figure-structure-probe-v2
```

The defaults are deliberately conservative. They are not declared calibrated until the manual source-coordinate reference set is scored.

## Acceptance sequence before full-book extraction

1. Run the 32-page stratified pilot and inspect failures, not just attractive examples.
2. Build the manual hard set around the already identified difficult pages (including split figures, neighbouring figures, sparse ropes/rails, weak strokes and text adjacent to figures).
3. Measure false split, false merge, missed support, text intrusion, bleed-through intrusion, false fill and alpha/boundary error.
4. Tune only against those explicit errors; do not use caption count as a target.
5. Generate a small human review pack of the hardest cases plus representative ordinary cases.
6. Treat severe bleed-through as a separate restoration problem after segmentation is stable.
7. Only after that gate passes, run the complete book in one pass.

## Remaining research risks

- thresholds are scale-relative but not yet fitted to a manual reference set;
- full-tone/damaged/near-blank page-domain classification is intentionally fail-closed and incomplete;
- continuation evidence is geometric rather than learned and may need orientation/stroke features on difficult engravings;
- bleed-through suppression belongs to the later restoration stage and must not be hidden inside segmentation scoring.
