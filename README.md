# marker: local, reproducible essay marking with gpt-oss

`marker` marks student essays from **0 to 100** against an analytic,
criterion-referenced rubric. By default it uses **gpt-oss** (OpenAI's
open-weights reasoning model) running **locally** in
[Ollama](https://ollama.com), so essays never leave your machine. It can also
use any other local Ollama model, including Mac-optimised MLX builds, or
several models at once for comparison.

It is built to be a **second marker**, not a replacement for one. Every mark
comes with a per-criterion justification, quoted evidence, feedback for the
student, and review flags that tell a human which essays need a closer look.

- **Secure and private**: loopback-only by default, standard library only (no third-party Python packages).
- **Reproducible**: fixed seeds; the model's content digest, prompt hash and settings are recorded in every result.
- **Any local model, or several**: choose models by tag, family ("ChatGPT-OSS",
  "Muse Glimmer") or size, prefer Mac-optimised MLX builds, and compare models
  side by side with a panel mark.
- **Transparent**: print the score, the per-criterion explanation and/or the model's thinking.
- **Fractional marks**: marks are held internally as fractions from 0.0 to
  1.0, as Python `float` by default or as exact `Decimal` with `--numeric decimal`.
  Scores can be reported on any scale: 0-100 (the default), 0.0000-1.0000, 0-20, and so on.
- **Grounded in marking practice**: UK classification bands, analytic criteria,
  median of independent samples (like double marking), borderline review, and
  calibration against human marks with QWK. See [`docs/research.md`](docs/research.md).

> **Status:** prototype for research and moderation support. Calibrate it
> against your own human-marked essays before relying on it, and keep a human
> responsible for every final mark.

---

## Contents

- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Commands](#commands)
- [Choosing models](#choosing-models)
- [Comparing models](#comparing-models)
- [Choosing what to print](#choosing-what-to-print)
- [Score scale and numeric precision](#score-scale-and-numeric-precision)
- [Rubrics](#rubrics)
- [Output](#output)
- [Review flags](#review-flags)
- [Calibration against human marks](#calibration-against-human-marks)
- [Reproducibility](#reproducibility)
- [Security and privacy](#security-and-privacy)
- [Using it as a library](#using-it-as-a-library)
- [Project layout](#project-layout)
- [AI agent support](#ai-agent-support)
- [Development](#development)
- [Limitations](#limitations)
- [License](#license)

---

## How it works

1. **Prompt**: the full rubric (bands, criteria, weights and per-band
   descriptors) goes in the system prompt. The optional assignment brief and
   the essay go in the user message, wrapped in `<essay>` tags and declared to
   be untrusted data.
2. **Judge**: gpt-oss reasons (`think` = `high`) and answers in a JSON schema
   that Ollama enforces. For each criterion it gives a **justification**,
   **evidence quotes**, a **band**, and a **mark** inside that band: a fraction
   from 0.0 to 1.0, asked for with 4 places (e.g. `0.6450`).
3. **Repeat**: this is done for 3 independent samples by default, with seeds
   `42, 43, 44`. An invalid answer is retried with a derived seed.
4. **Aggregate in code**: the median mark per criterion, then the weighted
   mean, mapped to a band (floats by default; exact Decimals rounded to 4
   places with `--numeric decimal`). The model
   never does arithmetic. The final **score** is that weighted mark mapped onto
   your reporting scale (by default 0-100 integers).
5. **Flag**: the essay is flagged if samples disagree, the mark is borderline,
   quotes can't be found in the essay, the word count is outside limits,
   injection-like text is present, or the model raised integrity concerns.
6. **Record**: a JSON file per essay with marks, feedback, flags, every raw
   sample, and full provenance, plus a summary CSV and an append-only audit log.

## Requirements

- **Python 3.11+**. No other Python packages are needed.
- **Ollama** (tested with 0.35.1), running locally.
- **A gpt-oss model** (the default; any other local Ollama model also works, see [Choosing models](#choosing-models)):
  - `gpt-oss:20b`: about 13 GB download, runs on machines with about 16 GB of
    free (V)RAM. This is the default.
  - `gpt-oss:120b`: about 65 GB, better judgement, needs about 80 GB.

## Installation

```sh
# 1. Ollama and the model
brew install ollama            # or see https://ollama.com/download
ollama serve &                 # or start the Ollama app
ollama pull gpt-oss:20b

# 2. This tool: either run it in place...
python3 -m marker --help

# ...or install the `marker` command
pip install -e .               # or: uv tool install -e .
marker --help
```

> If Ollama reports `unsupported tensor … size overflows`, your model files
> predate your Ollama version. Re-pull with `ollama pull gpt-oss:20b`.

## Quick start

```sh
python3 -m marker check                          # Ollama reachable? model installed?
python3 -m marker grade assets/example-papers/ --out results/example
```

Output on stdout (progress goes to stderr):

```
critical-evaluation-of-research-papers-psychology-essay	<mark>	<band>  [REVIEW]
```

Then open `results/example/<essay-id>.json` for justifications and feedback,
or `results/example/summary.csv` for the batch overview.

To mark a real assignment, give the question too:

```sh
python3 -m marker grade submissions/ \
  --brief briefs/ps2001-essay-1.txt \
  --rubric rubrics/uk-undergraduate-essay.toml \
  --out results/ps2001-essay-1
```

## Commands

| Command | Purpose |
|---|---|
| `marker models` | List installed models (family, size, quantisation, MLX build); `*` marks the current selection |
| `marker check` | Check that Ollama is up and the selected models are usable |
| `marker rubric` | Validate and show a rubric |
| `marker grade` | Mark essays with one or more models |
| `marker compare` | Compare existing results from different models |
| `marker show` | Print the score, explanation and/or thinking from existing results |
| `marker evaluate` | Measure agreement with human marks |

Options shared by `models`, `check` and `grade`:

| Option | Default | Meaning |
|---|---|---|
| `--model TAG` | `gpt-oss:20b` | Exact model tag. Repeat or comma-separate for several |
| `--family NAME` | | Model family, loosely matched. Repeatable |
| `--size SIZE` | | Parameter sizes to keep, e.g. `20b,120b`. Repeatable |
| `--mlx POLICY` | `auto` | Mac MLX builds: `auto`, `prefer`, `only`, `never`, `any` |
| `--host` | `http://127.0.0.1:11434` (or `$OLLAMA_HOST_URL`) | Ollama URL |
| `--allow-remote` | off | Allow a non-loopback host (see [security](#security-and-privacy)) |

### `marker models` and `marker check`

```sh
python3 -m marker models                                   # everything installed
python3 -m marker models --family gpt-oss --size 20b,120b  # * shows what would be used
python3 -m marker check --family "ChatGPT-OSS" --family "Muse Glimmer"
```

`check` prints each selected model's digest, size, quantisation, build (GGUF
or MLX) and whether it supports thinking. It exits with `1` if Ollama can't
load a model (e.g. "unsupported tensor … size overflows"). Re-pull that model.

### `marker rubric`

Validates a rubric and prints its bands and weighted criteria.

```sh
python3 -m marker rubric --rubric rubrics/uk-undergraduate-essay.toml
```

### `marker grade ESSAY_OR_DIR...`

Marks `.txt`, `.md` and `.docx` files. Directories are expanded (not
recursively) in sorted order, skipping README and hidden files. The essay id
is the file name without its extension.

| Option | Default | Meaning |
|---|---|---|
| `--rubric` | bundled UK undergraduate rubric | Rubric TOML |
| `--brief` | none | Text file with the assignment question or brief |
| `--out` | `results` | Output directory (one subdirectory per model when there are several) |
| `--model`, `--family`, `--size`, `--mlx` | `gpt-oss:20b` | Model selection (see [Choosing models](#choosing-models)) |
| `--show` | `score` | What to print: `score`, `explanation`, `thinking` or `all` (see [Choosing what to print](#choosing-what-to-print)) |
| `--samples` | `3` | Independent samples per essay (the median is used) |
| `--seed` | `42` | Base seed; sample *i* uses `seed + i` |
| `--temperature` | `0.6` | Sampling temperature (non-zero so samples are independent) |
| `--think` | `high` | Reasoning effort for gpt-oss: `low`, `medium` or `high`. Other thinking models have reasoning switched on |
| `--num-ctx` | `32768` | Context window (tokens) |
| `--numeric` | `float` | Numeric type for marks: `float` or `decimal` (exact, 4 places). See [numeric precision](#score-scale-and-numeric-precision) |
| `--disagreement` | `0.1000` | Flag if marks spread by more than this fraction (10 points), across samples or across models in a comparison |
| `--boundary-margin` | `0.0200` | Flag a final mark this close below a band boundary (e.g. 0.6800-0.6999 below a First) |
| `--score-min`, `--score-max`, `--score-places` | `0`, `100`, inferred | Reporting scale (see [Score scale](#score-scale-and-numeric-precision)) |
| `--keep-reasoning` | off | Store the model's reasoning trace (large). Implied by `--show thinking` |
| `--force` | off | Remark even if the inputs are unchanged |
| `--host`, `--allow-remote` | | As above |

Re-running is safe and cheap: an essay whose inputs (text, prompt, rubric,
brief, model digest and settings) are unchanged is **skipped**. An
interrupted batch can simply be restarted. If only marker's own analysis rules
(quote matching, flags, aggregation; tracked as `analysis_version`), the
**score scale** or the **numeric mode** have changed since then, the saved result is **re-analysed
from its stored samples** in under a second, with no model calls. Results saved
by marker versions that used integer 0-100 marks are re-marked automatically.

**Exit codes:** `0` all essays marked or skipped · `1` some essays failed · `2` usage or configuration error.

**Time:** with `--think high` and 3 samples, expect a few minutes per essay
with `gpt-oss:20b` on Apple Silicon. Use `--think medium` for faster drafts.
The default settings are the reference configuration.

### `marker compare DIR...`

Compares result directories from different models. Give it the `--out`
directory of a multi-model `grade` run, or several single-model result
directories. It writes `comparison.csv` and `comparison.json` (to the
directory given, or to `--out`) and prints the comparison table. `--json`
prints the full report.

### `marker show RESULT...`

Prints views of existing result files or directories, e.g.
`python3 -m marker show results/example --show explanation`. With
`--score-min`/`--score-max`, scores are re-mapped exactly onto that scale for
display; without them, each result's saved scale is used.

### `marker evaluate`

Compares machine marks with human marks. See [Calibration](#calibration-against-human-marks).

| Option | Default | Meaning |
|---|---|---|
| `--results` | `results/summary.csv` | `summary.csv` from `grade`, or `comparison.csv` from a comparison |
| `--results-mark-column` | `score` | Score column in `--results` (`panel_score` for `comparison.csv`) |
| `--human` | (required) | CSV of human marks, on the same scale |
| `--id-column` / `--mark-column` | `id` / `mark` | Column names in the human CSV |
| `--score-min`, `--score-max`, `--score-places` | `0`, `100` | The scale both CSVs use, e.g. `--score-min 0.0000 --score-max 1.0000` |
| `--rubric` | bundled | Used to map marks to bands |
| `--json` | off | Print the full report, including per-essay differences |

## Choosing models

By default, essays are marked with `gpt-oss:20b`. Any locally installed
Ollama model can be selected; list them with `marker models`.

```sh
# exact tags (repeat or comma-separate)
python3 -m marker grade paper.md --model gpt-oss:120b
python3 -m marker grade paper.md --model gpt-oss:20b,muse-glimmer:30b-mlx

# by family name: case, spaces and hyphens don't matter; "ChatGPT-OSS" means gpt-oss
python3 -m marker grade paper.md --family "ChatGPT-OSS" --family "Muse Glimmer"

# by size within a family
python3 -m marker grade paper.md --family gpt-oss --size 20b          # just the 20B
python3 -m marker grade paper.md --family gpt-oss --size 20b,120b     # both sizes, compared
```

**Mac-optimised (MLX) models.** On Apple Silicon, Ollama can run MLX builds
(tags ending `-mlx`, stored as safetensors), which run natively on the Mac
GPU. When you select by `--family`/`--size`, `--mlx` decides which build is used:

| `--mlx` | Behaviour |
|---|---|
| `auto` (default) | `prefer` on Apple Silicon, `never` elsewhere |
| `prefer` | For each family and size, use the MLX build if one is installed, otherwise the standard build |
| `only` | Only MLX builds |
| `never` | Only standard (GGUF) builds |
| `any` | Every matching build. Useful for comparing an MLX build with its GGUF counterpart |

Exact `--model` tags are always used as given.

> **Reproducibility warning for MLX builds.** In testing with Ollama 0.35.1,
> MLX builds **did not honour the seed**: identical requests gave different
> outputs, and the same essay scored 53 and then 50 with `muse-glimmer:30b-mlx`.
> GGUF builds (e.g. `gpt-oss:20b`, `mistral-small`) gave identical outputs
> for the same seed. When reproducibility matters, use `--mlx never` or an
> exact GGUF tag. `marker grade` prints a warning whenever it uses an MLX build.

**Reasoning.** gpt-oss gets the `--think` effort level. Other models that
report the *thinking* capability have reasoning switched on, and models
without it are run without. The effective setting is recorded in each
result's provenance.

**Reproducibility across models.** Every result records its model tag and
content digest. Results from different models are different experiments, so
calibrate each model separately.

## Comparing models

*"Mark this paper with model X and model Y, then compare the results":*

```sh
python3 -m marker grade paper.md --model gpt-oss:20b --model muse-glimmer:30b-mlx \
  --out results/paper-compare --show score,explanation
```

**Example: three local models.** This marks the bundled example essay with
Muse Glimmer 30B (MLX), Qwen 3.8 27B (MLX) and gpt-oss 20B, then compares them:

```sh
python3 -m marker grade assets/example-papers/ \
  --model muse-glimmer:30b-mlx \
  --model qwen3.8:27b-mlx \
  --model gpt-oss:20b \
  --out results/three-models \
  --show score,explanation
```

The same thing on one line: `--model muse-glimmer:30b-mlx,qwen3.8:27b-mlx,gpt-oss:20b`.
Add `--score-min 0.0000 --score-max 1.0000` to report every score, and the
comparison table, as a probability with 4 decimal places.
Check first that all three load: `python3 -m marker check --model muse-glimmer:30b-mlx,qwen3.8:27b-mlx,gpt-oss:20b`.
With an odd number of models, the panel mark is the middle model's mark. The
two MLX models trigger the [reproducibility warning](#choosing-models) above.

Each model marks every essay, one model after another, so each model is
loaded once. Results go to `results/paper-compare/<model>/`. A comparison is
then printed and saved to `results/paper-compare/comparison.{csv,json}`:

```
Model comparison: 2 models, 1 essay(s); marks shown on 0-100

Essay: paper
  criterion   gpt-oss:20b  muse-glimmer:30b-mlx  spread
  knowledge            62                    55       7
  argument             58                    52       6
  ...
  OVERALL              60                    53       7
  band       Upper second (2:1)  Lower second (2:2)
  panel mark: 57 (Lower second (2:2)), median 0.5650; models agree: no
  - models place the essay in different bands

Pairwise agreement (a vs b; mean diff = a - b, on 0-100)
  gpt-oss:20b vs muse-glimmer:30b-mlx: n=1  mean diff +7  MAE 7  exact band 0%  QWK n/a (needs 2+ essays)
```

*(Illustrative numbers.)*

- **Panel mark**: the exact median of the models' overall marks
  (0.0000-1.0000), shown on the reporting scale and saved as `panel_score`.
  Using several different judges reduces the effect of any one model's
  biases, much like double marking. To validate it against human marks:
  `marker evaluate --results results/paper-compare/comparison.csv --results-mark-column panel_score --human human.csv`.
- **Models agree** when every model gives the same band and the overall
  spread is within `--disagreement` (default 0.1000, i.e. 10 points).
- **Pairwise agreement** over several essays: mean difference (which model
  is more generous), MAE, exact band agreement, QWK, and per-criterion MAE (in the JSON).

Re-compare saved results at any time with `python3 -m marker compare results/paper-compare`.

## Choosing what to print

`--show` (on `grade` and `show`) picks what is printed. Combine views with
commas, or use `all`:

| View | Prints |
|---|---|
| `score` (default) | `id<TAB>mark<TAB>band[<TAB>model]`, plus `[REVIEW]` if flagged. One line, easy to pipe |
| `explanation` | Each criterion's mark, band, sample marks, justification and evidence quotes (quotes not found in the essay are marked), then strengths, improvements, integrity concerns and review flags |
| `thinking` | The model's reasoning trace for each sample. Asking for it during `grade` stores it automatically |

```sh
python3 -m marker grade paper.md --show score                       # just the mark
python3 -m marker grade paper.md --show explanation,score           # why, then the mark
python3 -m marker grade paper.md --show all                         # thinking, explanation, mark
python3 -m marker show results/paper.json --show thinking           # from a saved result
```

The reasoning trace is the model's working, not a polished explanation. It
can be long, and it may contain false starts. Use `explanation` for feedback.

## Score scale and numeric precision

**Internally, every mark is a fraction from 0.0 to 1.0.** This applies to
rubric bands, the model's answers, sample marks, medians, the weighted mark
and the review thresholds. A UK First starts at 0.7. `--numeric` chooses the
Python type, on `grade`, `compare`, `show`, `evaluate` and `rubric`:

| `--numeric` | Type | Behaviour |
|---|---|---|
| `float` (default) | Python `float`, 0.0-1.0 | Marks are used as the model gives them. Arithmetic is ordinary binary floating point, so `0.1 + 0.2` is `0.30000000000000004` and a weighted mark may be `0.44550000000000006`. In JSON, marks are numbers |
| `decimal` | Python `Decimal`, 0.0000-1.0000 | Exact: model marks and the final mark are rounded half up to 4 places, and `0.1 + 0.2` is exactly `0.3`. Model JSON is parsed with `parse_float=Decimal`, so no float is ever involved. In JSON, marks are exact strings (`"0.4455"`) |

In both modes:

- **Rounding:** reported scores are rounded **half up**, using each float's
  shortest decimal form, so `0.62125` gives `0.6213` and 64.5 gives 65. This
  differs from Python's `round()`, which rounds halves to even.
- **Bands are continuous:** a mark belongs to the highest band whose minimum
  is at or below it, so a float such as 0.79995 is still a First.
- **Configuration stays exact:** values you type (score bounds, thresholds)
  are read as exact decimals and converted to the mode only when used.
- **Same prompt and fingerprint:** switching mode only **re-analyses** saved
  results from their stored samples. The model isn't run again.

Out-of-range marks, such as `65` given out of habit, are rejected and the
model is retried.

The final **score** is the weighted mark mapped onto your reporting scale,
then rounded half up:

```
score = score_min + weighted_mark × (score_max − score_min)
```

| Options | Example score for weighted mark 0.62125 |
|---|---|
| (default) `--score-min 0 --score-max 100` | `62` |
| `--score-min 0.0000 --score-max 1.0000` (probability, 4 places) | `0.6213` |
| `--score-min 0.00 --score-max 1.00` | `0.62` |
| `--score-min 0 --score-max 20 --score-places 1` | `12.4` |

Decimal places are inferred from how you write the bounds (`1.0000` gives 4),
or set with `--score-places` (0-6). Changing the scale never re-runs the
model: saved results are re-analysed from their stored samples.

Each result records its mode in `"numeric"`. Values that aren't marks, such
as the sampling `--temperature`, timings and model sizes, are always ordinary numbers.

## Rubrics

A rubric is a TOML file with **bands** that tile 0.0000-1.0000 exactly (written
to 4 decimal places, so one file works in both numeric modes) and weighted
**criteria** that have per-band descriptors. TOML numbers are read as floats,
or as exact `Decimal`s with `--numeric decimal`. Validation always uses the
exact values as written. The bundled
[`rubrics/uk-undergraduate-essay.toml`](rubrics/uk-undergraduate-essay.toml) uses:

| Band | Marks (internal) | As a percentage |
|---|---|---|
| Exceptional first | 0.8000-1.0000 | 80-100 |
| First | 0.7000-0.7999 | 70-79 |
| Upper second (2:1) | 0.6000-0.6999 | 60-69 |
| Lower second (2:2) | 0.5000-0.5999 | 50-59 |
| Third | 0.4000-0.4999 | 40-49 |
| Marginal fail | 0.3000-0.3999 | 30-39 |
| Fail | 0.0000-0.2999 | 0-29 |

| Criterion | Weight |
|---|---|
| Knowledge, understanding and relevance to the question | 20% |
| Argument and critical analysis | 30% |
| Use of evidence and sources | 20% |
| Structure and organisation | 15% |
| Academic writing, style and referencing | 15% |

Minimal example:

```toml
name = "My module essay"
version = "1.0.0"
min_words = 0          # 0 = no check
max_words = 2500       # outside limits -> flagged, never penalised

[[bands]]
id = "pass"
label = "Pass"
min = 0.4000
max = 1.0000
descriptor = "Meets the learning outcomes."

[[bands]]
id = "fail"
label = "Fail"
min = 0.0000
max = 0.3999           # exactly 0.0001 below the next band's min
descriptor = "Does not meet the learning outcomes."

[[criteria]]
id = "argument"
name = "Argument"
weight = 1
description = "Clarity and quality of reasoning."
[criteria.descriptors]
pass = "A clear, supported argument."
fail = "No coherent argument."
```

Validation rules: band bounds have at most 4 decimal places, and the bands
cover 0.0000-1.0000 exactly (each band's `max` is 0.0001 below the `min` of the
band above), with unique ids. Criterion weights are positive and sum to
*exactly* 1. Descriptors refer only to existing bands.
For authoring guidance, see [`skills/write-rubric`](skills/write-rubric/SKILL.md).

## Output

For each batch directory (`--out`, or `--out/<model>/` when several models are used):

| File | Contents |
|---|---|
| `<essay-id>.json` | Full result (below). Written atomically. |
| `summary.csv` | `id, score, score_min, score_max, mark, band, needs_review, <criterion marks…>, word_count, flags, model, model_digest`. `mark` and the criterion marks are 0.0000-1.0000. Rebuilt on every run. |
| `audit.jsonl` | One line per event (`marked` or `reanalysed`): essay id and hash, mark, score, needs_review, time, model, digest, fingerprint. Append-only. |

With several models, `--out` also contains `comparison.csv` (one row per
essay: each model's mark, score and band, spread, panel median, panel mark,
panel score and band, whether the models agree, notes) and `comparison.json` (the full report,
including pairwise statistics).

Result JSON (abridged):

```jsonc
{
  "essay": {"id": "…", "path": "…", "word_count": 3412, "sha256": "…"},
  "numeric": "float",               // or "decimal"
  "mark": 0.581,                    // internal mark 0.0-1.0 (decimal mode: exact string "0.5810")
  "weighted_mark": 0.581,           // unrounded
  "score": {"value": 58.0, "min": "0", "max": "100", "places": 0},   // on the reporting scale
  "band": {"id": "lower_second", "label": "Lower second (2:2)"},
  "needs_review": true,
  "flags": ["borderline: 0.5810 is within 0.0200 of the 0.6000 boundary"],
  "criteria": {
    "argument": {
      "mark": 0.55, "band": "lower_second", "weight": 0.3,
      "sample_marks": [0.55, 0.52, 0.57], "spread": 0.05,
      "justification": "…", "evidence": ["exact quote…"], "unverified_evidence": []
    }
    // … one entry per criterion
  },
  "feedback": {"strengths": ["…"], "improvements": ["…"]},
  "integrity_concerns": [],
  "samples": [ /* every raw sample: seed, marks, justification, raw JSON, timing, tokens, reasoning if kept */ ],
  "provenance": {
    "marker_version": "0.1.0", "prompt_version": "2.0.0", "analysis_version": "5",
    "prompt_sha256": "…", "schema_sha256": "…",
    "rubric": {"name": "…", "version": "1.0.0", "sha256": "…"},
    "brief_sha256": "…", "model": "gpt-oss:20b", "model_digest": "f2b8…",
    "ollama_version": "0.35.1", "settings": {…}, "fingerprint": "…",
    "python": "3.11.11", "platform": "…", "marked_at": "2026-10-09T15:00:00+00:00"
  }
}
```

## Review flags

`needs_review` is true if **any** flag is raised.

| Flag | Why it matters |
|---|---|
| `samples disagree on <criterion>` | The model's independent judgements varied by more than `--disagreement` (0.1000), so it is less certain |
| `borderline: M is within 0.0200 of the B boundary` | UK exam-board practice: marks just below a classification boundary get a second look (`--boundary-margin`) |
| `<criterion>: k evidence quote(s) not found in the essay` | The model "quoted" text that isn't there (e.g. a paraphrase), so its justification is suspect. Matching ignores case, punctuation, spacing and dash/quote variants, so typography alone never triggers it |
| `word count … below the minimum / exceeds the limit` | Rubric word limits (flagged only, never penalised automatically) |
| `possible prompt injection in essay: …` | The essay contains text that seems aimed at an AI marker |
| `model raised integrity concerns` | E.g. fabricated-looking references or off-topic content. For a human to investigate, not an accusation |
| `model gave marks outside its chosen band (clamped)` | The band and mark were inconsistent, and the mark was clamped into the band |

For a reviewing workflow, see [`skills/moderate-flagged-essays`](skills/moderate-flagged-essays/SKILL.md).

## Calibration against human marks

Before using the marker on live work, check how well it agrees with your
markers on essays you have already marked:

```sh
python3 -m marker grade calibration-essays/ --brief brief.txt --out results/calibration
python3 -m marker evaluate --results results/calibration/summary.csv --human human-marks.csv
```

```
essays compared:         40
MAE:                     4 (on 0-100)
RMSE:                    5 (on 0-100)
mean bias (machine-human): +1 (on 0-100)
Pearson r:               0.812
QWK (marks):             0.795
QWK (bands):             0.741
exact band agreement:    65%
adjacent band agreement: 98%
```

*(Illustrative numbers, not a measured result.)* **QWK** (quadratic weighted
kappa) is the standard agreement metric in automated essay scoring. Compare it
with the agreement between your own human markers. Re-calibrate whenever the
model, rubric, brief or prompt changes. If your human marks use another scale,
say so, e.g. `--score-min 0.0000 --score-max 1.0000` for probabilities. See
[`skills/calibrate-marker`](skills/calibrate-marker/SKILL.md).

## Reproducibility

- **Fixed seeds**: sample *i* uses `seed + i`, and retry *n* uses `seed + i + 1000·n`.
- **Pinned weights**: the model's **content digest** is recorded. A re-pulled
  tag with different weights shows up as a different digest.
- **Hashed inputs**: SHA-256 of the essay text, exact prompt messages,
  schema, rubric file and brief.
- **Fingerprint**: one hash over all of the above plus settings. Equal
  fingerprints mean equal inputs.
- **Deterministic aggregation**: median, weighted mean, and round half up.
  For results that must be byte-identical across machines and languages, use
  `--numeric decimal`, which is exact. Floats are deterministic in Python on
  IEEE 754 hardware, but not exact.

**Limits:** bit-identical model output is expected only with the same
weights, Ollama build and hardware, and only for GGUF builds: MLX builds
ignore the seed (see [Choosing models](#choosing-models)). Floating-point kernels differ between
GPUs and versions, which is why the digest and Ollama version are recorded.

## Security and privacy

- **Local only.** The client refuses any Ollama host that doesn't resolve
  exclusively to loopback. `--allow-remote` overrides this; use it only for a
  server you control, on a network you trust.
- **No dependencies.** The code uses only the Python standard library, so
  there is no third-party code to audit.
- **Untrusted essays.** Prompt-injection defences: delimiters that can't be
  escaped, explicit "essay is data" instructions, and an independent
  heuristic detector that flags suspicious text for a human. `.docx` parsing
  is restricted to `document.xml`, with a size cap.
- **Data handling.** `results/` contains quotes from essays and is personal
  data. Keep it out of version control (it is in `.gitignore`) and apply your
  institution's retention policy. `audit.jsonl` holds hashes, not text.
- **Human decision.** The tool's marks are provisional. A human confirms
  every mark that is released to a student.

## Using it as a library

```python
from marker.essay import load_essay
from marker.grader import Settings, grade_essay
from marker.ollama import OllamaClient
from marker.rubric import load_rubric

client = OllamaClient()                       # loopback only
settings = Settings(model="gpt-oss:20b", samples=3, seed=42)
result = grade_essay(
    client,
    settings,
    load_rubric("rubrics/uk-undergraduate-essay.toml"),
    load_essay("essay.md"),
    brief="Critically evaluate two published research papers.",
    model_digest=client.model_digest(settings.model),
    ollama_version=client.version(),
)
print(result["mark"], result["band"]["label"], result["flags"])
```

All public functions have docstrings: `python3 -m pydoc marker.grader`.

## Project layout

```
marker/                 Python package (stdlib only)
  cli.py                  command-line interface
  models.py               model discovery and selection (family, size, MLX)
  compare.py              multi-model comparison and panel mark
  report.py               score / explanation / thinking views
  grader.py               sampling, validation, aggregation, flags, provenance
  prompt.py               system prompt, JSON schema, hashing
  rubric.py               rubric model and validation
  essay.py                .txt/.md/.docx loading
  ollama.py               loopback-only Ollama client
  evaluate.py             QWK, MAE, RMSE, bias, band agreement
rubrics/                marking rubrics (TOML)
assets/example-papers/  example essays for testing
docs/research.md        research on marking practice and LLM marking
spec/index.md           project specification
skills/                 AI agent skills (mark, rubric, calibrate, moderate, prepare)
AGENTS.md, AGENTS/      guidance for AI coding agents
tests/                  offline unit tests
```

## AI agent support

- [`AGENTS.md`](AGENTS.md) and [`AGENTS/`](AGENTS/): architecture, conventions,
  testing, security, reproducibility and methodology rules for coding agents.
  `CLAUDE.md` imports `AGENTS.md`.
- [`skills/`](skills/): task skills for agents that operate the tool:
  - `mark-essays`: run a marking batch and report the results
  - `compare-models`: mark with several models and compare them
  - `write-rubric`: turn a marking scheme into a rubric TOML
  - `calibrate-marker`: measure agreement with human marks
  - `moderate-flagged-essays`: support human review of flagged essays
  - `prepare-essays`: convert web pages and PDFs into markable Markdown

## Development

```sh
python3 -m unittest discover -s tests -t .   # offline tests, < 1 s
uvx ruff check marker tests --select F,E9    # lint
uvx mypy marker                              # types (clean)
```

The tests use a fake Ollama client, so no model is needed. For an end-to-end
check, see [`AGENTS/testing.md`](AGENTS/testing.md).

## Limitations

- LLM marks can be wrong with confidence. The flags catch some failure
  modes, not all.
- Known LLM-judge biases (length, central tendency, leniency) are mitigated
  by the prompt design, not eliminated. Measure them with `evaluate`.
- The tool only sees text. Figures, tables-as-images and formatting are lost.
- `.pdf` isn't read directly. Convert it first (see `skills/prepare-essays`).
- Reproducibility is exact only on the same weights, Ollama build and hardware.

## License

GPL-3.0. See [LICENSE.md](LICENSE.md).

Third-party example essays in `assets/example-papers/` are copyrighted by
their owners. They are gitignored and kept only as local test inputs. See
[`assets/example-papers/README.md`](assets/example-papers/README.md) for
sources and how to re-create them.
