---
name: compare-models
description: Mark the same essays with two or more local models (e.g. gpt-oss 20b vs 120b, gpt-oss vs Muse Glimmer, MLX vs GGUF builds) and compare their marks, bands, criteria and agreement, with a panel (median) mark. Use when the user says "evaluate this paper with model X and model Y and compare", asks which model marks better, or wants a multi-model panel mark.
---

# Compare models

## Steps

1. **Pick the models.** Run `python3 -m marker models` to see what's installed. Translate the request:
   - "model X and model Y": `--model X --model Y` (exact tags), or `--model X,Y`
   - "gpt-oss in both sizes": `--family gpt-oss --size 20b,120b`
   - "ChatGPT-OSS vs Muse Glimmer": `--family "ChatGPT-OSS" --family "Muse Glimmer"`
   - "MLX vs normal build": `--family <f> --size <s> --mlx any`
   Run `python3 -m marker check` with the same options. Drop any model reported as UNUSABLE, and tell the user.
2. **Run.** Use the same essays, brief, rubric and settings for every model:
   ```sh
   python3 -m marker grade <essays> --brief brief.txt --model X --model Y \
     --out results/compare-<name> [--show score,explanation]
   ```
   Models run one after another. Expect each model's time per essay to add up,
   so run long batches in the background.
3. **Report** from the printed table and `results/compare-<name>/comparison.csv`:
   - each model's mark and band per essay, and the **panel mark**;
   - where the models disagree (different bands, or a spread above `--disagreement`),
     and on which criteria (the per-criterion spread);
   - for several essays, the pairwise mean difference (which model is more
     generous), MAE, and QWK.
   For *why* the models differ, read both explanations:
   `python3 -m marker show results/compare-<name>/<model-slug>/<id>.json --show explanation`.
4. **Re-compare** without re-marking: `python3 -m marker compare results/compare-<name>`.

## Rules

- Model agreement is **not** accuracy. Two models can agree and both be
  wrong. To say which model is *better*, use `calibrate-marker` against
  human marks, including the panel: `--results comparison.csv --results-mark-column panel_mark`.
- Don't compare runs that used different essays, briefs, rubrics or settings.
  Check `provenance` in the result JSON if unsure.
- Report each model's tag **and** digest.
