# Code conventions

- Python ≥ 3.11, standard library only. Use `tomllib` for TOML and `urllib` for HTTP.
- `from __future__ import annotations` at the top of every module.
- **Fully annotated**: every function, method and dataclass field. `uvx mypy marker` must be clean.
- **Docstrings**: Google style, on every module, public class and public
  function. Include `Args:`, `Returns:` and `Raises:` where they apply. Explain
  *why* when the reason isn't obvious (e.g. why the median, why round half up).
- **Comments** explain intent and non-obvious decisions, not what the code
  literally does.
- Dataclasses for value objects (`frozen=True` where the value is immutable).
- Errors: raise `RubricError`, `OllamaError`, `InvalidResponse` or `ValueError`
  with an actionable message. `cli.main` turns expected errors into one-line
  messages and exit code 2.
- Output: progress and errors go to **stderr**; machine-readable results
  (`id<TAB>mark<TAB>band`) go to **stdout**.
- Rounding: always `round_half_up`, never the built-in `round` (banker's rounding).
- Line length: 120.
