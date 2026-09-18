# Exercise v1 editorial ownership

Exercise records deliberately contain layers with different epistemic status.
They must not be edited as if every field described the same kind of fact.

## 1. Source-owned fields

Owned by deterministic extraction from the canonical book:

- `source.source_id`
- `source.page_ids / physical_pages / printed_pages`
- `source.figure_ids`
- `source.text.diplomatic`
- `source.text.normalized_orthography`
- `source.segments` with exact offsets and hashes
- `names.source`
- `names.normalized`
- `media.source_figures`
- source-page hashes in provenance

These fields are synchronized by tooling. A source correction may change them,
but editorial or scientific work must not.

Deliberately excluded page-layout material (for example a footnote belonging to
a preceding exercise) remains auditable as an excluded range with offsets,
hash, reason and replacement.

## 2. Editorial interpretation

Human-readable interpretation of what movement the historical instructions
describe:

- `names.modern`
- `description`
- `execution`
- `phases`
- source/historical `variants`
- editorial notes

This layer may resolve archaic wording, commands, page breaks and implied
sequence, but may not silently add modern coaching advice or scientific claims.

## 3. Structural movement model

- `body_position`
- `movement`
- `equipment`
- `kinetic_chain`
- `biomechanics`

These are Corpus Motuum models, not quotations. When a value is uncertain it is
left unknown or explained in an editorial note instead of guessed.

## 4. Canonical anatomy

`anatomy.relations` references `ANAT-*` entities. Free-text muscle identity is
not canonical anatomy. Role, phase, contraction and joint action are relations
between an exercise and an anatomy entity.

The anatomy terminology source is recorded separately from exercise evidence.

## 5. Research Graph

Exercise records link to atomic `CLM-*` claims. Claims link to `EVSRC-*`
sources and retain support, contradiction, conditions, population and
limitations.

Historical author statements may be captured first as source claims and later
promoted to `HISTORICAL_CLAIM` nodes. Promotion does not imply modern support.

## 6. Render layer

`media.renders` is derived output. Four standard camera views must be
reproducible. Visual highlighting represents a declared display category only;
it is not an EMG percentage or force estimate.

## Status

- `draft`: source may be locked but one or more derived layers are incomplete.
- `review`: all intended layers for the current milestone exist and await review.
- `verified`: the record passed source, schema, graph and render validation for
  the frozen schema version.
