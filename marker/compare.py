"""Compare marking results from several models (a "panel" of LLM markers).

A typical question: *"Mark this paper with model X and model Y, then compare
the results."* ``marker grade --model X --model Y`` writes one result directory
per model and then calls :func:`compare_runs`. ``marker compare`` does the same
for result directories that already exist.

For every essay the comparison reports:

* each model's overall mark, band and per-criterion marks;
* the **spread** (max - min) across models, overall and per criterion;
* a **panel mark**: the median of the models' overall marks, rounded half up.
  This is the multi-model analogue of double marking. Using several different
  judges ("a jury") reduces the effect of any one model's biases;
* whether the models **agree**: the same band, and an overall spread within the threshold.

For every pair of models it reports agreement over the essays both marked:
n, mean difference, MAE, exact band agreement and QWK, using the metrics in
:mod:`marker.evaluate`.
"""

from __future__ import annotations

import csv
import itertools
import json
import statistics
from pathlib import Path
from typing import Any

from .evaluate import quadratic_weighted_kappa
from .grader import round_half_up
from .rubric import Rubric


def _is_result(data: Any) -> bool:
    """True if a parsed JSON file looks like a marker result rather than some other JSON."""
    return isinstance(data, dict) and "mark" in data and "essay" in data and "provenance" in data


