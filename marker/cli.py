"""Command-line interface: ``marker {check,models,rubric,grade,compare,show,evaluate}``.

Typical workflow::

    marker models                                   # what's installed: families, sizes, MLX builds
    marker check --family gpt-oss                   # is Ollama up, are the models usable?
    marker rubric --rubric rubrics/my-module.toml   # validate a rubric
    marker grade essays/ --brief brief.txt --out results/
    marker evaluate --results results/summary.csv --human human-marks.csv

Choosing models (``check``, ``models`` and ``grade``)::

    --model gpt-oss:20b --model muse-glimmer:30b-mlx   # exact tags (repeat, or comma-separate)
    --family gpt-oss --size 20b,120b                    # a family at chosen sizes
    --family "ChatGPT-OSS" --family "Muse Glimmer"      # loose names and aliases are fine
    --mlx auto|prefer|only|never|any                    # Mac (MLX) builds; auto = prefer on Apple Silicon

Comparing models: give ``grade`` more than one model and it marks every essay
with each one, then prints and saves a comparison::

    marker grade paper.md --model gpt-oss:20b --model muse-glimmer:30b-mlx --out results/cmp

``marker compare results/cmp`` re-runs the comparison on existing results.

Numeric mode (``grade``, ``compare``, ``show``, ``evaluate``, ``rubric``). Marks
are held internally as fractions 0.0-1.0::

    (default)            --numeric float     # Python floats
                         --numeric decimal   # exact Decimal, 4 places (0.0000-1.0000)

Choosing the score scale (``grade``, ``show``, ``evaluate``). The final score is reported on::

    (default)                                  # 0-100 integers, e.g. 62
    --score-min 0.0000 --score-max 1.0000      # probability with 4 places, e.g. 0.6213
    --score-min 0 --score-max 20 --score-places 1   # e.g. 12.4

Model options (``grade``); each is recorded in provenance and the fingerprint::

    --think high                     # reasoning: low|medium|high (gpt-oss effort), on, off
    --think medium,gpt-oss:120b=low  # a default plus per-model (or per-family) overrides
    --temperature 0.6 --top-p 1 --top-k 40 --min-p 0.05 --repeat-penalty 1.1
    --num-predict 8192 --num-ctx 32768 --max-retries 2 --samples 3 --seed 42
    --option num_gpu=99              # any other Ollama option, passed through

Choosing what to print (``grade`` and ``show``)::

    --show score                     # id<TAB>score<TAB>band (default)
    --show explanation               # per-criterion justification, evidence, feedback, flags
    --show thinking                  # the model's reasoning trace (stored automatically)
    --show score,explanation,thinking   (or --show all)

Outputs of ``grade``, written to ``--out``, or to ``--out/<model>/`` when
several models are used:

* ``<essay-id>.json``: the full result for each essay (marks, feedback, flags,
  samples, provenance). It is written atomically.
* ``summary.csv``: one row per essay, rebuilt from every JSON file after each run.
* ``audit.jsonl``: an append-only log with one line per marking event.
* With several models, also ``--out/comparison.csv`` and ``--out/comparison.json``.

Exit codes: 0 success, 1 some essays failed, 2 usage or configuration error.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import __version__
from .compare import (
    DEFAULT_THRESHOLD,
    compare_runs,
    discover_result_dirs,
    format_report,
    label_runs,
    load_results,
    outdated_results,
    write_comparison,
)
from .essay import Essay, expand_paths, load_essay
from .evaluate import evaluate, read_marks
from .grader import ANALYSIS_VERSION, MANAGED_OPTIONS, Settings, compute_fingerprint, grade_essay, is_compatible, reanalyse
from .models import (
    DEFAULT_THINK,
    MLX_POLICIES,
    ModelInfo,
    is_apple_silicon,
    is_gpt_oss,
    matches_target,
    model_info,
    parse_think,
    resolve_mlx_policy,
    select_models,
    slug,
    think_for,
    think_setting,
)
from .ollama import DEFAULT_HOST, OllamaClient, OllamaError
from .report import SHOW_CHOICES, has_reasoning, parse_show, render
from .rubric import Rubric, RubricError, load_rubric
from .scale import (
    DEFAULT_NUMERIC,
    DEFAULT_SCALE,
    MAX_PLACES,
    NUMERIC_MODES,
    Scale,
    dec,
    dumps,
    fmt_percent,
    get_numeric,
    normalise_mark,
    set_numeric,
    text,
)

#: The bundled generic UK undergraduate rubric, used when ``--rubric`` is not given.
DEFAULT_RUBRIC = Path(__file__).resolve().parent.parent / "rubrics" / "uk-undergraduate-essay.toml"


def eprint(*args: object) -> None:
    """Print to stderr. Progress goes to stderr so stdout carries only the requested output."""
    print(*args, file=sys.stderr, flush=True)


def split_csv(values: list[str] | None) -> list[str]:
    """Flatten repeatable, comma-separated option values: ``["a,b", "c"]`` gives ``["a", "b", "c"]``."""
    return [v.strip() for raw in (values or []) for v in raw.split(",") if v.strip()]


def make_scale(args: argparse.Namespace) -> Scale:
    """Build the reporting scale from ``--score-min``, ``--score-max`` and ``--score-places``."""
    return Scale.parse(args.score_min, args.score_max, args.score_places)


def make_client(args: argparse.Namespace) -> OllamaClient:
    """Create an Ollama client from the ``--host`` and ``--allow-remote`` options."""
    return OllamaClient(args.host, allow_remote=args.allow_remote)


def installed_models(client: OllamaClient) -> list[ModelInfo]:
    """Return the installed models with family, size and MLX information."""
    return [model_info(m) for m in client.list_models()]


def resolve_models(args: argparse.Namespace, client: OllamaClient) -> list[str]:
    """Turn the ``--model``, ``--family``, ``--size`` and ``--mlx`` options into a list of installed model names."""
    return select_models(
        installed_models(client),
        models=split_csv(args.model),
        families=split_csv(args.family),
        sizes=split_csv(args.size),
        mlx=args.mlx,
    )


def positive_int(text_value: str) -> int:
    """argparse type: an integer of at least 1."""
    n = int(text_value)
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, not {n}")
    return n


def non_negative_int(text_value: str) -> int:
    """argparse type: an integer of at least 0."""
    n = int(text_value)
    if n < 0:
        raise argparse.ArgumentTypeError(f"must be at least 0, not {n}")
    return n


def bounded_float(low: float, high: float, *, low_open: bool = False) -> Any:
    """argparse type factory: a finite float in ``[low, high]`` (or ``(low, high]`` with ``low_open``)."""

    def parse(text_value: str) -> float:
        """Parse one value; NaN and infinities fail the range check, so they are rejected too."""
        x = float(text_value)
        if not (low < x <= high if low_open else low <= x <= high):
            interval = f"({low}, {high}]" if low_open else f"[{low}, {high}]"
            raise argparse.ArgumentTypeError(f"must be in {interval}, not {text_value}")
        return x

    parse.__name__ = "number"  # argparse names the type in error messages
    return parse


def parse_options(values: list[str] | None) -> dict[str, Any]:
    """Parse ``--option KEY=VALUE`` values into extra Ollama options.

    VALUE is read as JSON when it can be (``99``, ``0.5``, ``true``), else kept as text.

    Raises:
        ValueError: A value has no ``=``, an empty key, or sets an option marker manages itself.
    """
    options: dict[str, Any] = {}
    for item in values or []:
        key, sep, raw = item.partition("=")
        key = key.strip()
        if not sep or not key:
            raise ValueError(f"--option {item!r}: expected KEY=VALUE")
        if key in MANAGED_OPTIONS:
            flag = "--seed" if key == "seed" else "--" + key.replace("_", "-")
            raise ValueError(f"--option {key}: use {flag} instead")
        try:
            options[key] = json.loads(raw)
        except json.JSONDecodeError:
            options[key] = raw.strip()
    return options


# --- check / models / rubric --------------------------------------------------------


def cmd_check(args: argparse.Namespace) -> int:
    """``marker check``: show the Ollama version, then each selected model's digest, build and reasoning support.

    Returns 1 if a model's capabilities can't be read. That usually means Ollama
    can't load the model's files, which a re-pull fixes.
    """
    client = make_client(args)
    print(f"Ollama {client.version()} at {client.host}")
    info = {i.name: i for i in installed_models(client)}
    problems = 0
    for name in resolve_models(args, client):
        caps = client.capabilities(name)
        i = info[name]
        build = "MLX (Apple Silicon)" if i.mlx else (i.format or "unknown format")
        if caps is None:
            problems += 1
            status = f"UNUSABLE: Ollama cannot read this model; try: ollama pull {name}"
        else:
            status = "thinking" if "thinking" in caps else "no thinking"
        print(f"{name}: {i.digest[:12]}  {i.parameter_size or '?'}  {i.quantization or '?'}  {build}  {status}")
    return 1 if problems else 0


def cmd_models(args: argparse.Namespace) -> int:
    """``marker models``: list installed models; with selection options, mark (``*``) the ones that would be used."""
    client = make_client(args)
    installed = installed_models(client)
    selecting = bool(args.model or args.family or args.size)
    chosen = set(resolve_models(args, client)) if selecting else set()
    policy = resolve_mlx_policy(args.mlx)
    print(f"Apple Silicon: {'yes' if is_apple_silicon() else 'no'}; --mlx {args.mlx} -> {policy}")
    header = f"  {'name':<32} {'family':<16} {'size':>6} {'params':>7} {'quant':<8} {'build':<12}"
    print(header)
    for i in sorted(installed, key=lambda m: (m.family, m.name)):
        mark = "*" if i.name in chosen else " "
        build = "mlx" if i.mlx else (i.format or "-")
        print(f"{mark} {i.name:<32} {i.family:<16} {i.size or '-':>6} {i.parameter_size or '-':>7} {i.quantization or '-':<8} {build:<12}")
    return 0


def cmd_rubric(args: argparse.Namespace) -> int:
    """``marker rubric``: validate a rubric and print its bands and weighted criteria."""
    rubric = load_rubric(args.rubric)
    print(f"{rubric.name} v{rubric.version}  sha256={rubric.sha256[:16]}")
    for b in rubric.bands:
        print(f"  {fmt_percent(b.min) + '+':>5}  {b.label}")
    for c in rubric.criteria:
        print(f"  {fmt_percent(c.weight):>5}  {c.id}: {c.name}")
    return 0


# --- grade ------------------------------------------------------------------------


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write JSON to a temporary file, then rename it into place, so readers never see a partial file.

    Decimal values (``decimal`` mode) are written as exact strings; floats are
    JSON numbers (see :func:`marker.scale.dumps`).
    """
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_summary(out_dir: Path, rubric: Rubric) -> Path:
    """Rebuild ``summary.csv`` from every result JSON in ``out_dir``.

    Columns: ``score`` on the reporting scale (with ``score_min``/``score_max``),
    ``mark`` as the internal 0.0-1.0 fraction, the band, the review flag,
    each criterion's median fraction, and provenance. Marks are written as
    exact text: a float's shortest form, or a Decimal without exponent.

    The summary is rebuilt from scratch rather than appended to, so it always
    matches the per-essay files, including results kept from earlier runs.
    Unreadable or non-result JSON files are skipped.

    Returns:
        Path of the written CSV.
    """
    path = out_dir / "summary.csv"
    crit_ids = [c.id for c in rubric.criteria]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["id", "score", "score_min", "score_max", "mark", "band", "needs_review", *crit_ids,
             "word_count", "flags", "model", "model_digest"]
        )
        for r in load_results(out_dir).values():
            sc = Scale.from_dict(r["score"])
            w.writerow(
                [
                    r["essay"]["id"],
                    sc.format(r["score"]["value"]),
                    sc.format(sc.min),
                    sc.format(sc.max),
                    text(normalise_mark(r["mark"])),
                    r["band"]["label"],
                    "yes" if r["needs_review"] else "no",
                    *[text(normalise_mark(r["criteria"][c]["mark"])) if c in r["criteria"] else "" for c in crit_ids],
                    r["essay"]["word_count"],
                    "; ".join(r["flags"]),
                    r["provenance"]["model"],
                    r["provenance"]["model_digest"],
                ]
            )
    return path


