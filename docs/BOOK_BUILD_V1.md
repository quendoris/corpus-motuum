# Book build v1

The first release artifact should be a faithful digital book assembled from canonical page records and canonical figure assets.

## Source layers

- text: `corpus/text/pages/*.json`;
- source/page provenance: `corpus/source/page-manifest.json`;
- figures: `corpus/figures/assets/v1/` once promoted from the validated figure pipeline.

## Build principle

The book structure is preserved. Rendering must not invent a new editorial hierarchy merely because a target format prefers one.

A neutral intermediate representation should carry, in reading order:

1. page/section boundaries and printed order;
2. headings and body paragraphs;
3. figure references and canonical figure IDs;
4. captions;
5. footnotes/notes that belong to the printed work;
6. source page IDs for traceability.

Copy-specific damage/stamps/bleed-through remain provenance/editorial metadata and are not inserted into the reading text.

## Editions

The same structure is rendered twice:

- `1.0`: `diplomatic_text`;
- `1.1`: `normalized_text`.

The figure set and structural map are identical between both editions.

## Outputs

HTML, PDF and EPUB may all be generated later from the same neutral book representation. None of them is the canonical editorial source.
