"""Marks, numeric modes and score scales.

**Internal representation.** Every mark inside marker (rubric bands, model
answers, sample marks, medians, the weighted mark, thresholds) is a fraction
from 0.0 to 1.0. A UK First starts at 0.7. The Python type depends on the
**numeric mode**:

=========== ===================================================================
``float``   The default. Python ``float``, 0.0 to 1.0. Marks are used as the model
            gives them, and arithmetic is ordinary binary floating point
            (so ``0.1 + 0.2`` is ``0.30000000000000004``). Only reported scores are
            rounded, half up, using each float's shortest decimal representation,
            so ``0.62125`` rounds to ``0.6213`` as a person expects.
``decimal`` Opt-in (``--numeric decimal``). Exact :class:`decimal.Decimal` with
            exactly :data:`MARK_PLACES` (4) decimal places, 0.0000 to 1.0000.
            Model marks and the final mark are rounded half up to 4 places,
            ``0.1 + 0.2`` is exactly ``0.3``, and JSON stores decimals as exact
            strings (``"0.6213"``).
=========== ===================================================================

The mode is process-wide, like Python's own decimal context. The CLI sets it
once with :func:`set_numeric`, and :func:`num` converts any value (str, int,
float or Decimal) into the current mode, so the two types are never mixed in
arithmetic. Configuration values the user types, such as score bounds and
thresholds, are parsed exactly as Decimal and converted with :func:`num` where
they are used.

**Reporting scale.** A :class:`Scale` maps the unrounded weighted fraction onto
the scale the user chose for the final score:

    score = min + fraction x (max - min),  rounded half up to ``places`` decimals

The default is 0-100 integers, so a weighted fraction of 0.621 is reported as 62.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, TypeAlias

#: A mark or score in the current numeric mode: ``float`` (default) or ``Decimal``.
#: Declared as Any because mypy can't follow a runtime-selected type; :func:`num`
#: guarantees every value in a computation has the same type.
Num: TypeAlias = Any

#: The numeric modes. ``float`` is the default.
NUMERIC_MODES = ("float", "decimal")
DEFAULT_NUMERIC = "float"

#: Decimal places of internal marks in ``decimal`` mode (0.0000-1.0000).
MARK_PLACES = 4

#: Smallest step between ``decimal``-mode marks: 0.0001. Rubric bands are written to this grid.
MARK_QUANTUM = Decimal(1).scaleb(-MARK_PLACES)

#: Most decimal places allowed on a reporting scale.
MAX_PLACES = 6

_mode = DEFAULT_NUMERIC


def set_numeric(mode: str) -> None:
    """Set the process-wide numeric mode: ``"float"`` or ``"decimal"``.

    Raises:
        ValueError: Unknown mode.
    """
    global _mode
    if mode not in NUMERIC_MODES:
        raise ValueError(f"unknown numeric mode {mode!r}; choose from {', '.join(NUMERIC_MODES)}")
    _mode = mode


def get_numeric() -> str:
    """Return the current numeric mode."""
    return _mode


@contextmanager
def numeric(mode: str) -> Iterator[None]:
    """Temporarily switch numeric mode (mainly for tests): ``with numeric("decimal"): ...``."""
    previous = _mode
    set_numeric(mode)
    try:
        yield
    finally:
        set_numeric(previous)


def is_decimal() -> bool:
    """True in ``decimal`` mode."""
    return _mode == "decimal"


def dec(value: Any) -> Decimal:
    """Convert a value to :class:`Decimal` exactly, whatever the mode.

    Accepts ``Decimal``, ``int``, ``str`` (the exact JSON form) and ``float``.
    Floats go through ``repr()`` (their shortest round-trip form), so ``0.1``
    becomes ``Decimal("0.1")``, not ``0.1000000000000000055...``.

    Raises:
        ValueError: The value isn't a finite number (booleans are rejected).
    """
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, bool):
        raise ValueError(f"not a number: {value!r}")
    elif isinstance(value, (int, str)):
        try:
            d = Decimal(value.strip() if isinstance(value, str) else value)
        except InvalidOperation as e:
            raise ValueError(f"not a number: {value!r}") from e
    elif isinstance(value, float):
        d = Decimal(repr(value))
    else:
        raise ValueError(f"not a number: {value!r}")
    if not d.is_finite():
        raise ValueError(f"not a finite number: {value!r}")
    return d


def num(value: Any) -> Num:
    """Convert a value into the current numeric mode: ``float`` or ``Decimal``.

    Raises:
        ValueError: The value isn't a finite number.
    """
    d = dec(value)
    return d if is_decimal() else float(d)


def zero() -> Num:
    """``0`` in the current mode; start sums with it so the types never mix."""
    return num(0)


def quantize(value: Num, places: int) -> Num:
    """Round half up to ``places`` decimal places, keeping the input's type.

    For a float, rounding is applied to its shortest decimal form, so
    ``quantize(0.625, 2) == 0.63`` and ``quantize(0.62125, 4) == 0.6213``.
    """
    q = dec(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return q if isinstance(value, Decimal) else float(q)


def quantize_mark(value: Any) -> Num:
    """Round a value half up to 4 places, in the current mode: ``quantize_mark("0.62125")`` gives 0.6213."""
    return quantize(num(value), MARK_PLACES)


def normalise_mark(value: Any) -> Num:
    """The canonical internal form of a mark: 4-place Decimal in ``decimal`` mode, or the plain float as given."""
    return quantize_mark(value) if is_decimal() else num(value)


def round_half_up(value: Any) -> int:
    """Round to the nearest integer, with .5 rounding up: ``round_half_up(64.5) == 65`` (unlike built-in ``round``)."""
    return int(dec(value).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def median(values: Iterable[Any]) -> Num:
    """Median in the current mode. An even count gives the midpoint (0.62 and 0.63 give 0.625)."""
    xs = sorted(num(v) for v in values)
    if not xs:
        raise ValueError("median of no values")
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


def sqrt(value: Num) -> Num:
    """Square root, keeping the input's type."""
    return value.sqrt() if isinstance(value, Decimal) else math.sqrt(value)


