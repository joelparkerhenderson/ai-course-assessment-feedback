"""Load and validate analytic marking rubrics stored as TOML.

A rubric has two parts:

* **bands**: classification bands that partition the internal 0.0-1.0 scale
  (e.g. First 0.7000-0.7999, 2:1 0.6000-0.6999, ...), written to 4 decimal
  places so the same file works in both numeric modes, each with a general
  descriptor; and
* **criteria**: weighted dimensions of quality (e.g. argument, evidence), each
  with a descriptor per band.

The model judges each criterion against its band descriptors. The weighted
overall mark is then calculated in code. See ``rubrics/uk-undergraduate-essay.toml``
for a complete example, and the ``write-rubric`` skill for authoring guidance.
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .scale import MARK_PLACES, MARK_QUANTUM, Num, dec, normalise_mark, num, parse_float


class RubricError(ValueError):
    """The rubric file is malformed or breaks a structural rule."""


@dataclass(frozen=True)
class Band:
    """One classification band on the internal 0.0-1.0 scale.

    Attributes:
        id: Stable identifier the model uses in its answer, e.g. ``"upper_second"``.
        label: Human-readable name, e.g. ``"Upper second (2:1)"``.
        min: Lowest mark in the band (inclusive), e.g. ``0.7``. A float or a
            Decimal, depending on the numeric mode when the rubric was loaded.
        max: Highest mark in the band as written, e.g. ``0.7999``. In float mode a
            mark between this and the next band's ``min`` (e.g. 0.79995) belongs to
            this band; see :meth:`Rubric.band_for`.
        descriptor: General description of work in this band.
    """

    id: str
    label: str
    min: Num
    max: Num
    descriptor: str

    def contains(self, mark: Num) -> bool:
        """Return True if ``mark`` lies within ``[min, max]``."""
        return self.min <= mark <= self.max


@dataclass(frozen=True)
class Criterion:
    """One weighted marking criterion.

    Attributes:
        id: Stable identifier and JSON key, e.g. ``"argument"``.
        name: Human-readable name.
        weight: Share of the overall mark (float or Decimal, by numeric mode). The
            weights as written in the file sum to exactly 1.
        description: What the criterion assesses.
        descriptors: Band id to what this criterion looks like in that band.
            Bands may be omitted, in which case the band's general descriptor applies.
    """

    id: str
    name: str
    weight: Num
    description: str
    descriptors: dict[str, str]


@dataclass(frozen=True)
class Rubric:
    """A validated analytic rubric.

    Attributes:
        name: Rubric title shown to the model.
        version: Rubric version string, recorded in provenance.
        level: Optional study level, e.g. ``"Undergraduate"``.
        bands: Bands sorted from highest to lowest.
        criteria: Criteria in file order.
        min_words: Flag essays shorter than this. ``0`` disables the check.
        max_words: Flag essays longer than this. ``0`` disables the check.
        sha256: SHA-256 of the rubric file bytes, recorded in provenance.
    """

    name: str
    version: str
    level: str
    bands: tuple[Band, ...]
    criteria: tuple[Criterion, ...]
    min_words: int
    max_words: int
    sha256: str

    def band_for(self, mark: Any) -> Band:
        """Return the band for ``mark``: the highest band whose ``min`` is at or below it.

        Using band minimums makes the bands continuous, which floats need: a
        float mark of 0.79995 lies between First (max 0.7999) and Exceptional
        first (min 0.8) and belongs to First. In ``decimal`` mode the mark is
        first rounded half up to 4 places, so the median 0.69995 of two samples
        becomes 0.7000, a First.

        Raises:
            RubricError: ``mark`` is outside 0.0-1.0.
        """
        m = normalise_mark(mark)
        if not 0 <= m <= 1:
            raise RubricError(f"mark {mark} is outside 0.0-1.0")
        for band in self.bands:  # highest first
            if m >= band.min:
                return band
        raise RubricError(f"mark {mark} is below every band")

    @property
    def boundaries(self) -> list[Num]:
        """Lower bounds of every band except the lowest, e.g. ``[0.8000, 0.7000, ..., 0.3000]``."""
        return [b.min for b in self.bands if b.min > 0]


def load_rubric(path: str | Path) -> Rubric:
    """Read, parse and validate a rubric TOML file.

    Args:
        path: Path to the ``.toml`` file.

    Returns:
        The validated rubric, with ``sha256`` set to the hash of the file bytes.

    Raises:
        RubricError: The file isn't valid TOML or breaks a rubric rule.
        OSError: The file can't be read.
    """
    raw_bytes = Path(path).read_bytes()
    try:
        # Numbers are read in the current numeric mode: float by default, or exact Decimal.
        data = tomllib.loads(raw_bytes.decode("utf-8"), parse_float=parse_float())
    except tomllib.TOMLDecodeError as e:
        raise RubricError(f"{path}: invalid TOML: {e}") from e
    return parse_rubric(data, hashlib.sha256(raw_bytes).hexdigest())


def parse_rubric(data: dict[str, Any], sha256: str = "") -> Rubric:
    """Build a :class:`Rubric` from already-parsed TOML or JSON data and validate it.

    Args:
        data: Mapping with ``bands`` and ``criteria`` lists, plus optional
            ``name``, ``version``, ``level``, ``min_words`` and ``max_words``.
        sha256: Hash to record as the rubric's identity.

    Raises:
        RubricError: Required fields are missing or the rubric is invalid
            (see :func:`validate`).
    """
    try:
        bands = tuple(
            sorted(
                (
                    Band(str(b["id"]), str(b["label"]), num(b["min"]), num(b["max"]), str(b["descriptor"]))
                    for b in data["bands"]
                ),
                key=lambda b: b.min,
                reverse=True,  # highest band first; prompts and band_for rely on this order
            )
        )
        criteria = tuple(
            Criterion(
                str(c["id"]),
                str(c["name"]),
                num(c["weight"]),
                str(c.get("description", "")),
                {str(k): str(v) for k, v in c.get("descriptors", {}).items()},
            )
            for c in data["criteria"]
        )
    except (KeyError, TypeError, ValueError) as e:
        raise RubricError(f"malformed rubric: {e!r}") from e

    rubric = Rubric(
        name=str(data.get("name", "Unnamed rubric")),
        version=str(data.get("version", "0")),
        level=str(data.get("level", "")),
        bands=bands,
        criteria=criteria,
        min_words=int(data.get("min_words", 0)),
        max_words=int(data.get("max_words", 0)),
        sha256=sha256,
    )
    validate(rubric)
    return rubric


def validate(rubric: Rubric) -> None:
    """Check a rubric's structural rules.

    The rules are:

    * at least one band and one criterion;
    * unique band ids and unique criterion ids;
    * band bounds have at most 4 decimal places, and the bands cover
      0.0000-1.0000 exactly: each band ends 0.0001 below where the band above
      begins, with no gaps or overlaps;

    These checks use exact Decimals of the numbers as written (floats via their
    shortest form), so a rubric is valid or invalid in both numeric modes alike.
    * criterion weights are positive and, as written, sum to exactly 1 (no tolerance);
    * criterion descriptors refer only to bands that exist.

    Raises:
        RubricError: The first rule broken.
    """
    if not rubric.bands:
        raise RubricError("rubric has no bands")
    if not rubric.criteria:
        raise RubricError("rubric has no criteria")

    ids = [b.id for b in rubric.bands]
    if len(set(ids)) != len(ids):
        raise RubricError("band ids must be unique")

    # Walk down from 1.0000: each band must end one step (0.0001) below where the band above began.
    expected = Decimal(1)
    for band in rubric.bands:
        lo, hi = dec(band.min), dec(band.max)
        for bound in (lo, hi):
            if bound != bound.quantize(MARK_QUANTUM):
                raise RubricError(f"band {band.id}: {bound} has more than {MARK_PLACES} decimal places")
        if lo > hi:
            raise RubricError(f"band {band.id}: min > max")
        if hi != expected:
            raise RubricError(
                f"bands must cover 0.0000-1.0000 without gaps or overlaps: band {band.id} should end at "
                f"{expected.quantize(MARK_QUANTUM)}, not {hi}"
            )
        expected = lo - MARK_QUANTUM
    if expected != -MARK_QUANTUM:
        raise RubricError("the lowest band must start at 0.0000")

    cids = [c.id for c in rubric.criteria]
    if len(set(cids)) != len(cids):
        raise RubricError("criterion ids must be unique")
    for c in rubric.criteria:
        if c.weight <= 0:
            raise RubricError(f"criterion {c.id}: weight must be positive")
        unknown = set(c.descriptors) - set(ids)
        if unknown:
            raise RubricError(f"criterion {c.id}: descriptors for unknown bands {sorted(unknown)}")
    # Summed as exact Decimals, so 0.1 + 0.2 + 0.7 is exactly 1 even when the weights are floats.
    total = sum((dec(c.weight) for c in rubric.criteria), Decimal(0))
    if total != 1:
        raise RubricError(f"criterion weights must sum to exactly 1 (got {total})")
