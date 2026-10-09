# Architecture

```
essay files ──► essay.py ──► Essay(text, sha256, word_count)
rubric.toml ──► rubric.py ──► Rubric(bands, criteria, sha256)
brief.txt   ─┐
             ▼
        prompt.py: build_messages() + response_schema()
             │
             ▼
        grader.py: grade_essay()
          ├─ for i in samples: sample_once(seed+i)  ──► ollama.py: OllamaClient.chat()  ──► Ollama /api/chat (gpt-oss)
          │     └─ parse_response(): validate JSON, clamp mark into band; retry with seed+1000·attempt
          ├─ median per criterion, weighted mean in code, round half up
          ├─ flags: disagreement, borderline, unverified quotes, word limits, injection, integrity
          └─ provenance: model digest, Ollama version, prompt/schema/rubric/brief/essay hashes, settings, fingerprint
             │
             ▼
        cli.py: <id>.json (atomic), summary.csv (rebuilt), audit.jsonl (append-only)
        evaluate.py: machine vs human marks → QWK, MAE, RMSE, bias, r, band agreement
```

## Modules

| Module | Responsibility | Must not |
|--------|----------------|----------|
| `rubric.py` | Parse and validate TOML rubrics; map marks to bands | know about the model |
| `essay.py` | Read .txt/.md/.docx; normalise; hash; count words | alter wording |
| `prompt.py` | System and user messages; JSON schema; canonical hashing | contain timestamps or randomness |
| `ollama.py` | HTTP to Ollama; loopback enforcement; model digest | do retries or parsing |
| `grader.py` | Sampling, validation, aggregation, flags, provenance | do file I/O |
| `evaluate.py` | Agreement metrics | call the model |
| `cli.py` | Arguments, files, skip-if-unchanged, summary, audit log | contain marking logic |

`grader.py` takes any object that matches the `ChatClient` protocol, which is
how tests inject a fake client.

## Outputs

- `<out>/<essay-id>.json`: the full result (schema: see `grade_essay` docstring).
- `<out>/summary.csv`: one row per essay. Regenerated from the JSON files on every run.
- `<out>/audit.jsonl`: one line per marking event. Append-only.