def text(value: Any) -> str:
    """Exact text for a mark: Decimal without exponent (``"0.6213"``), float as its shortest form (``"0.6213"``)."""
    if isinstance(value, Decimal):
        return f"{value:f}"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def fmt_mark(value: Any) -> str:
    """Readable 4-place text for a mark, for terminal output: ``0.645`` gives ``"0.6450"``."""
    return f"{quantize(dec(value), MARK_PLACES):f}"


def fmt_percent(value: Any) -> str:
    """Display text for a 0-1 fraction as a whole percent, with banker's rounding (half to even).

    This is the format for every mark, weight and rate shown in the terminal.
    Rounding uses the exact decimal value (a float's shortest form), so the
    float ``0.135`` is 13.5% and rounds to ``"14%"``, ``0.125`` rounds to
    ``"12%"``, and ``0.575`` to ``"58%"`` (``f"{0.575:.0%}"`` gives ``"57%"``,
    because the binary float is slightly below 0.575). Stored data (JSON, CSV)
    and reported scores keep their exact values.
    """
    n = int(dec(value).scaleb(2).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
    return f"{n}%"


def places_of(written: str) -> int:
    """Count the decimal places written in a number: ``"1.00"`` gives 2, ``"100"`` gives 0."""
    exponent = dec(written).as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


@dataclass(frozen=True)
class Scale:
    """A reporting scale for final scores.

    The bounds are exact Decimals (they're configuration); scores computed with
    them follow the current numeric mode.

    Attributes:
        min: Score given for 0.0.
        max: Score given for 1.0. Must be greater than ``min``.
        places: Decimal places in reported scores (0 to :data:`MAX_PLACES`).
    """

    min: Decimal
    max: Decimal
    places: int

    def __post_init__(self) -> None:
        if not (self.min.is_finite() and self.max.is_finite()):
            raise ValueError("score bounds must be finite numbers")
        if self.min >= self.max:
            raise ValueError(f"--score-min ({self.min}) must be less than --score-max ({self.max})")
        if not 0 <= self.places <= MAX_PLACES:
            raise ValueError(f"--score-places must be between 0 and {MAX_PLACES}")

    @classmethod
    def parse(cls, min_text: str = "0", max_text: str = "100", places: int | None = None) -> Scale:
        """Build a scale from command-line text.

        If ``places`` is None, it is the larger number of decimal places written
        in the two bounds, so ``"0.00"`` and ``"1.00"`` give 2 places.

        Raises:
            ValueError: A bound isn't a finite number, ``min >= max``, or ``places`` is out of range.
        """
        lo, hi = dec(min_text), dec(max_text)
        return cls(lo, hi, places if places is not None else max(places_of(min_text), places_of(max_text)))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Scale:
        """Inverse of :meth:`as_dict`."""
        return cls(dec(d["min"]), dec(d["max"]), int(d["places"]))

    def as_dict(self) -> dict[str, Any]:
        """JSON form with the bounds as exact strings: ``{"min": "0.00", "max": "1.00", "places": 2}``."""
        return {"min": self.format(self.min), "max": self.format(self.max), "places": self.places}

    @property
    def span(self) -> Num:
        """``max - min`` in the current mode, for converting mark differences to score units."""
        return num(self.max - self.min)

    def from_fraction(self, fraction: Any) -> Num:
        """Map an internal 0-1 mark onto this scale, rounded half up to ``places``, in the current mode."""
        return quantize(num(self.min) + num(fraction) * self.span, self.places)

    def to_fraction(self, score: Any) -> Num:
        """Map a score on this scale back to an internal 0-1 mark (canonical form for the current mode)."""
        return normalise_mark((num(score) - num(self.min)) / self.span)

    def format(self, value: Any) -> str:
        """Format a value with exactly ``places`` decimals, e.g. ``"0.60"``. Never uses exponent notation."""
        return f"{quantize(dec(value), self.places):f}"

    def label(self) -> str:
        """Human-readable range, e.g. ``"0.00-1.00"``."""
        return f"{self.format(self.min)}-{self.format(self.max)}"

    @property
    def is_default(self) -> bool:
        """True for the plain 0-100 integer scale."""
        return self == DEFAULT_SCALE


#: The default reporting scale: integers 0-100, the UK percentage mark.
DEFAULT_SCALE = Scale(Decimal(0), Decimal(100), 0)

#: The internal scale itself, shown to 4 places: 0.0000-1.0000.
FRACTION_SCALE = Scale(Decimal(0), Decimal(1), MARK_PLACES)


def json_default(o: Any) -> Any:
    """``json.dumps(default=...)`` hook: write any :class:`Decimal` as an exact string. Floats are native JSON numbers."""
    if isinstance(o, Decimal):
        return f"{o:f}"
    raise TypeError(f"{type(o).__name__} is not JSON serialisable")


def dumps(obj: Any, **kwargs: Any) -> str:
    """:func:`json.dumps` that also handles Decimals (as exact strings)."""
    return json.dumps(obj, default=json_default, **kwargs)


def parse_float() -> Any:
    """The ``parse_float`` hook for :func:`json.loads` and :func:`tomllib.loads` in the current mode."""
    return Decimal if is_decimal() else float
