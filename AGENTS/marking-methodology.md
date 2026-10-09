# Marking methodology (for code changes)

Background and citations: `docs/research.md`. Summary of what the code must keep doing:

1. **Criterion-referenced analytic marking.** Each criterion is judged against
   band descriptors. The band choice is primary, and the mark is clamped into it.
2. **Reasons before numbers.** The schema orders `justification` and `evidence` before `mark`.
3. **Several independent samples, median per criterion** (default 3). This is
   the machine analogue of double marking.
4. **Weighted overall mark computed in code**, rounded half up, mapped to a band.
5. **Review flags** (any flag means `needs_review = true`):
   - sample spread > `disagreement_threshold` (default 10) on any criterion;
   - final mark within `boundary_margin` (default 2) below a classification boundary;
   - evidence quotes not found in the essay;
   - word count outside the rubric's limits (flag only, never a penalty);
   - injection-like text in the essay;
   - integrity concerns raised by the model;
   - a model mark outside its chosen band (clamped).
6. **Validation** with `marker evaluate`: QWK, MAE, RMSE, bias, Pearson r, and
   band agreement against human marks.

Changes that alter marks (aggregation, rounding, clamping, prompt) need a
note in the README changelog and a re-calibration run.
