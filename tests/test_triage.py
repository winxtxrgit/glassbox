from __future__ import annotations

import pytest

from glassbox.backends import StubBackend
from glassbox.prompts import DEFAULT_POLICY
from glassbox.schema import Policy
from glassbox.triage import TriageError, triage

DOC = "Your account will be closed. Verify your password now."


def run(reply: str, document: str = DOC, policy: Policy = DEFAULT_POLICY):
    return triage(document, StubBackend(reply), policy)


def test_grounded_verdict_when_evidence_is_real():
    verdict = run(
        '{"label": "suspicious", "confidence": 0.9, "rationale": "phishing",'
        ' "evidence": ["Verify your password now"]}'
    )
    assert verdict.label == "suspicious"
    assert verdict.confidence == 0.9
    assert verdict.grounded
    assert verdict.hallucinated_citations == []


def test_fabricated_evidence_is_flagged_not_trusted():
    verdict = run(
        '{"label": "suspicious", "confidence": 0.9, "rationale": "x",'
        ' "evidence": ["Verify your password now", "wire the money today"]}'
    )
    assert not verdict.grounded
    assert len(verdict.hallucinated_citations) == 1
    assert verdict.hallucinated_citations[0].quote == "wire the money today"


def test_confidence_is_clamped_and_defaults_safely():
    assert run('{"label": "benign", "confidence": 5, "evidence": []}').confidence == 1.0
    assert run('{"label": "benign", "confidence": -2, "evidence": []}').confidence == 0.0
    assert run('{"label": "benign", "evidence": []}').confidence == 0.5  # missing


def test_label_outside_policy_is_rejected():
    with pytest.raises(TriageError, match="not one of"):
        run('{"label": "spicy", "confidence": 0.5, "evidence": []}')


def test_label_matching_is_case_insensitive():
    assert run('{"label": "BENIGN", "confidence": 0.5, "evidence": []}').label == "benign"


def test_non_json_reply_raises():
    with pytest.raises(TriageError, match="not valid JSON"):
        run("I think this looks suspicious, honestly.")


def test_evidence_missing_or_wrong_type_is_tolerated():
    verdict = run('{"label": "benign", "confidence": 0.4, "evidence": "nope"}')
    assert verdict.citations == []


def test_custom_policy_labels():
    policy = Policy(name="sentiment", question="Is this positive?", labels=("pos", "neg"))
    verdict = run(
        '{"label": "pos", "confidence": 0.8, "evidence": []}',
        document="great product",
        policy=policy,
    )
    assert verdict.label == "pos"
