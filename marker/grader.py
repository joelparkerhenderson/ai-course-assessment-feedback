"""Mark one essay: sample the model, validate each answer, aggregate, and flag for review.

This module is the core of the marker. For one essay it:

1. Builds the prompt (:mod:`marker.prompt`) and the JSON schema the model must follow.
2. Asks the model for ``Settings.samples`` independent judgements, each with its own
   fixed seed (``seed``, ``seed + 1``, ...) so the whole run can be reproduced.
3. Validates every answer (:func:`parse_response`) and retries an invalid one with a
   *derived* seed (``seed + 1000 * attempt``), so retries are reproducible too.
4. Aggregates criterion marks with the **median** across samples, then computes the
   weighted overall mark **in code**. The model never does arithmetic.
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
import statistics
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from . import __version__
from .essay import Essay
from .ollama import OllamaError
from .prompt import PROMPT_VERSION, build_messages, response_schema, sha256_of
from .rubric import Rubric


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
        think: gpt-oss reasoning effort: ``"low"``, ``"medium"`` or ``"high"``.
        num_ctx: Context window in tokens. It must hold the rubric, brief, essay
            and the model's reasoning.
        max_retries: Extra attempts allowed when the model's answer is invalid.
        disagreement_threshold: Flag a criterion whose sample marks spread
            (max - min) by more than this.
        boundary_margin: Flag a final mark this many marks or fewer *below* a
            classification boundary (e.g. 68-69 below 70).
        keep_reasoning: Store the model's reasoning trace in each sample. It is
            useful for auditing but makes result files much larger.
    """

    model: str = "gpt-oss:20b"
    samples: int = 3
    seed: int = 42
    temperature: float = 0.6
    top_p: float = 1.0
    think: str = "high"
    num_ctx: int = 32768
    max_retries: int = 2
    disagreement_threshold: int = 10
    boundary_margin: int = 2
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
    r"(give|award|assign|mark) (this|the|my) (essay|paper|work|assignment) (a |an )?(mark|grade|score)? ?(of )?\d{2,3}",
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
# against the essay after normalising typography the model may change.
_QUOTE_MAP = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def _normalise(s: str) -> str:
    """Normalise text for quote matching.

    Curly quotes and dashes become ASCII, the text is lower-cased, Markdown
    emphasis characters are removed, and whitespace runs collapse to one space.
    """
    s = s.translate(_QUOTE_MAP).lower()
    s = re.sub(r"[*_`#>]", "", s)  # markdown emphasis the model may drop or add
    return re.sub(r"\s+", " ", s).strip()


def quote_in_essay(quote: str, normalised_essay: str) -> bool:
    """Check that a quoted passage really appears in the essay.

    The quote is split on ellipses (``...``, ``…``, ``[...]``) and every fragment
    must appear in the essay. Fragments under 8 characters are ignored because
    they would match almost anything.

    Args:
        quote: A passage the model claims to have quoted.
        normalised_essay: The essay text, already passed through :func:`_normalise`.

    Returns:
        True if the quote is found, or too short to check meaningfully.
    """
    q = _normalise(quote).strip(" \"'")
    fragments = [f.strip(" \"'.,;:") for f in re.split(r"\.\.\.|…|\[\.\.\.\]", q)]
    fragments = [f for f in fragments if len(f) >= 8]
    if not fragments:
        return True  # too short to check meaningfully
    return all(f in normalised_essay for f in fragments)


# --- Response validation ----------------------------------------------------------


def parse_response(content: str, rubric: Rubric) -> tuple[dict[str, Any], list[str]]:
    """Parse and validate one model answer.

    Ollama's ``format`` parameter constrains the output to the JSON schema, but
    the answer is still checked here: the schema can't express "mark inside the
    chosen band", and constrained decoding is a convenience, not a guarantee.

    When the band and mark disagree, the *band* wins: choosing the descriptor
    that fits is the core judgement, and the mark only places the essay within
    that band. The mark is clamped into the band and a note is returned.

    Args:
        content: The model's raw message content. A surrounding Markdown code
            fence is tolerated.
        rubric: The rubric the essay was marked against.

    Returns:
        ``(parsed, notes)``. ``parsed`` has keys ``criteria`` (criterion id to
        mark, band, justification and evidence), ``strengths``, ``improvements``
        and ``integrity_concerns``. ``notes`` lists any corrections made, such as
        clamping.

    Raises:
        InvalidResponse: The content isn't JSON, a criterion is missing, or a
            mark isn't an integer from 0 to 100.
    """
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
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

        # Accept 65.0 as 65, but reject 65.5, booleans (bool is a subclass of int) and strings.
        mark = item.get("mark")
        if isinstance(mark, float) and mark.is_integer():
            mark = int(mark)
        if not isinstance(mark, int) or isinstance(mark, bool) or not 0 <= mark <= 100:
            raise InvalidResponse(f"criterion {c.id!r}: mark must be an integer 0-100, got {mark!r}")

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


