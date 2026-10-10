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

Choosing what to print (``grade`` and ``show``)::

    --show score                     # id<TAB>mark<TAB>band (default)
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
from pathlib import Path
from typing import Any

from . import __version__
from .compare import compare_runs, discover_result_dirs, format_report, label_runs, load_results, write_comparison
from .essay import Essay, expand_paths, load_essay
from .evaluate import evaluate, read_marks
from .grader import ANALYSIS_VERSION, Settings, compute_fingerprint, grade_essay, reanalyse
from .models import MLX_POLICIES, ModelInfo, is_apple_silicon, model_info, resolve_mlx_policy, select_models, slug, think_for
from .ollama import DEFAULT_HOST, OllamaClient, OllamaError
from .report import SHOW_CHOICES, has_reasoning, parse_show, render
from .rubric import Rubric, RubricError, load_rubric

#: The bundled generic UK undergraduate rubric, used when ``--rubric`` is not given.
DEFAULT_RUBRIC = Path(__file__).resolve().parent.parent / "rubrics" / "uk-undergraduate-essay.toml"


def eprint(*args: object) -> None:
    """Print to stderr. Progress goes to stderr so stdout carries only the requested output."""
    print(*args, file=sys.stderr, flush=True)


def split_csv(values: list[str] | None) -> list[str]:
    """Flatten repeatable, comma-separated option values: ``["a,b", "c"]`` gives ``["a", "b", "c"]``."""
    return [v.strip() for raw in (values or []) for v in raw.split(",") if v.strip()]


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
        print(f"  {b.min:>3}-{b.max:<3} {b.label}")
    for c in rubric.criteria:
        print(f"  {c.weight:>5.0%}  {c.id}: {c.name}")
    return 0


