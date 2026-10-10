# AGENTS.md

Guidance for AI coding agents (Claude Code, Codex, Cursor, etc.) working in this repository.

**Project:** `marker` marks student essays (internally fractions 0.0-1.0, as
float by default or exact Decimal with `--numeric decimal`; reported 0-100 or
on any chosen scale) against an analytic rubric,
using **local** models served by Ollama (default gpt-oss; any family, size or
MLX build can be selected, and several models can be compared). The priorities, in order, are
**security and privacy of student data**, **reproducibility**, and **marking
validity**. Read `spec/index.md` (requirements) and `docs/research.md` (why the
method is what it is) before making non-trivial changes.

## Quick commands

```sh
python3 -m unittest discover -s tests -t .     # tests (offline, no Ollama needed)
uvx ruff check marker tests --select F,E9      # lint
uvx mypy marker                                # type check (must be clean)
python3 -m marker check                        # Ollama up + model installed?
python3 -m marker grade assets/example-papers/ --out results/example
```

## Detailed guidance

| File | Read it when you are... |
|------|-------------------------|
| [AGENTS/architecture.md](AGENTS/architecture.md) | changing any module, or the data flow |
| [AGENTS/conventions.md](AGENTS/conventions.md) | writing any Python |
| [AGENTS/testing.md](AGENTS/testing.md) | adding features or fixing bugs |
| [AGENTS/security-and-privacy.md](AGENTS/security-and-privacy.md) | touching I/O, the network, the prompt or logging |
| [AGENTS/reproducibility.md](AGENTS/reproducibility.md) | touching the prompt, schema, settings, seeds or provenance |
| [AGENTS/marking-methodology.md](AGENTS/marking-methodology.md) | changing how marks are computed or flagged |

Task-level skills for using the tool are in [`skills/`](skills/): mark-essays,
compare-models, write-rubric, calibrate-marker, moderate-flagged-essays, prepare-essays.

## Hard rules

1. **No third-party runtime dependencies.** Standard library only (Python ≥ 3.11).
2. **Never send essay text off the machine.** Keep the loopback check in `marker/ollama.py`.
3. **The model never does arithmetic.** It gives per-criterion marks; the code aggregates.
4. **Marks go through `marker.scale`.** They are 0.0-1.0 in the current numeric
   mode: `float` by default, or `Decimal` with 4 places. Convert values with
   `scale.num()` / `scale.normalise_mark()`, start sums with `scale.zero()`, and
   never mix float and Decimal. The prompt must be identical in both modes.
5. **Any prompt or schema change bumps `PROMPT_VERSION`** in `marker/prompt.py`;
   any analysis change bumps `ANALYSIS_VERSION` in `marker/grader.py`.
6. **Don't commit real student essays or results.** `results/` is output, not source.
7. Tests must pass and mypy must be clean before you finish.
