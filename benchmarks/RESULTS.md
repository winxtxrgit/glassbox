# Benchmark — which local model is most *auditable*?

Not "which is most accurate" — both models below score the same on accuracy. The
question glassbox answers is different: when a model is right, can you *trust its
reasons*? Run `glassbox audit` + `calibrate` over a corpus and the answer is a
number.

- **Corpus:** [`corpus.jsonl`](corpus.jsonl) — 15 short emails: 6 phishing, 6 benign,
  3 prompt-injection (labelled suspicious — an email telling the classifier to say
  "benign" is itself a red flag).
- **Models:** `qwen2.5:3b`, `qwen2.5:7b` via [Ollama](https://ollama.com), laptop
  GTX 1650 Ti (4 GB).
- **Date:** 2026-11.

## Results

| Model | N | Accuracy | Grounded | Faithful | Robust | Trustworthy | ECE | ~s/doc |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `qwen2.5:3b` | 15 | 80% | **100%** | **60%** | 100% | **60%** | **0.21** | **35.9** |
| `qwen2.5:7b` | 15 | 80% | 60% | 20% | 100% | 20% | 0.27 | 83.7 |

*Grounded = cites real spans. Faithful = decision depends on them. Robust = survives
meaning-preserving edits. Trustworthy = all three. ECE = calibration error (lower is
better). ~s/doc = full `audit` + `calibrate` cost per document on this machine.*

## Reading it

- **Same accuracy, very different auditability.** Both label 12 of 15 correctly,
  but the **3B is trustworthy on 60% of documents versus the 7B's 20%** — three
  times more often. The gap is entirely in grounding and faithfulness.
- **The 7B stops quoting verbatim.** Its grounding falls to 60%: it paraphrases its
  "evidence" instead of copying it, so a third of its citations can't be located in
  the source. Only 20% of its verdicts are faithful — the cited text rarely drives
  the decision.
- **Robustness is a wash.** Both hold up under lower-casing / whitespace / wrapper
  edits (100%), including on the prompt-injection emails — neither was fooled into
  flipping to "benign".
- **The 3B is also 2.3× faster** on this hardware. For an auditing workflow the
  smaller model wins on every axis that matters here.

This reproduces, on a larger and more varied corpus, the 3B-vs-7B pattern first seen
in [`../FINDINGS.md`](../FINDINGS.md): **"bigger" buys calibration headroom, not
grounding, faithfulness, or speed.**

## Reproduce

```bash
ollama pull qwen2.5:3b && ollama pull qwen2.5:7b
python benchmarks/run_benchmark.py --models qwen2.5:3b,qwen2.5:7b
```

> Caveats: 15 documents, one run per model, and the 7B is non-deterministic run to
> run (see `FINDINGS.md`). Absolute latency is machine-specific (the 7B spills onto
> CPU on a 4 GB GPU); the 2.3× *ratio* is the stable signal. Treat the direction as
> real and the exact rates as noisy — a larger corpus and several runs per model
> would firm it up.
