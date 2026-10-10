"""Tests for model selection, multi-model comparison, output views, and the multi-model CLI flow."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path
from unittest import mock

from marker import cli
from marker.compare import compare_runs, discover_result_dirs, format_report, label_runs, load_results
from marker.grader import Settings, grade_essay
from marker.models import family_key, model_info, select_models, slug, think_for
from marker.report import parse_show, render
from marker.scale import FRACTION_SCALE, Scale, dumps

from marker.scale import numeric

from .test_marker import RUBRIC, RUBRIC_D, FakeClient, answer, load_essay, write_essay

TAGS = [
    {"name": "gpt-oss:20b", "digest": "d20", "details": {"format": "gguf", "parameter_size": "20.9B", "quantization_level": "MXFP4"}},
    {"name": "gpt-oss:120b", "digest": "d120", "details": {"format": "gguf", "parameter_size": "116.8B"}},
    {"name": "gpt-oss:20b-mlx", "digest": "d20m", "details": {"format": "safetensors"}},
    {"name": "muse-glimmer:30b-mlx", "digest": "dmg", "details": {"format": "safetensors"}},
    {"name": "mistral-small:latest", "digest": "dms", "details": {"format": "gguf", "parameter_size": "23.6B"}},
]
INSTALLED = [model_info(t) for t in TAGS]


class ModelSelectionTests(unittest.TestCase):
    def test_model_info(self):
        mg = model_info(TAGS[3])
        self.assertEqual((mg.family, mg.size, mg.mlx), ("muse-glimmer", "30b", True))
        ms = model_info(TAGS[4])
        self.assertEqual((ms.size, ms.mlx), ("23.6b", False))

    def test_family_aliases(self):
        self.assertEqual(family_key("ChatGPT-OSS"), family_key("gpt-oss"))
        self.assertEqual(family_key("Muse Glimmer"), family_key("muse-glimmer"))

    def test_default(self):
        self.assertEqual(select_models(INSTALLED), ["gpt-oss:20b"])

    def test_exact_models_ignore_mlx_policy(self):
        got = select_models(INSTALLED, models=["gpt-oss:20b", "muse-glimmer:30b-mlx"], mlx="never")
        self.assertEqual(got, ["gpt-oss:20b", "muse-glimmer:30b-mlx"])

    def test_missing_model(self):
        with self.assertRaises(ValueError):
            select_models(INSTALLED, models=["llama9:1t"])

    def test_mlx_policies(self):
        sel = lambda mlx, apple=True: select_models(INSTALLED, families=["ChatGPT-OSS"], mlx=mlx, apple_silicon=apple)
        self.assertEqual(sel("prefer"), ["gpt-oss:20b-mlx", "gpt-oss:120b"])
        self.assertEqual(sel("auto", apple=True), sel("prefer"))
        self.assertEqual(sel("auto", apple=False), ["gpt-oss:20b", "gpt-oss:120b"])
        self.assertEqual(sel("only"), ["gpt-oss:20b-mlx"])
        self.assertEqual(sel("any"), ["gpt-oss:20b", "gpt-oss:20b-mlx", "gpt-oss:120b"])

    def test_sizes(self):
        got = select_models(INSTALLED, families=["gpt-oss", "Muse Glimmer"], sizes=["120B", "30"], mlx="never")
        self.assertEqual(got, ["gpt-oss:120b"])
        self.assertEqual(select_models(INSTALLED, sizes=["24b"], mlx="any"), ["mistral-small:latest"])  # 23.6B within 10%
        with self.assertRaises(ValueError):
            select_models(INSTALLED, families=["gpt-oss"], sizes=["7b"])

    def test_think_for(self):
        self.assertEqual(think_for("gpt-oss:20b", "medium", None), "medium")
        self.assertEqual(think_for("gpt-oss-safeguard:120b", "high", ["completion", "thinking"]), "high")
        self.assertIs(think_for("muse-glimmer:30b-mlx", "high", ["completion", "thinking"]), True)
        self.assertIsNone(think_for("mistral-small:latest", "high", ["completion"]))
        self.assertIsNone(think_for("unknown:1b", "high", None))

    def test_slug(self):
        self.assertEqual(slug("library/gpt-oss:20b"), "library_gpt-oss_20b")


def make_result(essay, model: str, marks: dict[str, int], reasoning: str | None = None, rubric=RUBRIC_D) -> dict:
    """Produce a real result via grade_essay with a fake client (one sample), optionally keeping reasoning."""
    client = FakeClient([answer(marks)], thinking=reasoning or "")
    settings = Settings(model=model, samples=1, keep_reasoning=reasoning is not None)
    return grade_essay(client, settings, rubric, essay, model_digest=f"digest-{model}")


class CompareAndReportTests(unittest.TestCase):
    """Exact comparisons, in decimal mode."""

    def setUp(self):
        self.enterContext(numeric("decimal"))
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.e1 = load_essay(write_essay(root, "e1.txt"))
        self.e2 = load_essay(write_essay(root, "e2.txt"))
        hi = dict(knowledge=72, argument=70, evidence=70, structure=72, writing=72)
        mid = dict(knowledge=62, argument=63, evidence=61, structure=62, writing=62)
        lo = dict(knowledge=55, argument=55, evidence=55, structure=55, writing=55)
        self.runs = {
            "model-a": {"e1": make_result(self.e1, "model-a", hi), "e2": make_result(self.e2, "model-a", mid)},
            "model-b": {"e1": make_result(self.e1, "model-b", mid), "e2": make_result(self.e2, "model-b", mid)},
            "model-c": {"e1": make_result(self.e1, "model-c", lo)},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_compare(self):
        report = compare_runs(self.runs, RUBRIC_D, threshold=Decimal("0.1000"))
        e1, e2 = report["essays"]
        # model-a: 0.72*0.2 + 0.70*0.3 + 0.70*0.2 + 0.72*0.15 + 0.72*0.15 = 0.7100
        self.assertEqual(e1["marks"], {"model-a": Decimal("0.71"), "model-b": Decimal("0.621"), "model-c": Decimal("0.55")})
        self.assertEqual(e1["panel_mark"], Decimal("0.6210"))
        self.assertEqual(e1["panel_score"], Decimal("62"))  # all results share the default 0-100 scale
        self.assertEqual(e1["spread"], Decimal("0.16"))
        self.assertFalse(e1["models_agree"])
        self.assertFalse(e2["models_agree"])  # model-c did not mark e2
        self.assertIn("not marked by: model-c", e2["notes"])
        ab = report["pairs"][0]
        self.assertEqual((ab["a"], ab["b"], ab["n"]), ("model-a", "model-b", 2))
        self.assertEqual(ab["mean_diff"], Decimal("0.0445"))  # exact: ((0.71 - 0.621) + 0) / 2
        self.assertEqual(ab["exact_band_agreement"], 0.5)
        text = format_report(report, RUBRIC_D)
        self.assertIn("panel mark: 62", text)
        self.assertIn("model-a vs model-b", text)
        self.assertIn("marks shown on 0-100", text)
        exported = json.loads(dumps(report))
        self.assertEqual(exported["essays"][0]["panel_mark"], "0.6210")  # exact decimal text, never a float

    def test_compare_needs_two(self):
        with self.assertRaises(ValueError):
            compare_runs({"model-a": self.runs["model-a"]}, RUBRIC_D)

    def test_compare_float_mode(self):
        with numeric("float"):
            runs = {
                m: {"e1": make_result(self.e1, m, marks, rubric=RUBRIC)}
                for m, marks in (("model-a", dict(knowledge=72, argument=70, evidence=70, structure=72, writing=72)),
                                 ("model-b", dict(knowledge=62, argument=63, evidence=61, structure=62, writing=62)))
            }
            report = compare_runs(runs, RUBRIC)
            e1 = report["essays"][0]
            self.assertIsInstance(e1["panel_mark"], float)
            self.assertAlmostEqual(e1["panel_mark"], (0.71 + 0.621) / 2)
            self.assertIn("panel mark: 67", format_report(report, RUBRIC))

    def test_parse_show(self):
        self.assertEqual(parse_show(None), {"score"})
        self.assertEqual(parse_show(["score,explanation", "thinking"]), {"score", "explanation", "thinking"})
        self.assertEqual(parse_show(["all"]), {"score", "explanation", "thinking"})
        with self.assertRaises(ValueError):
            parse_show(["vibes"])

    def test_render_views(self):
        r = self.runs["model-a"]["e1"]
        self.assertEqual(render(r, {"score"}).split("\t")[:2], ["e1", "71"])
        self.assertEqual(render(r, {"score"}, scale=FRACTION_SCALE).split("\t")[:2], ["e1", "0.7100"])
        self.assertEqual(render(r, {"score"}, scale=Scale.parse("0", "20", 1)).split("\t")[:2], ["e1", "14.2"])
        expl = render(r, {"explanation"})
        self.assertIn("[argument]", expl)
        self.assertIn("Justification for argument.", expl)
        self.assertIn("not stored", render(r, {"thinking"}))
        with_thinking = make_result(self.e1, "model-a", dict(knowledge=60, argument=60, evidence=60, structure=60, writing=60), reasoning="Step 1: read the essay.")
        self.assertIn("Step 1: read the essay.", render(with_thinking, {"thinking"}))


class FakeOllama(FakeClient):
    """A fake server for CLI tests: knows its installed models and always answers with fixed marks per model."""

    def __init__(self, marks_by_model: dict[str, dict[str, int]]):
        super().__init__([])
        self.marks_by_model = marks_by_model
        self.calls: list[tuple[str, object]] = []

    def version(self): return "test"
    def list_models(self): return TAGS
    def capabilities(self, model): return ["completion", "thinking"]
    def model_digest(self, model): return f"digest-{model}"

    def chat(self, model, messages, *, schema, options, think):
        self.calls.append((model, think))
        return {"message": {"content": answer(self.marks_by_model[model]), "thinking": f"thoughts of {model}"}}


class CliMultiModelTests(unittest.TestCase):
    def test_grade_two_models_compare_and_show(self):
        fake = FakeOllama({
            "gpt-oss:20b": dict(knowledge=65, argument=65, evidence=65, structure=65, writing=65),
            "muse-glimmer:30b-mlx": dict(knowledge=58, argument=58, evidence=58, structure=58, writing=58),
        })
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cli, "make_client", return_value=fake):
            essay = write_essay(Path(d), "paper.txt")
            out = Path(d) / "out"
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = cli.main(["grade", str(essay), "--model", "gpt-oss:20b,muse-glimmer:30b-mlx",
                                 "--samples", "1", "--out", str(out), "--show", "score,thinking", "--think", "medium"])
            self.assertEqual(code, 0, stderr.getvalue())
            # gpt-oss gets an effort level; the other thinking model gets True.
            self.assertEqual(fake.calls, [("gpt-oss:20b", "medium"), ("muse-glimmer:30b-mlx", True)])
            self.assertTrue((out / "gpt-oss_20b" / "paper.json").exists())
            self.assertTrue((out / "muse-glimmer_30b-mlx" / "paper.json").exists())
            self.assertTrue((out / "comparison.csv").exists())
            text = stdout.getvalue()
            self.assertIn("thoughts of gpt-oss:20b", text)
            self.assertIn("paper\t65\tUpper second (2:1)\tgpt-oss:20b", text)
            self.assertIn("panel mark: 62", text)  # median of 65 and 58 = 61.5 -> 62

            # The re-run reuses results (no new chat calls) and still prints them.
            fake.calls.clear()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["grade", str(essay), "--model", "gpt-oss:20b,muse-glimmer:30b-mlx",
                                           "--samples", "1", "--out", str(out), "--show", "score,thinking", "--think", "medium"]), 0)
            self.assertEqual(fake.calls, [])

            # A different score scale is re-analysed from stored samples, with no model calls.
            path = out / "gpt-oss_20b" / "paper.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["grade", str(essay), "--model", "gpt-oss:20b,muse-glimmer:30b-mlx",
                                           "--samples", "1", "--out", str(out), "--think", "medium",
                                           "--score-min", "0.0000", "--score-max", "1.0000"]), 0)
            self.assertEqual(fake.calls, [])
            self.assertIn("paper\t0.6500\tUpper second (2:1)\tgpt-oss:20b", stdout.getvalue())
            fresh = json.loads(path.read_text())
            # Default float mode: the value is a JSON number; the scale bounds are exact strings.
            self.assertEqual(fresh["score"], {"value": 0.65, "min": "0.0000", "max": "1.0000", "places": 4})
            self.assertEqual(fresh["numeric"], "float")
            self.assertIn("reanalysed_at", fresh["provenance"])
            self.assertIn('"event": "reanalysed"', (out / "gpt-oss_20b" / "audit.jsonl").read_text())
            summary = (out / "gpt-oss_20b" / "summary.csv").read_text()
            self.assertIn("paper,0.6500,0.0000,1.0000,0.65,", summary)

            # Switching to --numeric decimal also re-analyses stored samples, with no model calls.
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["grade", str(essay), "--model", "gpt-oss:20b,muse-glimmer:30b-mlx",
                                           "--samples", "1", "--out", str(out), "--think", "medium", "--numeric", "decimal",
                                           "--score-min", "0.0000", "--score-max", "1.0000"]), 0)
            self.assertEqual(fake.calls, [])
            fresh = json.loads(path.read_text())
            self.assertEqual(fresh["numeric"], "decimal")
            self.assertEqual(fresh["score"]["value"], "0.6500")  # exact string
            self.assertEqual(fresh["mark"], "0.6500")
            self.assertIn("paper,0.6500,0.0000,1.0000,0.6500,", (out / "gpt-oss_20b" / "summary.csv").read_text())

            # Results from older marker versions (integer 0-100 marks) are re-marked, not misread.
            stale = json.loads(path.read_text())
            stale["provenance"]["analysis_version"] = "3"
            path.write_text(json.dumps(stale))
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["grade", str(essay), "--model", "gpt-oss:20b", "--samples", "1",
                                           "--out", str(out / "gpt-oss_20b"), "--think", "medium"]), 0)
            self.assertEqual(fake.calls, [("gpt-oss:20b", "medium")])
            fake.calls.clear()

            # compare and show work on the saved results.
            self.assertEqual(len(discover_result_dirs([str(out)])), 2)
            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["compare", str(out)]), 0)
                self.assertEqual(cli.main(["show", str(out / "gpt-oss_20b"), "--show", "explanation"]), 0)
            self.assertIn("gpt-oss:20b vs muse-glimmer:30b-mlx", stdout.getvalue())
            self.assertIn("[knowledge]", stdout.getvalue())
            self.assertEqual(set(label_runs(discover_result_dirs([str(out)]))), {"gpt-oss:20b", "muse-glimmer:30b-mlx"})
            self.assertEqual(len(load_results(out / "gpt-oss_20b")), 1)


if __name__ == "__main__":
    unittest.main()
