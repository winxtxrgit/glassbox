# glassbox findings

A place to record what the lenses say about a *real* model. The sample corpus
(`examples/phishing.jsonl`, 10 labelled emails) is enough to see every lens in
action. Fill this in after running the commands below.

## How to reproduce

**Local (free, needs a GPU-ish machine):**

```bash
pip install -e .
# install Ollama from https://ollama.com, then:
ollama pull qwen2.5:3b
glassbox batch     examples/phishing.jsonl
glassbox calibrate examples/phishing.jsonl
glassbox audit     examples/phishing.jsonl --json | head   # per-doc detail
```

**No GPU (free API tier):**

```bash
export OPENROUTER_API_KEY=...     # free key from openrouter.ai
glassbox batch     examples/phishing.jsonl --backend openrouter
glassbox calibrate examples/phishing.jsonl --backend openrouter
```

- **Model:** _e.g. `qwen2.5:3b` (Ollama) / `qwen/qwen-2.5-72b-instruct:free` (OpenRouter)_
- **Date:** _fill in_

## Corpus audit — `glassbox batch`

```
<paste the batch output here>
```

## Calibration — `glassbox calibrate`

```
<paste the calibrate output here>
```

## Takeaways

- **Grounded rate:** _does the model quote real spans, or invent evidence?_
- **Faithful rate:** _do the cited spans actually drive the decision?_
- **Robust rate:** _does it survive lower-casing / reflow, or key on surface form?_
- **Calibration (ECE):** _is the confidence number meaningful?_
- **Verdict:** _is this model trustworthy enough for the task, and where does it break?_
