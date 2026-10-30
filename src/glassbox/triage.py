"""Run a document through a backend and return a grounded :class:`Verdict`.

This is the seam the rest of the project builds on: give it any :class:`Backend`
and a :class:`Policy`, get back a decision whose evidence has already been located
in the source. Later phases wrap this to probe faithfulness — counterfactual
deletion, paraphrase consistency, confidence calibration.
"""

from __future__ import annotations

import json

from .backends import Backend
from .grounding import ground
from .prompts import DEFAULT_POLICY, build_prompts
from .schema import Policy, Verdict


class TriageError(Exception):
    """The backend replied, but not with a verdict we could parse or trust."""


def _coerce_confidence(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5  # a reply without a usable confidence is treated as a coin-flip
    return max(0.0, min(1.0, number))


def _match_label(raw: object, policy: Policy) -> str:
    if isinstance(raw, str):
        for label in policy.labels:
            if raw.strip().lower() == label.lower():
                return label
    raise TriageError(f"model returned label {raw!r}, which is not one of {list(policy.labels)}")


def triage(document: str, backend: Backend, policy: Policy = DEFAULT_POLICY) -> Verdict:
    """Classify *document* with *backend* under *policy*, grounding the evidence.

    Raises :class:`TriageError` if the reply is not JSON, omits the label, or
    labels the document with something outside the policy's allowed set.
    """
    system, user = build_prompts(document, policy)
    raw = backend.generate(system, user)

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as error:
        raise TriageError(f"model reply was not valid JSON: {error}") from None
    if not isinstance(data, dict):
        raise TriageError(f"model reply was not a JSON object: {type(data).__name__}")

    label = _match_label(data.get("label"), policy)
    confidence = _coerce_confidence(data.get("confidence"))
    rationale = str(data.get("rationale", "")).strip()

    evidence = data.get("evidence", [])
    if not isinstance(evidence, list):
        evidence = []
    citations = ground(document, evidence)

    return Verdict(label=label, confidence=confidence, rationale=rationale, citations=citations)
