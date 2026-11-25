"""Audit a whole corpus and report how often the model's verdicts hold up.

One `audit` tells you about one document. Run it over a corpus and the aggregate
is a property of the *model*: what fraction of its verdicts are grounded, how
often the decision actually depends on the cited evidence, how often it survives
a meaning-preserving edit. A model that is trustworthy 40% of the time is a very
different tool from one that is trustworthy 95% of the time — and only a batch
run tells you which you have.

A corpus audit against a slow model (a CPU box, a Raspberry Pi) can take hours, so
the run is built to survive: a request that dies can be recorded and skipped
instead of throwing away every document already audited, and a ``checkpoint``
file lets an interrupted run pick up where it stopped.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .audit import AuditReport, audit
from .backends import Backend, BackendError
from .calibration import DatasetError
from .prompts import DEFAULT_POLICY
from .schema import Policy

CHECKS = ("grounded", "faithful", "robust")


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
    """One document's audit, or the error that stopped it.

    ``report`` is the full audit when it ran in this process. An item restored
    from a checkpoint has no report, only the ``saved`` pass/fail flags, which is
    all the corpus rates need. ``backend_error`` marks a failure of the model
    server (timeout, unreachable) rather than of the model's reply.
    """

    document: str
    report: AuditReport | None
    error: str | None
    saved: dict[str, bool] | None = None
    backend_error: bool = False

    @property
    def checks(self) -> dict[str, bool] | None:
        """Pass/fail per lens that applied, or None if the item errored.

        ``faithful`` is absent when that test did not apply to the verdict (a
        default-label verdict such as "benign").
        """
        if self.report is not None:
            return self.report.checks
        return self.saved

    @property
    def trustworthy(self) -> bool:
        checks = self.checks
        return bool(checks) and all(checks.values())


def _rate(flags: list[bool]) -> float:
    return sum(flags) / len(flags) if flags else 0.0


def wilson_interval(passed: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a pass rate of *passed* out of *n*.

    A rate over a few dozen documents is a noisy estimate: 15/40 = 38% is
    consistent with anything from about 24% to 53%. Wilson stays inside [0, 1]
    and behaves at 0/n and n/n, unlike the textbook normal approximation.
    """
    if n <= 0:
        return (0.0, 1.0)
    p = passed / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True)
class BatchReport:
    items: list[BatchItem]

    @property
    def audited(self) -> list[AuditReport]:
        """Full reports for the documents audited in this process."""
        return [item.report for item in self.items if item.report is not None]

    @property
    def scored(self) -> list[BatchItem]:
        """Every item with pass/fail flags, whether audited now or restored."""
        return [item for item in self.items if item.checks is not None]

    @property
    def n(self) -> int:
        return len(self.scored)

    @property
    def errors(self) -> int:
        return sum(1 for item in self.items if item.error is not None)

    @property
    def backend_errors(self) -> int:
        """Errors from the model server (timeouts, dropped connections)."""
        return sum(1 for item in self.items if item.backend_error)

    @property
    def parse_errors(self) -> int:
        """Errors from the model's reply (not JSON, unknown label)."""
        return self.errors - self.backend_errors

    def _flags(self, name: str) -> list[bool]:
        """Pass/fail for one lens over the items it applied to."""
        if name == "trustworthy":
            return [item.trustworthy for item in self.scored]
        return [item.checks[name] for item in self.scored if name in item.checks]  # type: ignore[operator, index]

    def applicable(self, name: str) -> int:
        """How many scored items the lens *name* applied to (its denominator)."""
        return len(self._flags(name))

    def _check_rate(self, name: str) -> float:
        return _rate(self._flags(name))

    @property
    def grounded_rate(self) -> float:
        return self._check_rate("grounded")

    @property
    def faithful_rate(self) -> float:
        return self._check_rate("faithful")

    @property
    def robust_rate(self) -> float:
        return self._check_rate("robust")

    @property
    def trustworthy_rate(self) -> float:
        return _rate(self._flags("trustworthy"))

    def interval(self, name: str) -> tuple[float, float]:
        """95% Wilson interval for ``grounded``/``faithful``/``robust``/``trustworthy``."""
        flags = self._flags(name)
        return wilson_interval(sum(flags), len(flags))


