# r/LocalLLaMA draft

> Post as a text post to r/LocalLLaMA. This community cares about local models,
> reproducibility and honest caveats — lead with the finding, not the tool.
> A good crosspost target is r/MachineLearning (use the [P] Project flair there).

## Title

```
I measured whether Qwen 2.5 7B explains itself better than 3B. It doesn't — it's better calibrated but less auditable.
```

## Body

I built a small open-source tool (glassbox) that checks whether a local LLM's
*explanation* holds up, not just whether its label is right. It makes the model
quote its evidence verbatim, then:

- **grounds** each quote (does it actually exist in the document?),
- tests **faithfulness** by erasing the cited span and re-running (if the label
  doesn't change, the evidence was decorative), and
- tests **consistency** under meaning-preserving edits (lowercasing, whitespace,
  a forwarding wrapper).

Everything runs locally on Ollama — no API, nothing leaves the machine.

Then I used it to compare `qwen2.5:3b` vs `qwen2.5:7b` on phishing/benign email
triage (GTX 1650 Ti, 4 GB). Same accuracy (80%), but:

| | 3B | 7B |
|---|---:|---:|
| Grounded | 100% | 60% |
| Faithful | 60% | 20% |
| Trustworthy (all checks) | 60% | 20% |
| Calibration | underconfident | well calibrated |
| ~s/doc | 35.9 | 83.7 |

**The 7B stops quoting verbatim** — it paraphrases its "evidence", so a third of
its citations can't be located in the source. It's also non-deterministic at
temp 0 (re-running the same email flipped both the label and the citations). What
it *did* gain was calibration: its confidence tracks accuracy, the 3B's doesn't.

So on this task the smaller model is more auditable *and* 2.3× faster. Bigger
bought calibration headroom, not grounded/faithful explanations.

**Caveats:** small corpora (10 and 15 emails), one run each, 7B varies run to
run. Directional, not conclusive — I'd love for people with more VRAM to try it
on bigger models and more docs and report back.

Repo (MIT, pure-stdlib core): https://github.com/winxtxrgit/glassbox
Full numbers: FINDINGS.md and benchmarks/RESULTS.md in the repo.

```bash
pip install glassbox
ollama pull qwen2.5:3b
glassbox audit email.txt --model qwen2.5:3b
```
