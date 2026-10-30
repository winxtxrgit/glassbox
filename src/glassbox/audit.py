"""One verdict, every black-box lens at once.

The individual commands each answer one question about a decision. ``audit`` runs
the three single-document lenses together on one verdict and reports whether it
clears all of them: is the cited evidence real (grounding), does the decision
actually depend on it (faithfulness), and does it track meaning rather than
surface form (consistency)? A verdict is only as trustworthy as the weakest of
the three.

Calibration is deliberately left out — it is a property of a model over a
*labelled set*, not of a single document, so it stays its own command.
"""

from __future__ import annotations

from dataclasses import dataclass

from .backends import Backend
from .consistency import ConsistencyReport
from .consistency import check as consistency_check
from .faithfulness import FaithfulnessReport
from .faithfulness import analyze as faithfulness_analyze
from .prompts import DEFAULT_POLICY
from .schema import Policy, Verdict
from .triage import triage


@dataclass(frozen=True)
class AuditReport:
    """A verdict and the three lenses applied to it."""

    verdict: Verdict
    faithfulness: FaithfulnessReport
    consistency: ConsistencyReport

    @property
    def grounded(self) -> bool:
        """Every cited span exists in the document."""
        return self.verdict.grounded

    @property
    def faithful(self) -> bool:
        """The decision collapses once its cited evidence is erased."""
        return self.faithfulness.load_bearing

    @property
    def robust(self) -> bool:
        """The decision survives meaning-preserving edits."""
        return self.consistency.robust

    @property
    def checks(self) -> dict[str, bool]:
        return {"grounded": self.grounded, "faithful": self.faithful, "robust": self.robust}

    @property
    def passed(self) -> int:
        return sum(self.checks.values())

    @property
    def trustworthy(self) -> bool:
        """True only when all three lenses pass."""
        return self.passed == len(self.checks)


def audit(document: str, backend: Backend, policy: Policy = DEFAULT_POLICY) -> AuditReport:
    """Run grounding, faithfulness, and consistency on one verdict for *document*.

    The verdict is computed once and reused by both deeper analyses, so an audit
    costs one triage plus the probe and stress passes — not three independent
    classifications.
    """
    verdict = triage(document, backend, policy)
    faithfulness = faithfulness_analyze(document, backend, policy, verdict=verdict)
    consistency = consistency_check(document, backend, policy, original=verdict)
    return AuditReport(verdict=verdict, faithfulness=faithfulness, consistency=consistency)