def append_audit(out_dir: Path, essay: Essay, result: dict[str, Any], event: str = "marked") -> None:
    """Append one line to the audit log. The log is append-only and never rewritten.

    ``event`` is ``"marked"`` (the model was queried) or ``"reanalysed"``
    (marks and flags were recomputed from stored samples).
    """
    with (out_dir / "audit.jsonl").open("a", encoding="utf-8") as audit:
        record = {
            "event": event,
            "essay_id": essay.id,
            "essay_sha256": essay.sha256,
            "mark": result["mark"],
            "score": result["score"]["value"],
            "needs_review": result["needs_review"],
            **{k: result["provenance"][k] for k in ("marked_at", "model", "model_digest", "fingerprint")},
        }
        audit.write(dumps(record) + "\n")


def grade_batch(
    client: OllamaClient,
    settings: Settings,
    rubric: Rubric,
    brief: str | None,
    essays: list[Essay],
    out_dir: Path,
    *,
    force: bool,
    show: set[str],
    with_model: bool,
    ollama_version: str,
    scale: Scale = DEFAULT_SCALE,
) -> int:
    """Mark every essay with one model, writing results to ``out_dir`` and printing the requested views.

    An existing result is reused when its input fingerprint matches, unless
    ``force`` is set or the thinking view was requested and the stored result
    has no reasoning trace. A reused result made with older analysis rules
    (:data:`marker.grader.ANALYSIS_VERSION`), saved on a different score scale,
    or computed in a different numeric mode, is re-analysed from its stored
    samples, which makes no model calls.
    Results from marker versions with integer 0-100 marks have a different
    fingerprint (the prompt changed), so they are re-marked.

    Returns:
        The number of essays that failed.
    """
    digest = client.model_digest(settings.model)
    out_dir.mkdir(parents=True, exist_ok=True)
    eprint(f"marking {len(essays)} essay(s) with {settings.model} ({digest[:12]}, think={settings.think}), Ollama {ollama_version}")
    failures = 0
    for essay in essays:
        out_path = out_dir / f"{essay.id}.json"
        # Skip work whose inputs haven't changed since the last run.
        fp = compute_fingerprint(settings, rubric, essay, brief, digest)
        if out_path.exists() and not force:
            try:
                existing = json.loads(out_path.read_text(encoding="utf-8"))
                reusable = (
                    is_compatible(existing)
                    and existing["provenance"]["fingerprint"] == fp
                    and ("thinking" not in show or has_reasoning(existing))
                )
            except (KeyError, json.JSONDecodeError):
                reusable = False
            if reusable:
                stale = existing["provenance"].get("analysis_version") != ANALYSIS_VERSION
                rescaled = Scale.from_dict(existing["score"]) != scale
                # Results from before numeric modes existed were computed with Decimals.
                remoded = existing.get("numeric", "decimal") != get_numeric()
                if stale or rescaled or remoded:
                    existing = reanalyse(existing, settings, rubric, essay, scale)
                    write_json_atomic(out_path, existing)
                    append_audit(out_dir, essay, existing, event="reanalysed")
                    why = (
                        f"analysis v{ANALYSIS_VERSION}" if stale
                        else f"score scale {scale.label()}" if rescaled
                        else f"numeric mode {get_numeric()}"
                    )
                    eprint(f"{essay.id}: unchanged inputs; re-analysed stored samples for {why} (no model calls)")
                else:
                    eprint(f"{essay.id}: unchanged inputs, reusing existing result (use --force to remark)")
                print(render(existing, show, with_model=with_model), flush=True)
                continue
        try:
            result = grade_essay(
                client, settings, rubric, essay, brief,
                model_digest=digest, ollama_version=ollama_version, progress=eprint, scale=scale,
            )
        except OllamaError as e:
            eprint(f"{essay.id}: FAILED with {settings.model}: {e}")
            failures += 1
            continue
        write_json_atomic(out_path, result)
        append_audit(out_dir, essay, result)
        print(render(result, show, with_model=with_model), flush=True)
    write_summary(out_dir, rubric)
    return failures


