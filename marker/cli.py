"""Command-line interface: ``marker {check,rubric,grade,evaluate}``.

Typical workflow::

    marker check                                   # is Ollama up, is gpt-oss installed?
    marker rubric --rubric rubrics/my-module.toml  # validate a rubric
    marker grade essays/ --brief brief.txt --out results/
    marker evaluate --results results/summary.csv --human human-marks.csv

Outputs of ``grade`` (in ``--out``):

* ``<essay-id>.json``: the full result for each essay (marks, feedback, flags,
  samples, provenance). It is written atomically, so an interrupted run never
  leaves a half-written file.
* ``summary.csv``: one row per essay, rebuilt from every JSON file after each run.
* ``audit.jsonl``: an append-only log with one line per marking event.

Exit codes: 0 success, 1 some essays failed, 2 usage or configuration error.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .essay import expand_paths, load_essay
from .evaluate import evaluate, read_marks
from .grader import Settings, compute_fingerprint, grade_essay
from .ollama import DEFAULT_HOST, OllamaClient, OllamaError
from .rubric import Rubric, RubricError, load_rubric

#: The bundled generic UK undergraduate rubric, used when ``--rubric`` is not given.
DEFAULT_RUBRIC = Path(__file__).resolve().parent.parent / "rubrics" / "uk-undergraduate-essay.toml"


def eprint(*args: object) -> None:
    """Print to stderr. Progress goes to stderr so stdout stays a clean ``id<TAB>mark<TAB>band`` stream."""
    print(*args, file=sys.stderr, flush=True)


def make_client(args: argparse.Namespace) -> OllamaClient:
    """Create an Ollama client from the ``--host`` and ``--allow-remote`` options."""
    return OllamaClient(args.host, allow_remote=args.allow_remote)


def cmd_check(args: argparse.Namespace) -> int:
    """``marker check``: print the Ollama version and the model's digest. Fails if either is unavailable."""
    client = make_client(args)
    print(f"Ollama {client.version()} at {client.host}")
    print(f"{args.model}: {client.model_digest(args.model)}")
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
    rows: list[dict[str, Any]] = []
    for f in sorted(out_dir.glob("*.json")):
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
            rows.append(r)
        except (json.JSONDecodeError, OSError):
            continue
    path = out_dir / "summary.csv"
    crit_ids = [c.id for c in rubric.criteria]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "mark", "band", "needs_review", *crit_ids, "word_count", "flags", "model", "model_digest"])
        for r in rows:
            if "mark" not in r:
                continue
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


