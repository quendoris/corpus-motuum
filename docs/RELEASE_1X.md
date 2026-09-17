# Release series 1.x

The 1.x series is a digitization of the historical book, not a rewritten modern edition.

## 1.0 — diplomatic/prereform digitization

- preserve the printed wording and prereform orthography;
- preserve book order, headings, captions, footnotes and section structure;
- keep figure identities and their original logical placement;
- keep page provenance and source hashes;
- do not silently modernize language or repair authorial wording.

## 1.1 — normalized-orthography digitization

- use the same book structure and the same canonical figure set as 1.0;
- replace only the text layer with `normalized_text`;
- preserve vocabulary, formulations and archaic grammar where modernization would become rewriting;
- keep a deterministic page-by-page relation to 1.0.

## Publication invariants

Both published editions are built from the same 600 physical source pages and the same 168 canonical image assets: 158 numbered figures, six atlas plates and four paratext assets.

Publication requires all 168 assets to be linked to an exact Russian-edition source placement. No unreviewed fallback placement is allowed in a tagged release. Detector, logical-composition and manual-build provenance supply those coordinates wherever available. Restored donor-source figures use explicit Russian-placement overrides backed by preserved Russian evidence.

Figure 74 is the exceptional restoration case in which the original detector missed the engraving completely. Its page identity is `sheet-128-left` / physical 255. The preserved Russian QA page has source geometry 750×1200 and is rendered without aspect distortion at 280×448; registration of the restored 563×395 engraving against that Russian image gives thumbnail bbox `[18,313,131,392]`, deterministically mapping to source bbox `[48,838,351,1050]`. This closes the final placement gap without inventing geometry.

The fixed-layout PDF must contain exactly 600 pages and 168 image placements, retain canonical text on every page, and use landscape geometry only for the six terminal atlas leaves. Reflowable EPUB 3 and FB2 outputs are validated for page-anchor order, full text equivalence and the same 168-image inventory rather than pixel-identical pagination.

## Later 1.x reading edition

A later reading layer may improve word order, awkward historical forms and punctuation for modern readers, but it must remain a separate derivative text layer. It must not replace either the diplomatic or normalized canonical layers.

## Repository separation

- `figures/model-v1` preserves the research process and figure-processing history, including failed/intermediate evidence;
- `release/1.x-foundation` assembles only verified release material;
- `main` receives only verified canonical material, release tooling and the generated book payload;
- release rendering is generated from canonical page records plus canonical figure assets; HTML/PDF/EPUB are outputs, not editorial sources of truth.
