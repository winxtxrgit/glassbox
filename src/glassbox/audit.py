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
    default_label: str | None = None

    @property
    def is_default(self) -> bool:
        """The verdict is the policy's "nothing relevant here" label (e.g. benign)."""
        default = self.default_label
        return default is not None and self.verdict.label.lower() == default.lower()

    @property
    def grounded(self) -> bool:
        """Every cited span exists in the document.

        A default-label verdict that cites nothing is grounded: "nothing here is
        a signal" needs no quote, and inventing none is not hallucinating. Any
        quote it *does* offer must still be real.
        """
        if self.is_default and not self.verdict.citations:
            return True
        return self.verdict.grounded

    @property
    def faithful(self) -> bool | None:
        """The decision collapses once its cited evidence is erased.

        None when the test does not apply (a default-label verdict, or evidence
        that is the whole document); see :attr:`FaithfulnessReport.applicable`.
        """
        if not self.faithfulness.applicable:
            return None
        return self.faithfulness.load_bearing

    @property
    def robust(self) -> bool:
        """The decision survives meaning-preserving edits."""
        return self.consistency.robust

    @property
    def checks(self) -> dict[str, bool]:
        """The lenses that apply to this verdict, and whether each passed."""
        checks = {"grounded": self.grounded}
        if self.faithful is not None:
            checks["faithful"] = self.faithful
        checks["robust"] = self.robust
        return checks

    @property
    def passed(self) -> int:
        return sum(self.checks.values())

    @property
    def trustworthy(self) -> bool:
        """True only when every lens that applies passes."""
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
    return AuditReport(
        verdict=verdict,
        faithfulness=faithfulness,
        consistency=consistency,
        default_label=policy.default_label,
    )
