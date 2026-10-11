---
name: mark-essays
description: Mark student essays (0-100 by default, or any decimal scale such as a 0.0000-1.0000 probability) against a rubric with local Ollama models (default gpt-oss) using `marker grade`, choosing models by tag, family, size or MLX build, and showing the score, explanation and/or model thinking. Use when the user asks to mark, grade or score essays, papers or assignments in this repo.
---

# Mark essays

Marks essays with a local model through Ollama (default `gpt-oss:20b`).
Essays never leave the machine. See `README.md` for the full CLI (its
"Tutorial" section is the whole workflow), `llms.json` for every option, and
`docs/research.md` for the reasoning behind the method. To try it, use the
committed example: `assets/example-papers/sample-size-and-study-quality.md`
with `--brief assets/example-briefs/sample-size-and-study-quality.txt`.

## Steps

1. **Choose models and preflight.** Use `python3 -m marker models` to see what's
   installed. Turn the user's wording into options:
   - "gpt-oss 120b": `--model gpt-oss:120b`, or `--family gpt-oss --size 120b`
   - "ChatGPT-OSS and Muse Glimmer": `--family "ChatGPT-OSS" --family "Muse Glimmer"`
   - "the Mac-optimised one": `--mlx only` (or the default `auto`, which prefers MLX on Apple Silicon).
     Warn the user that MLX builds aren't deterministic, so their marks can
     change between runs; for marks that must reproduce, use `--mlx never` or exact GGUF tags.
   - two or more models: use the `compare-models` skill instead.
   Then run `python3 -m marker check` with the same options.
   - "model is not installed": tell the user to run `ollama pull gpt-oss:20b`.
   - HTTP 500 with "unsupported tensor … size overflows": the model blob is
     older than the installed Ollama. Re-pull it with `ollama pull <model>`.
   - "cannot reach Ollama": ask the user to start it (`ollama serve` or the app).
   - `UNUSABLE: Ollama cannot read this model`: re-pull it with `ollama pull <model>`.
2. **Choose the rubric.** Default: `rubrics/uk-undergraduate-essay.toml`. If
   the module has its own marking scheme, use or create a rubric with the
   `write-rubric` skill first. Validate it with `python3 -m marker rubric --rubric <file>`.
3. **Find the brief.** Ask for the assignment question if it isn't known. An
   essay is marked *against the question*. Save it as a text file and pass `--brief`.
4. **Check the inputs.** Essays must be `.txt`, `.md` or `.docx`. Convert anything
   else with the `prepare-essays` skill. The file name (without extension) is
   the essay id, so ids must be unique: `grade` refuses to start if, say,
   `paper.txt` and `paper.md` are both given. Rename one.
5. **Run.** It is slow: minutes per essay at `--think high`, so run it in the background for batches:
   ```sh
   python3 -m marker grade essays/ --brief brief.txt --out results/<batch> [--model gpt-oss:120b]
   ```
   If the user wants a different score scale, add e.g. `--score-min 0.0000 --score-max 1.0000`
   (probability, 4 places) or `--score-min 0 --score-max 20 --score-places 1`.
   Add `--show explanation,score` if the user wants the reasons, or
   `--show thinking` for the model's reasoning trace (`--show all` for everything).
   Keep the defaults (`--samples 3 --seed 42 --temperature 0.6 --think high`)
   unless there is a reason to change them. Reasoning can be set per model
   (`--think high,gpt-oss:120b=low`, or `off` for non-gpt-oss thinking models),
   and sampling tuned with `--top-p`, `--top-k`, `--min-p`, `--repeat-penalty`,
   `--num-predict` or `--option KEY=VALUE`. Any change alters the fingerprint
   and makes runs non-comparable.
   If an essay fails with "the prompt is about N tokens ... Use --num-ctx M or
   more", re-run with that `--num-ctx` (needs more memory). Never work around
   it: Ollama would otherwise silently cut the prompt.
   Re-running the same command is cheap: unchanged essays are reused, and a
   changed score scale or numeric mode is re-analysed without model calls.
6. **Report.** Read `results/<batch>/summary.csv` and give the user:
   - a table of id, score, band and needs_review;
   - for every flagged essay, its flags in plain language;
   - the model and digest used.
   For criterion-level justification and feedback, run
   `python3 -m marker show results/<batch> --show explanation`. It doesn't re-mark anything.

## Rules

- Never change a mark by hand in the result files. Human overrides belong in
  the moderation record (see `moderate-flagged-essays`).
- Never pass `--allow-remote` unless the user explicitly asks and controls that server.
- Present marks as **provisional, to be confirmed by a human marker**, especially flagged ones.
- Don't paste whole essays into chat or logs. Refer to them by id.
