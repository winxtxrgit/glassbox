#!/usr/bin/env python
"""Run glassbox across one or more Ollama models and print a comparison table.

For each model this runs `batch` (grounding / faithfulness / robustness /
trustworthiness rates over the corpus) and `calibrate` (accuracy + ECE against the
labels), and reports a rough per-document latency. It's the engine behind the
"which model hallucinates least?" table.

With `--runs N` each model is evaluated N times. The main table shows the mean of
each metric; a second table shows the min-max spread across runs, which is how you
*measure* a model's non-determinism rather than just asserting it (the 7B varies
run to run even at temperature 0 -- see FINDINGS.md).

Usage:
    python benchmarks/run_benchmark.py --models qwen2.5:3b,qwen2.5:7b
    python benchmarks/run_benchmark.py --models qwen2.5:7b --runs 5
    python benchmarks/run_benchmark.py --models qwen2.5:3b --corpus benchmarks/corpus.jsonl
"""

from __future__ import annotations

import argparse
import time
from statistics import mean

from glassbox.backends import OllamaBackend
from glassbox.batch import load_documents, run_batch
from glassbox.calibration import evaluate, load_dataset
from glassbox.prompts import DEFAULT_POLICY

# Metrics reported per run, in table order. latency is kept out of the spread
# table (it's machine noise, not model non-determinism).
RATE_METRICS = ("accuracy", "grounded", "faithful", "robust", "trustworthy")
SCORE_METRICS = ("ece",)


def benchmark(model: str, corpus: str, host: str) -> dict:
    """Run both lenses on *model* over *corpus* once and return the aggregate metrics."""
    backend = OllamaBackend(model=model, host=host)
    documents = load_documents(corpus)
    dataset = load_dataset(corpus)

    start = time.monotonic()
    batch = run_batch(documents, backend, DEFAULT_POLICY)
    calibration = evaluate(dataset, backend, DEFAULT_POLICY)
    elapsed = time.monotonic() - start

    return {
        "model": model,
        "n": batch.n,
        "accuracy": calibration.accuracy,
        "grounded": batch.grounded_rate,
        "faithful": batch.faithful_rate,
        "robust": batch.robust_rate,
        "trustworthy": batch.trustworthy_rate,
        "ece": calibration.ece,
        "latency": elapsed / max(batch.n, 1),  # per document, audit + one calibrate call
    }


def aggregate(runs: list[dict]) -> dict:
    """Collapse repeated runs of one model into mean / min / max per metric.

    A single run collapses to mean == min == max, so downstream formatting is the
    same whether ``--runs`` is 1 or 20.
    """
    model = runs[0]["model"]
    metrics = (*RATE_METRICS, *SCORE_METRICS, "latency")
    row: dict = {"model": model, "n": runs[0]["n"], "runs": len(runs)}
    for metric in metrics:
        values = [r[metric] for r in runs]
        row[metric] = {"mean": mean(values), "min": min(values), "max": max(values)}
    return row


def to_markdown(rows: list[dict]) -> str:
    """Mean table. Reads aggregate rows (each metric is a mean/min/max dict)."""
    header = "| Model | N | Accuracy | Grounded | Faithful | Robust | Trustworthy | ECE | ~s/doc |"
    sep = "|---|---:|---:|---:|---:|---:|---:|---:|---:|"
    lines = [header, sep]
    for r in rows:
        lines.append(
            f"| `{r['model']}` | {r['n']} | {r['accuracy']['mean']:.0%} "
            f"| {r['grounded']['mean']:.0%} | {r['faithful']['mean']:.0%} "
            f"| {r['robust']['mean']:.0%} | {r['trustworthy']['mean']:.0%} "
            f"| {r['ece']['mean']:.2f} | {r['latency']['mean']:.1f} |"
        )
    return "\n".join(lines)


def _range(cell: dict, pct: bool) -> str:
    """Format a metric's min-max span; a stable metric collapses to a single value."""
    lo, hi = cell["min"], cell["max"]
    fmt = (lambda v: f"{v:.0%}") if pct else (lambda v: f"{v:.2f}")
    return fmt(lo) if lo == hi else f"{fmt(lo)}-{fmt(hi)}"


def to_spread_markdown(rows: list[dict]) -> str:
    """Min-max span of each metric across runs -- the non-determinism readout."""
    runs = rows[0]["runs"]
    header = "| Model | Accuracy | Grounded | Faithful | Robust | Trustworthy | ECE |"
    sep = "|---|---:|---:|---:|---:|---:|---:|"
    lines = [f"### Spread across {runs} runs (min-max)", "", header, sep]
    for r in rows:
        cells = [_range(r[m], pct=True) for m in RATE_METRICS]
        cells.append(_range(r["ece"], pct=False))
        lines.append(f"| `{r['model']}` | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark glassbox across models (Ollama).")
    parser.add_argument("--models", required=True, help="comma-separated Ollama model tags")
    parser.add_argument("--corpus", default="benchmarks/corpus.jsonl", help="JSON Lines corpus")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama server URL")
    parser.add_argument(
        "--runs", type=int, default=1, help="evaluate each model this many times (default 1)"
    )
    args = parser.parse_args(argv)

    if args.runs < 1:
        parser.error("--runs must be at least 1")

    rows = []
    for model in (m.strip() for m in args.models.split(",") if m.strip()):
        runs = []
        for i in range(args.runs):
            suffix = f" (run {i + 1}/{args.runs})" if args.runs > 1 else ""
            print(f"# benchmarking {model}{suffix} ...", flush=True)
            runs.append(benchmark(model, args.corpus, args.host))
        rows.append(aggregate(runs))

    print()
    print(to_markdown(rows))
    if args.runs > 1:
        print()
        print(to_spread_markdown(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
