"""Consistency: does the verdict track meaning, or surface form?

Phase 2 asked whether the cited evidence was load-bearing. Phase 3 asks a
different question about the *decision* itself: is it stable under edits that
leave the meaning intact? Lower-case the text, reflow the whitespace, wrap it in
a forwarding header — a model that understands the content should not change its
mind. If the label flips when nothing of substance changed, the model is keying
on the surface (casing, spacing, formatting), and the verdict is fragile.

Each perturbation here is deliberately **meaning-preserving**, so any flip is a
property of the model, not of the edit. No-op edits (ones that don't change a
given document) are reported but don't count toward the score.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .backends import Backend
from .prompts import DEFAULT_POLICY
from .schema import Policy, Verdict
from .triage import triage


@dataclass(frozen=True)
class Perturbation:
    """A named, meaning-preserving edit to a document."""

    name: str
    description: str
    fn: Callable[[str], str]

    def apply(self, text: str) -> str:
        return self.fn(text)


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _pad_boilerplate(text: str) -> str:
    return f"---------- Forwarded message ----------\n\n{text}\n\n-- \nSent from my phone"


def _normalize_punctuation(text: str) -> str:
    for fancy, plain in (("—", "-"), ("–", "-"), ("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'")):
        text = text.replace(fancy, plain)
    return text


# --- Thai-language edits (no-ops on non-Thai text, so they're safe as defaults) ---

_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")

# Politeness particles: dropping them changes register, not propositional content —
# "within 24 hours" is just as urgent with or without a trailing ครับ/ค่ะ. Longest
# first so "นะครับ" is removed whole before "ครับ" could split it.
_THAI_PARTICLES = ("ครับผม", "นะครับ", "นะคะ", "ครับ", "ค่ะ", "จ้ะ")


def _thai_numerals(text: str) -> str:
    """Thai digits to Arabic (๒๔ -> 24) — same number, different glyphs."""
    return text.translate(_THAI_DIGITS)


def _drop_thai_particles(text: str) -> str:
    """Strip trailing politeness particles — a model shouldn't read threat from them."""
    for particle in _THAI_PARTICLES:
        text = text.replace(particle, "")
    return text


# Edits a semantically-grounded classifier should be invariant to. The Thai ones
# simply don't change English documents, so they're excluded from the score there.
DEFAULT_PERTURBATIONS: tuple[Perturbation, ...] = (
    Perturbation("lowercase", "fold everything to lower case", str.lower),
    Perturbation("collapse whitespace", "reflow runs of spaces and newlines", _collapse_whitespace),
    Perturbation(
        "pad boilerplate", "wrap in a benign forwarding header and signature", _pad_boilerplate
    ),
    Perturbation(
        "normalize punctuation", "smart quotes and dashes to ASCII", _normalize_punctuation
    ),
    Perturbation("thai numerals", "Thai digits to Arabic (๒๔ -> 24)", _thai_numerals),
    Perturbation(
        "drop thai particles", "strip politeness particles (ครับ/ค่ะ)", _drop_thai_particles
    ),
)


@dataclass(frozen=True)
class Variant:
    """One perturbed run: what the edit was, and how the verdict responded."""

    perturbation: Perturbation
    changed: bool  # did the edit actually alter this document?
    label: str
    confidence: float
    agreed: bool  # same label as the original verdict?


@dataclass(frozen=True)
class ConsistencyReport:
    original: Verdict
    variants: list[Variant]

    @property
    def effective(self) -> list[Variant]:
        """Variants whose edit actually changed the document — the real tests."""
        return [v for v in self.variants if v.changed]

    @property
    def testable(self) -> bool:
        return bool(self.effective)

    @property
    def flips(self) -> list[Variant]:
        """Meaning-preserving edits that nonetheless changed the decision."""
        return [v for v in self.effective if not v.agreed]

    @property
    def stability(self) -> float:
        """Fraction of effective edits that kept the label (1.0 when none apply)."""
        effective = self.effective
        if not effective:
            return 1.0
        return sum(v.agreed for v in effective) / len(effective)

    @property
    def robust(self) -> bool:
        """True when the verdict held under every edit that changed the document."""
        return self.testable and not self.flips


def check(
    document: str,
    backend: Backend,
    policy: Policy = DEFAULT_POLICY,
    perturbations: tuple[Perturbation, ...] = DEFAULT_PERTURBATIONS,
    original: Verdict | None = None,
) -> ConsistencyReport:
    """Re-decide *document* under each meaning-preserving edit and compare labels.

    Runs *backend* once per perturbation (plus once for the original, unless a
    precomputed *original* verdict is passed). A perturbation that leaves the
    document unchanged is recorded with ``changed=False`` and excluded from the
    stability score.
    """
    if original is None:
        original = triage(document, backend, policy)

    variants: list[Variant] = []
    for perturbation in perturbations:
        edited = perturbation.apply(document)
        changed = edited != document
        after = triage(edited, backend, policy)
        variants.append(
            Variant(
                perturbation=perturbation,
                changed=changed,
                label=after.label,
                confidence=after.confidence,
                agreed=after.label == original.label,
            )
        )

    return ConsistencyReport(original=original, variants=variants)