def cmd_grade(args: argparse.Namespace) -> int:
    """``marker grade``: mark every essay and write results, summary and audit log.

    An essay whose existing result has the same input fingerprint is skipped
    (unless ``--force`` is given), so an interrupted batch can simply be rerun.
    A failure on one essay is reported and the batch carries on.

    Returns:
        0 if every essay was marked or skipped, 1 if any failed, 2 if nothing was found.
    """
    rubric = load_rubric(args.rubric)
    brief = Path(args.brief).read_text(encoding="utf-8") if args.brief else None
    settings = Settings(
        model=args.model,
        samples=args.samples,
        seed=args.seed,
        temperature=args.temperature,
        think=args.think,
        num_ctx=args.num_ctx,
        disagreement_threshold=args.disagreement,
        keep_reasoning=args.keep_reasoning,
    )
    paths = expand_paths(args.essays)
    if not paths:
        eprint("no essays found")
        return 2
    client = make_client(args)
    # Record the exact server and weights once per batch; both go into every result's provenance.
    ollama_version = client.version()
    digest = client.model_digest(settings.model)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    eprint(f"marking {len(paths)} essay(s) with {settings.model} ({digest[:12]}), Ollama {ollama_version}")

    failures = 0
    for path in paths:
        try:
            essay = load_essay(path)
        except (OSError, ValueError, KeyError) as e:
            eprint(f"skip {path}: {e}")
            failures += 1
            continue
        out_path = out_dir / f"{essay.id}.json"
        # Skip work whose inputs haven't changed since the last run.
        fp = compute_fingerprint(settings, rubric, essay, brief, digest)
        if out_path.exists() and not args.force:
            try:
                if json.loads(out_path.read_text(encoding="utf-8"))["provenance"]["fingerprint"] == fp:
                    eprint(f"{essay.id}: unchanged inputs, keeping existing result (use --force to remark)")
                    continue
            except (KeyError, json.JSONDecodeError):
                pass
        try:
            result = grade_essay(
                client, settings, rubric, essay, brief,
                model_digest=digest, ollama_version=ollama_version, progress=eprint,
            )
        except OllamaError as e:
            eprint(f"{essay.id}: FAILED: {e}")
            failures += 1
            continue
        write_json_atomic(out_path, result)
        # The audit log is append-only: one line per marking event, never rewritten.
        with (out_dir / "audit.jsonl").open("a", encoding="utf-8") as audit:
            audit.write(
                json.dumps(
                    {
                        "essay_id": essay.id,
                        "essay_sha256": essay.sha256,
                        "mark": result["mark"],
                        "needs_review": result["needs_review"],
                        **{k: result["provenance"][k] for k in ("marked_at", "model", "model_digest", "fingerprint")},
                    }
                )
                + "\n"
            )
        review = "  [REVIEW]" if result["needs_review"] else ""
        print(f"{essay.id}\t{result['mark']}\t{result['band']['label']}{review}", flush=True)

    summary = write_summary(out_dir, rubric)
    eprint(f"results in {out_dir}/ (summary: {summary})")
    return 1 if failures else 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    """``marker evaluate``: report agreement between machine marks and a CSV of human marks."""
    rubric = load_rubric(args.rubric)
    machine = read_marks(args.results, "id", "mark")
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


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with one sub-command for each workflow step."""
    p = argparse.ArgumentParser(prog="marker", description="Mark student essays 0-100 with a local gpt-oss model.")
    p.add_argument("--version", action="version", version=f"marker {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add_server(sp: argparse.ArgumentParser) -> None:
        """Add the options shared by commands that talk to Ollama."""
        sp.add_argument("--host", default=os.environ.get("OLLAMA_HOST_URL", DEFAULT_HOST), help="Ollama URL (default %(default)s)")
        sp.add_argument("--allow-remote", action="store_true", help="allow a non-loopback Ollama host")
        sp.add_argument("--model", default="gpt-oss:20b", help="Ollama model (default %(default)s)")

    def add_rubric(sp: argparse.ArgumentParser) -> None:
        """Add the ``--rubric`` option."""
        sp.add_argument("--rubric", default=str(DEFAULT_RUBRIC), help="rubric TOML file")

    sp = sub.add_parser("check", help="verify Ollama is running and the model is installed")
    add_server(sp)
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("rubric", help="validate and show a rubric")
    add_rubric(sp)
    sp.set_defaults(func=cmd_rubric)

    sp = sub.add_parser("grade", help="mark essays (.txt, .md, .docx files or directories)")
    sp.add_argument("essays", nargs="+")
    add_server(sp)
    add_rubric(sp)
    sp.add_argument("--brief", help="assignment brief / question text file")
    sp.add_argument("--out", default="results", help="output directory (default %(default)s)")
    sp.add_argument("--samples", type=int, default=3, help="independent samples per essay; median is used (default %(default)s)")
    sp.add_argument("--seed", type=int, default=42, help="base seed; sample i uses seed+i (default %(default)s)")
    sp.add_argument("--temperature", type=float, default=0.6, help="sampling temperature (default %(default)s)")
    sp.add_argument("--think", default="high", choices=["low", "medium", "high"], help="gpt-oss reasoning effort")
    sp.add_argument("--num-ctx", type=int, default=32768, help="context window tokens (default %(default)s)")
    sp.add_argument("--disagreement", type=int, default=10, help="flag if a criterion's sample marks spread more than this")
    sp.add_argument("--keep-reasoning", action="store_true", help="store the model's reasoning trace in results")
    sp.add_argument("--force", action="store_true", help="remark even if inputs are unchanged")
    sp.set_defaults(func=cmd_grade)

    sp = sub.add_parser("evaluate", help="compare machine marks against human marks")
    sp.add_argument("--results", default="results/summary.csv", help="summary.csv from 'marker grade'")
    sp.add_argument("--human", required=True, help="CSV of human marks")
    sp.add_argument("--id-column", default="id")
    sp.add_argument("--mark-column", default="mark")
    sp.add_argument("--json", action="store_true", help="print the full report as JSON")
    add_rubric(sp)
    sp.set_defaults(func=cmd_evaluate)
    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``marker`` console script and ``python -m marker``.

    Expected errors (server, rubric, input and file problems) are printed as one
    line, without a traceback, and give exit code 2.

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
        return args.func(args)
    except (OllamaError, RubricError, ValueError, OSError) as e:
        eprint(f"error: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