def round_half_up(x: float) -> int:
    """Round a non-negative mark to the nearest integer, with .5 rounding up.

    Python's built-in :func:`round` uses banker's rounding (``round(64.5) == 64``),
    which is not what students or exam boards expect.
    """
    return int(x + 0.5)


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
            # Ollama returns gpt-oss's analysis channel as message.thinking.
            record["reasoning"] = msg.get("thinking", "")
        return record
    raise OllamaError(f"model gave no valid answer after {settings.max_retries + 1} attempts: {errors}")


# --- Marking ----------------------------------------------------------------------


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
) -> dict[str, Any]:
    """Mark one essay and return the full, JSON-serialisable result.

    Args:
        client: Chat client used to query the model.
        settings: Run settings (model, samples, seeds, thresholds, ...).
        rubric: The marking rubric.
        essay: The essay to mark.
        brief: Optional assignment brief or question text.
        model_digest: Content digest of the model weights, for provenance.
        ollama_version: Ollama server version, for provenance.
        progress: Optional callback that receives one progress message per sample.

    Returns:
        A dict with these keys:

        * ``mark``: final integer mark from 0 to 100.
        * ``weighted_mark``: the unrounded weighted mean.
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

    normalised = _normalise(essay.text)
    criteria_out: dict[str, dict[str, Any]] = {}
    flags: list[str] = []
    for c in rubric.criteria:
        marks = [s["criteria"][c.id]["mark"] for s in samples]
        # The median can be x.5 with an even sample count; it is kept unrounded
        # until the final weighted mark.
        median = statistics.median(marks)
        # Feedback comes from the sample closest to the median, so the written
        # justification matches the mark awarded.
        rep = min(samples, key=lambda s: abs(s["criteria"][c.id]["mark"] - median))["criteria"][c.id]
        unverified = [q for s in samples for q in s["criteria"][c.id]["evidence"] if not quote_in_essay(q, normalised)]
        spread = max(marks) - min(marks)
        criteria_out[c.id] = {
            "name": c.name,
            "weight": c.weight,
            "mark": median,
            "band": rubric.band_for(median).id,
            "sample_marks": marks,
            "spread": spread,
            "justification": rep["justification"],
            "evidence": rep["evidence"],
            "unverified_evidence": unverified,
        }
        if spread > settings.disagreement_threshold:
            flags.append(f"samples disagree on {c.id}: marks {marks}")
        if unverified:
            flags.append(f"{c.id}: {len(unverified)} evidence quote(s) not found in the essay")

    # The overall mark is computed here, never by the model.
    weighted = sum(criteria_out[c.id]["mark"] * c.weight for c in rubric.criteria)
    final = round_half_up(weighted)
    band = rubric.band_for(final)

    # Borderline review, as in UK exam boards: marks just below a classification
    # boundary get a second look.
    for boundary in rubric.boundaries:
        if boundary - settings.boundary_margin <= final < boundary:
            flags.append(f"borderline: {final} is within {settings.boundary_margin} of the {boundary} boundary")
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
        samples, key=lambda s: abs(sum(s["criteria"][c.id]["mark"] * c.weight for c in rubric.criteria) - weighted)
    )

    return {
        "essay": {"id": essay.id, "path": essay.path, "word_count": essay.word_count, "sha256": essay.sha256},
        "mark": final,
        "weighted_mark": round(weighted, 2),
        "band": {"id": band.id, "label": band.label},
        "needs_review": bool(flags),
        "flags": flags,
        "criteria": criteria_out,
        "feedback": {"strengths": rep_sample["strengths"], "improvements": rep_sample["improvements"]},
        "integrity_concerns": concerns,
        "samples": samples,
        "provenance": {
            "marker_version": __version__,
            "prompt_version": PROMPT_VERSION,
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
