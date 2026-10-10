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
  - [Model options](#model-options)
- [Comparing models](#comparing-models)
- [Full example: four local models](#full-example-four-local-models)
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
| `--think` (alias `--reasoning`) | `high` | Reasoning: `low`, `medium`, `high`, `on` or `off`, plus per-model overrides such as `gpt-oss:120b=low` (see [Model options](#model-options)) |
| `--samples` | `3` | Independent samples per essay (the median is used) |
| `--seed` | `42` | Base seed; sample *i* uses `seed + i` |
| `--temperature` | `0.6` | Sampling temperature, 0-2 (non-zero so samples are independent) |
| `--top-p` | `1.0` | Nucleus-sampling cut-off |
| `--top-k`, `--min-p`, `--repeat-penalty` | Ollama's | Further sampling controls; unset unless given |
| `--num-predict` | no limit | Most tokens per answer, reasoning included |
| `--num-ctx` | `32768` | Context window (tokens) |
| `--max-retries` | `2` | Extra attempts after an invalid answer |
| `--option KEY=VALUE` | none | Any other Ollama option (e.g. `num_gpu=99`), repeatable |
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
result's provenance. See [Model options](#model-options) for `on`/`off` and
per-model settings.

### Model options

`grade` takes these options for how the model reasons and samples:

| `--think` setting | gpt-oss and fine-tunes | Other thinking models | Models without thinking |
|---|---|---|---|
| `low`, `medium`, `high` (default) | that effort level | reasoning on | not sent |
| `on` | `medium` | reasoning on | not sent |
| `off` | `low` (gpt-oss can't switch reasoning off; a note is printed) | reasoning off | not sent |

A plain setting applies to every model. `MODEL=SETTING` applies to one exact
tag, or to a family when there's no `:`. Repeat the option or separate values
with commas:

```sh
# High reasoning for gpt-oss:20b, low for the slow 120b, reasoning off for Muse Glimmer.
python3 -m marker grade paper.md --model gpt-oss:20b,gpt-oss:120b,muse-glimmer:30b-mlx \
  --think high,gpt-oss:120b=low --think muse-glimmer=off --out results/cmp
```

Sampling and generation: `--temperature`, `--top-p`, `--top-k`, `--min-p`,
`--repeat-penalty`, `--num-predict` (token cap, reasoning included),
`--num-ctx` and `--max-retries`. `--top-k`, `--min-p`, `--repeat-penalty` and
`--num-predict` are only sent when given. Anything else Ollama accepts can be
passed with `--option KEY=VALUE`. The value is read as JSON (`99`, `0.5`,
`true`) or else kept as text. Options that have their own flag, and `seed`,
can't be set this way.

```sh
python3 -m marker grade essays/ --think medium --temperature 0.4 --top-k 40 \
  --num-predict 16384 --option num_gpu=99 --out results/tuned
```

Every option is recorded in each result's provenance and is part of its
fingerprint, so changing one re-marks the essays and makes the results a
different experiment from the defaults. Unset options are left out of the
fingerprint, so results saved before these options existed are still reused.

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

*(Illustrative numbers. For real output, see the [full example](#full-example-four-local-models).)*

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

## Full example: four local models

This is real output from marking the bundled example essay with four local
models, two samples each and high reasoning. All four are GGUF builds, which
are seed-deterministic in Ollama (see [Choosing models](#choosing-models)).

```sh
python3 -m marker grade assets/example-papers/ \
  --model mistral-small3.2,gpt-oss:20b,qwen3.8:27b,muse-glimmer:30b \
  --samples 2 \
  --think high \
  --out results/readme-example \
  --show score
```

Check first that every model loads:

```
$ python3 -m marker check --model mistral-small3.2,gpt-oss:20b,qwen3.8:27b,muse-glimmer:30b
Ollama 0.35.1 at http://127.0.0.1:11434
mistral-small3.2:latest: 5a408ab55df5  24.0B  Q4_K_M  gguf  no thinking
gpt-oss:20b: f38aa0c53da5  20.9B  MXFP4  gguf  thinking
qwen3.8:27b: aaee06c39dcf  27.3B  Q4_K_M  gguf  thinking
muse-glimmer:30b: de878ce33ad8  27.9B  Q4_K_M  gguf  thinking
```

**Progress (stderr).** Each model marks the whole batch in turn. The `think=`
value shows what each model actually received: an effort level for gpt-oss,
`True` for the other thinking models, and nothing for Mistral, which has no
reasoning mode.

```
note: mistral-small3.2:latest doesn't report the thinking capability, so --think has no effect on it
marking 1 essay(s) with mistral-small3.2:latest (5a408ab55df5, think=None), Ollama 0.35.1
critical-evaluation-of-research-papers-psychology-essay: sample 1/2
critical-evaluation-of-research-papers-psychology-essay: sample 2/2
marking 1 essay(s) with gpt-oss:20b (f38aa0c53da5, think=high), Ollama 0.35.1
critical-evaluation-of-research-papers-psychology-essay: sample 1/2
critical-evaluation-of-research-papers-psychology-essay: sample 2/2
marking 1 essay(s) with qwen3.8:27b (aaee06c39dcf, think=True), Ollama 0.35.1
critical-evaluation-of-research-papers-psychology-essay: sample 1/2
critical-evaluation-of-research-papers-psychology-essay: sample 2/2
marking 1 essay(s) with muse-glimmer:30b (de878ce33ad8, think=True), Ollama 0.35.1
critical-evaluation-of-research-papers-psychology-essay: sample 1/2
critical-evaluation-of-research-papers-psychology-essay: sample 2/2
comparison saved to results/readme-example/comparison.csv and comparison.json
results in results/readme-example/
```

**Output (stdout).** One score line per model, then the comparison:

```
critical-evaluation-of-research-papers-psychology-essay	66	Upper second (2:1)	mistral-small3.2:latest  [REVIEW]
critical-evaluation-of-research-papers-psychology-essay	52	Lower second (2:2)	gpt-oss:20b  [REVIEW]
critical-evaluation-of-research-papers-psychology-essay	49	Third	qwen3.8:27b  [REVIEW]
critical-evaluation-of-research-papers-psychology-essay	53	Lower second (2:2)	muse-glimmer:30b  [REVIEW]

Model comparison: 4 models, 1 essay(s); marks shown on 0-100

Essay: critical-evaluation-of-research-papers-psychology-essay
  criterion  mistral-small3.2:latest              gpt-oss:20b              qwen3.8:27b         muse-glimmer:30b  spread
  knowledge                       65                       59                       51                       55      15
  argument                        61                       49                       48                       52      14
  evidence                        66                       49                       47                       52      19
  structure                       72                       56                       54                       57      18
  writing                         72                       49                       48                       47      25
  OVERALL                         66                       52                       49                       53      17
  band            Upper second (2:1)       Lower second (2:2)                    Third       Lower second (2:2)
  panel mark: 52 (Lower second (2:2)), median 52%; models agree: no
  - models place the essay in different bands
  - overall marks spread by 17% (> 10%)
  - flagged for review by: mistral-small3.2:latest, gpt-oss:20b, qwen3.8:27b, muse-glimmer:30b

Pairwise agreement (a vs b; mean diff = a - b, on 0-100)
  mistral-small3.2:latest vs gpt-oss:20b: n=1  mean diff +14  MAE 14  exact band 0%  QWK n/a (needs 2+ essays)
  mistral-small3.2:latest vs qwen3.8:27b: n=1  mean diff +17  MAE 17  exact band 0%  QWK n/a (needs 2+ essays)
  mistral-small3.2:latest vs muse-glimmer:30b: n=1  mean diff +13  MAE 13  exact band 0%  QWK n/a (needs 2+ essays)
  gpt-oss:20b vs qwen3.8:27b: n=1  mean diff +3  MAE 3  exact band 0%  QWK n/a (needs 2+ essays)
  gpt-oss:20b vs muse-glimmer:30b: n=1  mean diff -1  MAE 1  exact band 100%  QWK n/a (needs 2+ essays)
  qwen3.8:27b vs muse-glimmer:30b: n=1  mean diff -4  MAE 4  exact band 0%  QWK n/a (needs 2+ essays)
```

**Reading it.**

- The three reasoning models agree closely: 49, 52 and 53, within 4 points.
  Mistral Small 3.2, the only model without reasoning, is 13-17 points more
  generous on every criterion, and most of all on structure and writing.
- With four models, the panel mark is the midpoint of the middle two marks
  (0.5185 and 0.5250 give 0.52175, so 52, a 2:2). One generous model can't
  pull the panel mark up the way it would pull up a mean.
- The essay is flagged because the models put it in different bands and
  their overall marks spread by 17 points, more than `--disagreement` (10).
  Each model also flagged it for its own reasons:

  | Model | Its review flags |
  |---|---|
  | mistral-small3.2 | samples disagree on argument: marks [55%, 67%]; evidence: 1 evidence quote(s) not found in the essay |
  | gpt-oss:20b | samples disagree on knowledge: marks [65%, 52%]; argument: 2 evidence quote(s) not found in the essay; samples disagree on structure: marks [65%, 47%]; model raised integrity concerns |
  | qwen3.8:27b | borderline: 49% is within 2% below the 50% boundary; model raised integrity concerns |
  | muse-glimmer:30b | model raised integrity concerns |

  A human should moderate this essay; see
  [moderate-flagged-essays](skills/moderate-flagged-essays/SKILL.md).

**One model's explanation.** `marker show` prints the reasons from the saved
result without calling the model again. Here is gpt-oss:20b, with three of the
five criteria and the feedback lists shortened (`[...]`) to keep this section
readable; everything else is exactly as printed:

```
$ python3 -m marker show results/readme-example/gpt-oss_20b --show explanation
== critical-evaluation-of-research-papers-psychology-essay  (gpt-oss:20b)
Score: 52 on 0-100  Lower second (2:2)  (mark 52%)

[knowledge] Knowledge, understanding and relevance to the question  weight 20%
  mark 58%  band lower_second  (samples: 65%, 52%; spread 13%)
  Lower second – The essay demonstrates a generally accurate but uneven understanding; it offers
  generic definitions and limited depth, drifting from the question, matching the descriptor.
    > "The researcher is going to critique two different studies, one of them from China and the
    second study is local relevant to Oman."

[argument] Argument and critical analysis  weight 30%
  mark 49%  band third  (samples: 52%, 46%; spread 6%)
  Third – The essay presents a weak, underdeveloped argument that is mostly descriptive; it offers
  isolated critiques but lacks a sustained, coherent, independent argument, fitting the descriptor.
    > "While (Bell, 2011) failed to outline details about sampling and sample size that was drawn
    from research population as well as type of sample used in the studies."  [NOT FOUND IN ESSAY]

[...evidence, structure and writing...]

Strengths:
  - The essay covers a wide range of components relevant to research critique, such as research
  problem, questions, objectives, significance, literature review, theoretical framework,
  methodology, sampling, instruments, validity, reliability, data collection, data analysis,
  findings, and ethical considerations.
  [...]
Improvements:
  - Develop a concise, explicit thesis statement in the introduction that frames the overall
  argument of the essay.
  - Move from descriptive statements to a sustained, critical evaluation that compares the strengths
  and weaknesses of the two papers in a coherent way.
  [...]
Integrity concerns (for a human to check):
  - Inconsistent referencing style and questionable authenticity of cited works.
  [...]
Review flags:
  - samples disagree on knowledge: marks [65%, 52%]
  - argument: 2 evidence quote(s) not found in the essay
  - samples disagree on structure: marks [65%, 47%]
  - model raised integrity concerns
```

`[NOT FOUND IN ESSAY]` marks a quote the model presented as evidence but which
isn't in the essay. Here it seems to be a paraphrase, so a human should check
that criterion.

**Files written.**

```
results/readme-example/
├── comparison.csv
├── comparison.json
├── gpt-oss_20b/
│   ├── audit.jsonl
│   ├── critical-evaluation-of-research-papers-psychology-essay.json
│   └── summary.csv
├── mistral-small3.2_latest/   (same three files)
├── muse-glimmer_30b/          (same three files)
└── qwen3.8_27b/               (same three files)
```

`gpt-oss_20b/summary.csv`. The criterion columns are the exact internal
fractions, so `0.49` means 49%:

```
id,score,score_min,score_max,mark,band,needs_review,knowledge,argument,evidence,structure,writing,word_count,flags,model,model_digest
critical-evaluation-of-research-papers-psychology-essay,52,0,100,0.5185,Lower second (2:2),yes,0.585,0.49,0.485,0.56,0.49,3502,"samples disagree on knowledge: marks [65%, 52%]; argument: 2 evidence quote(s) not found in the essay; samples disagree on structure: marks [65%, 47%]; model raised integrity concerns",gpt-oss:20b,f38aa0c53da5f8c49d08c43a99df24ff53167fe68e24664a7777288e7656fdfe
```

`comparison.csv` has one row per essay. Marks are exact, so with the default
float mode they can show binary floating-point tails such as
`0.48950000000000005` (use `--numeric decimal` for exact 4-place values):

```
id,mistral-small3.2:latest mark,gpt-oss:20b mark,qwen3.8:27b mark,muse-glimmer:30b mark,mistral-small3.2:latest score,gpt-oss:20b score,qwen3.8:27b score,muse-glimmer:30b score,mistral-small3.2:latest band,gpt-oss:20b band,qwen3.8:27b band,muse-glimmer:30b band,spread,panel_median,panel_mark,panel_score,panel_band,models_agree,notes
critical-evaluation-of-research-papers-psychology-essay,0.65925,0.5185,0.48950000000000005,0.525,66,52,49,53,Upper second (2:1),Lower second (2:2),Third,Lower second (2:2),0.16974999999999996,0.5217499999999999,0.5217499999999999,52,Lower second (2:2),no,"models place the essay in different bands; overall marks spread by 17% (> 10%); flagged for review by: mistral-small3.2:latest, gpt-oss:20b, qwen3.8:27b, muse-glimmer:30b"
```

**Provenance** from `gpt-oss_20b/<essay>.json` records everything needed to
reproduce the result:

```json
"provenance": {
  "marker_version": "0.1.0",
  "prompt_version": "2.0.0",
  "analysis_version": "6",
  "prompt_sha256": "50d0799be3103316318ee5703ab3130c6f80bbe29bd4ff4fd2be32f5d62ff8b8",
  "schema_sha256": "498ab0e5492342688299b78697cb42c6a67a5a99a252d87f067fdaf0b21289c2",
  "rubric": {"name": "UK undergraduate essay (generic)", "version": "2.0.0", "sha256": "86ff69d41833d0863d45b758684cb007f8bc6ce8548128089f96e5adc23eef6c"},
  "model": "gpt-oss:20b",
  "model_digest": "f38aa0c53da5f8c49d08c43a99df24ff53167fe68e24664a7777288e7656fdfe",
  "ollama_version": "0.35.1",
  "fingerprint": "a1addedc0c5f8fbf86e722b1811e80f31a4d7c924536b5e6a94a309f7860a49d",
  "settings": {"model": "gpt-oss:20b", "samples": 2, "seed": 42, "temperature": 0.6, "top_p": 1.0, "think": "high", "num_ctx": 32768, "max_retries": 2, "disagreement_threshold": "0.1000", "boundary_margin": "0.0200", "keep_reasoning": false, "top_k": null, "min_p": null, "repeat_penalty": null, "num_predict": null, "extra_options": {}},
  ...
}
```

**Time.** The whole run took 24 minutes on an Apple Silicon Mac. Each
sample's time and generated tokens (reasoning included) are saved in the result:

| Model | Sample 1 | Sample 2 | Total |
|---|---|---|---|
| mistral-small3.2 | 100 s, 1,206 tokens | 64 s, 1,099 tokens | 2.7 min |
| gpt-oss:20b | 254 s, 11,050 tokens | 107 s, 6,322 tokens | 6.0 min |
| qwen3.8:27b | 314 s, 3,587 tokens | 283 s, 3,982 tokens | 10.0 min |
| muse-glimmer:30b | 161 s, 2,508 tokens | 153 s, 3,019 tokens | 5.2 min |

Running the same command again finishes in under a second: every input is
unchanged, so each saved result is reused and the comparison is rebuilt
(`--force` re-marks). With the same Ollama version and model digests, a
forced re-run should reproduce these marks exactly, because all four are GGUF
builds and use the fixed seeds 42 and 43. Identical GGUF output for the same
seed was verified for gpt-oss:20b and Mistral Small; MLX builds don't give it.

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
- **Display:** marks, weights, spreads and agreement rates shown in the
  terminal (explanations, flags, `rubric`, `compare`, `evaluate`) are whole
  percents with **banker's rounding** (half to even) on the exact decimal
  value: `0.135` shows as `14%`, `0.125` as `12%`. This is display only; JSON,
  CSV and the reported score keep their exact values and half-up rounding.
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
