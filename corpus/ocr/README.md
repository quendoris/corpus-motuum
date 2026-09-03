# Raw OCR corpus

This directory stores **machine-generated candidate transcriptions**, not canonical book text.

Each engine writes one UTF-8 file per physical page using the stable page ID from
`corpus/source/page-manifest.json`:

```text
corpus/ocr/raw/
  rus/
    sheet-001-left.txt
    sheet-001-right.txt
    ...
    run.json
  orus/
    ...
  kraken/
    ...
```

The three candidate layers deliberately preserve different error profiles:

- `rus`: Tesseract Russian model, `local-gray-150`, PSM 3. Best general-text baseline in benchmark v1.
- `orus`: prereform Tesseract model, `local-gray-150`, PSM 3. Secondary prereform-aware candidate.
- `kraken`: Kraken 7.1 PP-OCRv6 medium, `raw-gray`. Complementary historical-glyph candidate.

None of these files may be treated as an authoritative transcription. Canonical diplomatic
transcription and reader-facing orthographic normalization are separate future layers.

`benchmark/v1` remains held out from any future OCR fine-tuning. Full-book OCR may include
those pages for editorial comparison, but benchmark GT must not be copied into training data.