def cmd_grade(args: argparse.Namespace) -> int:
    """``marker grade``: mark every essay with every selected model, then compare the models if there are several.

    Models run one after another, each over the whole batch. That way each
    model is loaded into memory once, instead of models being swapped for
    every essay.

    Returns:
        0 if every essay was marked or reused, 1 if any failed, 2 if no essays were found.
    """
    show = parse_show(args.show)
    think_default, think_overrides = parse_think(args.think)
    scale = make_scale(args)
    rubric = load_rubric(args.rubric)
    brief = Path(args.brief).read_text(encoding="utf-8") if args.brief else None

    essays: list[Essay] = []
    failures = 0
    for path in expand_paths(args.essays):
        try:
            essays.append(load_essay(path))
        except (OSError, ValueError, KeyError) as e:
            eprint(f"skip {path}: {e}")
            failures += 1
    if not essays:
        eprint("no essays found")
        return 2
    # The id (file name without extension) names the result file, so two essays with one id would overwrite each other.
    by_id: dict[str, list[str]] = {}
    for loaded in essays:
        by_id.setdefault(loaded.id, []).append(loaded.path)
    clashes = {i: paths for i, paths in by_id.items() if len(paths) > 1}
    if clashes:
        for i, paths in clashes.items():
            eprint(f"error: essays share the id {i!r} and would overwrite each other's results: {', '.join(paths)}")
        eprint("rename them so each file name (without extension) is unique")
        return 2

    client = make_client(args)
    # Record the exact server once per run; it goes into every result's provenance.
    ollama_version = client.version()
    models = resolve_models(args, client)
    info = {i.name: i for i in installed_models(client)}
    multi = len(models) > 1
    out_root = Path(args.out)
    base = Settings(
        samples=args.samples,
        seed=args.seed,
        temperature=args.temperature,
        top_p=args.top_p,
        top_k=args.top_k,
        min_p=args.min_p,
        repeat_penalty=args.repeat_penalty,
        num_predict=args.num_predict,
        num_ctx=args.num_ctx,
        max_retries=args.max_retries,
        extra_options=parse_options(args.option),
        disagreement_threshold=args.disagreement,
        boundary_margin=args.boundary_margin,
        keep_reasoning=args.keep_reasoning or "thinking" in show,
    )

    dirs: list[Path] = []
    unused = [t for t in think_overrides if not any(matches_target(m, t) for m in models)]
    if unused:
        eprint(f"warning: --think set for {', '.join(unused)}, which is not among the selected models")
    for model in models:
        setting = think_setting(model, think_default, think_overrides)
        think = think_for(model, setting, client.capabilities(model))
        settings = dataclasses.replace(base, model=model, think=think)
        if setting == "off" and is_gpt_oss(model):
            eprint(f"note: {model} can't switch reasoning off; using the lowest effort (low)")
        elif think is None and setting != "off":
            eprint(f"note: {model} doesn't report the thinking capability, so --think has no effect on it")
        if info[model].mlx:
            # Observed with Ollama 0.35.1: MLX builds ignore the seed, so reruns can give different marks.
            eprint(f"warning: {model} is an MLX build; Ollama's MLX engine is not seed-deterministic, "
                   "so marks may differ between runs. Use a GGUF build for reproducible marking.")
        out_dir = out_root / slug(model) if multi else out_root
        dirs.append(out_dir)
        failures += grade_batch(
            client, settings, rubric, brief, essays, out_dir,
            force=args.force, show=show, with_model=multi, ollama_version=ollama_version, scale=scale,
        )

    if multi:
        runs = label_runs([d for d in dirs if load_results(d)])
        if len(runs) >= 2:
            report = compare_runs(runs, rubric, args.disagreement)
            csv_path, _ = write_comparison(out_root, report, rubric)
            print("\n" + format_report(report, rubric), flush=True)
            eprint(f"comparison saved to {csv_path} and comparison.json")
        else:
            eprint("not enough successful models to compare")
    eprint(f"results in {out_root}/")
    return 1 if failures else 0


