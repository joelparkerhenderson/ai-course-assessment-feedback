# Marking methodology (for code changes)

Background and citations: `docs/research.md`. Summary of what the code must keep doing:

1. **Criterion-referenced analytic marking.** Each criterion is judged against
   band descriptors. The band choice is primary, and the mark is clamped into it.
2. **Reasons before numbers.** The schema orders `justification` and `evidence` before `mark`.
3. **Several independent samples, median per criterion** (default 3). This is
   the machine analogue of double marking.
4. **Weighted overall mark computed in code** (float 0.0-1.0 by default; exact
   Decimal rounded half up to 4 places with `--numeric decimal`), mapped to a
   band by its minimum, then mapped onto the reporting scale (`score`,
   default 0-100).
5. **Review flags** (any flag means `needs_review = true`):
   - sample spread > `disagreement_threshold` (default 0.1000) on any criterion;
   - final mark within `boundary_margin` (default 0.0200) below a classification boundary;
   - evidence quotes not found in the essay (each distinct quote counted once
     across samples);
   - word count outside the rubric's limits (flag only, never a penalty);
   - injection-like text in the essay;
   - integrity concerns raised by the model;
   - a model mark outside its chosen band (clamped).
6. **Validation** with `marker evaluate`: QWK, MAE, RMSE, bias, Pearson r, and
   band agreement against human marks.

7. **Several models** can mark the same essays. Their panel mark is the median
   of the models' marks (the midpoint of the middle two with an even number),
   and essays where models disagree on the band are noted for moderation.
8. **Display:** marks shown in the terminal are whole percents rounded half to
   even (`scale.fmt_percent`); stored values and scores are never affected.

Changes that alter marks (aggregation, rounding, clamping, prompt) need a
version bump (`ANALYSIS_VERSION` or `PROMPT_VERSION`), an explanation in the
commit message, updates to `spec/index.md`, and a re-calibration run
(`skills/calibrate-marker`).
