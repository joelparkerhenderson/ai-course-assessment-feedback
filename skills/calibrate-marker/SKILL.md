---
name: calibrate-marker
description: Measure how well the marker (any local model, or a multi-model panel) agrees with human markers (QWK, MAE, bias, band agreement) with `marker evaluate`, and interpret the result. Use before trusting the marker on live work, after changing the model, rubric or prompt, or when the user asks how accurate or reliable the marks are.
---

# Calibrate the marker against human marks

## Steps

1. Get a set of essays that experienced staff have **already marked**:
   ideally 30 or more, covering the full range of bands, with the same brief and rubric.
   Put the human marks in a CSV, e.g. `id,mark`, where `id` is the essay file
   name without its extension. Use `--id-column`/`--mark-column` for other headers.
   If the human marks aren't 0-100, give their scale when you grade *and* when you
   evaluate, e.g. `--score-min 0.0000 --score-max 1.0000`.
2. Mark the essays with the `mark-essays` skill into a dedicated directory,
   e.g. `results/calibration-<date>`. Use exactly the settings you will use
   for live marking (model, `--think`, `--samples`, rubric, brief), and a GGUF
   build: MLX builds aren't deterministic, so a calibration with them can't be
   reproduced.
3. Run:
   ```sh
   python3 -m marker evaluate --results results/calibration-<date>/summary.csv --human human.csv [--rubric ...]
   ```
   For a multi-model panel, evaluate the panel score:
   `--results results/<dir>/comparison.csv --results-mark-column panel_score`.
4. Interpret the result:
   - **QWK (marks)**: compare it with *human-human* agreement on the same
     essays if you have double-marked data. AES research treats agreement
     comparable to human-human QWK as the target. Don't promise a fixed threshold.
   - **Mean bias**: a consistent offset (e.g. +4) means the model is more
     generous. Report it; don't silently subtract it.
   - **MAE**: typical disagreement in marks. More than about 5 marks matters
     near classification boundaries.
   - **Exact/adjacent band agreement**: what students actually experience.
   - With `--json`, list the essays with the largest `diff` and read their
     criterion justifications to find out *why* they differ.
5. To improve agreement, change in this order: rubric descriptors (most
   effective), the brief, model size (`gpt-oss:120b`), `--think high`, and
   only then the prompt. After each change, re-run on the **same** essays
   and keep every run's directory, so comparisons stay honest.

## Rules

- Never tune on the same essays you then report agreement on, if you can
  avoid it. Keep a held-out set.
- Record the model digest, rubric version and prompt version with any
  agreement figures you report.