# --- compare / show / evaluate ------------------------------------------------------


def cmd_compare(args: argparse.Namespace) -> int:
    """``marker compare``: compare existing result directories from different models."""
    rubric = load_rubric(args.rubric)
    dirs = discover_result_dirs(args.dirs)
    for d in dirs:
        old = outdated_results(d)
        if old:
            eprint(f"warning: {d}: ignoring {len(old)} result(s) from an older marker version (re-run grade): {', '.join(old)}")
    report = compare_runs(label_runs(dirs), rubric, args.disagreement)
    out = Path(args.out) if args.out else (Path(args.dirs[0]) if len(args.dirs) == 1 else Path("."))
    csv_path, json_path = write_comparison(out, report, rubric)
    if args.json:
        print(dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_report(report, rubric))
    eprint(f"comparison saved to {csv_path} and {json_path}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """``marker show``: print the requested views of existing result files (or every result in a directory)."""
    show = parse_show(args.show)
    # Only re-map scores if the user gave a scale; otherwise show each result's saved scale.
    rescale = args.score_min is not None or args.score_max is not None or args.score_places is not None
    scale = Scale.parse(args.score_min or "0", args.score_max or "100", args.score_places) if rescale else None
    results: list[dict[str, Any]] = []
    for raw in args.results:
        p = Path(raw)
        if p.is_dir():
            results.extend(load_results(p).values())
            for essay_id in outdated_results(p):
                eprint(f"skip {essay_id}: saved by an older marker version (integer 0-100 marks); re-run grade")
        else:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not is_compatible(data):
                eprint(f"skip {p}: saved by an older marker version (integer 0-100 marks); re-run grade")
                continue
            results.append(data)
    if not results:
        eprint("no results found")
        return 2
    with_model = len({r["provenance"]["model"] for r in results}) > 1
    print("\n\n".join(render(r, show, with_model=with_model, scale=scale) for r in results))
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    """``marker evaluate``: report agreement between machine marks and a CSV of human marks."""
    rubric = load_rubric(args.rubric)
    scale = make_scale(args)
    machine = read_marks(args.results, "id", args.results_mark_column)
    human = read_marks(args.human, args.id_column, args.mark_column)
    report = evaluate(machine, human, rubric, scale)
    if args.json:
        print(dumps(report, indent=2))
        return 0
    unit = f"(on {scale.label()})"
    r = report["pearson_r"]
    print(f"essays compared:         {report['n']}")
    print(f"MAE:                     {scale.format(report['mae'])} {unit}")
    print(f"RMSE:                    {scale.format(report['rmse'])} {unit}")
    print(f"mean bias (machine-human): {'+' if report['mean_bias'] >= 0 else ''}{scale.format(report['mean_bias'])} {unit}")
    print(f"Pearson r:               {'n/a (no variance)' if r is None else f'{r:.3f}'}")
    print(f"QWK (marks):             {report['qwk_marks']:.3f}")
    print(f"QWK (bands):             {report['qwk_bands']:.3f}")
    print(f"exact band agreement:    {fmt_percent(report['exact_band_agreement'])}")
    print(f"adjacent band agreement: {fmt_percent(report['adjacent_band_agreement'])}")
    for key in ("unmatched_machine", "unmatched_human"):
        if report[key]:
            print(f"{key}: {', '.join(report[key])}")
    return 0


# --- parser ------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with one sub-command for each workflow step."""
    p = argparse.ArgumentParser(
        prog="marker",
        description="Mark student essays 0-100 against a rubric with local Ollama models (default gpt-oss:20b).",
    )
    p.add_argument("--version", action="version", version=f"marker {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add_server(sp: argparse.ArgumentParser) -> None:
        """Add the Ollama connection options."""
        sp.add_argument("--host", default=os.environ.get("OLLAMA_HOST_URL", DEFAULT_HOST), help="Ollama URL (default %(default)s)")
        sp.add_argument("--allow-remote", action="store_true", help="allow a non-loopback Ollama host")

    def add_models(sp: argparse.ArgumentParser) -> None:
        """Add the model-selection options."""
        g = sp.add_argument_group("model selection (default: gpt-oss:20b)")
        g.add_argument("--model", action="append", metavar="TAG",
                       help="exact Ollama model tag; repeat or comma-separate for several (e.g. gpt-oss:20b,muse-glimmer:30b-mlx)")
        g.add_argument("--family", action="append", metavar="NAME",
                       help='model family, loosely matched (e.g. gpt-oss, "ChatGPT-OSS", "Muse Glimmer"); repeatable')
        g.add_argument("--size", action="append", metavar="SIZE",
                       help="parameter size(s) to keep, e.g. 20b,120b; repeatable")
        g.add_argument("--mlx", default="auto", choices=MLX_POLICIES,
                       help="Mac MLX builds when selecting by family/size: auto (prefer on Apple Silicon, else never), "
                            "prefer, only, never, any (default %(default)s)")

    def add_rubric(sp: argparse.ArgumentParser) -> None:
        """Add the ``--rubric`` option."""
        sp.add_argument("--rubric", default=str(DEFAULT_RUBRIC), help="rubric TOML file")

    def add_scale(sp: argparse.ArgumentParser, *, defaults: bool = True) -> None:
        """Add the score-scale options. ``defaults=False`` leaves them unset (``show`` then keeps each saved scale)."""
        g = sp.add_argument_group("score scale (marks are held internally as fractions 0.0-1.0)")
        g.add_argument("--score-min", default="0" if defaults else None, metavar="DEC",
                       help="score for 0%%, as an exact decimal (default 0)")
        g.add_argument("--score-max", default="100" if defaults else None, metavar="DEC",
                       help="score for 100%%, e.g. 100 or 1.0000 (default 100)")
        g.add_argument("--score-places", type=int, default=None, metavar="N",
                       help=f"decimal places in scores, 0-{MAX_PLACES} (default: as written in --score-min/--score-max, "
                            "so 1.0000 gives 4)")

    def add_numeric(sp: argparse.ArgumentParser) -> None:
        """Add the ``--numeric`` option."""
        sp.add_argument("--numeric", default=DEFAULT_NUMERIC, choices=NUMERIC_MODES,
                        help="numeric type for marks 0.0-1.0: float (default) or decimal "
                             "(exact Decimal, 4 places, 0.0000-1.0000)")

    def add_model_options(sp: argparse.ArgumentParser) -> None:
        """Add reasoning, sampling and generation options (``grade``)."""
        g = sp.add_argument_group("model options (all are recorded in provenance and the fingerprint)")
        g.add_argument("--think", "--reasoning", dest="think", action="append", metavar="SETTING",
                       help="reasoning: low, medium or high (effort for gpt-oss; other thinking models just reason), "
                            "on, or off (no reasoning; gpt-oss can't switch off, so it gets low). "
                            "MODEL=SETTING sets one model or family, e.g. gpt-oss:120b=low "
                            f"(repeat or comma-separate; default {DEFAULT_THINK})")
        g.add_argument("--samples", type=positive_int, default=3, metavar="N",
                       help="independent samples per essay; the median is used (default %(default)s)")
        g.add_argument("--seed", type=int, default=42, metavar="N", help="base seed; sample i uses seed+i (default %(default)s)")
        g.add_argument("--temperature", type=bounded_float(0, 2), default=0.6, metavar="T",
                       help="sampling temperature, 0-2 (default %(default)s)")
        g.add_argument("--top-p", type=bounded_float(0, 1, low_open=True), default=1.0, metavar="P",
                       help="nucleus sampling cut-off, above 0 up to 1 (default %(default)s)")
        g.add_argument("--top-k", type=positive_int, metavar="N", help="sample only from the N most likely tokens (default: Ollama's)")
        g.add_argument("--min-p", type=bounded_float(0, 1), metavar="P", help="drop tokens below P x the top token's probability (default: Ollama's)")
        g.add_argument("--repeat-penalty", type=bounded_float(0, 2), metavar="X", help="penalty for repeated tokens; 1 = none (default: Ollama's)")
        g.add_argument("--num-predict", type=positive_int, metavar="N",
                       help="most tokens per answer, reasoning included (default: no limit)")
        g.add_argument("--num-ctx", type=positive_int, default=32768, metavar="N", help="context window tokens (default %(default)s)")
        g.add_argument("--max-retries", type=non_negative_int, default=2, metavar="N",
                       help="extra attempts after an invalid answer (default %(default)s)")
        g.add_argument("--option", action="append", metavar="KEY=VALUE",
                       help="any other Ollama option, e.g. num_gpu=99 or num_thread=8 (VALUE is JSON: number, "
                            "true/false, or text); repeatable")

    def add_show(sp: argparse.ArgumentParser) -> None:
        """Add the ``--show`` option."""
        sp.add_argument("--show", action="append", metavar="VIEWS",
                        help=f"what to print: {', '.join(SHOW_CHOICES)} or all; comma-separate or repeat (default score)")

    sp = sub.add_parser("check", help="verify Ollama is running and the selected models are usable")
    add_server(sp)
    add_models(sp)
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("models", help="list installed models (family, size, MLX build); * marks the selection")
    add_server(sp)
    add_models(sp)
    sp.set_defaults(func=cmd_models)

    sp = sub.add_parser("rubric", help="validate and show a rubric")
    add_rubric(sp)
    add_numeric(sp)
    sp.set_defaults(func=cmd_rubric)

    sp = sub.add_parser("grade", help="mark essays (.txt, .md, .docx files or directories) with one or more models")
    sp.add_argument("essays", nargs="+")
    add_server(sp)
    add_models(sp)
    add_rubric(sp)
    add_show(sp)
    add_numeric(sp)
    add_scale(sp)
    sp.add_argument("--brief", help="assignment brief / question text file")
    sp.add_argument("--out", default="results", help="output directory; one subdirectory per model when several (default %(default)s)")
    add_model_options(sp)
    sp.add_argument("--disagreement", type=dec, default=Decimal("0.1000"), metavar="DEC",
                    help="flag if marks spread more than this fraction, across samples or across models "
                         "(default %(default)s = 10 points)")
    sp.add_argument("--boundary-margin", type=dec, default=Decimal("0.0200"), metavar="DEC",
                    help="flag a final mark this close below a band boundary (default %(default)s)")
    sp.add_argument("--keep-reasoning", action="store_true", help="store the model's reasoning trace (implied by --show thinking)")
    sp.add_argument("--force", action="store_true", help="remark even if inputs are unchanged")
    sp.set_defaults(func=cmd_grade)

    sp = sub.add_parser("compare", help="compare result directories from different models")
    sp.add_argument("dirs", nargs="+", help="result directories, or one multi-model --out directory")
    add_rubric(sp)
    add_numeric(sp)
    sp.add_argument("--out", help="where to write comparison.csv/json (default: the single directory given, else .)")
    sp.add_argument("--disagreement", type=dec, default=DEFAULT_THRESHOLD, metavar="DEC",
                    help="models disagree if marks spread more than this fraction (default %(default)s)")
    sp.add_argument("--json", action="store_true", help="print the full comparison as JSON")
    sp.set_defaults(func=cmd_compare)

    sp = sub.add_parser("show", help="print score, explanation and/or thinking from existing results")
    sp.add_argument("results", nargs="+", help="result JSON files or result directories")
    add_show(sp)
    add_numeric(sp)
    add_scale(sp, defaults=False)
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("evaluate", help="compare machine marks against human marks")
    sp.add_argument("--results", default="results/summary.csv", help="summary.csv or comparison.csv from marker")
    sp.add_argument("--results-mark-column", default="score",
                    help="score column in --results (default score; panel_score for comparison.csv; "
                         "mark or panel_mark with --score-min 0 --score-max 1.0000)")
    sp.add_argument("--human", required=True, help="CSV of human marks")
    sp.add_argument("--id-column", default="id")
    sp.add_argument("--mark-column", default="mark")
    sp.add_argument("--json", action="store_true", help="print the full report as JSON")
    add_rubric(sp)
    add_numeric(sp)
    add_scale(sp)
    sp.set_defaults(func=cmd_evaluate)
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``marker`` console script and ``python -m marker``.

    Expected errors (server, rubric, input, selection and file problems) are
    printed as one line, without a traceback, and give exit code 2.

    Args:
        argv: Arguments without the program name. Defaults to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    args = build_parser().parse_args(argv)
    # Set the numeric mode before anything (rubric, results, CSVs) is read, so every value is one type.
    set_numeric(getattr(args, "numeric", DEFAULT_NUMERIC))
    try:
        return int(args.func(args))
    except (OllamaError, RubricError, ValueError, OSError) as e:
        eprint(f"error: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
