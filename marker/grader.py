"""Mark one essay: sample the model, validate each answer, aggregate, and flag for review.

This module is the core of the marker. For one essay it:

1. Builds the prompt (:mod:`marker.prompt`) and the JSON schema the model must follow.
2. Asks the model for ``Settings.samples`` independent judgements, each with its own
   fixed seed (``seed``, ``seed + 1``, ...) so the whole run can be reproduced.
3. Validates every answer (:func:`parse_response`) and retries an invalid one with a
   *derived* seed (``seed + 1000 * attempt``), so retries are reproducible too.
4. Aggregates criterion marks with the **median** across samples, then computes the
   weighted overall mark **in code**. The model never does arithmetic. Marks are
   fractions from 0.0 to 1.0: Python floats by default, or exact 4-place
   :class:`~decimal.Decimal` in ``decimal`` mode (see :mod:`marker.scale`).
5. Raises review flags for things a human examiner should look at: sample
   disagreement, borderline marks, quotes that aren't in the essay, word limits,
   possible prompt injection, and integrity concerns raised by the model.
6. Returns a JSON-serialisable result with full provenance (model digest, prompt
   hash, settings, versions) for audit and reproducibility.

See ``docs/research.md`` for the marking practice behind each step.
"""

from __future__ import annotations

import json
import platform
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from . import __version__
from .essay import Essay
from .ollama import OllamaError
from .prompt import PROMPT_VERSION, build_messages, response_schema, sha256_of
from .rubric import Rubric
from .scale import DEFAULT_SCALE, Num, Scale, fmt_mark, get_numeric, median, normalise_mark, num, parse_float, zero


class ChatClient(Protocol):
    """Anything that can answer a chat request the way :class:`marker.ollama.OllamaClient` does.

    Declared as a protocol so tests can pass a fake client without a running server.
    """

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any] | None,
        options: dict[str, Any],
        think: str | bool | None,
    ) -> dict[str, Any]: ...


@dataclass
class Settings:
    """Every knob that affects a marking run.

    All fields are recorded in each result's provenance, and every field except
    ``keep_reasoning`` feeds the input fingerprint (:func:`compute_fingerprint`).

    Attributes:
        model: Ollama model tag, e.g. ``"gpt-oss:20b"`` or ``"gpt-oss:120b"``.
        samples: Number of independent judgements per essay. The median of each
            criterion is used, which is robust to one outlier sample.
        seed: Base random seed. Sample *i* uses ``seed + i``.
        temperature: Sampling temperature. It is non-zero on purpose so the samples
            are genuinely independent; the fixed seeds keep them reproducible.
        top_p: Nucleus-sampling cut-off.
        think: Reasoning setting sent to Ollama: an effort level (``"low"``,
            ``"medium"``, ``"high"``) for gpt-oss, True for other thinking
            models, or None to leave it unset (see :func:`marker.models.think_for`).
        num_ctx: Context window in tokens. It must hold the rubric, brief, essay
            and the model's reasoning.
        max_retries: Extra attempts allowed when the model's answer is invalid.
        disagreement_threshold: Flag a criterion whose sample marks spread
            (max - min) by more than this fraction (default 0.1, i.e. 10 points).
            Stored as an exact Decimal (it's configuration) and converted to the
            numeric mode where it is used.
        boundary_margin: Flag a final mark within this fraction *below* a
            classification boundary (default 0.0200: 0.6800-0.6999 is borderline for 0.7000).
        keep_reasoning: Store the model's reasoning trace in each sample. It is
            useful for auditing but makes result files much larger.
    """

    model: str = "gpt-oss:20b"
    samples: int = 3
    seed: int = 42
    temperature: float = 0.6
    top_p: float = 1.0
    think: str | bool | None = "high"
    num_ctx: int = 32768
    max_retries: int = 2
    disagreement_threshold: Decimal = Decimal("0.1000")
    boundary_margin: Decimal = Decimal("0.0200")
    keep_reasoning: bool = False

    def options_for(self, seed: int) -> dict[str, Any]:
        """Return the Ollama ``options`` object for one request with the given seed."""
        return {"temperature": self.temperature, "top_p": self.top_p, "seed": seed, "num_ctx": self.num_ctx}

    def as_dict(self) -> dict[str, Any]:
        """Return the settings as a plain dict for JSON provenance."""
        return asdict(self)


