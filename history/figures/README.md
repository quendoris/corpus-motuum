# Figure research history

This directory is an archival research record, not the canonical book payload.

The project intentionally preserves successful, failed and intermediate figure-processing outputs so that the evolution of detection, cropping, restoration, donor/reference selection and manual composition remains auditable. A bad crop or a rejected candidate is evidence and must not be silently rewritten out of history.

## Policy

- `figures/model-v1` is the research/history branch. It may contain detector mistakes, rejected candidates, debug masks, overlays, restoration attempts and comparison material.
- Large raster/debug/reference files under `history/figures/**` are stored through Git LFS.
- Text manifests, metrics, notes and provenance stay as ordinary Git files whenever practical.
- Canonical release assets live separately under `corpus/figures/assets/v1/` and are promoted to `main` only after validation.
- `main` must not become a dump of the research workspace: only verified book material, reproducible tooling and release metadata are promoted there.
- Existing historical commits are not rewritten merely to make the process look cleaner.

## Pipeline snapshot

The current reproducible snapshot is materialized from the finalization workflow into `history/figures/pipeline-v1/` with stage boundaries preserved:

- `structure/` — detector output and geometry/debug evidence;
- `review/` — curated detector review set;
- `logical/` — declared joins/splits/compositions;
- `manual/` — manual and donor/reference restorations;
- `final/` — assembled final figure set;
- `reference/` — external reference material used for restoration, with checksums/provenance.

A generated `manifest.json` records file sizes and SHA-256 hashes for the frozen snapshot.

Earlier pilot archives such as `figure-pilot-v0` and `figure-structure-probe-v2` should be imported here unchanged when their original bytes are available, rather than regenerated and presented as if they were the originals.
