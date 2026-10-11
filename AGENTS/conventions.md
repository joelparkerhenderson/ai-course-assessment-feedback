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
- Errors: raise `RubricError`, `OllamaError` (or its subclass
  `ContextOverflow`), `InvalidResponse` or `ValueError` with an actionable
  message that says what to change (e.g. which `--num-ctx` to use).
  `cli.main` turns expected errors into one-line messages and exit code 2;
  `grade` counts an `OllamaError` for one essay as a failure and carries on.
- CLI options: validate on input with an argparse `type` (`positive_int`,
  `non_negative_int`, `bounded_float`) so bad values fail before any model
  call. After changing options, run `python3 tools/update_llms_json.py`
  (`tests/test_llms.py` enforces it).
- Output: progress and errors go to **stderr**; machine-readable results
  (`id<TAB>score<TAB>band[<TAB>model]`, the explanation and thinking views,
  and the comparison) go to **stdout**.
- **Marks follow the numeric mode** (`marker/scale.py`): Python `float` 0.0-1.0
  by default, or `Decimal` 0.0000-1.0000 with 4 places (`--numeric decimal`).
  The mode is process-wide; `cli.main` sets it first. Rules:
  - convert every mark with `scale.num()` or `scale.normalise_mark()` (both
    accept str, int, float or Decimal, including values saved in JSON);
  - start sums with `scale.zero()`, use `scale.median()`, `scale.sqrt()` and
    `scale.quantize()`, and never mix `float` and `Decimal` in arithmetic;
  - parse with `parse_float=scale.parse_float()` (TOML and model JSON);
  - keep configuration exact (`Decimal` from `scale.dec()`) and convert it with
    `num()` where it's used;
  - format marks shown to people with `scale.fmt_percent()` (whole percent,
    banker's rounding: `0.135` gives `14%`), marks in the prompt with
    `scale.fmt_mark()` (4 places), and exact output with `scale.text()`;
    write JSON with `scale.dumps()`;
  - test both modes: Decimal assertions inside `numeric("decimal")`.
- Rounding: half up for values (`scale.quantize`, `scale.round_half_up`);
  half to even only for display percents (`scale.fmt_percent`). Never use the
  built-in `round` or `:.0%` on marks, because they round the binary float
  (`f"{0.575:.0%}"` is `57%`). The one deliberate exception is the criterion
  weight in the prompt (`prompt.py`, `{c.weight:.0%}`): weights are exact
  whole percents, and changing that text would change every fingerprint.
- Line length: 120.
