# Canonical text corpus

`pages/` stores one reviewed record per physical page. Each record keeps two separate text layers:

- `diplomatic_text`: wording and prereform orthography as printed, corrected against the source image rather than trusted OCR;
- `normalized_text`: orthographically normalized Russian while retaining the author's vocabulary, formulations, and archaic grammar where rewriting would change the voice.

Copy-specific marks such as library stamps, bleed-through, stains, and scan damage belong in `notes`, not in the book text. Decorative table-of-contents dot leaders are omitted while page references and printed dashes are retained. Visible source anomalies are preserved and noted rather than silently corrected.

Raw OCR under `corpus/ocr/raw/` is evidence only and is never canonical text.
