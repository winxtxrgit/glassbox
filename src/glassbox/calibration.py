"""Calibration: do the confidence numbers mean anything?

Phases 1-3 interrogated a single verdict. Calibration needs a *labelled set*: run
the model over documents whose true labels you know, then ask whether its stated
confidence matches how often it is actually right. A model that stamps 0.90 on
everything yet is correct 60% of the time is overconfident — the number is
decoration, not a probability.

The headline metric is **ECE** (expected calibration error): bin predictions by
confidence, and in each bin compare the average confidence to the observed
accuracy. A perfectly calibrated model has ECE 0 — within every confidence band,
it is right exactly as often as it claims. We also report the Brier score (mean
squared error of confidence against correctness) and a reliability table.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .backends import Backend
from .prompts import DEFAULT_POLICY
from .schema import Policy
from .triage import TriageError, triage


class DatasetError(Exception):
    """A labelled dataset could not be read (bad JSON, missing fields, empty)."""


@dataclass(frozen=True)
class Example:
    """A labelled document: its text and the correct label."""

    document: str
    gold: str


def load_dataset(path: str | Path) -> list[Example]:
    """Load a JSON Lines dataset: one ``{"document": ..., "label": ...}`` per line.

    Blank lines are skipped. Raises :class:`DatasetError` on malformed JSON, a
    missing ``document``/``label`` field, or an empty file.
    """
    examples: list[Example] = []
    with open(path, encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                examples.append(Example(str(obj["document"]), str(obj["label"])))
            except (json.JSONDecodeError, KeyError, TypeError) as error:
                raise DatasetError(f"line {lineno}: {error}") from None
    if not examples:
        raise DatasetError(f"{path} contains no examples")
    return examples


@dataclass(frozen=True)
class ExampleResult:
    """One scored prediction: what the model said, and whether it was right."""

    gold: str
    predicted: str
    confidence: float
    correct: bool


@dataclass(frozen=True)
class ReliabilityBin:
    """A confidence band: how sure the model was vs. how often it was right."""

    low: float
    high: float
    count: int
    avg_confidence: float
    accuracy: float


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


@dataclass(frozen=True)
class CalibrationReport:
    """Aggregate calibration over a labelled set."""

    results: list[ExampleResult]
    errors: int = 0  # examples the model failed to classify (excluded from metrics)
    bins: int = 10

    @property
    def n(self) -> int:
        return len(self.results)

    @property
    def accuracy(self) -> float:
        return _mean([1.0 if r.correct else 0.0 for r in self.results])

    @property
    def mean_confidence(self) -> float:
        return _mean([r.confidence for r in self.results])

    @property
    def gap(self) -> float:
        """Mean confidence minus accuracy. Positive = overconfident."""
        return self.mean_confidence - self.accuracy

    @property
    def overconfident(self) -> bool:
        return self.gap > 0.05

    @property
    def underconfident(self) -> bool:
        return self.gap < -0.05

    def _bin_index(self, confidence: float) -> int:
        return min(self.bins - 1, max(0, int(confidence * self.bins)))

    @property
    def ece(self) -> float:
        """Expected calibration error: accuracy-vs-confidence gap, weighted by bin."""
        if not self.results:
            return 0.0
        buckets: dict[int, list[ExampleResult]] = {}
        for result in self.results:
            buckets.setdefault(self._bin_index(result.confidence), []).append(result)
        total = 0.0
        for bucket in buckets.values():
            conf = _mean([r.confidence for r in bucket])
            acc = _mean([1.0 if r.correct else 0.0 for r in bucket])
            total += (len(bucket) / self.n) * abs(acc - conf)
        return total

    @property
    def brier(self) -> float:
        """Mean squared error of confidence against correctness (0 is perfect)."""
        return _mean([(r.confidence - (1.0 if r.correct else 0.0)) ** 2 for r in self.results])

    def reliability(self) -> list[ReliabilityBin]:
        """The non-empty confidence bands, low to high, for display."""
        buckets: dict[int, list[ExampleResult]] = {}
        for result in self.results:
            buckets.setdefault(self._bin_index(result.confidence), []).append(result)
        rows = []
        for index in sorted(buckets):
            bucket = buckets[index]
            rows.append(
                ReliabilityBin(
                    low=index / self.bins,
                    high=(index + 1) / self.bins,
                    count=len(bucket),
                    avg_confidence=_mean([r.confidence for r in bucket]),
                    accuracy=_mean([1.0 if r.correct else 0.0 for r in bucket]),
                )
            )
        return rows


def evaluate(
    dataset: list[Example],
    backend: Backend,
    policy: Policy = DEFAULT_POLICY,
    bins: int = 10,
) -> CalibrationReport:
    """Classify each labelled *Example* and score the model's confidence.

    An example the model cannot classify (unparseable reply) is counted in
    ``errors`` and left out of every metric — calibration is measured over the
    predictions the model actually committed to. A :class:`BackendError` (model
    unreachable) is not caught: it aborts the whole run.
    """
    results: list[ExampleResult] = []
    errors = 0
    for example in dataset:
        try:
            verdict = triage(example.document, backend, policy)
        except TriageError:
            errors += 1
            continue
        results.append(
            ExampleResult(
                gold=example.gold,
                predicted=verdict.label,
                confidence=verdict.confidence,
                correct=verdict.label == example.gold,
            )
        )
    return CalibrationReport(results=results, errors=errors, bins=bins)
