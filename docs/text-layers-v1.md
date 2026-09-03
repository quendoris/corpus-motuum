# Text Layers v1

This document defines the editorial boundary between the historical source and Corpus Motuum commentary.

## Principle

The historical text is not rewritten into contemporary prose. We normalize the pre-reform orthography for readability while preserving the author's vocabulary, phrasing, sequence of thought, and meaning. Material added by Corpus Motuum is written separately in contemporary Russian.

## Layers

### `facsimile_transcription`

A source-faithful transcription intended for audit and philological comparison.

Preserve:
- pre-reform letters and spelling;
- historical vocabulary;
- authorial wording and sentence structure;
- headings, numbering, references, and meaningful punctuation;
- uncertain readings as uncertainty rather than invention.

Allowed intervention:
- removal of OCR garbage that is not present in the source image;
- reconstruction of a word split only by a physical line break, when unambiguous;
- explicit uncertainty markers for unreadable or damaged characters.

### `normalized_text`

The primary readable historical-text layer.

Normalize orthography, not authorship:
- `ѣ` -> `е`;
- `і` -> `и`;
- `ѳ` -> `ф` where applicable;
- historical final hard signs are removed where required by modern orthography;
- mechanical line-break hyphenation is resolved;
- spacing and punctuation may be minimally regularized when needed for readability.

Do not:
- replace historical vocabulary merely because it sounds old;
- paraphrase the author's claims;
- modernize the author's style;
- silently correct a substantive claim;
- insert modern anatomical or biomechanical interpretation into the historical text.

Example:

Historical form:

> Повторить въ точности 13-е упражненіе на лошади стр. 180 увеличивая по желанію затрудненія.

Normalized form:

> Повторить в точности 13-е упражнение на лошади, стр. 180, увеличивая по желанию затруднения.

### `editorial_notes`

Corpus Motuum commentary written in contemporary Russian. This layer may contain:
- explanations of obsolete terms;
- modern anatomical interpretation;
- biomechanical analysis;
- evidence and citations;
- uncertainty notes;
- corrections or suspected errors in the historical source;
- safety/risk claims with explicit evidence status.

It must always remain distinguishable from the historical text.

## Image-processing boundary

Two derived image branches are maintained from the immutable source:

1. `archival` - conservative human-facing restoration. It may improve geometry, background, noise and legibility but must not invent letterforms or illustration detail.
2. `ocr` - machine-facing working images. Aggressive contrast, thresholding and other transformations are allowed because these files are not presented as historical facsimiles.

Neither branch may overwrite the immutable source.

## Provenance

Every derived page should retain enough information to trace it back to:
- the source PDF checksum;
- source PDF sheet;
- embedded image object(s), where applicable;
- printed page number, when identified;
- processing pipeline/version;
- human corrections applied to transcription or normalization.
