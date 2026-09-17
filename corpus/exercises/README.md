# Exercise Corpus — v1 pilot

This directory is the structured exercise layer of Corpus Motuum. It is intentionally separate from the 600-page book representation.

## Rule zero

Historical source evidence and Corpus Motuum interpretation are different data.

A source quotation, source title, source claim, modern normalized description, biomechanical inference and evidence-backed scientific claim must never silently collapse into one field.

## Stable identity

Exercise IDs use the neutral namespace:

```text
EX-000001
EX-000002
...
```

An ID is permanent. Editorial improvements, evidence changes, translations and new renders do not create a new identity unless the represented exercise itself changes.

## Pilot before scale

Do **not** bulk-convert the whole book yet.

The first schema freeze requires 10–20 deliberately different exercises that collectively cover:

- simple single-pattern movement;
- complex multi-joint movement;
- bilateral and unilateral work;
- open and closed kinetic chains;
- exercise with one historical illustration;
- exercise with several/compound source illustrations;
- apparatus/equipment exercise;
- body-weight exercise;
- an exercise whose historical wording requires editorial interpretation;
- an exercise with a plausible modern risk claim;
- an exercise where modern literature is mixed or contradictory;
- at least one exercise suitable for a complete four-view 3D anatomy render.

Each pilot must complete the vertical path:

```text
source page
→ canonical source text
→ Exercise v1 record
→ anatomy references
→ joint actions / phases
→ kinetic-chain graph
→ atomic evidence claims
→ provenance
→ VIEW_A / VIEW_B / VIEW_C / VIEW_D
```

Only after the pilot survives review do we freeze `Exercise v1` and scale extraction across the book.

## Canonical files

- `schemas/exercise-v1.schema.json` — machine contract for one exercise record.
- `docs/PROJECT_MINDMAP.md` — project direction and stage relationships.
- `corpus/exercises/pilot-v1.json` — pilot selection and completion ledger (added before records are mass-created).
- future records: `corpus/exercises/items/EX-000001.json`, etc.

## Evidence discipline

Evidence claims are atomic. Preserve supporting sources, contradicting sources and limitations together.

Allowed claim classes:

- `FACT`
- `SUPPORTED_INFERENCE`
- `HYPOTHESIS`
- `HISTORICAL_CLAIM`

EMG amplitude, force contribution, long-term adaptation and injury risk are separate concepts. One must not be silently substituted for another.

## Anatomy discipline

Exercise records reference canonical anatomy entities; they do not invent local free-text anatomy identities. Latin FIPAT / Terminologia Anatomica identity is primary, with Russian/English names as labels/aliases.

An exercise-anatomy relation is phase-aware and role-aware, e.g.:

```text
m. vastus lateralis — agonist — concentric — knee extension
```

not merely “quadriceps works”.

## 3D discipline

The four standard views are reproducible presentation outputs, not subjective screenshots:

- `VIEW_A` — front-right-superior
- `VIEW_B` — front-left-inferior
- `VIEW_C` — posterior-left-superior
- `VIEW_D` — posterior-right-inferior

Camera geometry is frozen before corpus-wide rendering. `visual_weight` controls display hierarchy only; it is not physiological activation.
