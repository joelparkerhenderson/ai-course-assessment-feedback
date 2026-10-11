# Testing

- Run: `python3 -m unittest discover -s tests -t .`. Tests are offline, need
  no Ollama, and run in well under a second. Also run `uvx mypy marker tools`
  and `uvx ruff check marker tests tools --select F,E9`; both must be clean.
- Test files:
  - `tests/test_marker.py`: rubric, essay loading, prompt, parsing, grading,
    flags, fingerprints, context-window guard, evaluation, security, scales,
    in both numeric modes;
  - `tests/test_models_compare_report.py`: model selection, `--think`
    parsing, comparison, output views, and full CLI flows;
  - `tests/test_llms.py`: `llms.json` matches the CLI parser and the current
    versions (regenerate with `python3 tools/update_llms_json.py`), and every
    file that `llms.txt` and `llms.json` link to exists.
- Fakes (never require a running Ollama in unit tests):
  - `FakeClient` (`test_marker.py`) returns queued JSON answers and records
    each call's seed. Use it for any grader behaviour.
  - `TokenClient` (`test_marker.py`) also reports `prompt_eval_count`,
    `eval_count` and `done_reason`, for the context-window checks.
  - `FakeOllama` (`test_models_compare_report.py`) is a whole fake server for
    CLI tests: installed models, capabilities, digests, and fixed marks per
    model. It records `(model, think)` and the `options` of every call. Patch
    it in with `mock.patch.object(cli, "make_client", return_value=fake)`.
- Helpers: `answer(marks, evidence)` builds a valid model answer (ints are
  percents, so `65` becomes `0.65`); `write_essay(dir, name)` writes a test
  essay; `RUBRIC` is the default rubric in float mode and `RUBRIC_D` in
  decimal mode (subclass `DecimalMode` for decimal-mode tests).
- Every bug fix gets a regression test. Every new flag gets a test that
  triggers it and a test showing it isn't triggered on a normal essay.
- Seed behaviour is part of the contract (`seed + i`, retries at
  `seed + 1000 * attempt`), so tests assert on it.
- **End-to-end check** (needs Ollama and `gpt-oss:20b`; takes several minutes):
  ```sh
  python3 -m marker check
  python3 -m marker grade assets/example-papers/sample-size-and-study-quality.md \
    --brief assets/example-briefs/sample-size-and-study-quality.txt --out results/e2e --force
  ```
  Confirm the JSON has all criteria, sensible justifications and verified
  evidence. Run it again without `--force` and confirm the essay is reused
  (fingerprint unchanged). To check reproducibility, run it into a second,
  empty `--out` and compare each sample's `raw` field: GGUF builds should be
  byte-identical. The README's "Full example" was verified this way with four
  models.
