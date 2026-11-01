#!/usr/bin/env python
"""Run glassbox across one or more Ollama models and print a comparison table.

For each model this runs `batch` (grounding / faithfulness / robustness /
trustworthiness rates over the corpus) and `calibrate` (accuracy + ECE against the
labels), and reports a rough per-document latency. It's the engine behind the
"which model hallucinates least?" table.

Usage:
    python benchmarks/run_benchmark.py --models qwen2.5:3b,qwen2.5:7b
    python benchmarks/run_benchmark.py --models qwen2.5:3b --corpus benchmarks/corpus.jsonl
"""

from __future__ import annotations

import argparse
import time

from glassbox.backends import OllamaBackend
from glassbox.batch import load_documents, run_batch
from glassbox.calibration import evaluate, load_dataset
from glassbox.prompts import DEFAULT_POLICY


def benchmark(model: str, corpus: str, host: str) -> dict:
    """Run both lenses on *model* over *corpus* and return the aggregate metrics."""
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


def to_markdown(rows: list[dict]) -> str:
    header = "| Model | N | Accuracy | Grounded | Faithful | Robust | Trustworthy | ECE | ~s/doc |"
    sep = "|---|---:|---:|---:|---:|---:|---:|---:|---:|"
    lines = [header, sep]
    for r in rows:
        lines.append(
            f"| `{r['model']}` | {r['n']} | {r['accuracy']:.0%} | {r['grounded']:.0%} "
            f"| {r['faithful']:.0%} | {r['robust']:.0%} | {r['trustworthy']:.0%} "
            f"| {r['ece']:.2f} | {r['latency']:.1f} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark glassbox across models (Ollama).")
    parser.add_argument("--models", required=True, help="comma-separated Ollama model tags")
    parser.add_argument("--corpus", default="benchmarks/corpus.jsonl", help="JSON Lines corpus")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama server URL")
    args = parser.parse_args(argv)

    rows = []
    for model in (m.strip() for m in args.models.split(",") if m.strip()):
        print(f"# benchmarking {model} ...", flush=True)
        rows.append(benchmark(model, args.corpus, args.host))

    print()
    print(to_markdown(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