def _doc_key(document: str) -> str:
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def _fingerprint(backend: Backend, policy: Policy) -> dict:
    """What must match for a checkpoint's results to be reused."""
    return {
        "backend": type(backend).__name__,
        "model": getattr(backend, "model", None),
        "labels": list(policy.labels),
        "question": policy.question,
        "prompt_style": policy.prompt_style,
        "default_label": policy.default_label,
    }


class CheckpointError(ValueError):
    """The checkpoint file belongs to a different model/policy, or is corrupt."""


def _load_checkpoint(path: Path, fingerprint: dict) -> dict[str, dict]:
    """Read finished results from *path*, keyed by document hash."""
    done: dict[str, dict] = {}
    if not path.exists():
        return done
    with open(path, encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # A run killed mid-write leaves a torn last line: ignore it, the
                # document is simply audited again.
                continue
            if "meta" in record:
                if record["meta"] != fingerprint:
                    raise CheckpointError(
                        f"{path} was written for {record['meta']}, not {fingerprint}; "
                        "use a different checkpoint file for a different model or policy"
                    )
                continue
            if "key" in record:
                done[record["key"]] = record
            else:
                raise CheckpointError(f"{path} line {lineno}: not a glassbox checkpoint record")
    return done


def _restore(document: str, record: dict) -> BatchItem:
    if record.get("error") is not None:
        return BatchItem(document=document, report=None, error=record["error"])
    saved = {name: bool(record["checks"][name]) for name in CHECKS if name in record["checks"]}
    return BatchItem(document=document, report=None, error=None, saved=saved)


def run_batch(
    documents: list[str],
    backend: Backend,
    policy: Policy = DEFAULT_POLICY,
    *,
    skip_backend_errors: bool = False,
    max_consecutive_backend_errors: int = 3,
    checkpoint: str | Path | None = None,
    progress: Callable[[int, int, BatchItem], None] | None = None,
) -> BatchReport:
    """Audit every document, aggregating the pass rates across the corpus.

    A document the model cannot classify (unparseable reply) is recorded as an
    errored item and left out of the rates.

    A :class:`~glassbox.backends.BackendError` (timeout, server down) aborts the
    run by default. With ``skip_backend_errors=True`` it is recorded as an errored
    item instead and the run moves on -- unless ``max_consecutive_backend_errors``
    happen in a row, which means the server is gone, not slow, and the run stops
    rather than wait out a timeout on every remaining document.

    With ``checkpoint`` set, each finished document is appended to that JSON
    Lines file as it completes, and documents already in it are not audited
    again, so an interrupted run resumes. Backend errors are never checkpointed:
    they are retried on the next run.

    ``progress(index, total, item)`` is called after each document (1-based).
    """
    from .triage import TriageError

    path = Path(checkpoint) if checkpoint is not None else None
    fingerprint = _fingerprint(backend, policy)
    done = _load_checkpoint(path, fingerprint) if path is not None else {}
    sink = None
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not path.exists() or path.stat().st_size == 0
        sink = open(path, "a", encoding="utf-8")  # noqa: SIM115 - closed in finally
        if fresh:
            sink.write(json.dumps({"meta": fingerprint}) + "\n")
            sink.flush()

    items: list[BatchItem] = []
    consecutive = 0
    try:
        for index, document in enumerate(documents, 1):
            key = _doc_key(document)
            if key in done:
                item = _restore(document, done[key])
            else:
                try:
                    report = audit(document, backend, policy)
                    item = BatchItem(document=document, report=report, error=None)
                    consecutive = 0
                except TriageError as error:
                    item = BatchItem(document=document, report=None, error=str(error))
                    consecutive = 0
                except BackendError as error:
                    consecutive += 1
                    if not skip_backend_errors or consecutive >= max_consecutive_backend_errors:
                        raise
                    item = BatchItem(
                        document=document, report=None, error=str(error), backend_error=True
                    )
                if sink is not None and not item.backend_error:
                    record = {"key": key, "error": item.error, "checks": item.checks}
                    sink.write(json.dumps(record) + "\n")
                    sink.flush()
            items.append(item)
            if progress is not None:
                progress(index, len(documents), item)
    finally:
        if sink is not None:
            sink.close()
    return BatchReport(items=items)
