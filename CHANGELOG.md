# Changelog

All notable changes to glassbox. Format follows [Keep a Changelog](https://keepachangelog.com);
this project uses [semantic versioning](https://semver.org). Built over 2026-10 / 2026-11.

## Unreleased

### Added
- **CI** (`.github/workflows/ci.yml`): ruff + pytest across Python 3.10–3.13 on
  Ubuntu, plus Windows and macOS on 3.13, with an end-to-end CLI smoke test.
- **`FINDINGS.md`**: results from running the lenses against real local models
  (Qwen 2.5 3B and 7B via Ollama) on the sample phishing set, with a 3B-vs-7B
  comparison.

### Security
- `lens` now loads models with `use_safetensors=True` (and leaves
  `trust_remote_code=False`), so a model name cannot execute code via pickle
  weights. Added a "Security & trust boundary" section to the README.

## 0.7.0 — Pipelines & scale

### Added
- `--json` on every analysis command, for machine-readable output you can gate a
  CI job on (`serialize.py`).
- `OpenAICompatBackend` and `--backend {ollama,openrouter,groq}`: run against a
  free API tier when there's no GPU. API keys are read from the environment.
- **`batch`**: audit a whole corpus and report grounded / faithful / robust /
  trustworthy *rates* — a property of the model, not one document.

### Changed
- CLI refactored around shared `_make_backend` / `_emit` / `_resolve` helpers.

## 0.6.0 — One-command audit

### Added
- **`audit`**: runs grounding + faithfulness + consistency on a single verdict and
  reports whether it clears all three. A verdict is only as trustworthy as its
  weakest check.

## 0.5.0 — White-box lenses

### Added
- **`lens`** (optional `whitebox` extra): logit lens (watch the prediction form
  layer by layer) and occlusion attribution (mask each input token, read the drop
  in the predicted token's probability) over a small local Hugging Face model —
  the inside-the-model echo of `probe`'s erasure test.

## 0.4.0 — Calibration

### Added
- **`calibrate`**: over a labelled set, score the model's stated confidence against
  how often it is actually right — ECE, Brier score, a reliability table, and an
  over/under-confidence verdict. JSON-Lines dataset loader; sample
  `examples/phishing.jsonl`.

## 0.3.0 — Consistency

### Added
- **`stress`**: re-decide after meaning-preserving edits (lower-casing, whitespace
  reflow, a forwarding wrapper, ASCII punctuation). A verdict that flips when
  nothing of substance changed is keying on surface form, not meaning. No-op edits
  are excluded from the score.

## 0.2.0 — Faithfulness

### Added
- **`probe`**: counterfactual faithfulness — erase each grounded citation and
  re-decide. A span whose removal flips the label was load-bearing; one the verdict
  survives without is decoration (ERASER-style comprehensiveness, through the
  model's output). `CallableBackend` for document-dependent test models.

## 0.1.0 — Grounding

### Added
- **`triage`**: classify a document into `{label, confidence, rationale,
  evidence}` with an open-weights model, then locate each cited span back in the
  source. Evidence that isn't there is flagged and the verdict marked ungrounded.
- `OllamaBackend` (local, zero-dependency HTTP) and `StubBackend`; `glassbox demo`
  runs the whole pipeline with no model installed.
