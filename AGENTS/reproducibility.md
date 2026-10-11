# Reproducibility

Goal: anyone with the same inputs, model weights and Ollama build can rerun a
batch and get the same result, and anyone can tell *exactly* which inputs
produced a result.

## What is recorded (`provenance` in every result)

`marker_version`, `prompt_version`, `analysis_version`, `prompt_sha256` (of
the exact messages), `schema_sha256`, rubric name, version and sha256,
`brief_sha256`, essay `sha256`, `model`, `model_digest` (content hash of the
weights), `ollama_version`, every `Settings` field, `fingerprint`, Python
version, platform, `marked_at` (UTC), and `reanalysed_at` after a re-analysis.
Each sample also records its seed, attempt number, time, token counts, any
errors from earlier attempts, and the raw model output.

## Determinism rules

- Sample *i* uses seed `seed + i`. Retry *n* of that sample uses
  `seed + i + 1000 * n`.
- The prompt is a pure function of the rubric, brief and essay: no dates, no
  randomness, no dict-order dependence (`sha256_of` sorts keys).
- Aggregation is deterministic: median per criterion, then the weighted sum
  (rounded half up to 4 places in decimal mode only). The default float mode
  is deterministic in Python on IEEE 754 hardware. `--numeric decimal` is
  exact and byte-identical across platforms. The mode is recorded in each
  result (`"numeric"`).
- The numeric mode must never change the prompt: band bounds are always
  formatted to 4 places (`fmt_mark`). A test enforces this.
- `compute_fingerprint` hashes the essay, the exact messages, the schema, the
  model digest and `Settings.fingerprint_dict()`. That includes every
  `Settings` field except `keep_reasoning` (storage only) and optional options
  that are unset (`OPTIONAL_FIELDS`), so adding a new optional option doesn't
  invalidate existing results. A new field that affects marks is included
  automatically; a new optional one belongs in `OPTIONAL_OPTIONS`.
- When the prompt or schema wording changes, bump `PROMPT_VERSION` in
  `prompt.py`. Every fingerprint changes, so every essay is re-marked.
- When the deterministic analysis changes (`grader.analyse`: aggregation,
  quote matching, flags, flag text), bump `ANALYSIS_VERSION`. Saved results
  are then re-analysed from their stored samples without calling the model.
  Results older than `MIN_COMPATIBLE_ANALYSIS` (4, the switch from integer
  0-100 marks to fractions) can't be re-analysed; they are re-marked by
  `grade` and skipped by `compare` and `show`.
- A change of reporting scale or numeric mode also triggers re-analysis,
  never a model call.

## Known limits

Bit-exact output isn't guaranteed across different hardware, GPU drivers or
Ollama versions, because floating-point kernels differ. That is why
`model_digest` and `ollama_version` are recorded. Say so honestly in any
documentation; don't claim stronger guarantees.

**GGUF builds reproduce exactly** on the same machine and Ollama version
(verified 2026-10-10, Ollama 0.35.1): the README's four-model example was run
twice into empty output directories, and all 8 raw answers from
`mistral-small3.2`, `gpt-oss:20b`, `qwen3.8:27b` and `muse-glimmer:30b` were
byte-identical.

**MLX builds are not deterministic** (Ollama 0.35.1). `qwen3.8:27b-mlx` gave
different text for two identical requests even at temperature 0 (so the GPU
arithmetic varies), and with sampling and a fixed seed the outputs diverged
almost immediately (so the seed is very likely not applied either). The same
essay once scored 53, then 50, with `muse-glimmer:30b-mlx`. `marker grade`
warns whenever it uses an MLX build. Re-test this when Ollama is upgraded.
