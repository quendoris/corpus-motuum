# Figure curation v1

Status: conservative post-census curation plus explicit logical-layout fixes for
the full-book `model-v1` output.

The detector output is immutable physical evidence. Curation does not delete or
rewrite it. `tools/build_figure_review_set.py` creates a separate review set,
and `tools/compose_figure_assets.py` creates logical publication assets.

## Census result

Input detector assets: **378**.

The committed curation policy yields:

- **157** numbered-figure crops;
- **52** atlas/plate candidates;
- **3** paratext/title/cover illustrations;
- **166** high-confidence false detections excluded from the review set.

Excluded detections are mostly display typography, footnote/section rules,
scanner-edge slivers, page signatures and provenance marks. They remain
recorded in `removed.json`.

These are physical crop counts, not logical illustration counts. Editorial
evidence contains figures `1..156` plus `28 bis` and `87 bis`, so the
working target is **158 logical numbered illustrations**.

## Logical-layout compositor

`corpus/figures/logical-compositions-v1.json` now resolves five files:

| Source page | Logical output | Operation |
|---|---:|---|
| `sheet-076-right` | 35 | clip merged asset 1 above page y=744 |
| `sheet-076-right` | 36 | clip merged asset 1 below page y=744 |
| `sheet-228-left` | 129 | compose physical assets 1 and 2 |
| `sheet-256-right` | 144 | clip merged asset 1 above page y=846 |
| `sheet-256-right` | 145 | clip merged asset 1 below page y=846 |

Run:

```bash
python tools/compose_figure_assets.py batch \
  --source-root work/figure-structure-full-v3 \
  --out-root work/figure-logical-v3
```

The resulting layout is:

```text
work/figure-logical-v3/
├── numbered/
│   ├── figs-035__p0152__sheet-076-right__split.png
│   ├── figs-036__p0152__sheet-076-right__split.png
│   ├── figs-129__p0455__sheet-228-left__composite.png
│   ├── figs-144__p0512__sheet-256-right__split.png
│   └── figs-145__p0512__sheet-256-right__split.png
└── compositions-manifest.json
```

The compositor supports either:

- `asset_indices`: two or more complete physical crops placed at their recorded
  page offsets;
- `parts`: one or more declared full-page `clip_bbox` windows, optionally
  trimmed only at fully transparent outer pixels.

It verifies page bounds, crop dimensions and declared SHA-256 values. Windows
from one physical asset may be reused only when they are disjoint; overlapping
reuse is rejected. Rendering uses Porter-Duff alpha-over and never resizes a
part.

All committed logical outputs default to transparent lossless RGBA. Transparent
pixels carry white RGB, so the soft edge stays white rather than turning into a
dark fringe on a black page. Figure 129 retains the exact gap and horizontal
alignment encoded by its source-page bboxes; no pixel gap is hard-coded.

For a complete-asset ad-hoc join:

```bash
python tools/compose_figure_assets.py one \
  work/figure-structure-full-v3/sheet-228-left.metrics.json \
  --assets 1 2 \
  --out work/figure-logical-v3/numbered/figure-129.png
```

This writes `figure-129.provenance.json` beside the PNG.

## Weak-support correction class

The reported pages 83, 100, 112, 113, 114, both sides of 119 and both sides of
122 exposed a separate problem: localization was good, but conservative
renderable support discarded pale limbs, faces, apparatus, clothing and
shadows.

The detector now keeps its conservative graph support and adds a bounded,
anchor-only weak-residual pass. Dense compact islands far from core ink are
pruned by actual pixel distance. The final fill-ratio gate is `0.60`: it
removes the known dense blemishes while preserving the outlined ball on
`sheet-084-left` and elongated fragments.

The hard set and ten held-out control pages are source-hash anchored in
`corpus/figures/regressions-v1.json`.

## Review-set build

```bash
python tools/audit_figure_census.py \
  --index work/figure-structure-full-v3/index.json \
  --metrics-root work/figure-structure-full-v3 \
  --out work/figure-structure-full-v3/census-audit

python tools/build_figure_review_set.py \
  --audit work/figure-structure-full-v3/census-audit/figure-census-audit.json \
  --source-root work/figure-structure-full-v3 \
  --out work/figure-review-v3 \
  --dry-run

python tools/build_figure_review_set.py \
  --audit work/figure-structure-full-v3/census-audit/figure-census-audit.json \
  --source-root work/figure-structure-full-v3 \
  --out work/figure-review-v3
```

Atlas plates stay separate because their crops mix whole plates, sub-diagrams,
dimensions and labels; caption equality is not a valid grouping rule there.

## Still unresolved

- `sheet-128-left`: figure 74 is missed and has severe bleed-through.
- `sheet-133-right`: figure 78 remains a two-crop grouping candidate.
- `sheet-001-right`: the cover/title illustration needs separate extraction.
- atlas plates need their own logical-grouping pass.

Each further manual-layout correction should become a manifest entry or a
separate restoration task, not an unrecorded edit to detector output.
