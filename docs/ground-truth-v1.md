# Ground Truth v1

Corpus Motuum keeps OCR training truth separate from reader-facing normalization.

## Layers

### `gt_ocr`

`gt_ocr` is the transcription used to evaluate and, if needed, fine-tune OCR models.
It must describe what is actually printed, not what an editor thinks the text should say.

Rules:
- preserve prereform Russian glyphs (`ѣ`, `і`, `ѳ`, `ѵ`, final `ъ`, etc.);
- preserve printed punctuation and capitalization;
- preserve line-end hyphens when the page image contains them;
- preserve lexical and grammatical forms of the source;
- do not silently modernize spelling;
- do not silently repair suspected author/typesetter errors;
- exclude an uncertain line from training rather than inventing a character;
- record uncertainty separately from the canonical training string.

For OCR training, the preferred atomic unit is a text line paired with the exact line image (or a region and coordinates from which it can be reproduced).

### `normalized_text`

`normalized_text` is reader-facing. It keeps the author's vocabulary, wording, order and meaning while normalizing prereform spelling and purely typographic line breaks.

Allowed examples:
- `ѣ -> е` where required by modern orthography;
- `і -> и`;
- remove orthographic final hard signs;
- join words split only by a printed line-end hyphen;
- modernize spacing and minimally regularize punctuation when needed for readability;
- use modern spelling for the same lexical item (for example, a prereform spelling of a word), without replacing it with a new synonym.

Not allowed:
- paraphrasing;
- replacing historical vocabulary merely because it sounds old;
- changing the author's claim into a modern medical/biomechanical claim;
- mixing Corpus Motuum commentary into the source text.

### `editorial_notes`

Everything added by Corpus Motuum belongs here or in later structured data layers and is written in contemporary language.

## Benchmark metrics

At minimum:
- character error rate (CER);
- word error rate (WER);
- per-glyph accuracy for prereform characters;
- merged-word and split-word counts;
- dropped/inserted character counts.

Results must be reported against `gt_ocr`, not `normalized_text`.

## Status values

Recommended transcription states:
- `draft`;
- `reviewed`;
- `verified`;
- `excluded-uncertain` for material not safe to use as training truth.

A page may be `reviewed` for publication while still containing individual lines excluded from model training.
