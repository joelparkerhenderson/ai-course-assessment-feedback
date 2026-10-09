"""Load and validate analytic marking rubrics stored as TOML.

A rubric has two parts:

* **bands**: classification bands that partition the 0-100 scale (e.g. First
  70-79, 2:1 60-69, ...), each with a general descriptor; and
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
from pathlib import Path
from typing import Any


class RubricError(ValueError):
    """The rubric file is malformed or breaks a structural rule."""


@dataclass(frozen=True)
class Band:
    """One classification band on the 0-100 scale.

    Attributes:
        id: Stable identifier the model uses in its answer, e.g. ``"upper_second"``.
        label: Human-readable name, e.g. ``"Upper second (2:1)"``.
        min: Lowest mark in the band (inclusive).
        max: Highest mark in the band (inclusive).
        descriptor: General description of work in this band.
    """

    id: str
    label: str
    min: int
    max: int
    descriptor: str

    def contains(self, mark: float) -> bool:
        """Return True if ``mark`` lies within ``[min, max]``."""
        return self.min <= mark <= self.max


@dataclass(frozen=True)
class Criterion:
    """One weighted marking criterion.

    Attributes:
        id: Stable identifier and JSON key, e.g. ``"argument"``.
        name: Human-readable name.
        weight: Share of the overall mark. All weights in a rubric sum to 1.0.
        description: What the criterion assesses.
        descriptors: Band id to what this criterion looks like in that band.
            Bands may be omitted, in which case the band's general descriptor applies.
    """

    id: str
    name: str
    weight: float
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

    def band_for(self, mark: float) -> Band:
        """Return the band containing ``mark``, rounding half up first.

        Rounding first matters for fractional marks such as a median of 69.5,
        which belongs to the band containing 70.

        Raises:
            RubricError: ``mark`` is outside 0-100.
        """
        rounded = int(mark + 0.5)  # round half up, consistent with grader.round_half_up
        for band in self.bands:
            if band.contains(rounded):
                return band
        raise RubricError(f"mark {mark} is outside every band")

    @property
    def boundaries(self) -> list[int]:
        """Lower bounds of every band except the lowest, e.g. ``[80, 70, 60, 50, 40, 30]``."""
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
        data = tomllib.loads(raw_bytes.decode("utf-8"))
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
                    Band(str(b["id"]), str(b["label"]), int(b["min"]), int(b["max"]), str(b["descriptor"]))
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
                float(c["weight"]),
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
    * bands cover 0-100 exactly, with no gaps or overlaps;
    * criterion weights are positive and sum to 1.0;
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

    # Walk down from 100: each band must end one below where the previous band began.
    expected = 100
    for band in rubric.bands:
        if band.min > band.max:
            raise RubricError(f"band {band.id}: min > max")
        if band.max != expected:
            raise RubricError(f"bands must cover 0-100 without gaps or overlaps (problem at band {band.id})")
        expected = band.min - 1
    if expected != -1:
        raise RubricError("the lowest band must start at 0")

    cids = [c.id for c in rubric.criteria]
    if len(set(cids)) != len(cids):
        raise RubricError("criterion ids must be unique")
    for c in rubric.criteria:
        if c.weight <= 0:
            raise RubricError(f"criterion {c.id}: weight must be positive")
        unknown = set(c.descriptors) - set(ids)
        if unknown:
            raise RubricError(f"criterion {c.id}: descriptors for unknown bands {sorted(unknown)}")
    total = sum(c.weight for c in rubric.criteria)
    if abs(total - 1.0) > 1e-6:  # tolerate floating-point error, e.g. 0.1 + 0.2
        raise RubricError(f"criterion weights must sum to 1.0 (got {total:g})")
