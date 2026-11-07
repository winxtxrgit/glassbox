# Changelog

All notable changes to glassbox. Format follows [Keep a Changelog](https://keepachangelog.com);
this project uses [semantic versioning](https://semver.org). Built over 2026-10 / 2026-11.

## 0.8.0 — Benchmarks & PyPI

### Changed
- **Published on PyPI as `glassbox-audit`** (`pipx install glassbox-audit`), since
  the `glassbox` name there belongs to an unrelated project. The import package
  (`import glassbox`) and the `glassbox` command are unchanged.

### Added
- **`--prompt {plain,few-shot}`** on every analysis verb (and the benchmark
  runner). `few-shot` adds two worked examples that copy evidence
  character-for-character, aimed at a fluent model that paraphrases its citations
  instead of quoting them (which breaks grounding — see `FINDINGS.md`). The style
  is carried on `Policy.prompt_style`, so it flows through every lens unchanged;
  `plain` is the default and leaves the prompt as it was.
- **Thai / multilingual consistency**: two meaning-preserving perturbations for
  the consistency lens — Thai numerals to Arabic (`๒๔` → `24`) and dropping
  politeness particles (`ครับ`/`ค่ะ`) — so `stress`/`audit` catch a verdict that
  keys on Thai surface form. They are no-ops on non-Thai text, so they never
  count against an English document. Added `examples/thai.jsonl` (six labelled
  Thai emails) and tests that grounding locates Thai spans by codepoint offset.
- **Contributor onboarding**: a `CONTRIBUTING.md` (dev setup, the module map, and
  worked "first change" recipes), GitHub issue/PR templates under `.github/`, and
  a set of drafted launch materials + good-first-issues under `docs/launch/`.
- **CI** (`.github/workflows/ci.yml`): ruff + pytest across Python 3.10–3.13 on
  Ubuntu, plus Windows and macOS on 3.13, with coverage (87%) and an end-to-end
  CLI smoke test.
- **PyPI packaging**: a `publish.yml` Trusted-Publishing workflow, a `py.typed`
  marker (the package ships type hints), and richer metadata (classifiers,
  keywords, project URLs) — `python -m build` + `twine check --strict` pass.
- **README**: a "How glassbox compares" table and a "Used for" section.
- **Benchmark suite** (`benchmarks/`): a corpus (now 40 documents — phishing / scam
  / BEC / benign / prompt-injection, across many categories), a `run_benchmark.py`
  runner that compares models on grounding / faithfulness / robustness / calibration
  / latency, and `RESULTS.md` with a real 3B-vs-7B run.
- **Benchmark results** (`benchmarks/RESULTS.md`): a full 40-document, 4-cell run
  (3B/7B × plain/few-shot). Headline finding — `--prompt few-shot` lifts the 7B's
  grounding 48% → 80% and sharpens accuracy/calibration, but leaves faithfulness
  (and so trustworthiness) flat, and it backfires on the small 3B. It buys back
  locatability, not honesty.
- **`--runs N`** on the benchmark runner: evaluate each model N times and print both
  the mean and a min-max spread table per metric, so a model's run-to-run
  non-determinism is measured rather than asserted (a stable metric collapses to a
  single value). Covered by backend-free tests for the aggregation/formatting.
- **`docs/ROADMAP-30DAY.md`**: the 30-day plan to take glassbox from project to
  published tool.
- **`FINDINGS.md`**: results from running the lenses against real local models
  (Qwen 2.5 3B and 7B via Ollama) on the sample phishing set, with a 3B-vs-7B
  comparison.

### Fixed
- CLI output is now forced to UTF-8, so non-ASCII evidence quotes (e.g. Thai
  spans) render correctly instead of as mojibake on a legacy console code page
  (cp874). Guarded so a captured stream under pytest is left untouched.

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
