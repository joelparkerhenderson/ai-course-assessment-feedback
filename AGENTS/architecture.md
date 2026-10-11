# Architecture

```
essay files ──► essay.py ──► Essay(id, text, sha256, word_count)
rubric.toml ──► rubric.py ──► Rubric(bands, criteria, sha256)
brief.txt   ─┐
             ▼
        prompt.py: build_messages() + response_schema()
             │
             ▼
        grader.py: grade_essay()
          ├─ check_context(): refuse a prompt that may not fit num_ctx (ContextOverflow)
          ├─ for i in samples: sample_once(seed+i)  ──► ollama.py: OllamaClient.chat()  ──► Ollama /api/chat
          │     ├─ token check: prompt filled num_ctx → ContextOverflow; prompt+answer overflow → retry
          │     └─ parse_response(): validate JSON, clamp mark into band; retry with seed+1000·attempt
          ├─ analyse() (deterministic, ANALYSIS_VERSION; reanalyse() re-runs it on stored samples):
          │     median per criterion, weighted mean in code (4-place half-up rounding in decimal mode only)
          ├─ flags: disagreement, borderline, unverified quotes, word limits, injection, integrity, clamping
          └─ provenance: model digest, Ollama version, prompt/schema/rubric/brief/essay hashes, settings, fingerprint
             │
             ▼
        cli.py: models.py selects models (tag / family / size / MLX policy) and each model's think value;
                refuses duplicate essay ids; runs each model over the whole batch
                <out>[/<model-slug>]/<id>.json (atomic), summary.csv (rebuilt), audit.jsonl (append-only)
                report.py prints --show score / explanation / thinking
        compare.py: several models' results → per-essay table, panel mark (median), pairwise agreement
                <out>/comparison.csv + comparison.json
        evaluate.py: machine vs human marks → QWK, MAE, RMSE, bias, r, band agreement
```

## Modules

| Module | Responsibility | Must not |
|--------|----------------|----------|
| `scale.py` | Numeric modes (float default, Decimal 4 places), reporting scales, rounding, median, display percents (`fmt_percent`), JSON encoding | mix float and Decimal |
| `rubric.py` | Parse and validate TOML rubrics (bands and weights in the numeric mode, validated exactly); map marks to bands | know about the model |
| `essay.py` | Read .txt/.md/.docx; normalise; hash; count words; expand directories | alter wording |
| `prompt.py` | System and user messages; JSON schema; canonical hashing | contain timestamps or randomness |
| `ollama.py` | HTTP to Ollama; loopback enforcement; model list, capabilities, digest | do retries or parsing |
| `grader.py` | `Settings`; context-window guard; sampling, validation, aggregation, flags, provenance, fingerprint | do file I/O |
| `evaluate.py` | Agreement metrics (QWK, MAE, RMSE, bias, r) | call the model |
| `models.py` | Parse installed models; select by tag, family, size and MLX policy; `--think` parsing (`parse_think`, `think_setting`) and per-model `think` value (`think_for`) | call the server (it takes the list it is given) |
| `compare.py` | Compare models' results; panel mark; pairwise agreement; comparison files | call the model |
| `report.py` | Text views: score, explanation, thinking | write files |
| `cli.py` | Arguments and validation, files, skip-if-unchanged and re-analysis, summary, audit log, orchestration | contain marking logic |

`grader.py` takes any object that matches the `ChatClient` protocol, which is
how tests inject a fake client.

## Outputs

- `<out>/<essay-id>.json` (or `<out>/<model-slug>/<essay-id>.json` with several models): the full result (schema: see `grade_essay` docstring).
- `<out>/summary.csv`: one row per essay. Regenerated from the JSON files on every run.
- `<out>/audit.jsonl`: one line per event (`marked` or `reanalysed`). Append-only.
- `<out>/comparison.csv`, `<out>/comparison.json`: multi-model runs and `marker compare`.

## Common changes

**Add a model option** (an Ollama `options` key):

1. Add a `Settings` field in `grader.py`. If it is optional (None means "let
   Ollama decide"), add it to `OPTIONAL_OPTIONS`, so it is sent only when set
   and left out of the fingerprint while unset (old results stay valid).
2. Add the flag in `cli.py` `add_model_options`, with a validating `type`
   (`positive_int`, `non_negative_int`, `bounded_float`), and pass it into
   `Settings` in `cmd_grade`.
3. Regenerate `llms.json` with `python3 tools/update_llms_json.py` (the
   `test_llms` test fails until it matches the parser), and update `README.md` (grade options table
   and "Model options") and `spec/index.md` (MODEL-8).
4. Test that the option reaches `options` and changes the fingerprint
   (`test_grade_model_options`).

**Add a review flag:** add it in `grader.analyse`, bump `ANALYSIS_VERSION`,
document it in `spec/index.md` (FLAG-*), the README "Review flags" table,
`llms.json` (`review_flags`, written by hand) and `AGENTS/marking-methodology.md`, and test
that it fires and doesn't fire on a normal essay.

**Change the prompt or schema:** bump `PROMPT_VERSION` (every fingerprint
changes, so every essay is re-marked), keep the prompt identical in both
numeric modes, and re-calibrate (`skills/calibrate-marker`).
