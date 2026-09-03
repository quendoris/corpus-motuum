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

## Tesseract environment on Arch Linux

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

## Kraken 7.1 / PP-OCRv6 medium

Kraken is tested as an independent historical-document OCR architecture, not as another Tesseract language file. Keep it in a separate virtual environment because it brings a large PyTorch dependency stack:

```bash
python -m venv .venv-kraken
source .venv-kraken/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-kraken.txt
```

The current comparison uses Kraken 7.1's multilingual PP-OCRv6 **medium** base model (15.92M parameters), Zenodo DOI `10.5281/zenodo.21788410`. Download it through Kraken's model manager:

```bash
kraken get 10.5281/zenodo.21788410
```

The downloaded recognition file is `medium.safetensors` and Kraken can resolve it from its local model store. On an NVIDIA Ampere GPU, run the two-page smoke test with CUDA and BF16 mixed precision:

```bash
python tools/kraken_benchmark.py \
  --model medium.safetensors \
  --device cuda:0 \
  --precision bf16-mixed \
  --only-samples sheet-169-left sheet-169-right \
  --out work/benchmark-v1/runs-kraken-medium-gpu

cat work/benchmark-v1/runs-kraken-medium-gpu/summary.csv
```

If CUDA/PyTorch setup is unavailable, the accuracy test can be run on CPU instead:

```bash
python tools/kraken_benchmark.py \
  --model medium.safetensors \
  --device cpu \
  --precision 32 \
  --only-samples sheet-169-left sheet-169-right \
  --out work/benchmark-v1/runs-kraken-medium-cpu
```

Kraken's 7.1 multilingual base model was trained/evaluated with Unicode NFD. The Kraken scorer converts both prediction and ground truth to canonical NFC before edit-distance scoring so canonically equivalent forms such as decomposed/composed Cyrillic letters are not counted as OCR errors.

The current Kraken runner invokes the CLI once per page/preset, so model-startup cost is included in its timing. Its `estimated_600_pages_minutes_naive` field is deliberately labelled naive and must **not** be compared directly to production Tesseract throughput. If Kraken survives the accuracy test, a batched throughput runner will be added next.

## Metrics

The scorer records strict CER, content CER, content WER, substitutions/deletions/insertions, missing/inserted word boundaries, recall and false positives for `ѣ/Ѣ`, `і/І`, `ѳ/Ѳ`, `ѵ/Ѵ`, `ъ/Ъ`, and wall-clock time.

`content CER` is the primary page-level OCR comparison. Strict CER remains useful for detecting layout/line-segmentation regressions.

## Ground-truth principle

`gt_ocr.txt` records what is printed, including prereform glyphs and printed line-end hyphenation. `normalized_text.txt` is a separate reader-facing layer. OCR is scored only against `gt_ocr.txt`.
