"""Counterfactual faithfulness: does the verdict actually depend on its evidence?

Phase 1 asked a weak question — *does the cited span exist?* A model can cite
real text and still be rationalizing: the quote is in the document, but the
decision wasn't made from it. Phase 2 asks the load-bearing question. Take a
grounded verdict, **remove** a cited span from the document, and re-decide. If
the label flips, that citation was doing real work. If the verdict survives
without any of its stated evidence, the explanation is post-hoc — faithful-looking,
but not faithful.

This is the erasure idea from rationale-faithfulness work (ERASER's
comprehensiveness), applied to a black-box model through its own API: we can't
see the weights, but we *can* see whether the output moves when the cited
evidence is taken away.
"""

from __future__ import annotations

from dataclasses import dataclass

from .backends import Backend
from .prompts import DEFAULT_POLICY
from .schema import Citation, Policy, Verdict
from .triage import triage


def ablate(document: str, spans: list[tuple[int, int]]) -> str:
    """Return *document* with each ``(start, end)`` span removed.

    Spans are deleted from right to left so earlier offsets stay valid while
    later ones are cut. Overlapping or out-of-order spans are handled by sorting.
    """
    result = document
    for start, end in sorted(spans, reverse=True):
        if 0 <= start <= end <= len(result):
            result = result[:start] + result[end:]
    return result


@dataclass(frozen=True)
class CitationProbe:
    """What happened to the verdict when one cited span was erased."""

    citation: Citation
    new_label: str
    new_confidence: float
    flipped: bool  # did removing this span change the label?

    @property
    def load_bearing(self) -> bool:
        """True when erasing this evidence changed the decision — it mattered."""
        return self.flipped


@dataclass(frozen=True)
class FaithfulnessReport:
    """A verdict plus the counterfactual evidence it survived (or didn't)."""

    verdict: Verdict
    probes: list[CitationProbe]
    comprehensive_label: str  # label after ALL cited evidence is erased at once
    comprehensive_flipped: bool

    @property
    def testable(self) -> bool:
        """True when there was grounded evidence to erase in the first place."""
        return bool(self.probes)

    @property
    def necessary(self) -> list[Citation]:
        """Citations whose individual removal flips the verdict — real drivers."""
        return [p.citation for p in self.probes if p.flipped]

    @property
    def decorative(self) -> list[Citation]:
        """Grounded citations the verdict does not actually need — cited anyway."""
        return [p.citation for p in self.probes if not p.flipped]

    @property
    def load_bearing(self) -> bool:
        """The headline: does the decision collapse once its evidence is gone?

        True means the model's stated evidence genuinely drives the verdict.
        False means the verdict stands without any of what it cited — the
        explanation is decoration, not reason.
        """
        return self.comprehensive_flipped


def analyze(
    document: str,
    backend: Backend,
    policy: Policy = DEFAULT_POLICY,
    verdict: Verdict | None = None,
) -> FaithfulnessReport:
    """Probe the counterfactual faithfulness of a verdict on *document*.

    Re-runs *backend* once per grounded citation (erasing that span) plus once
    with every grounded span erased together. Only verified citations are tested
    — you cannot erase evidence that was never in the document. Pass a precomputed
    *verdict* to avoid an extra model call.
    """
    if verdict is None:
        verdict = triage(document, backend, policy)

    verified = [c for c in verdict.citations if c.verified]

    probes: list[CitationProbe] = []
    for citation in verified:
        ablated = ablate(document, [(citation.start, citation.end)])
        after = triage(ablated, backend, policy)
        probes.append(
            CitationProbe(
                citation=citation,
                new_label=after.label,
                new_confidence=after.confidence,
                flipped=after.label != verdict.label,
            )
        )

    if verified:
        ablated_all = ablate(document, [(c.start, c.end) for c in verified])
        after_all = triage(ablated_all, backend, policy)
        comprehensive_label = after_all.label
        comprehensive_flipped = after_all.label != verdict.label
    else:
        comprehensive_label = verdict.label
        comprehensive_flipped = False

    return FaithfulnessReport(
        verdict=verdict,
        probes=probes,
        comprehensive_label=comprehensive_label,
        comprehensive_flipped=comprehensive_flipped,
    )
