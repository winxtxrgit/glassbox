# glassbox findings — Qwen 2.5, 3B vs 7B

Does a **bigger** model explain itself better — are its citations more grounded,
its reasoning more load-bearing? I ran the same lenses over both sizes locally to
find out. Short answer: **no. The 7B is better *calibrated* but noticeably less
*auditable*.**

- **Models:** `qwen2.5:3b` and `qwen2.5:7b` via [Ollama](https://ollama.com), laptop GTX 1650 Ti (4 GB).
- **Dataset:** [`examples/phishing.jsonl`](examples/phishing.jsonl) — 10 labelled emails (5 phishing / 5 benign).
- **Date:** 2026-11. Small set, single run each — read these as directional, not conclusive.

## Reproduce

```bash
ollama pull qwen2.5:3b && ollama pull qwen2.5:7b
for m in qwen2.5:3b qwen2.5:7b; do
  glassbox batch     examples/phishing.jsonl --model "$m"
  glassbox calibrate examples/phishing.jsonl --model "$m"
done
```

## Head to head

| Metric | 3B | 7B | |
|---|---:|---:|---|
| **Accuracy** | **1.00** | 0.90 | 3B got all 10; 7B missed one phishing email. |
| **Grounded** (cites real spans) | **1.00** | **0.40** | The big one often fails to quote verbatim. |
| **Faithful** (decision depends on the cited spans) | 0.30 | **0.10** | Already scarce at 3B; scarcer at 7B. |
| **Robust** (survives meaning-preserving edits) | 1.00 | 1.00 | Both solid. |
| **Trustworthy** (all three) | 0.30 | 0.10 | Grounding + faithfulness drag it down. |
| Mean confidence | 0.88 | 0.95 | 7B is bolder. |
| ECE | 0.12 | 0.12 | Same error… |
| Calibration verdict | **under**confident | **well calibrated** | …but 7B's confidence tracks accuracy. |

## What actually happened

**The 7B stopped quoting verbatim.** glassbox's prompt asks for evidence copied
character-for-character so each quote can be located in the source. The 3B obeyed
every time (grounded 1.00). The 7B paraphrased instead — so 6 of its 10 citations
couldn't be found in the document, and grounding collapsed to 0.40. A more fluent
model wrote nicer-sounding "quotes" that were no longer quotes.

**The 7B was also non-deterministic.** On one phishing email the batch run labelled
it with ungrounded evidence; re-running `triage` on the *same* email flipped it to
`benign` (confidence 0.95) with verbatim evidence. Same input, temperature 0,
different verdict *and* different citations. That instability is itself a reason
its grounding/faithfulness rates are noisier than the 3B's — and a problem for any
after-the-fact audit, which assumes the explanation you're auditing is the one the
model would stand behind.

## Takeaways

- **"Bigger" bought calibration, not auditability.** The 7B's confidence became
  meaningful (well calibrated vs. the 3B's underconfidence), but its *explanations*
  got worse: less grounded, less faithful, less stable.
- **Grounding and faithfulness don't scale for free.** They're separate axes from
  accuracy and confidence — a model can improve on one while regressing on the
  others, which is exactly why you measure them rather than assume them.
- **Instruction-following regressed on the "quote verbatim" contract.** The larger
  model preferred fluent paraphrase over literal copying — worth a prompt tweak
  (few-shot verbatim examples) before trusting its citations.
- **Caveat:** 10 emails, one run per model, and the 7B visibly varies run to run.
  Treat the direction as real and the exact rates as noisy; a bigger corpus and
  several runs per model would firm this up.
