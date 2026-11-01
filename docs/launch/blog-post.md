---
title: "Does a bigger LLM explain itself better? I built a tool to find out — it doesn't."
published: false
description: "glassbox checks whether an LLM's cited evidence is real and load-bearing. Running it on Qwen 2.5 3B vs 7B gave a counterintuitive result: the bigger model is better calibrated but less auditable."
tags: llm, machinelearning, python, opensource
canonical_url: https://github.com/winxtxrgit/glassbox
---

> Draft for dev.to / personal blog. Set `published: true` and fill `canonical_url`
> before posting. The r/LocalLLaMA, Show HN and X versions live next to this file.

When an LLM classifies a document and tells you *why* — "this email is phishing
because it says 'verify your password within 24 hours'" — do you believe the
*why*? The label might be right and the reason decorative: a fluent sentence the
model wrote *after* deciding, that has nothing to do with how it decided.

I wanted a way to check that, so I built **glassbox**: a small tool that makes a
local LLM quote its evidence verbatim, then audits whether that explanation
actually holds up. Then I pointed it at a question I was curious about — *do
bigger models explain themselves better?* — and the answer surprised me.

## Three questions about an explanation

glassbox asks the model to triage a document into `{label, confidence,
evidence[]}`, where every piece of evidence must be **quoted verbatim** from the
source. That one constraint unlocks three cheap, mechanical checks — "lenses":

1. **Grounding** — does each quote actually appear in the document? If the model
   "quotes" text that isn't there, it fabricated its evidence. glassbox locates
   every citation back in the source by offset; anything it can't find is
   flagged and the verdict is marked ungrounded.
2. **Faithfulness** — is the cited evidence *load-bearing*? glassbox erases each
   grounded quote and re-runs the triage. If the label doesn't change when you
   remove the "reason", that reason was decorative. (This is the ERASER
   *comprehensiveness* idea, applied one citation at a time.)
3. **Consistency** — does the verdict track meaning, or surface form? glassbox
   re-decides after **meaning-preserving edits**: lower-casing, whitespace
   reflow, wrapping in a forwarding header. If the label flips when nothing of
   substance changed, the model was keying on formatting.

A verdict is **trustworthy** only when it clears all three. The label being
correct is not enough — a right answer for an unauditable reason is a liability
in anything that has to be reviewed.

Everything runs **locally and free** on [Ollama](https://ollama.com) — no API
key, no data leaving the machine. The core is pure Python standard library.

```bash
pip install glassbox
glassbox audit suspicious_email.txt --model qwen2.5:3b
```

## The experiment: 3B vs 7B

Conventional wisdom says a bigger model is better across the board. I tested it
on the thing I actually care about — not accuracy, but *auditability*. Same
lenses, same emails, `qwen2.5:3b` vs `qwen2.5:7b`, on a laptop GTX 1650 Ti.

On a 15-email corpus (phishing / benign / prompt-injection):

| Model | Accuracy | Grounded | Faithful | Robust | Trustworthy | ECE | ~s/doc |
|---|---:|---:|---:|---:|---:|---:|---:|
| `qwen2.5:3b` | 80% | **100%** | **60%** | 100% | **60%** | **0.21** | **35.9** |
| `qwen2.5:7b` | 80% | 60% | 20% | 100% | 20% | 0.27 | 83.7 |

**Same accuracy. The small model is trustworthy three times as often.**

## What the bigger model actually did

**It stopped quoting verbatim.** The prompt asks for evidence copied
character-for-character so each quote can be located. The 3B obeyed every time
(grounding 1.00). The 7B *paraphrased* — it wrote nicer-sounding "quotes" that
were no longer quotes, so a third of its citations couldn't be found in the
source and grounding fell to 0.60. More fluency, less literal honesty.

**It was non-deterministic.** On one phishing email the 7B produced ungrounded
evidence in a batch run; re-running triage on the *same* email at temperature 0
flipped it to `benign` with verbatim evidence. Same input, different verdict
*and* different citations. Post-hoc auditing assumes the explanation you're
auditing is the one the model stands behind — and the 7B didn't always stand
behind one.

**What it bought instead was calibration.** On a separate 10-email set the 3B
was *underconfident* (right 100% of the time while claiming ~0.88); the 7B's
confidence tracked its accuracy and came out well calibrated. So the bigger
model's *numbers* got more meaningful even as its *explanations* got worse.

## The takeaway

> "Bigger" bought calibration, not auditability. Grounding, faithfulness and
> consistency are separate axes from accuracy and confidence — a model can
> improve on one while regressing on the others. Which is exactly why you
> measure them rather than assume them.

For this phishing-triage task, on this hardware, the 3B is the better auditing
model on every axis that matters *and* 2.3× faster. I did not expect to write
that sentence.

**Caveats, loudly:** small corpora (10 and 15 emails), one run per model, and the
7B visibly varies run to run. Treat the *direction* as real and the exact rates
as noisy — a bigger corpus and several runs per model would firm it up. (Good
first issue, if you're reading this on GitHub.)

## Try it

```bash
pip install glassbox
ollama pull qwen2.5:3b
glassbox demo              # no model needed — shows a fabricated citation caught
glassbox audit email.txt   # the real thing
```

It's MIT-licensed and the repo is small enough to read in one sitting:
**https://github.com/winxtxrgit/glassbox**. There's also a GitHub Action that
fails CI when a model's verdict isn't trustworthy, a `--json` mode for every
command, and white-box lenses (logit lens + occlusion) for when you want to look
*inside* a model instead of just at its output.

If this is useful, a star helps — and contributions, especially more corpora and
multi-run benchmarks, are very welcome.
