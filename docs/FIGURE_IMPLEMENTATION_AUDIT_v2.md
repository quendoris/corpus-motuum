# Figure implementation audit v2

Status: architecture-first audit after manual review and after discovering divergence between the committed probe and several local exploratory implementations.

This document is deliberately stricter than `FIGURE_PILOT_FINDINGS_v0.md`. The goal is not to preserve any experimental line of code. The goal is to identify which implementation choices follow the extraction invariants and which must be discarded or re-derived.

## 1. Implementation divergence found

Three different experimental lines had existed at once:

1. the originally committed `probe_figure_segmentation.py`, which stopped at seed visualization;
2. an uncommitted/local growth + review-export path that produced the first 30-crop web review;
3. a later local “territory” probe that introduced additional grouping and optional expected-count priors.

The documentation meanwhile described ideas from more than one of those lines. This made the repository look more coherent than the executable state actually was.

That is an engineering defect. From this audit onward, the branch implementation and the branch documentation are the only canonical research state. Local experiments may exist, but claims derived from them must be labelled as local until reproduced by committed code.

## 2. Rejected implementation choices

### 2.1 Parametric normal-tail seed threshold without a validated distribution

The original seed probe converted robust feature deviations into a `NormalDist` tail threshold. The transformed features were not shown to be independent Gaussian variables. The apparent false-positive probability was therefore unjustified.

Decision: rejected. Use an explicitly empirical/robust outlier score until a statistical family is validated against data.

### 2.2 Exporting raw seed families as assets

This produced orphan floor strokes, duplicate partial drawings and split illustrations.

Decision: rejected. Asset identity exists only after grouping/coalescing.

### 2.3 Cropping a page-wide union mask by each region rectangle

This allows one asset to inherit pixels owned by another asset if those pixels happen to fall inside the rectangle.

Decision: rejected. Every asset owns an independent mask.

### 2.4 Treating traversed graph nodes as renderable support

A graph path may legitimately cross uncertain evidence to connect a broken stroke. That does not make the bridge evidence part of the illustration.

Decision: rejected. Growth ownership and renderable support are separate stages.

### 2.5 Caption count as an implicit correctness target

A local experimental implementation contained an optional `expected_count` grouping prior derived from caption metadata. Even when opt-in, this is dangerous during evaluation because it can make asset count agree with the reference by construction.

Decision: caption identity/count may be used for sampling and post-hoc QA, but not for segmentation/grouping when measuring model performance. If a project later chooses to use layout metadata as an explicit production prior, that must be a separate, declared composition layer and evaluated separately from image-only segmentation.

### 2.6 Uncalibrated “territory around support” as a replacement for closure

One local probe expanded figure territory by distance from strong support, constrained by distance from text, then closed/filled that territory. It produced visually useful experiments, but it no longer represented the original question “is this paper actually enclosed by the drawing?” and could make large opaque areas because of proximity rather than contour evidence.

Decision: do not promote this as the canonical fill model. Return to explicit support + closure topology + exterior flood-fill. A proximity territory may later be evaluated as a separate margin/context model, not silently substituted for closure.

## 3. Remaining weaknesses in the rebuilt committed probe

The rebuilt branch tool is a material correction, but it is still a research probe. The following items remain open and must not be hidden behind the improved pilot appearance.

### 3.1 Neighbor discovery must preserve geometric semantics

A centroid-bucket spatial index can miss two components whose bounding boxes are close but whose centroids are far apart, especially for long rails, ropes or ladder pieces.

Required correction: index expanded bounding-box coverage (or use an R-tree/k-d tree over suitable support geometry) so the optimization never removes a graph edge that the declared bbox/support-gap relation says should exist.

### 3.2 Renderable-support recovery is still provisional

A one-hop “close to core” test can miss a chain of several weak but geometrically continuous fragments. Conversely, making it recursive without a stricter cost can reintroduce text capture.

Required correction: define a second bounded support graph/cost distinct from growth ownership. It should reward stroke/orientation continuation and penalize text evidence, then validate against weak-stroke ground truth.

### 3.3 Grouping is still rectangle-heavy

Bounding-box intersection/nesting and axis gap/overlap are useful priors but can merge distinct neighboring drawings or fail on long sparse composite drawings.

Required correction: asset grouping must incorporate actual support distance/continuation and be scored explicitly for false merge/false split.

### 3.4 Page-domain classification is incomplete

The current pilot preflight reliably catches the two dark endpoint pages, but a single dark-fraction rule does not constitute a general classifier for blank pages, photographs/full-tone scans, extreme damage or unusual layouts.

Required correction: keep unsupported-domain behaviour fail-closed and calibrate additional domain observables before claiming generality.

### 3.5 Closure threshold is not calibrated

The current dimensionless closure cost is structurally preferable to a literal “80%” or a pixel constant, but its default budget is not fitted against manual open/closed masks.

Required correction: record bridge/topology diagnostics and fit the decision only on explicit closure ground truth; preserve open drawings without forced fill.

### 3.6 Output integrity checks are incomplete

Image writes should be checked, source/asset SHA-256 should be recorded where practical, and every reported bbox/mask should be validated as non-empty and inside source coordinates before a review/export pack is published.

Required correction: fail closed on output-integrity violations.

## 4. Canonical stage model after the audit

The intended pipeline is now:

`source page`
→ `page-domain decision`
→ `local paper/ink measurement`
→ `text-scale estimation`
→ `component evidence`
→ `strong seeds`
→ `growth ownership graph`
→ `raw seed families`
→ `asset grouping`
→ `asset-specific renderable support`
→ `closure/interior-paper decision`
→ `outer margin + alpha`
→ `source-preserving derivative + clean publication derivative`
→ `audit manifest/review UI`.

No downstream stage may silently stand in for an upstream one. In particular:

- a seed is not a figure;
- a reachable component is not automatically ink;
- a seed family is not automatically an asset;
- a bbox is not a mask;
- observed ink is not the same thing as opaque interior paper;
- a clean derivative is not source evidence.

## 5. Evaluation rule

The next model comparison must be based on manual source-coordinate reference data, not on whether a review page “looks better” or asset counts happen to match captions.

At minimum measure:

- missed weak-stroke pixels/support;
- ordinary-text intrusion;
- bleed-through intrusion;
- boundary/alpha error;
- false split;
- false merge;
- false fill;
- false figure on independently inspected negative pages;
- page-domain classification errors.

Caption-derived pilot selection may continue as sampling metadata, but caption-negative pages are not certified figure-free negatives. The pilot exporter now labels them accordingly.

## 6. IAM rule for this work

There is no “legacy algorithm” to defend at this stage. There are only hypotheses and evidence.

A prior implementation is retained only when its behaviour follows the declared invariants and survives measurement. If it does not, it is replaced. Compatibility with a failed experiment is not a requirement.
