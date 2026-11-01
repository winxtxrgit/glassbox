# Contributing to glassbox

Thanks for your interest. glassbox is a small, dependency-light tool with a clear
shape, so it's an easy codebase to land a first change in. This guide gets you
from clone to green tests and points at good first contributions.

## What glassbox is (the one-paragraph model)

glassbox asks a local LLM to triage a document and **quote its evidence
verbatim**, then checks whether that explanation holds up: are the quotes real
(grounding), do they actually drive the decision (faithfulness), and does the
verdict survive meaning-preserving edits (consistency)? Each check is a "lens".
The core is pure standard library; everything heavier is an optional extra.

## Dev setup

```bash
git clone https://github.com/winxtxrgit/glassbox
cd glassbox
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

The core needs no third-party packages. The `[dev]` extra adds pytest, coverage
and ruff; the `[whitebox]` extra (torch + transformers) is only needed to work on
`glassbox lens`.

## Before you open a PR

```bash
ruff check .
ruff format --check .
pytest -q
```

All three must pass — CI runs exactly these on Python 3.10–3.13. Please add or
update tests for any behaviour change; most lenses are tested against a
`CallableBackend` toy model (see `tests/`), so you do **not** need a GPU or a
running Ollama to test logic.

## The layout

| File | Responsibility |
|---|---|
| `schema.py` | `Verdict`, `Policy`, `Citation` — the data every lens speaks. |
| `prompts.py` | The system prompt and `DEFAULT_POLICY`. |
| `backends.py` | `OllamaBackend`, `OpenAICompatBackend`, `StubBackend`, `CallableBackend`. |
| `triage.py` | One call: document → `Verdict` with grounded citations. |
| `grounding.py` | Locate each cited quote back in the source (lens 1). |
| `faithfulness.py` | Erase each cited span, re-decide — is it load-bearing? (lens 2). |
| `consistency.py` | Re-decide under meaning-preserving edits (lens 3). |
| `calibration.py` | ECE / Brier / reliability over a labelled set (lens 4). |
| `whitebox/lenses.py` | Logit lens + occlusion attribution (lens 5, optional extra). |
| `audit.py` | Runs lenses 1–3 on one verdict → `trustworthy`. |
| `batch.py` | Audit a whole corpus → rates. |
| `serialize.py` | `--json` output for every verb. |
| `cli.py` | Argument parsing and rendering for each command. |

## Common first changes

**Add a consistency perturbation.** A perturbation is a named,
*meaning-preserving* edit to a document. Add a function and a `Perturbation`
entry to `DEFAULT_PERTURBATIONS` in `consistency.py`, then a test. The Thai
`_thai_numerals` / `_drop_thai_particles` edits are worked examples — follow
them, and make sure your edit is a no-op on text it doesn't apply to so it
doesn't skew scores for other languages.

**Add a backend.** Implement the `Backend` protocol (a `generate(system, user)
-> str`) in `backends.py` and wire it into `_make_backend` in `cli.py`. Keep API
keys read from the environment only — never log them, never accept them as a
flag.

**Keep the core zero-dependency.** Anything that needs a third-party package
belongs behind an optional extra with a clear install hint when it's missing
(see how `lens` handles a missing `whitebox` extra).

## Conventions

- ASCII-only in **CLI runtime output** glyphs (status markers etc.) — the tool
  runs on consoles that choke on fancy symbols. Document *content* (e.g. Thai
  evidence quotes) is fine; `cli.py` forces UTF-8 output for exactly that.
- Line length and style are whatever `ruff format` produces. Don't hand-format.
- Commits: a short imperative subject, a body explaining *why* when it isn't
  obvious.

## Good first issues

See the issues tagged [`good first issue`](https://github.com/winxtxrgit/glassbox/labels/good%20first%20issue).
If something's unclear, open an issue and ask — questions are welcome.
