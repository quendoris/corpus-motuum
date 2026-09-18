# Research Graph

Research evidence is stored independently from exercise records.

Stable namespaces:

```text
EVSRC-000001   bibliographic / experimental source
CLM-000001     atomic claim
```

An Exercise v1 record links to claims. Claims link to supporting,
partially-supporting, contradicting or contextual sources.

This avoids copying the same paper and conclusion into many exercise JSON files
and allows the graph to change when evidence changes without changing exercise
identity.

## Claim classes

- `FACT` — directly supported by strong sources within the stated scope.
- `SUPPORTED_INFERENCE` — follows strongly from evidence but is not directly measured as stated.
- `HYPOTHESIS` — plausible explanation requiring stronger evidence.
- `HISTORICAL_CLAIM` — statement made by the historical source, irrespective of modern support.

Population, conditions and outcome are part of the claim. “Higher EMG”,
“greater force contribution”, “better long-term adaptation” and “higher injury
risk” are different claims and must never be substituted for one another.

Schemas:

- `schemas/evidence-source-v1.schema.json`
- `schemas/evidence-claim-v1.schema.json`
