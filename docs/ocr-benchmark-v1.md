# OCR benchmark v1

`benchmark/v1` is a held-out evaluation set for OCR and preprocessing choices. It is **not training data**. If Corpus Motuum later fine-tunes an OCR model, training pages must come from a disjoint set.

## Samples

The benchmark contains 12 physical-page samples from six ordinary source PDF spreads: sheets 2, 20, 60, 100, 169 and 260, left and right page from each spread. PDF sheet 169 contains printed pages 297 and 298 and is deliberately included as a difficult text sample.

Ground truth progresses through `pending -> draft -> reviewed -> verified`. Benchmark conclusions intended for publication should use only `verified` pages. During development, `reviewed` pages may be used for provisional comparisons provided their status is reported.

## Exact-image extraction

Do not benchmark against a rerender if an embedded source JPEG is available. The extractor uses Poppler `pdfimages -j` and refuses to guess when a selected spread does not contain exactly two 750x1200 embedded JPEG pages.

```bash
python tools/extract_benchmark_pages.py \
  sources/source-001/raw/original.pdf
```

Generated exact pages live in `work/benchmark-v1/exact/` and are ignored by Git.

## Preprocessing arena

```bash
python tools/prepare_benchmark_images.py
```

Current presets:

- `exact-jpeg` — unmodified embedded JPEG;
- `raw-gray` — grayscale only;
- `local-gray` — illumination normalization + conservative local contrast;
- `local-gray-150` — the same, enlarged 1.5x with cubic interpolation;
- `otsu-150` — global Otsu threshold after local normalization;
- `adaptive-150` — aggressive adaptive threshold.

The last two are intentionally retained even if they look worse to a human: benchmark results decide whether they help OCR.

## Environment on Arch Linux

```bash
sudo pacman -S --needed poppler tesseract tesseract-data-rus python
python -m venv .venv-benchmark
source .venv-benchmark/bin/activate
python -m pip install -r requirements-benchmark.txt
```

## Standard Russian baseline (`rus`)

Keep the result directory named after the model so it cannot be confused with the historical-model run:

```bash
python tools/ocr_benchmark.py \
  --langs rus \
  --psm 3 6 \
  --out work/benchmark-v1/runs-rus
```

Outputs are written to `work/benchmark-v1/runs-rus/`: `results.json`, aggregated `summary.csv`, and the OCR text/stderr of every run.

## Pre-reform `orus` model

The `orus` Tesseract model is maintained by the Iskra OCR project:
https://github.com/AButon-8/iskra_ocr

Keep third-party model files outside Git:

```bash
mkdir -p .cache/tessdata-orus
curl -L \
  https://raw.githubusercontent.com/AButon-8/iskra_ocr/main/orus.traineddata \
  -o .cache/tessdata-orus/orus.traineddata

python tools/ocr_benchmark.py \
  --langs orus \
  --psm 3 6 \
  --tessdata-dir .cache/tessdata-orus \
  --out work/benchmark-v1/runs-orus
```

Upstream model metrics are useful background only; they are not evidence of performance on this book. Corpus Motuum reports performance from this held-out benchmark.

## Metrics

The scorer records strict CER, content CER, content WER, substitutions/deletions/insertions, missing/inserted word boundaries, recall and false positives for `ѣ/Ѣ`, `і/І`, `ѳ/Ѳ`, `ѵ/Ѵ`, `ъ/Ъ`, wall-clock seconds per page, and a simple 600-page runtime projection.

`content CER` is the primary page-level OCR comparison. Strict CER remains useful for detecting layout/line-segmentation regressions.

## Ground-truth principle

`gt_ocr.txt` records what is printed, including prereform glyphs and printed line-end hyphenation. `normalized_text.txt` is a separate reader-facing layer. OCR is scored only against `gt_ocr.txt`.
