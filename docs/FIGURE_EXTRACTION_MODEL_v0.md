# Figure extraction model v0

Status: design + calibration protocol. The purpose is to preserve illustrations from the scanned book as archival assets without whitening, redrawing, or silently discarding weak strokes.

## 1. Invariants

1. The source page image is immutable evidence. Never overwrite or resave it as part of figure extraction.
2. Pixel distances are not fixed in absolute pixels. Geometry is normalized by per-page text scale.
3. Large connected components are **seeds**, not the complete definition of an illustration.
4. Paper tone, stains, print-density variation, and weak historical marks inside a valid figure region are preserved as luminance information.
5. The archival derivative is lossless and carries alpha. The intended format is PNG.
6. Canonical text and explicit `N ФИГ.` captions may be used to select/calibrate/QA pilot pages, but must not determine the segmentation mask itself.

## 2. Page-scale observables

Let the source page luminance be `L(x, y)` and a slowly varying estimate of the paper field be `B(x, y)`. Define local ink response

`I(x, y) = max(B(x, y) - L(x, y), 0)`.

Thresholding `I` gives a foreground support mask from which connected components are measured. For component `c`, retain at least

- `h(c)`, `w(c)` — bounding-box dimensions;
- `A(c)` — foreground area;
- `e(c) = max(h(c), w(c))` — spatial extent;
- local stroke-width samples from the distance transform;
- location and neighborhood relations.

The dominant population of small components is treated as the text population. From that population estimate:

- `H_cap` — robust upper characteristic height of ordinary capital letters / full-height text components;
- `W_stroke` — characteristic printed stroke width from local maxima of the foreground distance transform.

These are measured separately for every page. A 750×1200 page and a rerendered higher-resolution fallback page should therefore produce comparable normalized decisions without changing pixel constants.

## 3. Strong figure seeds

The original intuition is retained but made probabilistic: a component is not selected merely because `h > H_cap`; instead its feature vector is tested against the learned text-component distribution.

For example, use the normalized/log features

`f(c) = [log(h/H_cap), log(e/H_cap), log(A/(H_cap·W_stroke))]`.

A component becomes a strong seed when its probability under the text model is sufficiently small. The false-seed budget is expressed per page rather than as a magic pixel threshold. This makes a tall narrow rope, a wide apparatus line, and a dense human silhouette all capable of becoming seeds by different evidence.

This seed stage should be visualized explicitly in red during calibration.

## 4. Growth as a component graph

Pixel adjacency alone is too brittle for old scans because one printed stroke can be broken into several components. Conversely, simple dilation can allow two neighboring illustrations to merge.

Build a graph whose nodes are foreground components. Edge distance is measured in units of `W_stroke`; node traversal cost also incorporates text-likeness. Starting from strong seeds, perform multi-source geodesic growth through this graph.

Consequences:

- gaps of a few stroke widths can be crossed when the continuation remains figure-like;
- a chain of ordinary letters is expensive and therefore does not automatically pull an entire text line into the figure;
- when two seed families approach the same ambiguous area, multi-source ownership prevents both figures from claiming it silently.

The exact graph-cost coefficients are calibration parameters, not hand-tuned page constants.

## 5. Interior fill and closure cost

The intuitive “about 80% enclosed” rule is treated as a measurable closure problem rather than a literal percentage.

For each grown region:

1. close only small contour gaps, with the closing scale expressed as `k_close · W_stroke`;
2. flood-fill from the page exterior;
3. pixels unreachable from the exterior are candidate interior;
4. measure the **closure cost**: how much artificial bridge support had to be introduced relative to the observed contour/support.

Low-cost closure is accepted as a true interior. High-cost closure is rejected, which prevents the empty space between two nearby figures from being filled merely because they happen to stand beside each other. Open drawings remain valid; they simply do not receive unjustified interior fill.

## 6. Relative outer margin

The final archival region is expanded by a relative amount such as

`r = k_margin · W_stroke`

(or a learned combination of `W_stroke` and `H_cap`). This replaces hard-coded 5–10 px while preserving the original intent: retain ambiguous weak edge pixels without swallowing neighboring text.

## 7. Desaturation and alpha

The extracted crop is **desaturated, not whitened**. RGB chroma is discarded while luminance is preserved. Thus paper unevenness, stains, weak lines, and print density inside the figure remain part of the historical image.

Only the *outer mask boundary* is feathered into transparency. Let `d(x, y)` be signed distance to the accepted region boundary. Alpha is a smooth function of `d` over a width `k_alpha · W_stroke`; the interior remains opaque.

This produces a portable grayscale+alpha archival asset that can be placed on white, cream, or yellow UI paper without a hard rectangular scan edge.

## 8. JPEG vs PNG for this project

JPEG and PNG are not equivalent.

- JPEG is raster, normally **lossy**, and does not support alpha transparency. It is often much smaller for photographic/noisy scans. It is not “scalable” in the vector sense: enlarging it does not create new detail.
- PNG is raster, **lossless**, and supports alpha. For line art, masks, and repeated editing it avoids generation loss and often compresses simple graphics well, although noisy scanned paper can make it substantially larger than JPEG.

Therefore:

- preserve the original embedded JPEG page bitstream as source evidence when available;
- store the extracted archival figure derivative as PNG with alpha;
- generate smaller delivery derivatives later if a web/UI layer needs them. Never make those lossy derivatives the canonical image asset.

## 9. Calibration instead of manual tuning

There is one important limit: truly “optimal” hyperparameters cannot be inferred from an unlabeled page alone. Optimality only exists after we define what errors cost us.

Create a small stratified pilot with positive figure pages and negative controls. For each pilot page, create a reference region/mask. Optimize the dimensionless model parameters against an explicit loss, for example

`L = λ_miss·E_missed_stroke + λ_text·E_text_intrusion + λ_boundary·E_boundary + λ_merge·E_false_merge + λ_split·E_false_split`.

For this archive `λ_text` and `λ_miss` should both be high: we do not want body text in the asset, but losing a thin historical stroke is also unacceptable.

Estimate uncertainty with page-level resampling/bootstrapping. Freeze a versioned parameter set only after it is stable across the pilot. Then run the entire 600-page corpus deterministically.

This is the key difference from hand-picking “80%”, “7 px”, etc.: the page-specific quantities are measured, the global dimensionless coefficients are fitted against a declared error model, and their uncertainty is recorded.

## 10. Required outputs

For each accepted figure keep:

- archival PNG (grayscale/RGB + alpha);
- binary/soft alpha mask;
- source page id and source SHA-256;
- source bounding box in page coordinates;
- measured `H_cap`, `W_stroke` and model version;
- seed ids and segmentation confidence/closure cost;
- debug overlay for audit.

A manifest must make every derivative traceable back to the immutable page image.
