"""Audit a whole corpus and report how often the model's verdicts hold up.

One `audit` tells you about one document. Run it over a corpus and the aggregate
is a property of the *model*: what fraction of its verdicts are grounded, how
often the decision actually depends on the cited evidence, how often it survives
a meaning-preserving edit. A model that is trustworthy 40% of the time is a very
different tool from one that is trustworthy 95% of the time — and only a batch
run tells you which you have.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .audit import AuditReport, audit
from .backends import Backend
from .calibration import DatasetError
from .prompts import DEFAULT_POLICY
from .schema import Policy


def load_documents(path: str | Path) -> list[str]:
    """Load a corpus: one ``{"document": ...}`` JSON object per line.

    Any ``label`` field is ignored — a batch audit needs only the text. Raises
    :class:`~glassbox.calibration.DatasetError` on malformed JSON, a missing
    ``document`` field, or an empty file.
    """
    documents: list[str] = []
    with open(path, encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                documents.append(str(json.loads(line)["document"]))
            except (json.JSONDecodeError, KeyError, TypeError) as error:
                raise DatasetError(f"line {lineno}: {error}") from None
    if not documents:
        raise DatasetError(f"{path} contains no documents")
    return documents


@dataclass(frozen=True)
class BatchItem:
    """One document's audit, or the error that stopped it."""

    document: str
    report: AuditReport | None
    error: str | None


def _rate(flags: list[bool]) -> float:
    return sum(flags) / len(flags) if flags else 0.0


@dataclass(frozen=True)
class BatchReport:
    items: list[BatchItem]

    @property
    def audited(self) -> list[AuditReport]:
        return [item.report for item in self.items if item.report is not None]

    @property
    def n(self) -> int:
        return len(self.audited)

    @property
    def errors(self) -> int:
        return sum(1 for item in self.items if item.error is not None)

    @property
    def grounded_rate(self) -> float:
        return _rate([r.grounded for r in self.audited])

    @property
    def faithful_rate(self) -> float:
        return _rate([r.faithful for r in self.audited])

    @property
    def robust_rate(self) -> float:
        return _rate([r.robust for r in self.audited])

    @property
    def trustworthy_rate(self) -> float:
        return _rate([r.trustworthy for r in self.audited])


def run_batch(
    documents: list[str], backend: Backend, policy: Policy = DEFAULT_POLICY
) -> BatchReport:
    """Audit every document, aggregating the pass rates across the corpus.

    A document the model cannot classify is recorded as an errored item and left
    out of the rates. A :class:`~glassbox.backends.BackendError` (model down) is
    not caught — it aborts the run.
    """
    from .triage import TriageError

    items: list[BatchItem] = []
    for document in documents:
        try:
            report = audit(document, backend, policy)
            items.append(BatchItem(document=document, report=report, error=None))
        except TriageError as error:
            items.append(BatchItem(document=document, report=None, error=str(error)))
    return BatchReport(items=items)
