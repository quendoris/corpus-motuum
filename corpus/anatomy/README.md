# Canonical anatomy

This directory will contain stable anatomy entities referenced by Exercise v1.

## Identity rule

Corpus Motuum uses its own immutable IDs:

```text
ANAT-000001
ANAT-000002
...
```

The ID survives terminology updates. The canonical Latin term and its external
terminology identifier are attributes, not the primary database identity.

The terminology source is recorded explicitly (for example a FIPAT
Terminologia Anatomica edition) rather than inferred from a familiar English or
Russian label.

## Exercise relations

Exercise records reference anatomy IDs and describe the relation separately:

```text
anatomy entity
+ movement phase
+ role (agonist / synergist / antagonist / stabilizer)
+ contraction class
+ joint action
+ evidence claim IDs
```

A display weight is only a rendering hierarchy. It is never an EMG percentage,
force contribution or physiological activation estimate.

Schema: `schemas/anatomy-entity-v1.schema.json`.
