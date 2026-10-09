---
name: write-rubric
description: Create or convert a marking scheme into a valid marker rubric TOML (bands covering 0-100, weighted criteria with per-band descriptors). Use when the user supplies module marking criteria, asks for a rubric for a new assignment, or wants to change weights or descriptors.
---

# Write a rubric

Rubrics live in `rubrics/*.toml`. Start from
`rubrics/uk-undergraduate-essay.toml` and copy it; don't edit it in place if
results already depend on it.

## Structure

```toml
name = "PS2001 Critical review"
version = "1.0.0"          # bump on any change; recorded in every result
level = "Undergraduate year 2"
min_words = 1800            # 0 = no check; out-of-range essays are flagged, never penalised
max_words = 2200

[[bands]]                   # must tile 0-100 exactly: no gaps, no overlaps
id = "first"                # snake_case; the model answers with these ids
label = "First"
min = 70
max = 100
descriptor = "..."

[[criteria]]
id = "argument"             # snake_case JSON key
name = "Argument and critical analysis"
weight = 0.30               # weights must sum to 1.0
description = "What this criterion assesses."
[criteria.descriptors]      # optional per band; keys must be band ids
first = "..."
```

## Converting a department's scheme

1. Take the bands and boundaries exactly as the department publishes them.
   Keep extra bands (e.g. 80-89, 90-100) if the scheme has them; they help
   the model use the whole scale.
2. One criterion for each assessed dimension. Take the weights from the scheme.
   If the scheme is holistic (no weights), use equal weights and say so.
3. Descriptors should be **observable** ("counter-arguments weighed
   convincingly"), not vague ("good"). Each band's descriptor should differ
   from its neighbours in a way a marker can check. Include assignment-specific
   expectations (required theories, methods, sources) in `description`.
4. Validate: `python3 -m marker rubric --rubric rubrics/<file>.toml`.
5. Calibrate before live use with the `calibrate-marker` skill.

## Rules

- Don't put instructions for the model in descriptors, beyond describing the
  quality of work. The prompt is in `marker/prompt.py`.
- Any change to a rubric changes the prompt hash, so earlier results are no
  longer comparable. Bump `version`.
