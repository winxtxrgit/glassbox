# glassbox

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

A longer build toward **behavioural interpretability of black-box LLMs**:

- **✅ Grounding** — does the cited evidence exist? (`glassbox triage`)
- **✅ Counterfactual faithfulness** — erase the cited span; does the verdict actually flip? If not, the citation wasn't load-bearing. (`glassbox probe`) *(you are here)*
- **Consistency** — paraphrase the document; does the decision hold?
- **Calibration** — do the confidence numbers mean anything across a labelled set?
- **White-box side-quest** — because the weights are local, open the model up: attention and logit-lens views of *where* the signal is read, connected back to the black-box findings.

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
