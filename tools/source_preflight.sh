#!/usr/bin/env bash
set -euo pipefail

SOURCE="${1:-sources/source-001/raw/original.pdf}"
OUT="${2:-sources/source-001/audit}"
SHA_FILE="${SOURCE}.sha256"

mkdir -p "$OUT"

{
  echo "Corpus Motuum source preflight"
  echo "source: $SOURCE"
  echo "generated_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  echo "== file =="
  file "$SOURCE"
  stat --printf='size_bytes: %s\n' "$SOURCE"
  echo
  echo "== sha256 =="
  sha256sum "$SOURCE"
} > "$OUT/summary.txt"

if [[ -f "$SHA_FILE" ]]; then
  sha256sum -c "$SHA_FILE" > "$OUT/sha256-check.txt"
fi

pdfinfo "$SOURCE" > "$OUT/pdfinfo.txt" 2>&1
qpdf --check "$SOURCE" > "$OUT/qpdf-check.txt" 2>&1 || true
pdfimages -list "$SOURCE" > "$OUT/pdfimages-list.txt" 2>&1 || true
pdffonts "$SOURCE" > "$OUT/pdffonts.txt" 2>&1 || true

# Probe existing text without OCR. This deliberately does not modify the source.
pdftotext -layout "$SOURCE" "$OUT/existing-text.txt" 2> "$OUT/pdftotext-stderr.txt" || true

python3 - "$OUT/existing-text.txt" "$OUT/text-layer-stats.txt" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
out = Path(sys.argv[2])
text = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
non_ws = sum(not ch.isspace() for ch in text)
letters = sum(ch.isalpha() for ch in text)
replacement = text.count("\ufffd")
lines = text.count("\n") + (1 if text else 0)

with out.open("w", encoding="utf-8") as f:
    f.write(f"characters_total: {len(text)}\n")
    f.write(f"characters_non_whitespace: {non_ws}\n")
    f.write(f"letters: {letters}\n")
    f.write(f"replacement_characters: {replacement}\n")
    f.write(f"lines: {lines}\n")
PY

# Keep a short probe in Git so the current text layer can be inspected without
# storing a second full transcription at this stage.
python3 - "$OUT/existing-text.txt" "$OUT/existing-text-sample.txt" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
text = src.read_text(encoding="utf-8", errors="replace") if src.exists() else ""
dst.write_text(text[:40000], encoding="utf-8")
PY

# The full pdftotext dump is a disposable diagnostic, not a canonical source.
rm -f "$OUT/existing-text.txt"

echo "Preflight written to $OUT"
