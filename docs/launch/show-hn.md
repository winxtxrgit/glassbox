# Show HN draft

> Post at https://news.ycombinator.com/submit. Keep the title under 80 chars.
> URL field → the GitHub repo. First comment is yours, posted immediately after.

## Title

```
Show HN: Glassbox – check whether a local LLM's cited evidence is real
```

## URL

```
https://github.com/winxtxrgit/glassbox
```

## First comment (post right after submitting)

I kept seeing LLMs give the right label with a reason that turned out to be
decorative — a fluent sentence written after the fact that didn't actually drive
the decision. glassbox is my attempt to measure that instead of trusting it.

It makes a local model (Ollama, no API key, nothing leaves the machine) quote its
evidence verbatim, then runs three checks:

- grounding: does each quote actually exist in the document, or did the model
  fabricate it?
- faithfulness: erase the cited span and re-run — if the label doesn't change,
  the "reason" was decorative (ERASER comprehensiveness, one citation at a time).
- consistency: re-decide after meaning-preserving edits (lowercasing, whitespace,
  a forwarding wrapper) — if the label flips, it was keying on surface form.

The thing I didn't expect: running it on Qwen 2.5 3B vs 7B, the *bigger* model is
better calibrated but noticeably less auditable — it stops quoting verbatim and
paraphrases instead, so grounding collapses from 100% to 60% and faithfulness
from 60% to 20%, at the same accuracy. Writeup and numbers are in the repo
(FINDINGS.md / benchmarks/RESULTS.md).

Core is pure stdlib Python, MIT. There's also a `--json` mode, a GitHub Action
that fails CI on an untrustworthy verdict, and optional white-box lenses (logit
lens + occlusion) via transformers.

Caveats are real: small corpora, few runs, 7B is non-deterministic. Happy to hear
where the methodology is weak — and whether the 3B-beats-7B-on-auditability result
reproduces for anyone else.
