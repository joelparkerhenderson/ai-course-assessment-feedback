"""Compare marking results from several models (a "panel" of LLM markers).

A typical question: *"Mark this paper with model X and model Y, then compare
the results."* ``marker grade --model X --model Y`` writes one result directory
per model and then calls :func:`compare_runs`. ``marker compare`` does the same
for result directories that already exist.

For every essay the comparison reports:

* each model's overall mark, band and per-criterion marks;
* the **spread** (max - min) across models, overall and per criterion;
* a **panel mark**: the median of the models' overall marks, rounded half up to
  4 places. This is the multi-model analogue of double marking. Using several
  different judges ("a jury") reduces the effect of any one model's biases;
* a **panel score** on the reporting scale, when every model's result uses the same scale;
* whether the models **agree**: the same band, and an overall spread within the threshold.

For every pair of models it reports agreement over the essays both marked:
n, mean difference, MAE, exact band agreement and QWK, using the metrics in
:mod:`marker.evaluate`.

Marks in the JSON report are internal 0.0-1.0 fractions in the numeric mode
(floats by default; exact Decimal strings in ``decimal`` mode). The text report
shows them on the reporting scale (by default 0-100).
"""

from __future__ import annotations

import csv
import itertools
import json
import statistics
from decimal import Decimal
from pathlib import Path
from typing import Any

from .evaluate import quadratic_weighted_kappa
from .grader import is_compatible
from .rubric import Rubric
from .scale import FRACTION_SCALE, Num, Scale, dec, dumps, median, normalise_mark, num, round_half_up, text, zero

#: Default disagreement threshold between models: 0.1 (10 percentage points). Exact configuration value.
DEFAULT_THRESHOLD = Decimal("0.1000")


def _is_result(data: Any) -> bool:
    """True if a parsed JSON file looks like a marker result rather than some other JSON."""
    return isinstance(data, dict) and "mark" in data and "essay" in data and "provenance" in data


