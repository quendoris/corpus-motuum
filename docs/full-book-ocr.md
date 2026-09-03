# Full-book OCR pipeline

The benchmark phase is complete enough to choose three complementary candidate layers. The goal of this pipeline is **not** to produce canonical text automatically. It produces three page-aligned OCR candidates for later editorial fusion against the source images.

## Chosen candidates

| Layer | Input | Settings | Role |
|---|---|---|---|
| `rus` | `local-gray-150` | Tesseract `rus`, PSM 3 | strongest general Russian text baseline |
| `orus` | `local-gray-150` | Tesseract `orus`, PSM 3 | prereform-aware secondary candidate |
| `kraken` | `raw-gray` | Kraken 7.1 PP-OCRv6 medium | complementary historical-glyph candidate |

Benchmark v1 remains held out from future OCR fine-tuning. It is still OCRed here because the editorial corpus needs complete page coverage.

## 1. Update and obtain the source

```bash
cd ~/Projects/corpus-motuum
git pull --ff-only
git lfs pull
```

The source PDF is expected at:

```text
sources/source-001/raw/original.pdf
```

## 2. Extract all 600 physical pages

Use the Tesseract/benchmark environment because the extractor uses OpenCV only for exceptional PDF sheets whose embedded image layout is not the ordinary two-JPEG spread.

```bash
source .venv-benchmark/bin/activate

python tools/extract_book_pages.py \
  sources/source-001/raw/original.pdf
```

Expected final line:

```text
Physical pages: 600
```

Ordinary spreads are copied from the embedded `750x1200` JPEG bitstreams. Exceptional sheets are rendered at 300 dpi and split at the spread midpoint; their sheet numbers are recorded in `fallback_sheets`.

Tracked provenance is written to:

```text
corpus/source/page-manifest.json
```

Working page images stay under ignored `work/book-v1/pages/`.

Sanity check:

```bash
python - <<'PY'
import json
p = json.load(open('corpus/source/page-manifest.json'))
print('sheets:', p['source_pdf_sheets'])
print('physical pages:', p['physical_pages'])
print('fallback sheets:', p['fallback_sheets'])
print('held-out benchmark pages:', sum(x['held_out_benchmark_v1'] for x in p['records']))
PY
```

## 3. Prepare the two selected OCR views

```bash
python tools/prepare_book_ocr_inputs.py
```

This creates only:

```text
work/book-v1/inputs/raw-gray/
work/book-v1/inputs/local-gray-150/
```

No six-preset full-book matrix is generated.

## 4. Run Tesseract `rus`

```bash
python tools/run_book_tesseract.py \
  --model rus \
  --preset local-gray-150 \
  --psm 3 \
  --out corpus/ocr/raw/rus
```

The runner is resumable: existing page text files are skipped by default.

## 5. Run Tesseract `orus`

Ensure the historical model is still available:

```bash
mkdir -p .cache/tessdata-orus
curl -L \
  https://raw.githubusercontent.com/AButon-8/iskra_ocr/main/orus.traineddata \
  -o .cache/tessdata-orus/orus.traineddata
```

Then:

```bash
python tools/run_book_tesseract.py \
  --model orus \
  --preset local-gray-150 \
  --psm 3 \
  --tessdata-dir .cache/tessdata-orus \
  --out corpus/ocr/raw/orus
```

## 6. Run Kraken medium in page batches

Activate the Python 3.13 Kraken environment:

```bash
deactivate 2>/dev/null || true
source .venv-kraken/bin/activate
```

The tested CUDA baseline segmenter on the RTX 3070 environment mixed CPU and CUDA tensors, so the full-book accuracy run deliberately uses CPU. The runner supplies repeated Kraken `-i input output` pairs per invocation to amortize model startup across many pages.

```bash
python tools/run_book_kraken.py \
  --model medium.safetensors \
  --device cpu \
  --precision 32-true \
  --preset raw-gray \
  --batch-pages 24 \
  --line-batch-size 64 \
  --line-workers 4 \
  --out corpus/ocr/raw/kraken
```

Successful page files are retained if a batch fails. Re-running the same command resumes missing pages.

## 7. Verify complete coverage

Either environment can run this checker:

```bash
python tools/check_full_book_ocr.py
```

Success means:

```text
Expected physical pages: 600
rus: present=600/600 ...
orus: present=600/600 ...
kraken: present=600/600 ...
All requested OCR layers are complete.
```

## 8. Commit only the reproducible text/provenance layer

Do **not** add `work/`; it is intentionally ignored.

```bash
git status

git add \
  corpus/source/page-manifest.json \
  corpus/ocr/raw/rus \
  corpus/ocr/raw/orus \
  corpus/ocr/raw/kraken

git commit -m "data: add full-book three-engine OCR candidates"
git push origin main
```

After this push, every physical page has three independently generated text candidates plus a stable source-page record.

## 9. Export editorial batches for canonical transcription

Source JPEGs remain local under `work/`, while the three OCR layers live in Git. Package a manageable contiguous range with all evidence attached:

```bash
python tools/export_editorial_batch.py --start 1 --count 20
```

This creates, for example:

```text
work/editorial-batches/editorial-0001-0020.tar.gz
```

The archive contains:

```text
pages/          exact/fallback source page images
ocr/rus/        general OCR candidate
ocr/orus/       prereform-aware OCR candidate
ocr/kraken/     complementary OCR candidate
manifest.json   page provenance and stable IDs
```

Editorial work then produces two separate canonical layers: a diplomatic transcription faithful to the printed source and an orthographically normalized reader-facing text that preserves the author's vocabulary and formulations. Those layers must never be silently collapsed into raw OCR.
