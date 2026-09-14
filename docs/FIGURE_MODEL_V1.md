# Figure extraction model v1

Status: research implementation on `figures/model-v1`.

`v1` separates page evidence, graph ownership, logical asset identity and
publication rendering. The detector remains conservative; the publication pass
can now recover pale detail that is structurally attached to an accepted figure
without globally lowering the foreground threshold.

## Pipeline

`source page`
→ page-domain check
→ local paper / foreground response
→ text/stroke scale estimation
→ component evidence
→ robust figure seeds
→ exact bbox-gap neighbour graph
→ bounded ownership growth
→ support-based asset grouping
→ bounded renderable support
→ compact-island pruning
→ anchored weak-stroke recovery
→ topology/closure decision
→ territory + feathered alpha
→ source and clean RGBA derivatives
→ hashed audit manifest.

The following identities are intentionally false:

- seed ≠ figure;
- reachable component ≠ renderable ink;
- physical detector crop ≠ logical illustration;
- bbox ≠ mask;
- observed ink ≠ enclosed paper territory;
- clean derivative ≠ source evidence.

## Structural corrections

### Exact neighbour geometry

The spatial index covers expanded bounding boxes, not component centroids. It
only proposes candidates; every edge is rechecked by the exact bbox-gap
relation. Long rails, ropes and ladder sides therefore retain valid neighbours
even when their centroids are far apart.

### Text and asset evidence

Text-row endpoints receive one-sided row evidence. Seed families are grouped
only when component-support edges provide proximity or continuation evidence;
overlapping rectangles alone are never a grouping reason.

### Conservative support, then weak detail

Ownership growth and output support are separate graph passes. After that
conservative support is accepted, two publication-only operations run:

1. dense compact non-core islands may be removed, but only when their actual
   pixel distance from core ink exceeds the scale-relative gap;
2. the unnormalised local paper-minus-page residual is searched inside the
   accepted region plus a small stroke-relative allowance. Weak components
   survive only when connected, after a one-pixel-scale bridge, to a dilation of
   accepted support.

The adaptive weak threshold is `max(2, 0.28 × P1(strong residual))`. With the
observed `W_stroke≈2`, the defaults use about one pixel of bridging, two pixels
of anchoring and four pixels of bbox allowance.

Compact pruning defaults to a minimum fill ratio of `0.60`. A lower trial
threshold removed the legitimate outlined ball on `sheet-084-left`; the
regression audit caught that error. Dense scan blemishes on sheets 100, 105 and
114 are still removed, while the outlined ball and elongated fragments remain.

### Closure and transparent sticker output

Closure is evaluated at several stroke-relative radii and records bridge area,
maximum bridge width, enclosed-hole area and boundary support. If none satisfy
the topology constraints, the drawing remains open and no interior is invented.

Every clean asset is lossless RGBA:

- alpha is zero outside the sticker;
- accepted paper territory is white and opaque;
- the feather is a white-to-transparent transition;
- accepted ink remains crisp;
- RGB under zero and intermediate alpha is white, preventing dark fringes when
  the PNG is placed on a black page.

Each physical asset emits `support`, `territory`, `alpha`, `source` and
`clean` derivatives. The logical compositor also defaults to transparent RGBA.

## Regressions

The source-identified hard set and ten held-out controls are recorded in
`corpus/figures/regressions-v1.json`. It includes the structural page 76 case
and the reported weak-support failures on sheets 83, 100, 112, 113, 114, both
sides of 119 and both sides of 122.

`tests/test_figure_probe_geometry.py` covers:

1. long-component neighbour indexing;
2. refusal to merge on bbox overlap alone;
3. bounded closure versus a genuinely open shape;
4. anchored weak continuation versus an isolated pale blob;
5. compact dense-island pruning while preserving elongated and outlined items;
6. white RGB beneath soft alpha;
7. end-to-end separation and output hashing.

The compositor tests cover complete-asset joins, page-coordinate clipping,
disjoint clip reuse, overlap rejection, dimensions, hashes and batch manifests.

Run:

```bash
python -m unittest -v \
  tests.test_figure_probe_geometry \
  tests.test_compose_figure_assets
```

## Full-book evidence

The final one-pass audit used all 600 source pages re-extracted from the tracked
PDF and matched the committed page manifest exactly: 600/600 dimensions,
SHA-256 values and extraction methods, including fallback sheets
`1, 297, 298, 299, 300`.

Against the immutable `work/figure-structure-full-v2` baseline:

- failures: **0**;
- physical assets: **378 → 378**;
- pages with a changed asset count: **0**;
- pages with a changed page class: **0**;
- support pixels: **3,435,508 → 4,572,719**;
- newly recovered support: **1,138,805 px**;
- pruned artifacts: **1,594 px in 356 components** across 111 assets;
- numbered-set recovery: **276,701 px**;
- numbered-set pruning: **648 px**;
- assets whose alpha bbox changed: **302**.

The count stability matters: pale limbs, faces, apparatus and hatching were
restored without changing detector census or page classification.

## Commands

```bash
python tools/probe_figure_segmentation.py \
  work/book-v1/pages \
  --out work/figure-structure-full-v3

python tools/audit_figure_census.py \
  --index work/figure-structure-full-v3/index.json \
  --metrics-root work/figure-structure-full-v3 \
  --out work/figure-structure-full-v3/census-audit

python tools/build_figure_review_set.py \
  --audit work/figure-structure-full-v3/census-audit/figure-census-audit.json \
  --source-root work/figure-structure-full-v3 \
  --out work/figure-review-v3

python tools/compose_figure_assets.py batch \
  --source-root work/figure-structure-full-v3 \
  --out-root work/figure-logical-v3
```

## Remaining known work

- `sheet-128-left`, figure 74: still missed and affected by severe
  bleed-through; this needs a separate restoration/re-extraction task.
- `sheet-133-right`, figure 78: still a two-crop review candidate.
- atlas plates need a separate grouping policy.
- `sheet-001-right` cover/title illustration needs separate re-extraction.

Bleed-through suppression should remain a restoration stage rather than being
hidden inside the segmentation threshold.
