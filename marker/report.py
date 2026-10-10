"""Render a marking result for the terminal, in the parts the user asks for.

Three views can be combined (``--show score,explanation,thinking``):

* ``score``: the final 0-100 mark, its band, and whether review is needed.
  This is one tab-separated line, ``id<TAB>mark<TAB>band[<TAB>model]``, so it can be
  piped into other tools.
* ``explanation``: for each criterion, the mark, band, sample marks,
  justification and evidence quotes (unverified quotes are marked), followed by
  the feedback (strengths and improvements), integrity concerns and review flags.
* ``thinking``: the model's reasoning trace for each sample (gpt-oss's
  analysis channel, or another thinking model's reasoning). It is only
  available if the result was produced with reasoning kept
  (``--keep-reasoning``, or ``--show thinking`` during ``grade``).
"""

from __future__ import annotations

import textwrap
from typing import Any

#: The views that can be requested, in display order.
SHOW_CHOICES = ("score", "explanation", "thinking")


def parse_show(values: list[str] | None) -> set[str]:
    """Parse ``--show`` values (repeatable and/or comma-separated) into a set of views.

    ``all`` expands to every view. With nothing given, the default is ``{"score"}``.

    Raises:
        ValueError: An unknown view was requested.
    """
    items = {v.strip().lower() for raw in (values or []) for v in raw.split(",") if v.strip()}
    if "all" in items:
        return set(SHOW_CHOICES)
    unknown = items - set(SHOW_CHOICES)
    if unknown:
        raise ValueError(f"unknown --show value(s) {sorted(unknown)}; choose from {', '.join(SHOW_CHOICES)}, all")
    return items or {"score"}


def score_line(result: dict[str, Any], *, with_model: bool = False) -> str:
    """Return the one-line, tab-separated score: ``id, mark, band[, model]``, plus ``[REVIEW]`` if flagged."""
    parts = [result["essay"]["id"], str(result["mark"]), result["band"]["label"]]
    if with_model:
        parts.append(result["provenance"]["model"])
    line = "\t".join(parts)
    return line + ("  [REVIEW]" if result.get("needs_review") else "")


def _wrap(text: str, indent: str, width: int = 100) -> list[str]:
    """Wrap a paragraph to ``width`` columns with a hanging indent, keeping the existing line breaks."""
    out: list[str] = []
    for para in str(text).splitlines() or [""]:
        out.extend(textwrap.wrap(para, width=width, initial_indent=indent, subsequent_indent=indent) or [indent])
    return out


def explanation_text(result: dict[str, Any]) -> str:
    """Return the per-criterion explanation, feedback, integrity concerns and review flags."""
    lines = [
        f"== {result['essay']['id']}  ({result['provenance']['model']})",
        f"Mark: {result['mark']}/100  {result['band']['label']}  (weighted {result['weighted_mark']})",
        "",
    ]
    for cid, c in result["criteria"].items():
        samples = ", ".join(str(m) for m in c["sample_marks"])
        lines.append(f"[{cid}] {c['name']}  weight {c['weight']:.0%}")
        lines.append(f"  mark {_num(c['mark'])}  band {c['band']}  (samples: {samples}; spread {c['spread']})")
        lines.extend(_wrap(c["justification"], "  "))
        unverified = set(c.get("unverified_evidence", []))
        for q in c["evidence"]:
            tag = "  [NOT FOUND IN ESSAY]" if q in unverified else ""
            lines.extend(_wrap(f'> "{q}"{tag}', "    "))
        lines.append("")
    fb = result.get("feedback", {})
    for title, key in (("Strengths", "strengths"), ("Improvements", "improvements")):
        if fb.get(key):
            lines.append(f"{title}:")
            for item in fb[key]:
                lines.extend(_wrap(f"- {item}", "  "))
    if result.get("integrity_concerns"):
        lines.append("Integrity concerns (for a human to check):")
        for item in result["integrity_concerns"]:
            lines.extend(_wrap(f"- {item}", "  "))
    if result.get("flags"):
        lines.append("Review flags:")
        for item in result["flags"]:
            lines.extend(_wrap(f"- {item}", "  "))
    else:
        lines.append("Review flags: none")
    return "\n".join(lines)


def thinking_text(result: dict[str, Any]) -> str:
    """Return each sample's reasoning trace, or a note explaining why there is none."""
    lines = [f"== {result['essay']['id']}  ({result['provenance']['model']}): model thinking"]
    for i, s in enumerate(result.get("samples", []), 1):
        lines.append(f"--- sample {i} (seed {s.get('seed')}) ---")
        reasoning = s.get("reasoning")
        if reasoning is None:
            lines.append("(not stored: re-run grade with --show thinking or --keep-reasoning)")
        elif not reasoning.strip():
            lines.append("(the model returned no reasoning trace)")
        else:
            lines.append(reasoning.rstrip())
    return "\n".join(lines)


def has_reasoning(result: dict[str, Any]) -> bool:
    """True if every sample in a result has a stored reasoning trace."""
    samples = result.get("samples") or []
    return bool(samples) and all("reasoning" in s for s in samples)


def render(result: dict[str, Any], show: set[str], *, with_model: bool = False) -> str:
    """Render the requested views of one result, in the order thinking, explanation, score.

    The score comes last so it stays visible at the end of long output.
    """
    blocks: list[str] = []
    if "thinking" in show:
        blocks.append(thinking_text(result))
    if "explanation" in show:
        blocks.append(explanation_text(result))
    if "score" in show:
        blocks.append(score_line(result, with_model=with_model))
    return "\n\n".join(blocks)


def _num(x: float | int) -> str:
    """Format a mark: ``62`` stays ``62``, a median of ``62.5`` stays ``62.5``."""
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}"
