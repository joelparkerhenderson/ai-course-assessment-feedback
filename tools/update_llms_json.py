"""Regenerate the generated parts of ``llms.json`` from the code.

``llms.json`` describes marker for AI assistants. Its ``commands`` list (every
subcommand, argument and option, with help text, choices and defaults) and its
``versions`` are generated here from the real argument parser and version
constants, so they can't drift. The rest of the file (summary, principles,
outputs, examples, ...) is written by hand and kept as it is.

Run it after changing any CLI option::

    python3 tools/update_llms_json.py

``tests/test_llms.py`` fails until the file matches.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from marker.cli import DEFAULT_RUBRIC, build_parser  # noqa: E402
from marker.grader import ANALYSIS_VERSION, PROMPT_VERSION  # noqa: E402

LLMS_JSON = ROOT / "llms.json"


def _default(value: Any) -> Any:
    """A JSON-friendly default: repository-relative paths, Decimals and other objects as text."""
    if value == str(DEFAULT_RUBRIC):
        return str(DEFAULT_RUBRIC.relative_to(ROOT))
    if value is None or isinstance(value, (str, int, float, bool, list)):
        return value
    return str(value)


def commands() -> list[dict[str, Any]]:
    """Describe every subcommand from the argument parser, in parser order."""
    sub = next(a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    summaries = {c.dest: c.help for c in sub._choices_actions}
    out: list[dict[str, Any]] = []
    for name, parser in sub.choices.items():
        arguments: list[dict[str, Any]] = []
        options: list[dict[str, Any]] = []
        for action in parser._actions:
            if isinstance(action, argparse._HelpAction):
                continue
            default = _default(action.default)
            text = (action.help or "").replace("%(default)s", str(default))
            if not action.option_strings:
                arguments.append({"name": action.dest, "nargs": action.nargs, "help": text})
                continue
            option: dict[str, Any] = {"flags": list(action.option_strings), "help": text}
            if action.choices:
                option["choices"] = list(action.choices)
            if default not in (None, False):
                option["default"] = default
            if isinstance(action, argparse._AppendAction):
                option["repeatable"] = True
            options.append(option)
        out.append({"name": name, "summary": summaries[name], "arguments": arguments, "options": options})
    return out


def versions() -> dict[str, str]:
    """The prompt and analysis versions that results record."""
    return {"prompt_version": PROMPT_VERSION, "analysis_version": ANALYSIS_VERSION}


def main() -> int:
    """Rewrite the generated sections of ``llms.json`` in place."""
    doc = json.loads(LLMS_JSON.read_text(encoding="utf-8"))
    doc["versions"] = versions()
    doc["commands"] = commands()
    LLMS_JSON.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"updated {LLMS_JSON.relative_to(ROOT)}: {len(doc['commands'])} commands")
    return 0


if __name__ == "__main__":
    sys.exit(main())
