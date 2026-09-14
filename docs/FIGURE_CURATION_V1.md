# Figure curation v1

Status: first conservative post-census cleanup of the full-book `model-v1` output.

The full probe output under `work/figure-structure-full-v2` is immutable evidence. Curation does **not** delete or rewrite it. Instead `tools/build_figure_review_set.py` materializes a separate review set after removing only high-confidence junk identified from the 600-page census, editorial indices and overlay inspection.

## First-pass result

Input detector assets: **378**.

Dry-run of the committed policy yields:

- **157** numbered-figure crops kept for human review;
- **52** atlas/plate candidates kept separately;
- **3** paratext/title/cover illustrations kept separately;
- **166** high-confidence false detections excluded from the clean review set.

The 166 excluded detections are dominated by display typography, footnote/section rules, scanner-edge slivers, page signatures and library/provenance marks. They are logged in `removed.json` when the review set is built.

The numbered count is deliberately a **crop count**, not yet the final logical illustration count. Editorial evidence contains numbered figures `1..156` plus two distinct `bis` captions (`28 bis` and `87 bis`), so the current working logical target is **158 numbered illustrations**. The cleaned detector set has 157 crops because known split/merge/miss cases do not cancel perfectly.

## Logical compositions

Physical detector crops remain unchanged in `work/figure-review-v1`. A separate,
versioned layout manifest records cases where several crops belong to one
logical illustration:

```text
corpus/figures/logical-compositions-v1.json
```

Render all declared compositions with:

```bash
python tools/compose_figure_assets.py batch
```

Default output:

```text
work/figure-logical-v1/
├── numbered/
│   └── figs-129__p0455__sheet-228-left__composite.png
└── compositions-manifest.json
```

The compositor reads each crop bbox and SHA-256 from the page metrics. It checks
the crop dimensions and hash, creates the union bbox, and places every part at
its exact source-page offset without resizing. Consequently figure 129 keeps
the original horizontal alignment and the 12-pixel vertical gap between
`asset-01` and `asset-02`; the white area between them is deliberate page
geometry rather than guessed spacing.

For an ad-hoc composition outside the committed batch manifest:

```bash
python tools/compose_figure_assets.py one \
  work/figure-structure-full-v2/sheet-228-left.metrics.json \
  --assets 1 2 \
  --out work/figure-logical-v1/numbered/figure-129.png
```

This also writes `figure-129.provenance.json`. Use `--background transparent`
only when a transparent union canvas is explicitly required; the committed
figure 129 layout uses an opaque white canvas.

## Known structural cases before manual review

- `sheet-076-right`: figures 35 and 36 are merged into one crop.
- `sheet-128-left`: figure 74 is missed; page is also a strong bleed-through case.
- `sheet-133-right`: figure 78 is currently two crops; keep both pending grouping review.
- `sheet-228-left`: figure 129 is a known logical split across two spatial regions.
- `sheet-256-right`: figures 144 and 145 are merged into one crop.
- `sheet-001-right`: a real cover/title illustration exists, but the current crop merges most of the page with typography; exclude it and re-extract separately.

## Review-set build

```bash
python tools/build_figure_review_set.py --dry-run
python tools/build_figure_review_set.py
```

Default output:

```text
work/figure-review-v1/
├── numbered/   # main book figures, easy to scroll in sequence
├── atlas/      # plates kept separate because grouping semantics differ
├── paratext/   # title / cover illustrations
├── manifest.json
└── removed.json
```

The default materialization mode is hard-link with copy fallback, so the cleaned review folder costs little extra space while the baseline stays untouched. Use `--mode copy` for a self-contained folder.

## Why atlas is separate

Atlas plates are not evaluated by `N ФИГ.` caption equality. Their detector output mixes whole plate regions, sub-diagrams, dimensions and labels, so they require their own manual grouping pass. Keeping them outside `numbered/` prevents atlas oversegmentation from hiding the quality of the main 1..156+bis sequence.

## Next gate

The next gate is visual, not statistical: scroll `numbered/` first and mark bad crops / wrong grouping / missed support. Then review `atlas/`. Each confirmed correction should become an explicit curation decision or a restoration/re-extraction task rather than a global detector threshold tweak.
