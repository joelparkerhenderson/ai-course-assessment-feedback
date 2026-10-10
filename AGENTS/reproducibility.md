# Reproducibility

Goal: anyone with the same inputs, model weights and Ollama build can rerun a
batch and get the same result, and anyone can tell *exactly* which inputs
produced a result.

## What is recorded (`provenance` in every result)

`marker_version`, `prompt_version`, `prompt_sha256` (of the exact messages),
`schema_sha256`, rubric name, version and sha256, `brief_sha256`, essay
`sha256`, `model`, `model_digest` (content hash of the weights), `ollama_version`,
every `Settings` field, `fingerprint`, Python version, platform, and a UTC timestamp.

## Determinism rules

- Sample *i* uses seed `seed + i`. Retry *n* uses `seed + i + 1000 * n`.
- The prompt is a pure function of the rubric, brief and essay: no dates, no
  randomness, no dict-order dependence (`sha256_of` sorts keys).
- Aggregation is deterministic: median, weighted sum, then round half up.
- `compute_fingerprint` covers every input that affects marks. If you add a
  setting that affects marks, it must be included (it is automatically, if
  it is a `Settings` field). If it affects only storage, exclude it as
  `keep_reasoning` is excluded.
- When the prompt or schema wording changes, bump `PROMPT_VERSION`.
- When the deterministic analysis changes (`grader.analyse`: aggregation,
  quote matching, flags), bump `ANALYSIS_VERSION`. Saved results are then
  re-analysed from their stored samples, without calling the model.

## Known limits

Bit-exact output isn't guaranteed across different hardware, GPU drivers or
Ollama versions, because floating-point kernels differ. That is why
`model_digest` and `ollama_version` are recorded. Say so honestly in any
documentation; don't claim stronger guarantees.

**MLX builds are not seed-deterministic** (observed with Ollama 0.35.1 on
2026-10-09): `muse-glimmer:30b-mlx` returned different text for identical
requests with the same seed, and the same essay scored 53, then 50.
`mistral-small` (GGUF) was identical across runs. `marker grade` warns when it
uses an MLX build. Re-test this when Ollama is upgraded.
