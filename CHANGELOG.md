# Changelog

All notable changes to glassbox. Format follows [Keep a Changelog](https://keepachangelog.com);
this project uses [semantic versioning](https://semver.org). Built over 2026-10 / 2026-11.

## Unreleased

### Added
- **Audit your own classification task**: `--labels A,B,...`, `--question TEXT`
  and `--default-label LABEL` on every model command (`audit`, `triage`, `probe`,
  `stress`, `calibrate`, `batch`, `serve`), e.g. `--labels spam,ham --question
  "Is this spam?" --default-label ham`. Validated up front (two or more distinct
  labels, a question to go with them, a default label among them, no few-shot
  examples for labels they don't use); `calibrate` refuses a dataset whose labels
  are outside the task. `Policy.default_label` and `prompts.make_policy()` for the
  Python API; `/healthz` reports the served task's labels.
- **`batch --checkpoint PATH`**: each finished document is appended to a JSON Lines
  file, and a re-run skips the documents already in it, so a multi-hour run on a
  Raspberry Pi survives a reboot. The file records the model and policy and is
  refused for a different one; a torn last line from a killed run is ignored.
  Timed-out requests are never saved, so they are retried on resume.
- **`batch --skip-errors`**: a request that times out or gets no reply is counted
  as an error and the run moves on, instead of discarding every document already
  audited. Three in a row still stop the run (the server is down, not slow).
  `batch` now shows per-document progress on stderr and splits errors into
  "unparseable" and "timed out / no reply" (also in `--json`: `parse_errors`,
  `backend_errors`).
- `benchmarks/run_benchmark.py` skips dead requests the same way, prints progress,
  adds an **Errors** column (and CSV field), and takes `--checkpoint-dir DIR` to
  resume a killed run.
- **Read the document from stdin** when the file is `-`, on `audit`, `triage`,
  `probe` and `stress` (`pbpaste | glassbox audit -`). Stdin is decoded as UTF-8,
  like a file. ([#15](https://github.com/winxtxrgit/glassbox/issues/15))
- **`--min-trustworthy RATE`** on `batch`: the pass rate (0–1, default `1.0`)
  the corpus needs for exit code 0, so CI can fail only when, say, fewer than 90%
  of documents are trustworthy. ([#20](https://github.com/winxtxrgit/glassbox/pull/20), thanks @bfs2021)
- **`--base-url`** and an **`openai-compatible`** backend for local servers
  (llama.cpp, LM Studio, vLLM). An optional key comes from `OPENAI_COMPAT_API_KEY`,
  and no empty `Authorization` header is sent without one. `--base-url` also
  overrides the OpenRouter/Groq endpoint. ([#13](https://github.com/winxtxrgit/glassbox/pull/13), thanks @yinbowen-dc)
- **ASCII reliability diagram** under the `calibrate` table: mean confidence (`C`)
  and observed accuracy (`A`) per bin on a shared 0–100% scale. ([#12](https://github.com/winxtxrgit/glassbox/pull/12),
  thanks @yinbowen-dc)
- **Synonym consistency edit**: swaps whole-word `urgent` ↔ `time-sensitive`,
  keeping capitalization, and reuses the original verdict when neither word
  appears. ([#11](https://github.com/winxtxrgit/glassbox/pull/11), thanks @yinbowen-dc)
- **Grounding tolerates typographic punctuation**: a quote that differs from the
  document only by curly quotes or en/em dashes (and line wrapping) now resolves
  to the original offsets. ([#19](https://github.com/winxtxrgit/glassbox/pull/19), thanks @jeelthacker1)
- `examples/chinese.jsonl`: six labelled Simplified Chinese messages (three
  suspicious, three benign), with a grounding test on Chinese text.
  ([#14](https://github.com/winxtxrgit/glassbox/pull/14), thanks @yinbowen-dc)
- **`--csv PATH`** on the benchmark runner (`benchmarks/run_benchmark.py`): also
  writes the main table's mean values as CSV, so results are easy to chart or diff
  across runs. Pure stdlib. ([#8](https://github.com/winxtxrgit/glassbox/pull/8),
  thanks @teotiabhumik-ship-it)
- **`glassbox serve`**: audit and triage over HTTP, standard library only. A
  phone-friendly page at `/` and a job API (`POST /api/jobs`, poll
  `GET /api/jobs/<id>`; results are the `--json` payloads) with one worker
  thread, because an audit on slow hardware takes minutes. Binds to localhost
  unless `--bind` says otherwise; `GLASSBOX_TOKEN` enables bearer auth (constant-time
  compare), and it warns when exposed without one. Bodies are capped at 64 KB,
  the queue is bounded (`--max-queue`, 429 when full), finished jobs are
  evicted and submitted text is dropped once a job ends. The page renders the
  (untrusted) quoted evidence with `textContent` only, under a nonce CSP.
  `serve --demo` runs it on the toy model.
- **`deploy/raspberry-pi/`**: idempotent `setup-pi.sh` that turns a Pi 5 into a
  LAN glassbox server: Ollama (LAN-only, one request at a time), qwen2.5
  1.5b/3b, ufw (SSH allowed before enabling), a generated token and a hardened
  `glassbox.service`, then runs a smoke test.
- **`--timeout SECONDS`** on every analysis verb (and the benchmark runner): how
  long to wait for each model reply, default 120. Slow hardware — CPU-only
  machines, a Raspberry Pi, a cold model load — can need several minutes.
- **`GLASSBOX_HOST`** environment variable sets the default Ollama URL (for the
  CLI and the benchmark runner), so a model on another machine on your LAN
  doesn't need `--host` on every command. An explicit `--host` still wins.

### Fixed
- A backend timeout is now reported as a timeout ("no reply from … within Ns …
  retry with a larger --timeout") instead of "unreadable reply", and an
  unreachable server is still told apart from a slow one.
- **Reasoning models no longer think before every verdict.** The Ollama backend
  sends `think: false`, so qwen3-style models answer the JSON directly instead of
  spending most of the latency on hidden reasoning (on a Pi, 30-50 s per request).
  If the server rejects the flag it is dropped for the rest of the run. A
  `<think>...</think>` block in any backend's reply is stripped before parsing.
- **An HTTP error from Ollama is no longer reported as "cannot reach Ollama".**
  A missing model (HTTP 404) now says so, with Ollama's own message and an
  `ollama pull` hint, instead of sending you to check a server that is up.
- **`setup-pi.sh` no longer firewalls off what already runs on the Pi.** It keeps
  any existing listener on tcp 53/80/443 and udp 53/67/123 open to the LAN (a
  Pi-hole's DNS would otherwise die for the whole house), and allows the LAN over
  IPv6 too (link-local and the on-link /64), since `ssh pi.local` often resolves
  to IPv6 and would be locked out once ufw is enabled.

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
- The consistency lens no longer spends a backend call on a perturbation that
  leaves the document unchanged — it reuses the original verdict. Unchanged
  variants were already excluded from the score, so this is a pure saving; it
  matters because the Thai perturbations are no-ops on every non-Thai document
  (two wasted model calls per English `stress`/`audit`/`batch` before this).
- The benchmark runner shows N as a range when a model errors on a different
  number of documents across `--runs`, so the displayed N and the averaged rates
  no longer silently describe different document sets.

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
