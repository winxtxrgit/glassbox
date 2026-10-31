# glassbox

[![CI](https://github.com/winxtxrgit/glassbox/actions/workflows/ci.yml/badge.svg)](https://github.com/winxtxrgit/glassbox/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-0e75b6)
![No dependencies](https://img.shields.io/badge/dependencies-none-2ea44f)
![Runs locally](https://img.shields.io/badge/LLM-local%20%2F%20free-8957e5)
[![MIT License](https://img.shields.io/badge/license-MIT-0e75b6)](https://github.com/winxtxrgit/glassbox/blob/main/LICENSE)

**Explainable LLM triage that cites its evidence — and catches the model inventing it.**

glassbox runs a document past an open-weights model and returns a verdict whose every claim is pinned to a verbatim span of the source. A rationale that quotes text the document never contained is not an explanation — it's a tell. glassbox surfaces those tells.

> **Rules decide. The model explains. The explanation gets checked.**

## Why

LLMs are happy to hand you a confident paragraph about *why* they decided something. That paragraph is usually plausible and occasionally fabricated — it cites a sentence that isn't there, or quietly reasons from something the document never said. If you can't tell the two apart, the explanation is decoration.

glassbox makes the explanation **auditable**: the model must quote its evidence word-for-word, and glassbox locates each quote back in the source. Evidence that doesn't exist is flagged, and the verdict is marked **ungrounded**. That check — does the cited span actually exist? — is the cheapest faithfulness test there is, and the foundation this project builds on.

## Try it in 10 seconds (no model, no API key, no cost)

```bash
pip install -e .
glassbox demo
```

```
label:      suspicious
confidence: 0.93
rationale:  Phishing: false urgency plus a credential-harvesting link on a look-alike domain.

evidence:
  [ok  ] 'Verify your password within 24 hours'  (chars 121-157)
  [MISS] 'click here to claim your reward'  (not in source)

grounding:  UNFAITHFUL - 1 cited span(s) are not in the document.
```

The model cited one real span and one it made up. glassbox caught the fake.

## One command: `audit`

Each lens below answers one question. `audit` runs the three single-document lenses at once — grounding, faithfulness, consistency — and tells you whether a verdict clears all three. A verdict is only as trustworthy as its weakest check.

```bash
glassbox audit --demo        # no model needed
```

```
verdict:    suspicious (confidence 0.90)

grounded:   PASS - every cited span exists in the document
faithful:   PASS - erasing the cited evidence flips the verdict
robust:     FAIL - a meaning-preserving edit flipped the decision (lowercase)

trust:      2/3 checks passed - treat this verdict with caution.
```

This verdict cited real evidence and genuinely depended on it — but lower-casing the document flipped the decision, so it was keying on surface form. `audit` catches that in one shot. Run it for real with `glassbox audit email.txt`; exit code is `0` only when all three pass.

## Run it for real (free, local)

glassbox talks to [Ollama](https://ollama.com) — open models on your own machine, no API bill:

```bash
ollama pull qwen2.5:3b        # ~2 GB, comfortable on a 4 GB GPU
glassbox triage suspicious_email.txt
```

Bigger and sharper if your hardware allows: `qwen2.5:7b`, `qwen3:4b`. Point at any model and host:

```bash
glassbox triage report.txt --model qwen3:4b --host http://localhost:11434
```

Exit code is `0` when the verdict is grounded, `1` when it isn't — so you can gate a pipeline on *faithfulness*, not just on the label.

### No GPU? Use a free API tier

Every command also runs against an OpenAI-compatible free tier — no local model needed. Set the provider's key in your environment and pass `--backend`:

```bash
export OPENROUTER_API_KEY=...        # free key from openrouter.ai
glassbox audit email.txt --backend openrouter        # defaults to a free Qwen
glassbox audit email.txt --backend groq --model llama-3.3-70b-versatile
```

### JSON output and whole corpora

Add `--json` to any analysis command for machine-readable output you can gate a CI job on:

```bash
glassbox audit email.txt --json | jq '.trustworthy'
```

And `batch` audits a whole corpus (JSON Lines of `{"document": ...}`), reporting how often the *model's* verdicts hold up — a property of the model, not one document:

```bash
glassbox batch inbox.jsonl --backend openrouter
```
```
batch audit over 120 document(s):

  grounded:     118/120  (98%)
  faithful:     104/120  (87%)
  robust:       89/120   (74%)
  trustworthy:  81/120   (68%)
```

## Does the verdict actually depend on its evidence?

Citing real text isn't the same as reasoning from it. `glassbox probe` runs the **counterfactual** test: it erases each cited span, re-decides, and checks whether the verdict actually moves. Evidence whose removal flips the label is load-bearing; evidence the verdict survives without is decoration.

```bash
glassbox probe --demo          # a toy model, no install needed
```

```
verdict:    suspicious (confidence 0.90)

evidence faithfulness (erase each cited span, then re-decide):
  [load-bearing] 'Verify your password'
               -> removing it flips the verdict to 'benign'
  [decorative  ] 'mailbox is over quota'
               -> verdict holds at 'suspicious' without it

faithful:   YES - erasing all cited evidence flips the verdict to 'benign'.
```

Run it for real with `glassbox probe email.txt`. A model that keeps its verdict no matter which cited span you erase is rationalizing, not explaining — and `probe` says so (`faithful: NO`, exit code `1`). This is the erasure idea from rationale-faithfulness research (ERASER's *comprehensiveness*), applied to a black-box model through its own output.

## Does the verdict track meaning, or surface form?

A model that understands the content shouldn't change its mind when you reword the document without changing what it means. `glassbox stress` re-decides after **meaning-preserving edits** — lower-casing, whitespace reflow, a forwarding header, ASCII-normalized punctuation — and flags any edit that flips the label.

```bash
glassbox stress --demo
```

```
verdict:    suspicious (confidence 0.90)

consistency (re-decide after meaning-preserving edits):
  [FLIP  ] lowercase              -> changes to 'benign'
  [stable] pad boilerplate        -> still 'suspicious'
  (no change to this document: collapse whitespace, normalize punctuation)

robust:     NO - 1 of 2 edit(s) flipped the verdict. The decision tracks surface form, not meaning.
```

Here the toy model was reading letter case, not content — so lower-casing alone broke it. A verdict that survives every edit is robust (exit `0`); one that flips is fragile (exit `1`). No-op edits don't count toward the score.

## Do the confidence numbers mean anything?

A verdict comes with a confidence like `0.90`. Is that a probability, or decoration? `glassbox calibrate` needs a **labelled set** — documents whose true labels you know — and checks whether stated confidence matches observed accuracy. The headline is **ECE** (expected calibration error): bin predictions by confidence and compare each bin's average confidence to how often it was actually right.

```bash
glassbox calibrate --demo
```

```
accuracy:        0.60
mean confidence: 0.90
gap:             +0.30
ECE:             0.30   (0 = perfectly calibrated)
Brier:           0.33   (0 = perfect)

reliability (per confidence band):
  conf 0.90-1.00 | n=10  | said 0.90 | right 0.60

verdict:    OVERCONFIDENT - it claims 0.90 but is right 0.60 of the time.
```

The toy model stamps `0.90` on everything — a confidence that never varies carries no information, and calibration proves it. Run it on real data with a JSON-Lines file of `{"document", "label"}`:

```bash
glassbox calibrate examples/phishing.jsonl --model qwen2.5:3b
```

Exit code is `0` when the confidence is well calibrated, `1` when it's materially over- or under-confident.

## Inside the model (white-box)

Phases 1-4 only ever saw the model's output. Because the weights are local, the last phase opens a small model up. `glassbox lens` shows two classic views — install the optional extra first (`pip install -e ".[whitebox]"`, torch + transformers):

```bash
glassbox lens "The Eiffel Tower is in the city of" --model gpt2
```

```
predicted:  ' Paris'  (p=0.070)

logit lens (top token as the prediction forms, by layer):
  L01  ' the'         0.690
  ...
  L09  ' Rome'        0.192
  L10  ' London'      0.285
  L11  ' Paris'       0.183
  L12  ' Paris'       0.070

occlusion attribution (mask each token -> drop in P(predicted)):
  +0.070  ' of' (token 9)
  +0.065  ' the' (token 7)
  ...
```

The **logit lens** decodes the residual stream at each layer through the model's own output head: you watch the answer form — generic `the`, then a cluster of cities (`Rome`, `London`), settling on `Paris`. **Occlusion attribution** masks each input token and measures the drop in the predicted token's probability — the mechanistic echo of `probe`'s erasure test, but read straight off the logits instead of inferred from the output. The black box and the glass box, measuring the same thing two ways.

## How it works

```
document ──▶ model (verbatim-quote prompt) ──▶ {label, confidence, rationale, evidence[]}
                                                        │
                                   grounding ◀──────────┘   locate each quote in the source
                                                        │
                                                   Verdict{..., citations[verified?]}
```

- **`prompts.py`** pins the model to a JSON shape and demands verbatim evidence.
- **`grounding.py`** finds each quote's real character offsets (exact, then whitespace-flexible), or marks it unverified.
- **`schema.py`** — `Verdict.grounded` is true only when every cited span exists.
- **`backends.py`** — swap `OllamaBackend` for `StubBackend` (tests/demos) or your own.

Zero third-party dependencies — the Ollama backend speaks plain HTTP from the standard library.

## Roadmap

Five lenses on **interpretability of (mostly black-box) LLMs** — all five built:

- **✅ Grounding** — does the cited evidence exist? (`glassbox triage`)
- **✅ Counterfactual faithfulness** — erase the cited span; does the verdict actually flip? If not, the citation wasn't load-bearing. (`glassbox probe`)
- **✅ Consistency** — reword the document meaning-for-meaning; does the decision hold? (`glassbox stress`)
- **✅ Calibration** — do the confidence numbers mean anything across a labelled set? (`glassbox calibrate`)
- **✅ White-box** — the weights are local, so open the model up: logit-lens and occlusion views of *where* the signal is read, connected back to the black-box findings. (`glassbox lens`)

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
```

The whole suite runs against a stub backend — no model required for tests.

## License

[MIT](https://github.com/winxtxrgit/glassbox/blob/main/LICENSE) © Thanut Jongteerathanachote