# --- grade ------------------------------------------------------------------------


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write JSON to a temporary file, then rename it into place, so readers never see a partial file."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_summary(out_dir: Path, rubric: Rubric) -> Path:
    """Rebuild ``summary.csv`` from every result JSON in ``out_dir``.

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
        w.writerow(["id", "mark", "band", "needs_review", *crit_ids, "word_count", "flags", "model", "model_digest"])
        for r in load_results(out_dir).values():
            w.writerow(
                [
                    r["essay"]["id"],
                    r["mark"],
                    r["band"]["label"],
                    "yes" if r["needs_review"] else "no",
                    *[r["criteria"].get(c, {}).get("mark", "") for c in crit_ids],
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
            "needs_review": result["needs_review"],
            **{k: result["provenance"][k] for k in ("marked_at", "model", "model_digest", "fingerprint")},
        }
        audit.write(json.dumps(record) + "\n")


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
) -> int:
    """Mark every essay with one model, writing results to ``out_dir`` and printing the requested views.

    An existing result is reused when its input fingerprint matches, unless
    ``force`` is set or the thinking view was requested and the stored result
    has no reasoning trace. A reused result made with older analysis rules
    (:data:`marker.grader.ANALYSIS_VERSION`) is re-analysed from its stored
    samples, which makes no model calls.

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
                reusable = existing["provenance"]["fingerprint"] == fp and (
                    "thinking" not in show or has_reasoning(existing)
                )
            except (KeyError, json.JSONDecodeError):
                reusable = False
            if reusable:
                if existing["provenance"].get("analysis_version") != ANALYSIS_VERSION:
                    existing = reanalyse(existing, settings, rubric, essay)
                    write_json_atomic(out_path, existing)
                    append_audit(out_dir, essay, existing, event="reanalysed")
                    eprint(f"{essay.id}: unchanged inputs; re-analysed stored samples with analysis v{ANALYSIS_VERSION}")
                else:
                    eprint(f"{essay.id}: unchanged inputs, reusing existing result (use --force to remark)")
                print(render(existing, show, with_model=with_model), flush=True)
                continue
        try:
            result = grade_essay(
                client, settings, rubric, essay, brief,
                model_digest=digest, ollama_version=ollama_version, progress=eprint,
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
        num_ctx=args.num_ctx,
        disagreement_threshold=args.disagreement,
        keep_reasoning=args.keep_reasoning or "thinking" in show,
    )

    dirs: list[Path] = []
    for model in models:
        settings = dataclasses.replace(base, model=model, think=think_for(model, args.think, client.capabilities(model)))
        if info[model].mlx:
            # Observed with Ollama 0.35.1: MLX builds ignore the seed, so reruns can give different marks.
            eprint(f"warning: {model} is an MLX build; Ollama's MLX engine is not seed-deterministic, "
                   "so marks may differ between runs. Use a GGUF build for reproducible marking.")
        out_dir = out_root / slug(model) if multi else out_root
        dirs.append(out_dir)
        failures += grade_batch(
            client, settings, rubric, brief, essays, out_dir,
            force=args.force, show=show, with_model=multi, ollama_version=ollama_version,
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
    report = compare_runs(label_runs(dirs), rubric, args.disagreement)
    out = Path(args.out) if args.out else (Path(args.dirs[0]) if len(args.dirs) == 1 else Path("."))
    csv_path, json_path = write_comparison(out, report, rubric)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_report(report, rubric))
    eprint(f"comparison saved to {csv_path} and {json_path}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """``marker show``: print the requested views of existing result files (or every result in a directory)."""
    show = parse_show(args.show)
    results: list[dict[str, Any]] = []
    for raw in args.results:
        p = Path(raw)
        if p.is_dir():
            results.extend(load_results(p).values())
        else:
            results.append(json.loads(p.read_text(encoding="utf-8")))
    if not results:
        eprint("no results found")
        return 2
    with_model = len({r["provenance"]["model"] for r in results}) > 1
    print("\n\n".join(render(r, show, with_model=with_model) for r in results))
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    """``marker evaluate``: report agreement between machine marks and a CSV of human marks."""
    rubric = load_rubric(args.rubric)
    machine = read_marks(args.results, "id", args.results_mark_column)
    human = read_marks(args.human, args.id_column, args.mark_column)
    report = evaluate(machine, human, rubric)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"essays compared:         {report['n']}")
    print(f"MAE:                     {report['mae']:.2f} marks")
    print(f"RMSE:                    {report['rmse']:.2f} marks")
    print(f"mean bias (machine-human): {report['mean_bias']:+.2f} marks")
    print(f"Pearson r:               {report['pearson_r']:.3f}")
    print(f"QWK (marks):             {report['qwk_marks']:.3f}")
    print(f"QWK (bands):             {report['qwk_bands']:.3f}")
    print(f"exact band agreement:    {report['exact_band_agreement']:.0%}")
    print(f"adjacent band agreement: {report['adjacent_band_agreement']:.0%}")
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
    sp.set_defaults(func=cmd_rubric)

    sp = sub.add_parser("grade", help="mark essays (.txt, .md, .docx files or directories) with one or more models")
    sp.add_argument("essays", nargs="+")
    add_server(sp)
    add_models(sp)
    add_rubric(sp)
    add_show(sp)
    sp.add_argument("--brief", help="assignment brief / question text file")
    sp.add_argument("--out", default="results", help="output directory; one subdirectory per model when several (default %(default)s)")
    sp.add_argument("--samples", type=int, default=3, help="independent samples per essay; median is used (default %(default)s)")
    sp.add_argument("--seed", type=int, default=42, help="base seed; sample i uses seed+i (default %(default)s)")
    sp.add_argument("--temperature", type=float, default=0.6, help="sampling temperature (default %(default)s)")
    sp.add_argument("--think", default="high", choices=["low", "medium", "high"],
                    help="reasoning effort for gpt-oss; other thinking models just have reasoning switched on (default %(default)s)")
    sp.add_argument("--num-ctx", type=int, default=32768, help="context window tokens (default %(default)s)")
    sp.add_argument("--disagreement", type=int, default=10,
                    help="flag if marks spread more than this, across samples or across models (default %(default)s)")
    sp.add_argument("--keep-reasoning", action="store_true", help="store the model's reasoning trace (implied by --show thinking)")
    sp.add_argument("--force", action="store_true", help="remark even if inputs are unchanged")
    sp.set_defaults(func=cmd_grade)

    sp = sub.add_parser("compare", help="compare result directories from different models")
    sp.add_argument("dirs", nargs="+", help="result directories, or one multi-model --out directory")
    add_rubric(sp)
    sp.add_argument("--out", help="where to write comparison.csv/json (default: the single directory given, else .)")
    sp.add_argument("--disagreement", type=int, default=10, help="models disagree if marks spread more than this")
    sp.add_argument("--json", action="store_true", help="print the full comparison as JSON")
    sp.set_defaults(func=cmd_compare)

    sp = sub.add_parser("show", help="print score, explanation and/or thinking from existing results")
    sp.add_argument("results", nargs="+", help="result JSON files or result directories")
    add_show(sp)
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("evaluate", help="compare machine marks against human marks")
    sp.add_argument("--results", default="results/summary.csv", help="summary.csv or comparison.csv from marker")
    sp.add_argument("--results-mark-column", default="mark", help="mark column in --results (use panel_mark for comparison.csv)")
    sp.add_argument("--human", required=True, help="CSV of human marks")
    sp.add_argument("--id-column", default="id")
    sp.add_argument("--mark-column", default="mark")
    sp.add_argument("--json", action="store_true", help="print the full report as JSON")
    add_rubric(sp)
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
    if getattr(args, "samples", 1) < 1:
        eprint("--samples must be at least 1")
        return 2
    try:
        return int(args.func(args))
    except (OllamaError, RubricError, ValueError, OSError) as e:
        eprint(f"error: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
