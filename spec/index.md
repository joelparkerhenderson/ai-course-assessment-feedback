# Specification: marker

## Purpose

This project evaluates student essays and marks them with a numeric grade 0-100.

Original brief: *"Research how to mark student papers. I want to use ChatGPT-OSS
because I want a local model and I want security and reproducibility."*

The research is in [`docs/research.md`](../docs/research.md). This
specification records the requirements that came out of it. Requirement ids
(e.g. `MARK-3`) are stable; refer to them in code reviews and changes.

## Constraints

| Id | Requirement |
|----|-------------|
| CON-1 | The model is **gpt-oss** (`gpt-oss:20b` by default, `gpt-oss:120b` optional), run **locally** through Ollama. |
| CON-2 | **Security:** essay text must never leave the machine. Only loopback model servers are allowed unless the operator explicitly opts in (`--allow-remote`). |
| CON-3 | **Security:** no third-party runtime dependencies (Python ≥ 3.11 standard library only). |
| CON-4 | **Reproducibility:** fixed seeds; every result records the model content digest, Ollama version, settings, and SHA-256 of the prompt, schema, rubric, brief and essay. |
| CON-5 | Marks are **provisional**: a human marker confirms every released mark. |

## Inputs

| Id | Requirement |
|----|-------------|
| IN-1 | Essays as `.txt`, `.md` or `.docx`, given as files or directories. The essay id is the file name without its extension. |
| IN-2 | HTML comments in `.md` files are provenance metadata and are not marked. |
| IN-3 | An optional assignment brief (plain text) is marked against. |
| IN-4 | A rubric TOML: bands that tile 0-100 exactly, and criteria with positive weights summing to 1.0 and optional per-band descriptors. Optional word limits. |

## Marking

| Id | Requirement |
|----|-------------|
| MARK-1 | Analytic, criterion-referenced marking: the model judges each criterion against band descriptors. |
| MARK-2 | For each criterion the model returns, in this order: justification, verbatim evidence quotes, band, and an integer mark inside the band. If the mark falls outside the band, it is clamped into the band. |
| MARK-3 | N independent samples (default 3) with seeds `seed + i`. The median per criterion is used. |
| MARK-4 | The overall mark is the weighted mean of the criterion medians, **computed in code**, rounded half up to an integer 0-100, and mapped to a band. |
| MARK-5 | Invalid model output is retried with deterministic derived seeds (`seed + 1000·attempt`), up to `max_retries`. |
| MARK-6 | The essay is treated as untrusted data: delimited, unable to close its own delimiter, and the model is told never to follow instructions in it. |
| MARK-7 | Feedback for the student (strengths, improvements) is taken from the most representative sample. |

## Review flags (`needs_review`)

| Id | Flag raised when |
|----|-----------------|
| FLAG-1 | A criterion's sample marks spread by more than the threshold (default 10). |
| FLAG-2 | The final mark is within 2 marks below a classification boundary. |
| FLAG-3 | An evidence quote isn't found in the essay, after normalising typography. |
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
| OUT-4 | Rerunning with an unchanged input fingerprint skips the essay unless `--force` is given. |

## Validation

| Id | Requirement |
|----|-------------|
| VAL-1 | `marker evaluate` compares machine marks with human marks: QWK (marks and bands), MAE, RMSE, mean bias, Pearson r, and exact/adjacent band agreement. |
| VAL-2 | Calibrate before live use, and re-calibrate after any change to the model, rubric or prompt. |

## Deliverables

- `marker/`: the Python package and CLI (`check`, `rubric`, `grade`, `evaluate`), fully type-annotated and documented.
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
