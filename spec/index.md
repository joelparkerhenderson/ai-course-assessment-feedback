# Specification: marker

## Purpose

This project evaluates student essays and marks them with a numeric grade:
0-100 by default, or any scale such as a 0.0000-1.0000 probability. Internally,
marks are floats from 0.0 to 1.0 by default.

Original brief: *"Research how to mark student papers. I want to use ChatGPT-OSS
because I want a local model and I want security and reproducibility."*

The research is in [`docs/research.md`](../docs/research.md). This
specification records the requirements that came out of it. Requirement ids
(e.g. `MARK-3`) are stable; refer to them in code reviews and changes.

## Constraints

| Id | Requirement |
|----|-------------|
| CON-1 | The default model is **gpt-oss** (`gpt-oss:20b`; `gpt-oss:120b` optional), run **locally** through Ollama. Any other local Ollama model may be selected (see MODEL-*). |
| CON-2 | **Security:** essay text must never leave the machine. Only loopback model servers are allowed unless the operator explicitly opts in (`--allow-remote`). |
| CON-3 | **Security:** no third-party runtime dependencies (Python ≥ 3.11 standard library only). |
| CON-4 | **Reproducibility:** fixed seeds; every result records the model content digest, Ollama version, settings, and SHA-256 of the prompt, schema, rubric, brief and essay. |
| CON-5 | Marks are **provisional**: a human marker confirms every released mark. |
| CON-6 | **Numeric type:** marks are Python `float` by default. Exact `Decimal` is available with `--numeric decimal`. Rounding is always half up. JSON writes floats as numbers and Decimals as exact strings. |

## Inputs

| Id | Requirement |
|----|-------------|
| IN-1 | Essays as `.txt`, `.md` or `.docx`, given as files or directories (directories skip README and hidden files). The essay id is the file name without its extension. |
| IN-2 | HTML comments in `.md` files are provenance metadata and are not marked. |
| IN-3 | An optional assignment brief (plain text) is marked against. |
| IN-4 | A rubric TOML: bands that tile 0.0000-1.0000 exactly, written to 4 decimal places (each band's max is 0.0001 below the next band's min), and criteria with positive weights summing to exactly 1 and optional per-band descriptors. Values are read in the numeric mode, and validation uses the exact written values. Optional word limits. |

## Model selection

| Id | Requirement |
|----|-------------|
| MODEL-1 | Users can choose one or more models by exact tag (`--model`, repeatable or comma-separated). |
| MODEL-2 | Users can choose models by family (`--family`), matched loosely: case, spaces, hyphens and underscores are ignored, and aliases are accepted (e.g. "ChatGPT-OSS" means `gpt-oss`, "Muse Glimmer" means `muse-glimmer`). |
| MODEL-3 | Users can choose model sizes (`--size`, e.g. `20b,120b`), matched against the tag size or, failing that, the reported parameter count (±10%). |
| MODEL-4 | Users can choose Mac-optimised MLX builds (`--mlx auto/prefer/only/never/any`). `auto` prefers MLX on Apple Silicon and excludes it elsewhere. Exact tags are never filtered. |
| MODEL-5 | `marker models` lists installed models with family, size, quantisation and build, and marks the selection. `marker check` reports each selected model as usable or not. |
| MODEL-6 | The reasoning setting is per model: an effort level for gpt-oss, on for other thinking-capable models, and unset otherwise. The effective value is recorded in provenance. |

## Scores and precision

| Id | Requirement |
|----|-------------|
| SCORE-1 | The default internal representation of every mark (rubric bands, model answers, sample marks, medians, weighted mark) is a Python **float from 0.0 to 1.0**. |
| SCORE-1a | `--numeric decimal` switches the internal representation to exact `Decimal` from 0.0000 to 1.0000, with exactly 4 decimal places. The mode is recorded in each result (`"numeric"`). Switching mode re-analyses stored samples without calling the model. |
| SCORE-2 | The model answers with fractional marks 0.0-1.0 (asked for with 4 places). Its JSON is parsed in the numeric mode (`parse_float=float` or `Decimal`). In decimal mode, marks with more places are rounded half up to 4 and noted. Out-of-range marks (e.g. `65`) are rejected and retried in both modes. The prompt is identical in both modes. |
| SCORE-2a | Band lookup is continuous: a mark belongs to the highest band whose minimum is at or below it, so floats between written bounds (e.g. 0.79995) are classified. |
| SCORE-3 | Users choose the reporting scale with `--score-min` and `--score-max`, as exact decimals (default 0 and 100), and `--score-places` (default: inferred from the bounds as written, so `1.0000` gives 4). |
| SCORE-4 | score = min + weighted_mark × (max − min), rounded half up to the scale's places. It is stored with its scale in each result (`score: {value, min, max, places}`). |
| SCORE-5 | Changing the reporting scale never calls the model: saved results are re-analysed from their stored samples. `show` can display any scale on the fly. |
| SCORE-6 | `evaluate` accepts machine and human scores on any scale (`--score-min`/`--score-max`). MAE, RMSE and bias are in that scale's units; bands use the 0-1 fraction; QWK uses whole percentage points from exact integer counts. Statistics use the numeric mode. |
| SCORE-7 | Results saved with integer 0-100 marks (analysis version < 4) are recognised as outdated: `grade` re-marks them, and `compare` and `show` skip them with a message. |

