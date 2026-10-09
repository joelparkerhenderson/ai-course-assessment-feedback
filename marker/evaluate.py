"""Compare machine marks with human marks, for calibration and validation.

Before the marker is trusted on live assessments, it should be run on essays
that experienced staff have already marked, and the agreement measured. The
metrics follow automated-essay-scoring practice:

* **QWK** (quadratic weighted kappa; Cohen, 1968): chance-corrected agreement
  in which large disagreements cost more than near misses. 1.0 is perfect
  agreement and 0 is chance level. Compare it with human-human QWK on the same essays.
* **MAE / RMSE**: typical size of the disagreement, in marks.
* **Mean bias**: machine minus human. A positive value means the machine is more generous.
* **Pearson r**: linear correlation. It can be high even when there is a constant bias.
* **Exact / adjacent band agreement**: how often the classification band
  matches, or is at most one band away.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any

from .rubric import Rubric


def quadratic_weighted_kappa(a: list[int], b: list[int], lo: int, hi: int) -> float:
    """Cohen's quadratic weighted kappa for two raters' integer ratings.

    Args:
        a: First rater's ratings.
        b: Second rater's ratings, in the same order as ``a``.
        lo: Lowest possible rating.
        hi: Highest possible rating.

    Returns:
        Kappa, at most 1.0. If both raters give one constant rating
        (no variance), 1.0 is returned.

    Raises:
        ValueError: The lists differ in length or are empty.
    """
    if len(a) != len(b) or not a:
        raise ValueError("need two equal-length, non-empty rating lists")
    k = hi - lo + 1
    # Observed confusion matrix.
    observed = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b):
        observed[x - lo][y - lo] += 1
    n = len(a)
    hist_a = [sum(row) for row in observed]
    hist_b = [sum(observed[i][j] for i in range(k)) for j in range(k)]
    # kappa = 1 - sum(w * observed) / sum(w * expected), with expected = outer(hist_a, hist_b) / n
    # and quadratic weights w = (i - j)^2 / (k - 1)^2.
    num = den = 0.0
    for i in range(k):
        for j in range(k):
            w = (i - j) ** 2 / (k - 1) ** 2 if k > 1 else 0.0
            num += w * observed[i][j]
            den += w * hist_a[i] * hist_b[j] / n
    return 1.0 if den == 0 else 1.0 - num / den


def pearson(a: list[float], b: list[float]) -> float:
    """Pearson correlation coefficient. Returns NaN if either list has no variance."""
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = math.sqrt(sum((x - ma) ** 2 for x in a))
    vb = math.sqrt(sum((y - mb) ** 2 for y in b))
    return float("nan") if va == 0 or vb == 0 else cov / (va * vb)


def read_marks(path: str | Path, id_col: str, mark_col: str) -> dict[str, float]:
    """Read ``id -> mark`` from a CSV file with a header row.

    Rows with an empty mark are skipped. Ids are whitespace-stripped and must
    match essay file names without their extensions.

    Raises:
        ValueError: A required column is missing, or a mark isn't a number.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = {id_col, mark_col} - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing column(s) {sorted(missing)}; found {reader.fieldnames}")
        return {row[id_col].strip(): float(row[mark_col]) for row in reader if row[mark_col].strip()}


def evaluate(machine: dict[str, float], human: dict[str, float], rubric: Rubric) -> dict[str, Any]:
    """Compute agreement metrics between machine and human marks.

    Only essays present in both inputs are compared. The ids found in only one
    input are listed so mismatches are visible.

    Args:
        machine: Essay id to machine mark.
        human: Essay id to human mark.
        rubric: Rubric used to map marks to bands.

    Returns:
        Dict with ``n``, ``mae``, ``rmse``, ``mean_bias``, ``pearson_r``,
        ``qwk_marks`` (on 0-100), ``qwk_bands`` (on band index),
        ``exact_band_agreement``, ``adjacent_band_agreement``,
        ``unmatched_machine``, ``unmatched_human`` and ``per_essay`` details.

    Raises:
        ValueError: No ids are in common.
    """
    ids = sorted(set(machine) & set(human))
    if not ids:
        raise ValueError("no essay ids in common between machine and human marks")
    m = [machine[i] for i in ids]
    h = [human[i] for i in ids]
    diffs = [x - y for x, y in zip(m, h)]
    # Band index: 0 = lowest band, so the ordinal distance between bands is meaningful for QWK.
    band_order = [b.id for b in reversed(rubric.bands)]
    mb = [band_order.index(rubric.band_for(x).id) for x in m]
    hb = [band_order.index(rubric.band_for(x).id) for x in h]
    return {
        "n": len(ids),
        "unmatched_machine": sorted(set(machine) - set(human)),
        "unmatched_human": sorted(set(human) - set(machine)),
        "mae": sum(abs(d) for d in diffs) / len(diffs),
        "rmse": math.sqrt(sum(d * d for d in diffs) / len(diffs)),
        "mean_bias": sum(diffs) / len(diffs),
        "pearson_r": pearson(m, h),
        "qwk_marks": quadratic_weighted_kappa([round(x) for x in m], [round(x) for x in h], 0, 100),
        "qwk_bands": quadratic_weighted_kappa(mb, hb, 0, len(band_order) - 1),
        "exact_band_agreement": sum(x == y for x, y in zip(mb, hb)) / len(ids),
        "adjacent_band_agreement": sum(abs(x - y) <= 1 for x, y in zip(mb, hb)) / len(ids),
        "per_essay": [{"id": i, "machine": x, "human": y, "diff": x - y} for i, x, y in zip(ids, m, h)],
    }
