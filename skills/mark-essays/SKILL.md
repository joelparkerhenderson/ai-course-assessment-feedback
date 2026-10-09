---
name: mark-essays
description: Mark student essays 0-100 against a rubric with the local gpt-oss marker (`marker grade`), then summarise the marks, bands and review flags. Use when the user asks to mark, grade or score essays, papers or assignments in this repo.
---

# Mark essays

Marks essays with a local gpt-oss model through Ollama. Essays never leave the
machine. See `README.md` for the full CLI and `docs/research.md` for the
reasoning behind the method.

## Steps

1. **Preflight.** Run `python3 -m marker check` (add `--model gpt-oss:120b` if
   that model is wanted).
   - "model is not installed": tell the user to run `ollama pull gpt-oss:20b`.
   - HTTP 500 with "unsupported tensor … size overflows": the model blob is
     older than the installed Ollama. Re-pull it with `ollama pull <model>`.
   - "cannot reach Ollama": ask the user to start it (`ollama serve` or the app).
2. **Choose the rubric.** Default: `rubrics/uk-undergraduate-essay.toml`. If
   the module has its own marking scheme, use or create a rubric with the
   `write-rubric` skill first. Validate it with `python3 -m marker rubric --rubric <file>`.
3. **Find the brief.** Ask for the assignment question if it isn't known. An
   essay is marked *against the question*. Save it as a text file and pass `--brief`.
4. **Check the inputs.** Essays must be `.txt`, `.md` or `.docx`. Convert anything
   else with the `prepare-essays` skill. The file name (without extension) is
   the essay id.
5. **Run.** It is slow: minutes per essay at `--think high`, so run it in the background for batches:
   ```sh
   python3 -m marker grade essays/ --brief brief.txt --out results/<batch> [--model gpt-oss:120b]
   ```
   Keep the defaults (`--samples 3 --seed 42 --temperature 0.6 --think high`)
   unless there is a reason to change them. Any change alters the fingerprint
   and makes runs non-comparable.
6. **Report.** Read `results/<batch>/summary.csv` and give the user:
   - a table of id, mark, band and needs_review;
   - for every flagged essay, its flags in plain language;
   - the model and digest used.
   Point to the per-essay JSON for criterion-level justification and feedback.

## Rules

- Never change a mark by hand in the result files. Human overrides belong in
  the moderation record (see `moderate-flagged-essays`).
- Never pass `--allow-remote` unless the user explicitly asks and controls that server.
- Present marks as **provisional, to be confirmed by a human marker**, especially flagged ones.
- Don't paste whole essays into chat or logs. Refer to them by id.
