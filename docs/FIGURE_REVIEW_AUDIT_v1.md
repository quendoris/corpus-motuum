# Figure review audit v1

Status: corrective audit after manual visual review of the first 30-crop web pack.

The previous count-level QA was too weak. Matching the number of accepted regions to the number of explicit figure captions did **not** establish that the produced assets were correct. Manual review exposed several implementation defects and one review-export defect that must be treated as design errors, not tuned around.

## 1. What the screenshots proved

The review set showed all of the following failure classes:

- one illustration split into a large body region plus tiny orphan fragments such as floor strokes;
- one illustration split into two large overlapping regions;
- nested/duplicated candidate regions exported as if they were separate assets;
- ordinary text or bleed-through promoted into the alpha mask;
- enclosed paper that should have remained opaque inside the illustration left transparent;
- small nearby artifacts admitted into crops;
- at least one real illustration omitted from the review pack because the exporter sampled only a manually chosen number of raw regions from that page.

The small nearby artifacts are not necessarily a production defect: for archival recovery, retaining a doubtful speck can be preferable to deleting a weak historical stroke. The other classes are defects.

## 2. Root-cause audit

### 2.1 Raw seed families were confused with final assets

The exploratory growth pass produced multiple accepted seed families per real illustration. The review exporter then iterated those raw accepted regions directly.

That violated the architecture already documented in this branch: foreground/seed-family segmentation and asset grouping are different stages. Coalescing must happen **before** a crop receives an asset identity.

Consequences visible in review:

- isolated ground/floor lines were emitted as separate images;
- overlapping partial versions of the same ladder/person drawing were emitted independently;
- composite drawings were fragmented even when their parent spatial relationship was already known by the growth stage.

### 2.2 The review renderer cropped a union mask, not a region-specific mask

The ad-hoc review pack used the page-wide accepted mask and cropped that union by each candidate bounding box. Therefore an individual crop could inherit foreground that belonged to another accepted region merely because that foreground happened to lie inside the crop rectangle.

A final asset must own its own support mask. Cropping a union mask by a rectangle is not equivalent.

### 2.3 Graph traversal was treated as rendering permission

Geodesic growth is allowed to traverse weak or text-like bridge nodes in order to test whether a seed family can continue. The exploratory renderer then emitted all traversed nodes as figure support.

That is incorrect. Traversal ownership and renderable support are separate decisions.

This was especially damaging on bleed-through pages: a growth path could reach many reverse-side text fragments and those fragments then became opaque pixels in the asset.

### 2.4 Closure/interior fill was not actually present in the review assets

The architecture describes justified contour closure followed by exterior flood-fill. The review exporter did not render that layer; it feathered the grown foreground support directly.

As a result, paper enclosed by a valid illustration contour could remain transparent and show the green review background through the drawing. This is the opposite of the intended archival compositing behaviour.

### 2.5 Publication cleanup and archival preservation were conflated

One output cannot simultaneously satisfy both of these goals without making the policy explicit:

- preserve every source luminance sample inside an accepted region for audit;
- suppress unrelated reverse-side text/foreign ink for a clean publication asset.

The corrected probe therefore emits two derivatives from the same geometry:

- `*.source.png`: source-preserving grayscale + alpha;
- `*.clean.png`: observed figure support from the source, while accepted interior paper that is not figure support comes from a slow local paper-field estimate.

The second derivative is a publication preview, not a replacement for source evidence.

### 2.6 The first seed model contained an unjustified statistical assumption

The original committed probe converted robust per-feature deviations into normal-distribution z tails and derived a family-wise threshold from `NormalDist`.

The three transformed component features were neither shown to be Gaussian nor independent. That threshold therefore looked more rigorous than it was.

The revised probe uses a robust empirical page model (median/MAD plus an empirical extreme quantile) and keeps the threshold explicitly provisional until calibration against manual reference masks.

### 2.7 Documentation had outrun committed implementation

`FIGURE_PILOT_FINDINGS_v0.md` described page classification, text-likeness penalties, geodesic growth, region filtering, coalescing, closure and rendering experiments. At that moment, the committed `probe_figure_segmentation.py` still stopped at seed visualization.

Those findings came from local exploratory passes and were not reproducible from the branch. That gap is now closed by rebuilding the committed probe around the full experimental structure.

## 3. Corrected implementation invariants

The rebuilt probe now enforces these separations:

1. **Seed** — high-confidence evidence that a figure may exist.
2. **Growth ownership** — components reachable from seeds under bounded normalized cost.
3. **Grouped region / asset candidate** — overlapping/nested/nearby seed families coalesced before export.
4. **Renderable support** — only figure-like support is emitted; a node traversed by growth is not automatically rendered.
5. **Interior alpha** — justified closure/fill is a separate layer from observed ink.
6. **Asset-specific mask** — every asset owns a mask; the page-wide union is never cropped as a substitute.
7. **Source and clean derivatives** — evidence-preserving and publication-clean outputs are distinct.

## 4. Pilot re-run after the correction

The rebuilt probe was run against the same 32-page pilot used for the original review.

On that pilot:

- the two dark endpoint/full-tone pages are classified out of the ordinary text-page path;
- all eight caption-negative control pages produce zero accepted assets;
- every positive pilot page produces at least one accepted asset;
- accepted asset count again matches explicit caption count on the 24 positive pages;
- importantly, the previously reviewed orphan floor strokes and overlapping duplicate ladder/person regions are no longer emitted as separate assets;
- bleed-through text is no longer rendered wholesale merely because growth traversed it.

The count agreement remains only a coarse QA signal. It is now accompanied by a materially different asset construction path, but it is still **not segmentation accuracy**.

## 5. What is still not proved

The current numerical coefficients are research defaults, not fitted parameters. In particular, the following still require manual source-coordinate ground truth and held-out validation:

- missed weak-stroke rate;
- ordinary-text intrusion;
- reverse-side bleed-through suppression versus accidental weak-stroke loss;
- boundary error;
- false merge / false split;
- closure false-fill;
- stability of page-domain classification;
- stability of dimensionless thresholds across other books/resolutions.

No compatibility obligation exists toward the discarded exploratory behaviour. If a later model better satisfies the declared invariants, the research implementation should be replaced rather than protected as legacy.