## Marking

| Id | Requirement |
|----|-------------|
| MARK-1 | Analytic, criterion-referenced marking: the model judges each criterion against band descriptors. |
| MARK-2 | For each criterion the model returns, in this order: justification, verbatim evidence quotes, band, and a decimal mark (0.0000-1.0000) inside the band. If the mark falls outside the band, it is clamped into the band. |
| MARK-3 | N independent samples (default 3) with seeds `seed + i`. The exact median per criterion is used. |
| MARK-4 | The overall mark is the exact weighted mean of the criterion medians, **computed in code**, rounded half up to 4 places, and mapped to a band. |
| MARK-5 | Invalid model output is retried with deterministic derived seeds (`seed + 1000·attempt`), up to `max_retries`. |
| MARK-6 | The essay is treated as untrusted data: delimited, unable to close its own delimiter, and the model is told never to follow instructions in it. |
| MARK-7 | Feedback for the student (strengths, improvements) is taken from the most representative sample. |

## Review flags (`needs_review`)

| Id | Flag raised when |
|----|-----------------|
| FLAG-1 | A criterion's sample marks spread by more than the threshold (`--disagreement`, default 0.1000). |
| FLAG-2 | The final mark is within the margin below a classification boundary (`--boundary-margin`, default 0.0200). |
| FLAG-3 | An evidence quote isn't found in the essay, comparing letters and digits only (case, punctuation, spacing, and Unicode dash and quote variants are ignored), with ellipses splitting the quote into fragments. |
| FLAG-4 | The word count is outside the rubric's limits (flag only; no penalty). |
| FLAG-5 | The essay contains injection-like text (heuristic detector, independent of the model). |
| FLAG-6 | The model raises integrity concerns. |
| FLAG-7 | The model gave a mark outside its chosen band (clamped). |

## Outputs

| Id | Requirement |
|----|-------------|
| OUT-1 | A per-essay JSON file with mark, band, criteria, feedback, flags, all samples and provenance, written atomically. |
| OUT-2 | `summary.csv`, rebuilt from all results in the output directory. |
| OUT-3 | `audit.jsonl`, append-only, containing hashes and marks but no essay text. |
| OUT-4 | Rerunning with an unchanged input fingerprint skips the essay unless `--force` is given. If the analysis rules have changed (`analysis_version`), the stored samples are re-analysed without calling the model, and the event is logged as `reanalysed`. |

## Multi-model comparison

| Id | Requirement |
|----|-------------|
| CMP-1 | `grade` with more than one model marks every essay with every model (one model at a time), writing to `--out/<model>/`. |
| CMP-2 | It then compares the models: per essay, each model's overall, band and criterion marks, the spread, a panel mark (exact median of the models' marks, rounded half up to 4 places), a panel score on the shared reporting scale, and whether the models agree (same band and spread ≤ threshold, default 0.1000). |
| CMP-3 | Pairwise agreement for each pair of models: n, mean difference, MAE, exact band agreement, QWK (2+ essays) and per-criterion MAE. |
| CMP-4 | The comparison is printed and saved as `comparison.csv` and `comparison.json`. `marker compare` recomputes it from existing result directories. |
| CMP-5 | The panel score can be validated against human marks (`evaluate --results-mark-column panel_score`). |

## Output views

| Id | Requirement |
|----|-------------|
| SHOW-1 | `--show` on `grade` and `show` selects any combination of `score` (id, score on the reporting scale, band), `explanation` (per-criterion justification, evidence, feedback, flags) and `thinking` (the model's reasoning trace); `all` selects every view. The default is `score`. |
| SHOW-2 | Requesting `thinking` during `grade` stores the reasoning trace. A cached result without one is re-marked. |
| SHOW-3 | `marker show` prints the views from saved results without calling a model. |

## Validation

| Id | Requirement |
|----|-------------|
| VAL-1 | `marker evaluate` compares machine marks with human marks: QWK (marks and bands), MAE, RMSE, mean bias, Pearson r, and exact/adjacent band agreement. |
| VAL-2 | Calibrate before live use, and re-calibrate after any change to the model, rubric or prompt. |

## Deliverables

- `marker/`: the Python package and CLI (`models`, `check`, `rubric`, `grade`, `compare`, `show`, `evaluate`), fully type-annotated and documented.
- `rubrics/uk-undergraduate-essay.toml`: the default UK undergraduate rubric.
- `docs/research.md`: research on marking practice and LLM marking.
- `assets/example-papers/`: example essays for end-to-end testing.
- `skills/`: AI agent skills for operating the tool.
- `AGENTS.md`, `AGENTS/`: guidance for AI coding agents.
- `tests/`: offline unit tests.
- `README.md` and `LICENSE.md` (GPL-3.0).

## Out of scope (for now)

PDF input, images and figures, plagiarism detection, LMS integration, and
publishing marks to students.
