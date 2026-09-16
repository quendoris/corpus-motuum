# Figure research history

This directory is an archival research record, not the canonical book payload.

The authoritative historical snapshot for the first figure pass is `figure-review-v1/`. It is imported byte-for-byte from the local `work/figure-review-v1/` directory and intentionally keeps both accepted and rejected evidence. In particular, `removed.json` and the files it describes are part of the history rather than disposable build output.

## Policy

- `figures/model-v1` is the research/history branch. Failed crops, rejected candidates, atlas experiments and paratext decisions are legitimate historical evidence.
- `history/figures/figure-review-v1/` mirrors the complete review snapshot: `atlas/`, `numbered/`, `paratext/`, `manifest.json`, and `removed.json`.
- The original files are not rewritten, recompressed, renamed or regenerated during import. `snapshot-manifest.json` is added alongside them to record SHA-256, byte size and storage class.
- Git LFS is selected by **individual file size**, not by a blanket image extension rule. The importer defaults to 5 MiB: larger files receive exact-path LFS rules, smaller files stay as ordinary Git objects.
- Canonical release assets live separately under `corpus/figures/assets/v1/` and are generated from the reproducible pipeline. They are promoted to `main` only after validation.
- `main` must contain the finished book, reproducible tooling and release metadata, not the research workspace.
- Historical commits and rejected evidence are not rewritten merely to make the process look cleaner.

## Importing the historical snapshot

From a checkout where `work/figure-review-v1/` exists:

```bash
python tools/import_figure_review_history.py --track-lfs

git add .gitattributes history/figures/figure-review-v1
git status --short
git commit -m "history: preserve figure review v1"
git push origin figures/model-v1
```

The importer refuses an incomplete snapshot if any of `atlas/`, `numbered/`, `paratext/`, `manifest.json`, or `removed.json` is absent.

## Reproducible current output

The current detector, curation rules, logical compositions, manual/restored declarations and source hashes remain versioned as code/configuration. CI rebuilds the canonical figure set from those inputs and materializes only the final verified payload under `corpus/figures/assets/v1/`; it does not create a new giant historical snapshot on every run.