def _read_dir(directory: str | Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Read result JSON files: ``(compatible results by essay id, ids of outdated results)``."""
    current: dict[str, dict[str, Any]] = {}
    outdated: list[str] = []
    for f in sorted(Path(directory).glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not _is_result(data):
            continue
        if is_compatible(data):
            current[data["essay"]["id"]] = data
        else:
            outdated.append(data["essay"]["id"])
    return current, outdated


def load_results(directory: str | Path) -> dict[str, dict[str, Any]]:
    """Load every current marker result JSON in a directory, keyed by essay id.

    Non-result and unreadable JSON files are skipped, as are results from older
    marker versions that stored integer 0-100 marks (see :func:`outdated_results`).
    """
    return _read_dir(directory)[0]


def outdated_results(directory: str | Path) -> list[str]:
    """Essay ids of results from older marker versions (integer 0-100 marks) that need re-marking."""
    return _read_dir(directory)[1]


def discover_result_dirs(paths: list[str]) -> list[Path]:
    """Expand the given paths into directories that contain marker results.

    A path that contains result JSON files is used as it is. A path with
    no results of its own, such as the ``--out`` directory of a multi-model
    ``grade`` run, is replaced by its subdirectories that contain results.

    Raises:
        ValueError: No result directories were found. If outdated results were
            found instead, the message says to re-run ``marker grade``.
    """
    found: list[Path] = []
    outdated: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if not p.is_dir():
            raise ValueError(f"{p}: not a directory")
        candidates = [p] + sorted(d for d in p.iterdir() if d.is_dir())
        for d in candidates:
            current, old = _read_dir(d)
            if current:
                found.append(d)
            elif old:
                outdated.append(d)
            if d == p and current:
                break  # results directly in p; don't look at its subdirectories
    if not found:
        hint = ""
        if outdated:
            hint = (
                f"; {', '.join(map(str, outdated))} hold results from an older marker version "
                "(integer 0-100 marks); re-run 'marker grade' to re-mark them"
            )
        raise ValueError(f"no current marker results found in {', '.join(paths)}{hint}")
    return list(dict.fromkeys(found))


def label_runs(dirs: list[Path]) -> dict[str, dict[str, dict[str, Any]]]:
    """Load each directory and label it with its model name.

    If two directories used the same model (for example with different
    settings), they are labelled ``model (dir-name)`` so both stay distinct.
    """
    loaded = [(d, load_results(d)) for d in dirs]
    models = [next(iter(r.values()))["provenance"]["model"] for _, r in loaded]
    runs: dict[str, dict[str, dict[str, Any]]] = {}
    for (d, results), model in zip(loaded, models):
        label = model if models.count(model) == 1 else f"{model} ({d.name})"
        runs[label] = results
    return runs


def common_scale(runs: dict[str, dict[str, dict[str, Any]]]) -> Scale | None:
    """The reporting scale shared by every result, or None if results use different scales."""
    scales = {
        (str(r["score"]["min"]), str(r["score"]["max"]), int(r["score"]["places"]))
        for results in runs.values()
        for r in results.values()
        if "score" in r
    }
    if len(scales) != 1:
        return None
    lo, hi, places = scales.pop()
    return Scale(dec(lo), dec(hi), places)


def compare_runs(
    runs: dict[str, dict[str, dict[str, Any]]], rubric: Rubric, threshold: Decimal = DEFAULT_THRESHOLD
) -> dict[str, Any]:
    """Compare several models' results for the same essays.

    Args:
        runs: Model label to (essay id to result).
        rubric: Rubric used to map panel marks to bands and to list criteria.
        threshold: Models "disagree" if their overall marks spread by more than
            this fraction (default 0.1000, i.e. 10 percentage points).

    Returns:
        A report (JSON-serialisable with :func:`marker.scale.dumps`) with keys
        ``models``, ``threshold``, ``scale`` (the shared reporting scale, or
        None), ``essays`` (per-essay comparison), ``pairs`` (pairwise
        agreement) and ``per_model`` (summary statistics). All marks are
        fractions 0.0-1.0 in the current numeric mode.

    Raises:
        ValueError: Fewer than two runs were given.
    """
    if len(runs) < 2:
        raise ValueError("need results from at least two models to compare")
    labels = list(runs)
    crit_ids = [c.id for c in rubric.criteria]
    ids = sorted(set().union(*(set(r) for r in runs.values())))
    scale = common_scale(runs)

    essays: list[dict[str, Any]] = []
    for essay_id in ids:
        present = {m: runs[m][essay_id] for m in labels if essay_id in runs[m]}
        marks = {m: normalise_mark(r["mark"]) for m, r in present.items()}
        panel = median(marks.values())
        panel_mark = normalise_mark(panel)
        spread = max(marks.values()) - min(marks.values())
        bands = {m: r["band"]["id"] for m, r in present.items()}
        criteria = {
            cid: {m: normalise_mark(r["criteria"][cid]["mark"]) for m, r in present.items() if cid in r["criteria"]}
            for cid in crit_ids
        }
        criteria_spread = {cid: (max(v.values()) - min(v.values())) if v else zero() for cid, v in criteria.items()}
        same_band = len(set(bands.values())) == 1
        notes: list[str] = []
        if not same_band:
            notes.append("models place the essay in different bands")
        if spread > num(threshold):
            notes.append(f"overall marks spread by {spread:.4f} (> {num(threshold):.4f})")
        missing = [m for m in labels if m not in present]
        if missing:
            notes.append(f"not marked by: {', '.join(missing)}")
        flagged = [m for m, r in present.items() if r.get("needs_review")]
        if flagged:
            notes.append(f"flagged for review by: {', '.join(flagged)}")
        essays.append(
            {
                "id": essay_id,
                "marks": marks,
                "scores": {m: num(r["score"]["value"]) for m, r in present.items() if "score" in r} if scale else {},
                "bands": bands,
                "criteria": criteria,
                "criteria_spread": criteria_spread,
                "spread": spread,
                "panel_median": panel,
                "panel_mark": panel_mark,
                "panel_score": scale.from_fraction(panel) if scale else None,
                "panel_band": rubric.band_for(panel_mark).id,
                "models_agree": same_band and spread <= num(threshold) and not missing,
                "notes": notes,
            }
        )

    # Band index (0 = lowest) makes band distances ordinal, for agreement statistics.
    band_order = [b.id for b in reversed(rubric.bands)]
    pairs: list[dict[str, Any]] = []
    for a, b in itertools.combinations(labels, 2):
        common = sorted(set(runs[a]) & set(runs[b]))
        pair: dict[str, Any] = {"a": a, "b": b, "n": len(common)}
        if common:
            ma = [normalise_mark(runs[a][i]["mark"]) for i in common]
            mb = [normalise_mark(runs[b][i]["mark"]) for i in common]
            n = num(len(common))
            diffs = [x - y for x, y in zip(ma, mb)]
            ba = [band_order.index(runs[a][i]["band"]["id"]) for i in common]
            bb = [band_order.index(runs[b][i]["band"]["id"]) for i in common]
            pair.update(
                {
                    "mean_diff": sum(diffs, zero()) / n,  # a minus b; positive means a is more generous
                    "mae": sum((abs(d) for d in diffs), zero()) / n,
                    "exact_band_agreement": num(sum(x == y for x, y in zip(ba, bb))) / n,
                    # QWK needs integer categories (whole percentage points) and at least two essays.
                    "qwk_marks": num(quadratic_weighted_kappa(
                        [round_half_up(x * 100) for x in ma], [round_half_up(y * 100) for y in mb], 0, 100
                    ))
                    if len(common) >= 2
                    else None,
                    "criteria_mae": {
                        cid: sum(
                            (
                                abs(normalise_mark(runs[a][i]["criteria"][cid]["mark"]) - normalise_mark(runs[b][i]["criteria"][cid]["mark"]))
                                for i in common
                            ),
                            zero(),
                        )
                        / n
                        for cid in crit_ids
                    },
                }
            )
        pairs.append(pair)

    per_model: dict[str, dict[str, Any]] = {}
    for m in labels:
        ms = [normalise_mark(r["mark"]) for r in runs[m].values()]
        any_result = next(iter(runs[m].values()))
        per_model[m] = {
            "n": len(ms),
            "mean_mark": statistics.mean(ms),
            "sd_mark": statistics.stdev(ms) if len(ms) > 1 else zero(),
            "needs_review": sum(bool(r.get("needs_review")) for r in runs[m].values()),
            "model": any_result["provenance"]["model"],
            "model_digest": any_result["provenance"]["model_digest"],
        }

    return {
        "models": labels,
        "threshold": threshold,
        "scale": scale.as_dict() if scale else None,
        "essays": essays,
        "pairs": pairs,
        "per_model": per_model,
    }


def write_comparison(out_dir: str | Path, report: dict[str, Any], rubric: Rubric) -> tuple[Path, Path]:
    """Write ``comparison.csv`` (one row per essay) and ``comparison.json`` (the full report).

    Marks are written as exact text: a float's shortest form or a Decimal without
    exponent. The CSV has ``id``, ``panel_mark`` (0.0-1.0) and, with a shared scale,
    ``panel_score`` columns, so the panel can be validated against human marks
    with ``marker evaluate --results comparison.csv --results-mark-column panel_score``.

    Returns:
        ``(csv_path, json_path)``.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    labels = report["models"]
    labels_by_id = {b.id: b.label for b in rubric.bands}
    has_scores = report["scale"] is not None
    csv_path = out / "comparison.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["id", *[f"{m} mark" for m in labels], *([f"{m} score" for m in labels] if has_scores else []),
             *[f"{m} band" for m in labels], "spread", "panel_median", "panel_mark",
             *(["panel_score"] if has_scores else []), "panel_band", "models_agree", "notes"]
        )
        for e in report["essays"]:
            w.writerow(
                [
                    e["id"],
                    *[_s(e["marks"].get(m)) for m in labels],
                    *([_s(e["scores"].get(m)) for m in labels] if has_scores else []),
                    *[labels_by_id.get(e["bands"].get(m, ""), "") for m in labels],
                    _s(e["spread"]),
                    _s(e["panel_median"]),
                    _s(e["panel_mark"]),
                    *([_s(e["panel_score"])] if has_scores else []),
                    labels_by_id[e["panel_band"]],
                    "yes" if e["models_agree"] else "no",
                    "; ".join(e["notes"]),
                ]
            )
    json_path = out / "comparison.json"
    json_path.write_text(dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return csv_path, json_path


def format_report(report: dict[str, Any], rubric: Rubric) -> str:
    """Render a comparison report as a plain-text table for the terminal.

    Marks are shown on the shared reporting scale (by default 0-100), or as
    0.0000-1.0000 fractions if the models' results use different scales.
    """
    labels: list[str] = report["models"]
    scale = Scale.from_dict(report["scale"]) if report["scale"] else FRACTION_SCALE
    span = scale.span  # in the current numeric mode

    def show(x: Num | None) -> str:
        """A mark on the display scale."""
        return "-" if x is None else scale.format(scale.from_fraction(x))

    def show_diff(x: Num) -> str:
        """A mark difference (spread, MAE) in display-scale units."""
        return scale.format(x * span)

    band_label = {b.id: b.label for b in rubric.bands}
    name_w = max(len("criterion"), *(len(c.id) for c in rubric.criteria), len("OVERALL"))
    col_w = max(8, *(len(m) for m in labels), *(len(b.label) for b in rubric.bands))
    lines = [
        f"Model comparison: {len(labels)} models, {len(report['essays'])} essay(s); marks shown on {scale.label()}",
        "",
    ]

    for e in report["essays"]:
        lines.append(f"Essay: {e['id']}")
        lines.append(f"  {'criterion':<{name_w}}  " + "  ".join(f"{m:>{col_w}}" for m in labels) + "  spread")
        for c in rubric.criteria:
            row = e["criteria"][c.id]
            cells = "  ".join(f"{show(row.get(m)):>{col_w}}" for m in labels)
            lines.append(f"  {c.id:<{name_w}}  {cells}  {show_diff(e['criteria_spread'][c.id]):>6}")
        cells = "  ".join(f"{show(e['marks'].get(m)):>{col_w}}" for m in labels)
        lines.append(f"  {'OVERALL':<{name_w}}  {cells}  {show_diff(e['spread']):>6}")
        cells = "  ".join(f"{band_label.get(e['bands'].get(m, ''), '-'):>{col_w}}" for m in labels)
        lines.append(f"  {'band':<{name_w}}  {cells}")
        lines.append(
            f"  panel mark: {show(e['panel_median'])} ({band_label[e['panel_band']]}), "
            f"median {e['panel_mark']:.4f}; models agree: {'yes' if e['models_agree'] else 'no'}"
        )
        for note in e["notes"]:
            lines.append(f"  - {note}")
        lines.append("")

    lines.append(f"Pairwise agreement (a vs b; mean diff = a - b, on {scale.label()})")
    for p in report["pairs"]:
        if not p["n"]:
            lines.append(f"  {p['a']} vs {p['b']}: no essays in common")
            continue
        qwk = "n/a (needs 2+ essays)" if p["qwk_marks"] is None else f"{p['qwk_marks']:.3f}"
        sign = "+" if p["mean_diff"] >= 0 else "-"
        lines.append(
            f"  {p['a']} vs {p['b']}: n={p['n']}  mean diff {sign}{show_diff(abs(p['mean_diff']))}  "
            f"MAE {show_diff(p['mae'])}  exact band {p['exact_band_agreement']:.0%}  QWK {qwk}"
        )
    return "\n".join(lines)


def _s(x: Num | None) -> str:
    """Exact text for a CSV cell (see :func:`marker.scale.text`); None gives ``""``."""
    return "" if x is None else text(x)
