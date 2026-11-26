"""The shapes glassbox works with: a decision, and the evidence it rests on.

A :class:`Verdict` is only as trustworthy as its citations. Each :class:`Citation`
records whether the model's quoted evidence was actually found in the source, so a
verdict can report not just *what* it decided but whether its own justification
holds up — the first, cheapest faithfulness check there is.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Citation:
    """A span the model offered as evidence, located back in the source.

    ``start``/``end`` are character offsets into the original document, or -1
    when the quote could not be found — meaning the model cited text that isn't
    there. ``verified`` is the headline: did the evidence survive the lookup?
    """

    quote: str
    start: int
    end: int
    verified: bool

    @property
    def located(self) -> bool:
        return self.start >= 0 and self.end >= self.start


@dataclass(frozen=True)
class Policy:
    """What question the model is answering, and the labels it may answer with.

    ``prompt_style`` selects how the instruction is phrased: ``"plain"`` (the
    default) states the rules; ``"few-shot"`` also shows worked examples of
    verbatim quoting, which helps a fluent model that otherwise paraphrases its
    evidence instead of copying it.

    ``default_label`` names the label a document gets when *nothing* in it is a
    signal -- ``benign`` for security triage, ``ham`` for spam filtering. Such a
    verdict rests on an absence, so there is no span whose removal should flip
    it, and counterfactual faithfulness does not apply to it. ``None`` (the
    default for a custom policy) treats every label as one that needs evidence.
    """

    name: str
    question: str
    labels: tuple[str, ...]
    prompt_style: str = "plain"
    default_label: str | None = None


@dataclass(frozen=True)
class Verdict:
    """A labelled decision with a confidence, a rationale, and located evidence."""

    label: str
    confidence: float
    rationale: str
    citations: list[Citation]

    @property
    def grounded(self) -> bool:
        """True when the verdict offered evidence and every piece checked out.

        A grounded verdict points only at text that exists; an ungrounded one
        either cited nothing or cited something the document never said.
        """
        return bool(self.citations) and all(c.verified for c in self.citations)

    @property
    def hallucinated_citations(self) -> list[Citation]:
        """Cited evidence that does not appear in the source — unfaithful claims."""
        return [c for c in self.citations if not c.verified]
