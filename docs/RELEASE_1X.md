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

## Later 1.x reading edition

A later reading layer may improve word order, awkward historical forms and punctuation for modern readers, but it must remain a separate derivative text layer. It must not replace either the diplomatic or normalized canonical layers.

## Repository separation

- `figures/model-v1` preserves the research process and figure-processing history, including failed/intermediate evidence;
- `release/1.x-foundation` assembles only verified release material;
- `main` receives only verified canonical material, release tooling and the generated book payload;
- release rendering is generated from canonical page records plus canonical figure assets; HTML/PDF/EPUB are outputs, not editorial sources of truth.
