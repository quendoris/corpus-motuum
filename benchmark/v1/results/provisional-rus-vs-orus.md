# Provisional OCR comparison: `rus` vs `orus`

Status: **development-only**. These results are based on two reviewed (not yet verified) ground-truth pages: printed pages 297 and 298 / benchmark samples `sheet-169-left` and `sheet-169-right`.

## Best content-CER configuration per model

| model | preset | psm | content CER | content WER | sec/page | yat recall | decimal-i recall | hard-sign recall |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `rus` | `local-gray-150` | 3 | 0.056243 | 0.305019 | 1.398 | 0.0000 | 0.0000 | 0.8509 |
| `orus` | `local-gray-150` | 3 | 0.123453 | 0.384170 | 1.607 | 0.3704 | 0.6429 | 0.7456 |

On these two pages, the specialist `orus` model is about 2.20x worse in total content CER than the standard `rus` model under the same best preprocessing/PSM combination. It is also worse in WER, while being slightly slower.

However, `orus` recovers historical glyphs that `rus` largely collapses into modern equivalents. This makes it potentially useful as a specialist signal rather than as the sole transcription engine.

## Notable historical-glyph result

- `rus`, best overall pipeline: `ѣ` recall 0%, `і` recall 0%, final `ъ` recall ~85.1%.
- `orus`, best overall pipeline: `ѣ` recall ~37.0%, `і` recall ~64.3%, final `ъ` recall ~74.6%.
- The `orus` configuration with highest observed `ѣ` recall is `adaptive-150 / PSM 3` at ~44.4%, but its total CER is much worse (13.33%).
- Highest observed `і` recall is ~67.9% (`exact-jpeg` or `raw-gray`, PSM 6), again without a corresponding overall OCR win.

## Current interpretation

Do not replace the `rus` baseline with `orus`. The next experiments should test whether a stronger historical OCR model can improve both general recognition and prereform glyph retention. A hybrid approach may later use a strong general OCR transcript as the base and a historical specialist to flag/restore ambiguous prereform glyph positions, but such fusion should only be evaluated after the full 12-page benchmark ground truth is verified.

Raw provisional summaries are stored beside this file.
