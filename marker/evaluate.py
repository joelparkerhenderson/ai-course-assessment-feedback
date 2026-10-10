"""Compare machine marks with human marks, for calibration and validation.

Statistics use the current numeric mode (floats by default, exact
:class:`~decimal.Decimal` in ``decimal`` mode); QWK is computed from exact
integer counts. Scores can be on any :class:`~marker.scale.Scale` (e.g. 0-100 or
0.0000-1.0000). For bands they are converted to the internal 0.0000-1.0000
fraction, and QWK uses whole percentage points (0-100) as its categories.

Before the marker is trusted on live assessments, it should be run on essays
that experienced staff have already marked, and the agreement measured. The
metrics follow automated-essay-scoring practice:

* **QWK** (quadratic weighted kappa; Cohen, 1968): chance-corrected agreement
  in which large disagreements cost more than near misses. 1.0 is perfect
  agreement and 0 is chance level. Compare it with human-human QWK on the same essays.
* **MAE / RMSE**: typical size of the disagreement, in score units (marks on
  the default 0-100 scale).
* **Mean bias**: machine minus human. A positive value means the machine is more generous.
* **Pearson r**: linear correlation. It can be high even when there is a constant bias.
* **Exact / adjacent band agreement**: how often the classification band
  matches, or is at most one band away.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from typing import Any

from .rubric import Rubric
from .scale import DEFAULT_SCALE, Num, Scale, num, round_half_up, sqrt, zero


def quadratic_weighted_kappa(a: list[int], b: list[int], lo: int, hi: int) -> Decimal:
    """Cohen's quadratic weighted kappa for two raters' integer ratings.

    Args:
        a: First rater's ratings.
        b: Second rater's ratings, in the same order as ``a``.
        lo: Lowest possible rating.
        hi: Highest possible rating.

    Returns:
        Kappa as an exact-arithmetic Decimal, at most 1. If both raters give one
        constant rating (no variance), 1 is returned.

    Raises:
        ValueError: The lists differ in length or are empty.
    """
    if len(a) != len(b) or not a:
        raise ValueError("need two equal-length, non-empty rating lists")
    k = hi - lo + 1
    # Observed confusion matrix (integer counts).
    observed = [[0] * k for _ in range(k)]
    for x, y in zip(a, b):
        observed[x - lo][y - lo] += 1
    n = len(a)
    hist_a = [sum(row) for row in observed]
    hist_b = [sum(observed[i][j] for i in range(k)) for j in range(k)]
    # kappa = 1 - sum(w * observed) / sum(w * expected), with expected = outer(hist_a, hist_b) / n
    # and quadratic weights w = (i - j)^2 / (k - 1)^2. The (k - 1)^2 factor cancels, so integer
    # weights (i - j)^2 give the same kappa. Multiplying the numerator by n instead of dividing
    # the expected counts keeps both sums as exact integers, so the only rounding is the
    # single final division.
    num = 0
    den = 0
    for i in range(k):
        if not hist_a[i]:
            continue  # an empty row adds nothing to either sum
        for j in range(k):
            w = (i - j) ** 2
            num += w * observed[i][j]
            den += w * hist_a[i] * hist_b[j]
    return Decimal(1) if den == 0 else 1 - Decimal(num * n) / Decimal(den)


def pearson(a: list[Num], b: list[Num]) -> Num | None:
    """Pearson correlation coefficient in the numeric mode. Returns None if either list has no variance."""
    n = num(len(a))
    ma, mb = sum(a, zero()) / n, sum(b, zero()) / n
    cov = sum(((x - ma) * (y - mb) for x, y in zip(a, b)), zero())
    va = sqrt(sum(((x - ma) ** 2 for x in a), zero()))
    vb = sqrt(sum(((y - mb) ** 2 for y in b), zero()))
    return None if va == 0 or vb == 0 else cov / (va * vb)


def read_marks(path: str | Path, id_col: str, mark_col: str) -> dict[str, Num]:
    """Read ``id -> mark`` from a CSV file with a header row, in the current numeric mode.

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
        return {row[id_col].strip(): num(row[mark_col]) for row in reader if row[mark_col].strip()}


def evaluate(
    machine: dict[str, Num], human: dict[str, Num], rubric: Rubric, scale: Scale = DEFAULT_SCALE
) -> dict[str, Any]:
    """Compute agreement metrics between machine and human scores.

    Only essays present in both inputs are compared. The ids found in only one
    input are listed so mismatches are visible.

    Args:
        machine: Essay id to machine score, on ``scale``.
        human: Essay id to human score, on the same ``scale``.
        rubric: Rubric used to map scores to bands (through their internal 0-1 fraction).
        scale: The scale both inputs use (default 0-100).

    Returns:
        Dict with ``n``, ``scale``, ``mae``, ``rmse`` and ``mean_bias`` (in
        score units), ``pearson_r`` (or None),
        ``qwk_marks`` (on whole percentage points 0-100), ``qwk_bands`` (on band
        index), ``exact_band_agreement``, ``adjacent_band_agreement``,
        ``unmatched_machine``, ``unmatched_human`` and ``per_essay`` details.

    Raises:
        ValueError: No ids are in common, or a score lies outside the scale.
    """
    ids = sorted(set(machine) & set(human))
    if not ids:
        raise ValueError("no essay ids in common between machine and human marks")
    # Convert to the current numeric mode so float and Decimal inputs never mix in arithmetic.
    m = [num(machine[i]) for i in ids]
    h = [num(human[i]) for i in ids]
    for i, x, y in zip(ids, m, h):
        if not (scale.min <= x <= scale.max and scale.min <= y <= scale.max):
            raise ValueError(f"{i}: score outside the {scale.label()} scale (machine {x}, human {y}); check --score-min/--score-max")
    n = num(len(ids))
    diffs = [x - y for x, y in zip(m, h)]
    # Internal fractions (0.0-1.0) put both raters on the rubric's scale for bands.
    mf = [scale.to_fraction(x) for x in m]
    hf = [scale.to_fraction(y) for y in h]
    # QWK needs integer categories; whole percentage points (0-100) are fine-grained enough.
    mp = [round_half_up(x * 100) for x in mf]
    hp = [round_half_up(y * 100) for y in hf]
    # Band index: 0 = lowest band, so the ordinal distance between bands is meaningful for QWK.
    band_order = [b.id for b in reversed(rubric.bands)]
    mb = [band_order.index(rubric.band_for(x).id) for x in mf]
    hb = [band_order.index(rubric.band_for(y).id) for y in hf]
    return {
        "n": len(ids),
        "scale": scale.as_dict(),
        "unmatched_machine": sorted(set(machine) - set(human)),
        "unmatched_human": sorted(set(human) - set(machine)),
        "mae": sum((abs(d) for d in diffs), zero()) / n,
        "rmse": sqrt(sum((d * d for d in diffs), zero()) / n),
        "mean_bias": sum(diffs, zero()) / n,
        "pearson_r": pearson(m, h),
        "qwk_marks": num(quadratic_weighted_kappa(mp, hp, 0, 100)),
        "qwk_bands": num(quadratic_weighted_kappa(mb, hb, 0, len(band_order) - 1)),
        "exact_band_agreement": num(sum(x == y for x, y in zip(mb, hb))) / n,
        "adjacent_band_agreement": num(sum(abs(x - y) <= 1 for x, y in zip(mb, hb))) / n,
        "per_essay": [{"id": i, "machine": x, "human": y, "diff": x - y} for i, x, y in zip(ids, m, h)],
    }
