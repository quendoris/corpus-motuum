# Provisional 2-page OCR comparison

Scope: printed pages 297-298 (`sheet-169-left/right`), ground truth status `reviewed`, not yet `verified`. These results are for development decisions only.

## Best general-text result per engine

| Engine | Configuration | Content CER | Content WER | Notes |
|---|---|---:|---:|---|
| Tesseract `rus` | `local-gray-150`, PSM 3 | 5.62% | 30.50% | Best general OCR so far; prereform `ѣ/і` essentially lost. |
| Tesseract `orus` | `local-gray-150`, PSM 3 | 12.35% | 38.42% | Worse general OCR; detects prereform glyphs better than `rus`. |
| Kraken 7.1 medium | `local-gray-150`, CPU `32-true` | 14.60% | 40.35% | Best Kraken CER, but substantially worse than `rus`; CPU timing includes model/segmentation startup. |

On these two pages the best Kraken CER is about 2.60x the best `rus` CER. `rus` therefore reduces character error by about 61.5% relative to Kraken's best configuration.

## Kraken-specific observations

Kraken's best CER preset is `local-gray-150` (14.60%), but its best WER is on `raw-gray` (33.78%). The raw/exact inputs preserve prereform glyphs better than the local-contrast/upscaled preset:

- `raw-gray`: `ѣ` recall 51.85%, `і` recall 85.71%, `ъ` recall 76.32%;
- `exact-jpeg`: `ѣ` recall 50.00%, `і` recall 85.71%, `ъ` recall 75.44%;
- `local-gray-150`: `ѣ` recall 16.67%, `і` recall 71.43%, `ъ` recall 78.95%.

This is evidence that the preprocessing optimum is task-dependent: the preset minimizing global CER is not the preset best preserving historical glyphs.

## Current engineering conclusion

Do not use Kraken medium as the primary OCR engine for this source without fine-tuning. Keep it as an independent secondary signal for prereform glyph candidates and as a potential fine-tuning base. Continue to use Tesseract `rus` as the current general-text baseline and `orus`/Kraken outputs as historical-glyph evidence until the full 12-page benchmark is verified.

Do not optimize Kraken GPU throughput yet. Accuracy is not competitive enough to justify engineering around the current CUDA segmentation device-placement failure. Revisit GPU segmentation/recognition separation only if fine-tuning or a stronger Kraken model materially improves accuracy.