class InvalidResponse(ValueError):
    """The model's answer was not valid JSON or did not satisfy the rubric's schema."""


# --- Prompt-injection heuristics --------------------------------------------------
# Students may (deliberately or not) include text aimed at an AI marker. The prompt
# already tells the model to ignore such text; these patterns *also* flag the
# essay for a human, because a heuristic check is independent of the model's behaviour.
# They are deliberately narrow to keep false positives on ordinary essays low.
INJECTION_PATTERNS: list[str] = [
    r"ignore (all |any )?(the |your )?(previous|prior|above|earlier) (instructions|prompts?|rules)",
    r"disregard (the |your )?(rubric|instructions|marking)",
    r"(you are|act as) (now )?(a|an|the) (marker|grader|examiner|assistant|ai|language model)",
    r"(give|award|assign|mark) (this|the|my) (essay|paper|work|assignment) (a |an )?(mark|grade|score)? ?(of )?(\d{2,3}|[01]?\.\d+)",
    # Chat-template control tokens and role labels, including the gpt-oss "harmony" tokens.
    r"\b(system prompt|developer message|assistant:|<\|im_start\|>|<\|start\|>|<\|channel\|>)",
    r"(note|message|instruction)s? (to|for) (the )?(ai|llm|language model|grader|marker|chatgpt|gpt)",
]
_INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.IGNORECASE)


def detect_injection(text: str) -> list[str]:
    """Find text in an essay that looks like instructions aimed at an AI marker.

    Args:
        text: The essay text.

    Returns:
        Up to five snippets, each a match plus about 40 characters of context
        either side. An empty list means nothing suspicious was found.
    """
    hits: list[str] = []
    for m in _INJECTION_RE.finditer(text):
        start = max(0, m.start() - 40)
        hits.append(text[start : m.end() + 40].replace("\n", " ").strip())
    return hits[:5]


# --- Evidence verification ----------------------------------------------------------
# Models sometimes "quote" text that isn't in the essay. Every quote is checked
# against the essay, ignoring differences that don't change the words: case,
# punctuation, spacing, Unicode dash and quote variants (e.g. the non-breaking
# hyphen U+2011 that gpt-oss often writes), and Markdown markup. A paraphrase
# ("The researcher used..." for "(Manalo, 2012) used...") still fails.

#: Fragments shorter than this many letters and digits are too short to check meaningfully.
_MIN_FRAGMENT = 8


def _normalise(s: str) -> str:
    """Reduce text to lower-case letters and digits only, for quote matching.

    NFKC folds compatibility characters (e.g. full-width letters, ligatures),
    and everything that isn't a letter or digit (punctuation, dashes, quotes,
    whitespace, Markdown markup) is dropped. ``"Chi‑Square"`` and
    ``"chi-square"`` both become ``"chisquare"``, and the essay's typo
    ``"thelowest"`` matches a quote that says ``"the lowest"``.
    """
    s = unicodedata.normalize("NFKC", s).lower()
    return "".join(ch for ch in s if ch.isalnum())


def quote_in_essay(quote: str, normalised_essay: str) -> bool:
    """Check that a quoted passage really appears in the essay.

    The quote is split on ellipses (``...``, ``…``, ``[...]``), and every
    fragment must appear in the essay after :func:`_normalise`. Fragments with
    fewer than 8 letters and digits are ignored, because they would match almost anything.

    Args:
        quote: A passage the model claims to have quoted.
        normalised_essay: The essay text, already passed through :func:`_normalise`.

    Returns:
        True if the quote is found, or too short to check meaningfully.
    """
    fragments = [_normalise(f) for f in re.split(r"\[\.\.\.\]|\.\.\.|…", quote)]
    fragments = [f for f in fragments if len(f) >= _MIN_FRAGMENT]
    if not fragments:
        return True  # too short to check meaningfully
    return all(f in normalised_essay for f in fragments)


# --- Response validation ----------------------------------------------------------


