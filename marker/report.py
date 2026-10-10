"""Render a marking result for the terminal, in the parts the user asks for.

Three views can be combined (``--show score,explanation,thinking``):

* ``score``: the final score on the reporting scale (default 0-100; e.g.
  0.0000-1.0000 with ``--score-min``/``--score-max``), its band, and whether
  review is needed. This is one tab-separated line, ``id<TAB>score<TAB>band[<TAB>model]``,
  so it can be piped into other tools.
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

from .scale import MARK_QUANTUM, Scale, dec, fmt_mark, text

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


def score_of(result: dict[str, Any], scale: Scale | None = None) -> str:
    """The result's score as exact text: the stored score, or ``weighted_mark`` re-mapped onto ``scale`` if given."""
    if scale is not None:
        return scale.format(scale.from_fraction(result["weighted_mark"]))
    stored = result["score"]
    return Scale(dec(stored["min"]), dec(stored["max"]), int(stored["places"])).format(stored["value"])


def score_line(result: dict[str, Any], *, with_model: bool = False, scale: Scale | None = None) -> str:
    """Return the one-line, tab-separated score: ``id, score, band[, model]``, plus ``[REVIEW]`` if flagged."""
    parts = [result["essay"]["id"], score_of(result, scale), result["band"]["label"]]
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


def explanation_text(result: dict[str, Any], scale: Scale | None = None) -> str:
    """Return the per-criterion explanation, feedback, integrity concerns and review flags.

    Criterion marks are shown as internal 0.0-1.0 fractions to 4 places. The
    overall score is shown on the reporting scale.
    """
    shown = scale or Scale.from_dict(result["score"])
    lines = [
        f"== {result['essay']['id']}  ({result['provenance']['model']})",
        f"Score: {score_of(result, scale)} on {shown.label()}  {result['band']['label']}  "
        f"(mark {_mark(result['mark'])}, weighted {_mark(result['weighted_mark'], exact=True)})",
        "",
    ]
    for cid, c in result["criteria"].items():
        samples = ", ".join(_mark(m) for m in c["sample_marks"])
        lines.append(f"[{cid}] {c['name']}  weight {dec(c['weight']):.0%}")
        lines.append(
            f"  mark {_mark(c['mark'], exact=True)}  band {c['band']}  (samples: {samples}; spread {_mark(c['spread'])})"
        )
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


def render(result: dict[str, Any], show: set[str], *, with_model: bool = False, scale: Scale | None = None) -> str:
    """Render the requested views of one result, in the order thinking, explanation, score.

    The score comes last so it stays visible at the end of long output. With
    ``scale``, the score is re-mapped onto that scale (exactly, from the stored
    weighted mark) instead of the scale it was saved with.
    """
    blocks: list[str] = []
    if "thinking" in show:
        blocks.append(thinking_text(result))
    if "explanation" in show:
        blocks.append(explanation_text(result, scale))
    if "score" in show:
        blocks.append(score_line(result, with_model=with_model, scale=scale))
    return "\n\n".join(blocks)


def _mark(x: Any, *, exact: bool = False) -> str:
    """Format an internal mark to 4 places (``0.6200``).

    With ``exact``, a value with more than 4 places, such as a median ``0.62125``
    or a float sum ``0.44550000000000006``, is shown in full.
    """
    d = dec(x)
    if exact and d != d.quantize(MARK_QUANTUM):
        return text(x)
    return fmt_mark(x)