def load_results(directory: str | Path) -> dict[str, dict[str, Any]]:
    """Load every marker result JSON in a directory, keyed by essay id.

    Non-result and unreadable JSON files are skipped.
    """
    out: dict[str, dict[str, Any]] = {}
    for f in sorted(Path(directory).glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if _is_result(data):
            out[data["essay"]["id"]] = data
    return out


def discover_result_dirs(paths: list[str]) -> list[Path]:
    """Expand the given paths into directories that contain marker results.

    A path that contains result JSON files is used as it is. A path with
    no results of its own, such as the ``--out`` directory of a multi-model
    ``grade`` run, is replaced by its subdirectories that contain results.

    Raises:
        ValueError: No result directories were found.
    """
    found: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if not p.is_dir():
            raise ValueError(f"{p}: not a directory")
        if load_results(p):
            found.append(p)
        else:
            found.extend(sorted(d for d in p.iterdir() if d.is_dir() and load_results(d)))
    if not found:
        raise ValueError(f"no marker results found in {', '.join(paths)}")
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


def compare_runs(runs: dict[str, dict[str, dict[str, Any]]], rubric: Rubric, threshold: int = 10) -> dict[str, Any]:
    """Compare several models' results for the same essays.

    Args:
        runs: Model label to (essay id to result).
        rubric: Rubric used to map panel marks to bands and to list criteria.
        threshold: Models "disagree" if their overall marks spread by more than this.

    Returns:
        A JSON-serialisable report with keys ``models``, ``threshold``,
        ``essays`` (per-essay comparison), ``pairs`` (pairwise agreement) and
        ``per_model`` (summary statistics).

    Raises:
        ValueError: Fewer than two runs were given.
    """
    if len(runs) < 2:
        raise ValueError("need results from at least two models to compare")
    labels = list(runs)
    crit_ids = [c.id for c in rubric.criteria]
    ids = sorted(set().union(*(set(r) for r in runs.values())))

    essays: list[dict[str, Any]] = []
    for essay_id in ids:
        present = {m: runs[m][essay_id] for m in labels if essay_id in runs[m]}
        marks = {m: r["mark"] for m, r in present.items()}
        panel = statistics.median(marks.values())
        panel_mark = round_half_up(panel)
        spread = max(marks.values()) - min(marks.values())
        bands = {m: r["band"]["id"] for m, r in present.items()}
        criteria = {
            cid: {m: r["criteria"][cid]["mark"] for m, r in present.items() if cid in r["criteria"]} for cid in crit_ids
        }
        criteria_spread = {cid: (max(v.values()) - min(v.values())) if v else 0 for cid, v in criteria.items()}
        same_band = len(set(bands.values())) == 1
        notes: list[str] = []
        if not same_band:
            notes.append("models place the essay in different bands")
        if spread > threshold:
            notes.append(f"overall marks spread by {spread} (> {threshold})")
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
                "bands": bands,
                "criteria": criteria,
                "criteria_spread": criteria_spread,
                "spread": spread,
                "panel_median": panel,
                "panel_mark": panel_mark,
                "panel_band": rubric.band_for(panel_mark).id,
                "models_agree": same_band and spread <= threshold and not missing,
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
            ma = [runs[a][i]["mark"] for i in common]
            mb = [runs[b][i]["mark"] for i in common]
            diffs = [x - y for x, y in zip(ma, mb)]
            ba = [band_order.index(runs[a][i]["band"]["id"]) for i in common]
            bb = [band_order.index(runs[b][i]["band"]["id"]) for i in common]
            pair.update(
                {
                    "mean_diff": sum(diffs) / len(diffs),  # a minus b; positive means a is more generous
                    "mae": sum(abs(d) for d in diffs) / len(diffs),
                    "exact_band_agreement": sum(x == y for x, y in zip(ba, bb)) / len(common),
                    # QWK needs at least two essays to say anything about agreement beyond chance.
                    "qwk_marks": quadratic_weighted_kappa(ma, mb, 0, 100) if len(common) >= 2 else None,
                    "criteria_mae": {
                        cid: sum(
                            abs(runs[a][i]["criteria"][cid]["mark"] - runs[b][i]["criteria"][cid]["mark"])
                            for i in common
                        )
                        / len(common)
                        for cid in crit_ids
                    },
                }
            )
        pairs.append(pair)

    per_model: dict[str, dict[str, Any]] = {}
    for m in labels:
        ms = [r["mark"] for r in runs[m].values()]
        any_result = next(iter(runs[m].values()))
        per_model[m] = {
            "n": len(ms),
            "mean_mark": statistics.mean(ms),
            "sd_mark": statistics.stdev(ms) if len(ms) > 1 else 0.0,
            "needs_review": sum(bool(r.get("needs_review")) for r in runs[m].values()),
            "model": any_result["provenance"]["model"],
            "model_digest": any_result["provenance"]["model_digest"],
        }

    return {"models": labels, "threshold": threshold, "essays": essays, "pairs": pairs, "per_model": per_model}


def write_comparison(out_dir: str | Path, report: dict[str, Any], rubric: Rubric) -> tuple[Path, Path]:
    """Write ``comparison.csv`` (one row per essay) and ``comparison.json`` (the full report).

    The CSV has ``id`` and ``panel_mark`` columns, so ``marker evaluate
    --results comparison.csv --results-mark-column panel_mark`` can validate
    the panel against human marks.

    Returns:
        ``(csv_path, json_path)``.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    labels = report["models"]
    labels_by_id = {b.id: b.label for b in rubric.bands}
    csv_path = out / "comparison.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["id", *[f"{m} mark" for m in labels], *[f"{m} band" for m in labels],
             "spread", "panel_median", "panel_mark", "panel_band", "models_agree", "notes"]
        )
        for e in report["essays"]:
            w.writerow(
                [
                    e["id"],
                    *[e["marks"].get(m, "") for m in labels],
                    *[labels_by_id.get(e["bands"].get(m, ""), "") for m in labels],
                    e["spread"],
                    e["panel_median"],
                    e["panel_mark"],
                    labels_by_id[e["panel_band"]],
                    "yes" if e["models_agree"] else "no",
                    "; ".join(e["notes"]),
                ]
            )
    json_path = out / "comparison.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return csv_path, json_path


def format_report(report: dict[str, Any], rubric: Rubric) -> str:
    """Render a comparison report as a plain-text table for the terminal."""
    labels: list[str] = report["models"]
    band_label = {b.id: b.label for b in rubric.bands}
    name_w = max(len("criterion"), *(len(c.id) for c in rubric.criteria), len("OVERALL"))
    col_w = max(8, *(len(m) for m in labels))
    lines = [f"Model comparison: {len(labels)} models, {len(report['essays'])} essay(s)", ""]

    for e in report["essays"]:
        lines.append(f"Essay: {e['id']}")
        lines.append(f"  {'criterion':<{name_w}}  " + "  ".join(f"{m:>{col_w}}" for m in labels) + "  spread")
        for c in rubric.criteria:
            row = e["criteria"][c.id]
            cells = "  ".join(f"{_fmt(row.get(m)):>{col_w}}" for m in labels)
            lines.append(f"  {c.id:<{name_w}}  {cells}  {e['criteria_spread'][c.id]:>6}")
        cells = "  ".join(f"{_fmt(e['marks'].get(m)):>{col_w}}" for m in labels)
        lines.append(f"  {'OVERALL':<{name_w}}  {cells}  {e['spread']:>6}")
        cells = "  ".join(f"{band_label.get(e['bands'].get(m, ''), '-'):>{col_w}}" for m in labels)
        lines.append(f"  {'band':<{name_w}}  {cells}")
        lines.append(
            f"  panel mark: {e['panel_mark']} ({band_label[e['panel_band']]}), "
            f"median of {_fmt(e['panel_median'])}; models agree: {'yes' if e['models_agree'] else 'no'}"
        )
        for note in e["notes"]:
            lines.append(f"  - {note}")
        lines.append("")

    lines.append("Pairwise agreement (a vs b; mean diff = a - b)")
    for p in report["pairs"]:
        if not p["n"]:
            lines.append(f"  {p['a']} vs {p['b']}: no essays in common")
            continue
        qwk = "n/a (needs 2+ essays)" if p["qwk_marks"] is None else f"{p['qwk_marks']:.3f}"
        lines.append(
            f"  {p['a']} vs {p['b']}: n={p['n']}  mean diff {p['mean_diff']:+.1f}  "
            f"MAE {p['mae']:.1f}  exact band {p['exact_band_agreement']:.0%}  QWK {qwk}"
        )
    return "\n".join(lines)


def _fmt(x: float | int | None) -> str:
    """Format a mark for the table: integers stay integers, medians like 62.5 keep one decimal place, None becomes '-'."""
    if x is None:
        return "-"
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}"
