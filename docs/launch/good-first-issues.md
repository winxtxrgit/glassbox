# Good first issues — ready to file

Each block below is a drafted GitHub issue. Create the labels first
(`good first issue`, `enhancement`, `help wanted`, `docs`), then paste each
block as a new issue. They're ordered easy → harder. You can file them with:

```bash
gh issue create --title "<title>" --label "good first issue,enhancement" --body "<body>"
```

---

## 1. Add a synonym-swap consistency perturbation

**Labels:** `good first issue`, `enhancement`

The consistency lens edits a document in meaning-preserving ways and checks the
verdict doesn't flip. We have lowercase, whitespace, boilerplate, punctuation,
and two Thai edits. Add one that swaps a small set of safe synonyms
(e.g. "urgent" ↔ "time-sensitive") — a verdict that flips on wording, not
meaning, is fragile.

Add the function + a `Perturbation` entry in `src/glassbox/consistency.py`
(follow `_normalize_punctuation`) and a test in `tests/test_consistency.py`.
Keep the mapping tiny and genuinely meaning-preserving, and make it a no-op when
no term is present.

---

## 2. Add a labelled corpus in another language

**Labels:** `good first issue`, `help wanted`

We ship `examples/phishing.jsonl` (English) and `examples/thai.jsonl`. Add a
third language as a JSONL of `{"id","document","label","category"}` rows,
balanced between phishing/benign. No GPU or model needed — this is just data.
Bonus: add the matching numeral/particle perturbation for that language if it
has one (see the Thai edits in `consistency.py` as a template).

---

## 3. Text reliability diagram in `calibrate` output

**Labels:** `good first issue`, `enhancement`

`glassbox calibrate` prints ECE/Brier and a per-bin table. Add a small ASCII
reliability diagram (confidence bin vs. observed accuracy) under the table so
over/under-confidence is visible at a glance. Keep it ASCII-only (the CLI runs on
consoles that choke on fancy glyphs). Render logic in `cli.py`, with a unit test
on the string it produces.

---

## 4. Export benchmark/calibration results to CSV

**Labels:** `good first issue`, `enhancement`

`benchmarks/run_benchmark.py` prints a Markdown table. Add a `--csv PATH` option
that also writes the rows as CSV, so results are easy to chart or diff across
runs. Pure stdlib (`csv` module). Add a test that the file is written with the
expected header.

---

## 5. Allow a custom base URL for the OpenAI-compatible backend

**Labels:** `enhancement`, `help wanted`

`OpenAICompatBackend` targets OpenRouter/Groq. Let users point it at a local
OpenAI-compatible server (llama.cpp `server`, LM Studio, vLLM) by accepting a
`--base-url` on the CLI and threading it through `_make_backend` in `cli.py`.
Document it in the README backends section. Keep API-key handling env-only.

---

## 6. `--runs N` for the benchmark to measure non-determinism

**Labels:** `enhancement`, `help wanted`

FINDINGS.md notes the 7B gives different verdicts run-to-run at temperature 0.
Add `--runs N` to `benchmarks/run_benchmark.py` so each model is evaluated N
times; report the mean and the spread (min–max or stdev) for each metric. This
turns "the 7B is noisy" from an anecdote into a number. Include a short note in
RESULTS.md on how to read the spread.

---

## 7. Few-shot "quote verbatim" prompt variant + measure the delta

**Labels:** `enhancement`, `help wanted`

The 7B's grounding collapses because it paraphrases instead of quoting (see
FINDINGS.md). Add an optional prompt variant in `prompts.py` with one or two
few-shot examples that demonstrate copying evidence character-for-character,
selectable via a CLI flag (e.g. `--prompt few-shot`). Then re-run
`glassbox batch` on both prompts for one model and report whether grounding
improves. This is a small change with a real, measurable research payoff.
