# ReAct HotpotQA Reproduction

This project builds on the [official ReAct repository](https://github.com/ysymyth/ReAct) and reproduces its QA prompting and Wikipedia interaction methods with a chat model. Our recorded experiments use `gpt-4.1-mini-2025-04-14`: four basic methods on 500 HotpotQA questions, plus three additional methods on the same first 50 questions.

No fine-tuning is performed. The model, API format, and some implementation settings differ from the paper. This is a reproduction of the method, not an exact reproduction of the original scores. Original notebooks remain available for reference.

## Installation

The QA scripts were tested on Windows with Python 3.11. Open PowerShell in the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-hotpotqa.txt
```

You do not need to activate the environment. On Linux or macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-hotpotqa.txt
```

Examples below use PowerShell. Replace the Python executable path on other platforms. These dependencies cover QA, not ALFWorld or WebShop.

## API Configuration

Set the key in the terminal running the experiment:

```powershell
$env:OPENAI_API_KEY = "YOUR_API_KEY"
```

This applies only to the current terminal and its child processes. Do not commit keys. Scripts read environment variables and **do not automatically load `.env` files**.

For another Chat Completions-compatible service, optionally set:

```powershell
$env:OPENAI_BASE_URL = "https://YOUR_API_ENDPOINT/v1"
```

You can also specify `--base-url` and `--model`. The service must support the script's `stop`, `temperature`, and `max_tokens` parameters. Check model-specific reasoning settings separately; the script does not automatically disable reasoning modes.

## Check the Environment

```powershell
# Offline configuration, dataset, and sample check
.\.venv\Scripts\python.exe run_qa.py --dataset hotpotqa --methods all --check

# Wikipedia connectivity check; no model calls
.\.venv\Scripts\python.exe run_hotpotqa.py --check-wiki

# Offline tests
.\.venv\Scripts\python.exe -m unittest test_hotpotqa test_qa -v
```

Offline checks do not verify model API access. Start with a small real run.

## Four Basic Methods

Each test question uses a **question-only** setup: no supporting passages or reference answer are supplied. HotpotQA uses the original six few-shot examples. Reference answers are used only for scoring.

| Argument | Behavior | Wikipedia retrieval |
|---|---|---|
| `standard` | Answer directly | No |
| `cot` | Explicit reasoning, then answer | No |
| `act` | Alternate actions and observations | Yes |
| `react` | Alternate thoughts, actions, and observations | Yes |

ReAct's Thought is visible text generated through prompting. The script executes Search and Lookup, supplies real Observations, and accepts Finish to submit the answer. The model provider's built-in search tools are not enabled.

Start with 20 questions per method:

```powershell
.\.venv\Scripts\python.exe run_qa.py --dataset hotpotqa --methods standard cot act react --samples 20 --output results/basic-20 --resume
```

Run 500 questions per method:

```powershell
.\.venv\Scripts\python.exe run_qa.py --dataset hotpotqa --methods standard cot act react --samples 500 --seed 233 --model gpt-4.1-mini-2025-04-14 --output results/basic-500 --resume
```

These four methods are the default. Use `--methods react` to run only ReAct. `run_hotpotqa.py` also provides a standalone ReAct entry point; use `run_qa.py` for comparisons.

## CoT-SC and Hybrid Methods

| Argument | Behavior |
|---|---|
| `cot_sc` | Sample 21 CoT answers at temperature 0.7 and vote on normalized answers |
| `cot_sc_to_react` | Switch to ReAct when the most common answer receives fewer than half the samples |
| `react_to_cot_sc` | Fall back to CoT-SC when ReAct exhausts its step budget without submitting an answer |

Run the three additional methods on 50 questions:

```powershell
.\.venv\Scripts\python.exe run_qa.py --dataset hotpotqa --methods cot_sc cot_sc_to_react react_to_cot_sc --samples 50 --seed 233 --model gpt-4.1-mini-2025-04-14 --output results/hybrid-50 --resume
```

Run all seven methods:

```powershell
.\.venv\Scripts\python.exe run_qa.py --methods all --samples 20 --output results/all-20 --resume
```

Self-consistency increases costs: `cot_sc` requires 1,050 model calls for 50 questions. Each method generates its own traces rather than reusing other runs. A hybrid's first stage may differ from a separate baseline, so a higher total score alone does not prove that switching helped.

With the same seed, 50 questions are the prefix of the 500-question set. Compare hybrid results with the basic methods' **same first 50 questions**, not their full 500-question averages.

## Resume and Extend

After an interruption, rerun the same command with `--resume`. Completed questions are skipped. The unfinished question is restarted and may incur additional costs. This flag also works for new output directories.

Resume validates settings, sample order, and code/data/prompt hashes. If these change, normally use a new output directory rather than bypassing validation.

Extend a 100-question run to 500:

```powershell
# Start with 100 questions
.\.venv\Scripts\python.exe run_qa.py --samples 100 --output results/basic-expand --resume

# Keep completed questions and run the remaining prefix
.\.venv\Scripts\python.exe extend_qa.py --output results/basic-expand --samples 500
```

The extension script defaults to the four basic methods. To extend a hybrid run:

```powershell
.\.venv\Scripts\python.exe extend_qa.py --output results/hybrid-50 --samples 500 --methods cot_sc cot_sc_to_react react_to_cot_sc
```

Add `--prepare-only` to update the target without model calls. Targets cannot shrink. Extension stops on configuration mismatches.

## Parameters

| Argument | Default | Meaning |
|---|---|---|
| `--dataset` | `hotpotqa` | `hotpotqa` or `fever` |
| `--model` | `gpt-4.1-mini-2025-04-14` | Model identifier |
| `--samples` | `20` | Questions per method |
| `--seed` | `233` | Dataset shuffle seed; does not guarantee deterministic API outputs |
| `--max-steps` | HotpotQA 7; FEVER 5 | Interaction limit |
| `--max-tokens` | `256` | Interaction output budget |
| `--cot-max-tokens` | `512` | CoT output budget |
| `--sc-samples` | `21` | Self-consistency sample count |
| `--output` | `results/qa-comparison` | Output root |

Basic methods use temperature 0; CoT-SC uses 0.7. These temperatures have no CLI override. See `run_qa.py --help` for all options.

FEVER uses the original three few-shot examples:

```powershell
.\.venv\Scripts\python.exe run_qa.py --dataset fever --methods standard cot act react --samples 20 --output results/fever-20 --resume
```

## Outputs and Metrics

Each method writes to `<output>/<dataset>/<method>/`:

| File | Contents |
|---|---|
| `config.json` | Model, parameters, question IDs, and file hashes |
| `episodes.jsonl` | Answers, references, scores, stage traces, calls, and timing |
| `summary.json` | Average scores over completed questions, usage, and diagnostics |
| `interruptions.jsonl` | If interrupted, returned calls from unfinished questions |

`<output>/<dataset>/comparison.json` summarizes requested methods. Unfinished questions are excluded from scores, but their calls should be included in total costs.

HotpotQA reports average exact match (EM) and token-level F1. FEVER reports classification accuracy. `results/` is ignored by Git, so raw outputs are not uploaded by default.

## Recorded Results

These are results from saved runs, not guaranteed rerun scores. Live Wikipedia and independent model generation can vary.

### Four Methods: Same 500 Questions

| Method | Correct (EM) | EM | F1 |
|---|---:|---:|---:|
| Standard | 157/500 | 31.4% | 44.83% |
| CoT | 185/500 | 37.0% | 51.88% |
| Act-only | 76/500 | 15.2% | 25.94% |
| ReAct | 182/500 | 36.4% | 50.57% |

### Seven Methods: Same First 50 Questions

Basic scores use the first 50 episodes of the runs above. Additional methods were run independently.

| Method | Correct | EM | F1 | Model calls |
|---|---:|---:|---:|---:|
| Standard | 18/50 | 36% | 48.78% | 50 |
| CoT | 21/50 | 42% | 54.30% | 50 |
| Act-only | 11/50 | 22% | 31.75% | 259 |
| ReAct | 22/50 | 44% | 52.46% | 213 |
| CoT-SC | 21/50 | 42% | 54.32% | 1,050 |
| CoT-SC → ReAct | 22/50 | 44% | 52.69% | 1,123 |
| ReAct → CoT-SC | 24/50 | 48% | 57.02% | 355 |

CoT-SC → ReAct switched on 16 questions, fixing two answers and making one previously correct answer wrong: a net gain of one. The reverse hybrid's seven fallbacks rescued no answers; its new ReAct stage already answered 24 correctly. Its 48% cannot be attributed to fallback gains. One seed and 50 questions do not establish a stable hybrid advantage.

The 500-question run included logged Unicode and memory-cleanup fixes with configuration backups. The added disambiguation limit was not triggered in the final ReAct traces. This is a documented engineering adaptation, not a strictly frozen original-model replication.

## Script Overview

| File | Role |
|---|---|
| `run_qa.py` | Seven-method runner, prompts, evaluation, summaries |
| `run_hotpotqa.py` | Model adapter, action parsing, interaction loop, standalone ReAct |
| `wikienv.py` | Wikipedia search and lookup |
| `wrappers.py` | Dataset loading, task state, scoring |
| `extend_qa.py` | Validate configuration, expand targets, resume |
| `test_hotpotqa.py` / `test_qa.py` | Offline tests |

## Troubleshooting

- **Missing key:** set `OPENAI_API_KEY` in the running terminal. The scripts do not load `.env`.
- **Wikipedia 403 or timeouts:** run `--check-wiki`. Infrastructure failures are not model answer errors.
- **Gym warning:** a maintenance warning alone does not mean the run failed. Dependencies pin NumPy to 1.26.4.
- **Resume mismatch:** check parameters and versions; do not mix experiment conditions.
- **Strict EM:** use F1 and trace inspection as additional evidence. Do not change reference answers to improve scores.
- **Migration script:** `migrate_wiki_unicode_fix.py` supports specific documented historical migrations only. New experiments do not need it.

Additional Chinese instructions: [REPRODUCE.zh-CN.md](REPRODUCE.zh-CN.md).

## Source and Scope

Original paper: [ReAct: Synergizing Reasoning and Acting in Language Models](https://arxiv.org/abs/2210.03629), ICLR 2023. Preserve the original license and attribution.

Original `hotpotqa.ipynb`, `FEVER.ipynb`, `alfworld.ipynb`, and `WebShop.ipynb` are historical implementations. Added scripts focus on QA prompting, not the original PaLM model, full ALFWorld/WebShop experiments, or fine-tuning.

## Citation

```bibtex
@inproceedings{yao2023react,
  title = {{ReAct}: Synergizing Reasoning and Acting in Language Models},
  author = {Yao, Shunyu and Zhao, Jeffrey and Yu, Dian and Du, Nan and Shafran, Izhak and Narasimhan, Karthik and Cao, Yuan},
  booktitle = {International Conference on Learning Representations (ICLR)},
  year = {2023},
  html = {https://arxiv.org/abs/2210.03629},
}
```