def parse_response(content: str, rubric: Rubric) -> tuple[dict[str, Any], list[str]]:
    """Parse and validate one model answer.

    Marks are returned in the current numeric mode: plain floats 0.0-1.0 (the
    default), or Decimals rounded half up to 4 places (``decimal`` mode).

    Ollama's ``format`` parameter constrains the output to the JSON schema, but
    the answer is still checked here: the schema can't express "mark inside the
    chosen band", and constrained decoding is a convenience, not a guarantee.

    When the band and mark disagree, the *band* wins: choosing the descriptor
    that fits is the core judgement, and the mark only places the essay within
    that band. The mark is clamped into the band and a note is returned.

    Args:
        content: The model's raw message content. A surrounding Markdown code
            fence, or prose around the JSON object, is tolerated.
        rubric: The rubric the essay was marked against.

    Returns:
        ``(parsed, notes)``. ``parsed`` has keys ``criteria`` (criterion id to
        mark, band, justification and evidence), ``strengths``, ``improvements``
        and ``integrity_concerns``. ``notes`` lists any corrections made, such as
        clamping.

    Raises:
        InvalidResponse: The content isn't JSON, a criterion is missing, or a
            mark isn't a number from 0.0000 to 1.0000.
    """
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        # Numbers are parsed in the numeric mode; in decimal mode 0.6450 never passes through a float.
        data = json.loads(text, parse_float=parse_float())
    except json.JSONDecodeError as e:
        # Some models wrap the JSON in prose even with a schema; try the outermost {...}.
        start, end = text.find("{"), text.rfind("}")
        try:
            if start == -1 or end <= start:
                raise e
            data = json.loads(text[start : end + 1], parse_float=parse_float())
        except json.JSONDecodeError:
            raise InvalidResponse(f"not valid JSON: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("criteria"), dict):
        raise InvalidResponse("missing 'criteria' object")

    notes: list[str] = []
    bands = {b.id: b for b in rubric.bands}
    criteria: dict[str, dict[str, Any]] = {}
    for c in rubric.criteria:
        item = data["criteria"].get(c.id)
        if not isinstance(item, dict):
            raise InvalidResponse(f"missing criterion {c.id!r}")

        # Marks are fractions 0.0-1.0. Accept JSON numbers (float, Decimal or int) and numeric
        # strings; reject booleans (bool is a subclass of int) and anything out of range, such as a
        # percentage like 65 given out of habit.
        raw = item.get("mark")
        try:
            if isinstance(raw, bool) or not isinstance(raw, (float, Decimal, int, str)):
                raise ValueError
            mark = num(raw)
        except ValueError:
            raise InvalidResponse(f"criterion {c.id!r}: mark must be a number 0.0000-1.0000, got {raw!r}") from None
        if not 0 <= mark <= 1:
            raise InvalidResponse(f"criterion {c.id!r}: mark must be between 0.0000 and 1.0000, got {raw!r}")
        if mark != normalise_mark(mark):  # only possible in decimal mode
            notes.append(f"{c.id}: mark {raw} rounded to 4 decimal places")
        mark = normalise_mark(mark)

        band = bands.get(str(item.get("band", "")))
        if band is None:
            # Missing or unknown band id: work it out from the mark.
            band = rubric.band_for(mark)
        elif not band.contains(mark):
            # The band is the descriptor judgement; keep the mark inside it.
            clamped = min(max(mark, band.min), band.max)
            notes.append(f"{c.id}: mark {mark} was outside chosen band {band.id}; clamped to {clamped}")
            mark = clamped

        evidence = item.get("evidence") or []
        if not isinstance(evidence, list):
            evidence = [str(evidence)]
        criteria[c.id] = {
            "mark": mark,
            "band": band.id,
            "justification": str(item.get("justification", "")).strip(),
            "evidence": [str(e) for e in evidence if str(e).strip()],
        }

    def str_list(key: str) -> list[str]:
        """Read an optional list-of-strings field and coerce it to a list."""
        v = data.get(key) or []
        return [str(x) for x in v] if isinstance(v, list) else [str(v)]

    parsed = {
        "criteria": criteria,
        "strengths": str_list("strengths"),
        "improvements": str_list("improvements"),
        "integrity_concerns": str_list("integrity_concerns"),
    }
    return parsed, notes


# --- Sampling ---------------------------------------------------------------------


def sample_once(
    client: ChatClient,
    settings: Settings,
    rubric: Rubric,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    seed: int,
) -> dict[str, Any]:
    """Get one valid judgement from the model, retrying invalid answers.

    Attempt *n* (counting from 0) uses ``seed + 1000 * n``. Retries are therefore
    deterministic, and they can't collide with the seeds of other samples while
    there are fewer than 1000 samples.

    Args:
        client: The chat client (normally :class:`marker.ollama.OllamaClient`).
        settings: Run settings.
        rubric: Rubric for validation.
        messages: Chat messages from :func:`marker.prompt.build_messages`.
        schema: JSON schema from :func:`marker.prompt.response_schema`.
        seed: Seed for the first attempt.

    Returns:
        A sample record: the parsed judgement plus seed, attempt number, timing,
        token counts, any notes or errors from earlier attempts, the raw content
        and, if requested, the reasoning trace.

    Raises:
        OllamaError: Every attempt was invalid, or the server failed.
    """
    errors: list[str] = []
    for attempt in range(settings.max_retries + 1):
        attempt_seed = seed + 1000 * attempt
        started = time.monotonic()
        resp = client.chat(
            settings.model, messages, schema=schema, options=settings.options_for(attempt_seed), think=settings.think
        )
        msg = resp.get("message", {})
        content = msg.get("content", "")
        try:
            parsed, notes = parse_response(content, rubric)
        except InvalidResponse as e:
            errors.append(f"seed {attempt_seed}: {e}")
            continue
        record: dict[str, Any] = {
            "seed": attempt_seed,
            "attempt": attempt,
            "seconds": round(time.monotonic() - started, 1),
            "eval_count": resp.get("eval_count"),  # tokens generated
            "prompt_eval_count": resp.get("prompt_eval_count"),  # prompt tokens processed
            "notes": notes,
            "errors": errors,
            **parsed,
            "raw": content,
        }
        if settings.keep_reasoning:
            # Ollama returns the reasoning (gpt-oss's analysis channel) as message.thinking.
            record["reasoning"] = msg.get("thinking", "")
        return record
    raise OllamaError(f"model gave no valid answer after {settings.max_retries + 1} attempts: {errors}")


# --- Marking ----------------------------------------------------------------------

#: Version of the deterministic analysis rules (aggregation, quote matching, flags).
#: Bump it when they change, so that saved results are re-analysed from their stored samples.
#: Version 2: quote matching ignores punctuation, spacing and Unicode dash variants.
#: Version 3: exact Decimal arithmetic; ``score`` on a configurable scale.
#: Version 4: internal marks are 0.0000-1.0000 Decimals with 4 places.
#: Version 5: numeric modes; float 0.0-1.0 by default, Decimal optional; continuous band lookup.
ANALYSIS_VERSION = "5"

#: Oldest analysis version whose results use the current mark representation
#: (0.0000-1.0000 decimals). Older results stored integer 0-100 marks and must be re-marked.
MIN_COMPATIBLE_ANALYSIS = 4


def is_compatible(result: dict[str, Any]) -> bool:
    """True if a saved result uses the current 0.0000-1.0000 mark representation."""
    try:
        return int(result["provenance"].get("analysis_version", "0")) >= MIN_COMPATIBLE_ANALYSIS
    except (KeyError, TypeError, ValueError):
        return False


def grade_essay(
    client: ChatClient,
    settings: Settings,
    rubric: Rubric,
    essay: Essay,
    brief: str | None = None,
    *,
    model_digest: str = "",
    ollama_version: str = "",
    progress: Callable[[str], None] | None = None,
    scale: Scale = DEFAULT_SCALE,
) -> dict[str, Any]:
    """Mark one essay and return the full result.

    The result is JSON-serialisable with :func:`marker.scale.dumps`, which
    writes Decimal values (``decimal`` mode) as exact strings; floats are JSON numbers.

    Args:
        client: Chat client used to query the model.
        settings: Run settings (model, samples, seeds, thresholds, ...).
        rubric: The marking rubric.
        essay: The essay to mark.
        brief: Optional assignment brief or question text.
        model_digest: Content digest of the model weights, for provenance.
        ollama_version: Ollama server version, for provenance.
        progress: Optional callback that receives one progress message per sample.
        scale: Reporting scale for ``score`` (default: 0-100 integers).

    Returns:
        A dict with these keys:

        * ``mark``: the final internal mark 0.0-1.0, used for bands: the weighted
          mark as a float, or rounded half up to 4 places in ``decimal`` mode.
        * ``weighted_mark``: the unrounded weighted mean.
        * ``score``: ``{"value", "min", "max", "places"}``: the weighted mark
          mapped onto ``scale`` and rounded half up. With the default scale
          this is the familiar 0-100 integer mark.
        * ``numeric``: the numeric mode used (``"float"`` or ``"decimal"``).
        * ``band``: the band ``id`` and ``label`` for the final mark.
        * ``needs_review`` and ``flags``: whether, and why, a human should check it.
        * ``criteria``: per criterion, the median mark, band, every sample's
          mark, the spread, the justification and evidence, and any quotes that
          weren't found in the essay.
        * ``feedback``: strengths and improvements from the most representative sample.
        * ``integrity_concerns``: concerns raised by any sample.
        * ``samples``: every raw sample record.
        * ``provenance``: everything needed to reproduce or audit the result.

    Raises:
        OllamaError: A sample couldn't be obtained.
    """
    messages = build_messages(rubric, essay.text, brief)
    schema = response_schema(rubric)

    samples: list[dict[str, Any]] = []
    for i in range(settings.samples):
        if progress:
            progress(f"{essay.id}: sample {i + 1}/{settings.samples}")
        samples.append(sample_once(client, settings, rubric, messages, schema, settings.seed + i))

    return {
        "essay": {"id": essay.id, "path": essay.path, "word_count": essay.word_count, "sha256": essay.sha256},
        **analyse(samples, settings, rubric, essay, scale),
        "samples": samples,
        "provenance": {
            "marker_version": __version__,
            "prompt_version": PROMPT_VERSION,
            "analysis_version": ANALYSIS_VERSION,
            "prompt_sha256": sha256_of(messages),
            "schema_sha256": sha256_of(schema),
            "rubric": {"name": rubric.name, "version": rubric.version, "sha256": rubric.sha256},
            "brief_sha256": sha256_of(brief or ""),
            "model": settings.model,
            "model_digest": model_digest,
            "ollama_version": ollama_version,
            "settings": settings.as_dict(),
            "fingerprint": compute_fingerprint(settings, rubric, essay, brief, model_digest),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "marked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    }


def analyse(
    samples: list[dict[str, Any]], settings: Settings, rubric: Rubric, essay: Essay, scale: Scale = DEFAULT_SCALE
) -> dict[str, Any]:
    """Turn validated samples into marks, feedback and review flags. This step is deterministic and makes no model calls.

    It is kept separate from sampling so that when these rules change
    (:data:`ANALYSIS_VERSION`), saved results can be re-analysed from their
    stored samples without querying the model again (see :func:`reanalyse`).

    Returns:
        The keys ``mark``, ``weighted_mark``, ``score``, ``band``, ``needs_review``,
        ``flags``, ``criteria``, ``feedback`` and ``integrity_concerns``, as
        described in :func:`grade_essay`.
    """
    def m(sample: dict[str, Any], cid: str) -> Num:
        """A sample's mark for one criterion, in the current numeric mode (saved results may hold strings)."""
        return normalise_mark(sample["criteria"][cid]["mark"])

    normalised = _normalise(essay.text)
    criteria_out: dict[str, dict[str, Any]] = {}
    flags: list[str] = []
    for c in rubric.criteria:
        marks = [m(s, c.id) for s in samples]
        # The median can fall between two marks with an even sample count; it is
        # kept unrounded until the final weighted mark.
        med = median(marks)
        # Feedback comes from the sample closest to the median, so the written
        # justification matches the mark awarded.
        rep = min(samples, key=lambda s: abs(m(s, c.id) - med))["criteria"][c.id]
        unverified = [q for s in samples for q in s["criteria"][c.id]["evidence"] if not quote_in_essay(q, normalised)]
        spread = max(marks) - min(marks)
        criteria_out[c.id] = {
            "name": c.name,
            "weight": c.weight,
            "mark": med,
            "band": rubric.band_for(med).id,
            "sample_marks": marks,
            "spread": spread,
            "justification": rep["justification"],
            "evidence": rep["evidence"],
            "unverified_evidence": unverified,
        }
        if spread > num(settings.disagreement_threshold):
            flags.append(f"samples disagree on {c.id}: marks [{', '.join(fmt_mark(x) for x in marks)}]")
        if unverified:
            flags.append(f"{c.id}: {len(unverified)} evidence quote(s) not found in the essay")

    # The overall mark is computed here, never by the model, in the numeric mode's arithmetic.
    weighted = sum((criteria_out[c.id]["mark"] * num(c.weight) for c in rubric.criteria), zero())
    final = normalise_mark(weighted)
    band = rubric.band_for(final)

    # Borderline review, as in UK exam boards: marks just below a classification
    # boundary get a second look.
    for boundary in rubric.boundaries:
        if num(boundary) - num(settings.boundary_margin) <= final < num(boundary):
            flags.append(
                f"borderline: {fmt_mark(final)} is within {fmt_mark(settings.boundary_margin)} "
                f"of the {fmt_mark(boundary)} boundary"
            )
    if essay.word_count < rubric.min_words:
        flags.append(f"word count {essay.word_count} is below the minimum {rubric.min_words}")
    if rubric.max_words and essay.word_count > rubric.max_words:
        flags.append(f"word count {essay.word_count} exceeds the limit {rubric.max_words}")
    injection = detect_injection(essay.text)
    if injection:
        flags.append("possible prompt injection in essay: " + " | ".join(injection))
    concerns = sorted({x for s in samples for x in s["integrity_concerns"]})
    if concerns:
        flags.append("model raised integrity concerns")
    if any(s["notes"] for s in samples):
        flags.append("model gave marks outside its chosen band (clamped)")

    # Overall feedback comes from the sample whose own weighted mark is closest to the final one.
    rep_sample = min(
        samples,
        key=lambda s: abs(sum((m(s, c.id) * num(c.weight) for c in rubric.criteria), zero()) - weighted),
    )

    return {
        "mark": final,
        "weighted_mark": weighted,
        "score": {"value": scale.from_fraction(weighted), **scale.as_dict()},
        "numeric": get_numeric(),
        "band": {"id": band.id, "label": band.label},
        "needs_review": bool(flags),
        "flags": flags,
        "criteria": criteria_out,
        "feedback": {"strengths": rep_sample["strengths"], "improvements": rep_sample["improvements"]},
        "integrity_concerns": concerns,
    }


def reanalyse(
    result: dict[str, Any], settings: Settings, rubric: Rubric, essay: Essay, scale: Scale = DEFAULT_SCALE
) -> dict[str, Any]:
    """Recompute a saved result's marks and flags from its stored samples with the current analysis rules.

    It is also used to re-report a result on a different ``scale``. The model
    isn't called. The samples and the original provenance are kept;
    ``analysis_version``, ``marker_version`` and ``reanalysed_at`` are updated.
    Use this only when the fingerprint matches, which means the samples came
    from the same inputs.
    """
    provenance = {
        **result["provenance"],
        "marker_version": __version__,
        "analysis_version": ANALYSIS_VERSION,
        "reanalysed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return {
        "essay": result["essay"],
        **analyse(result["samples"], settings, rubric, essay, scale),
        "samples": result["samples"],
        "provenance": provenance,
    }


def compute_fingerprint(settings: Settings, rubric: Rubric, essay: Essay, brief: str | None, model_digest: str) -> str:
    """Hash every input that determines a marking result.

    Two runs with the same fingerprint used the same essay, prompt (which
    includes the rubric and brief), schema, model weights and settings. The CLI
    uses this to skip essays that are already marked. ``keep_reasoning`` is left
    out because it changes only what is stored, not the marks.

    Returns:
        A hex SHA-256 digest.
    """
    return sha256_of(
        {
            "essay": essay.sha256,
            "messages": build_messages(rubric, essay.text, brief),
            "schema": response_schema(rubric),
            "model_digest": model_digest,
            "settings": {k: v for k, v in settings.as_dict().items() if k != "keep_reasoning"},
        }
    )
