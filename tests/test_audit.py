from __future__ import annotations

import json

from glassbox.audit import audit
from glassbox.backends import CallableBackend, StubBackend
from glassbox.prompts import DEFAULT_POLICY

DOC = "Your mailbox is over quota. Verify your password within 24 hours to avoid suspension."
TRIGGER = "Verify your password"  # exact case
CANDIDATES = ("Verify your password", "mailbox is over quota")


def case_brittle_model(system: str, user: str) -> str:
    # 'suspicious' iff the exact-case trigger is present; cites present candidates.
    document = user.split("DOCUMENT:\n", 1)[-1]
    suspicious = TRIGGER in document
    evidence = [phrase for phrase in CANDIDATES if phrase in document]
    return json.dumps(
        {
            "label": "suspicious" if suspicious else "benign",
            "confidence": 0.9 if suspicious else 0.3,
            "evidence": evidence,
        }
    )


def test_audit_runs_all_three_lenses():
    report = audit(DOC, CallableBackend(case_brittle_model), DEFAULT_POLICY)
    # Grounded (both cited phrases exist) and faithful (erasing them flips the
    # label), but NOT robust: the model is case-brittle, so lowercasing flips it.
    assert report.grounded
    assert report.faithful
    assert not report.robust
    assert report.checks == {"grounded": True, "faithful": True, "robust": False}
    assert report.passed == 2
    assert not report.trustworthy


def test_fully_trustworthy_verdict():
    # A model that reads meaning (case-insensitive) and whose decision genuinely
    # depends on the cited phrase passes all three lenses.
    def semantic_model(system, user):
        raw = user.split("DOCUMENT:\n", 1)[-1]
        suspicious = "verify your password" in raw.lower()  # case-insensitive decision
        evidence = ["Verify your password"] if "Verify your password" in raw else []
        return json.dumps(
            {
                "label": "suspicious" if suspicious else "benign",
                "confidence": 0.9 if suspicious else 0.3,
                "evidence": evidence,
            }
        )

    report = audit(DOC, CallableBackend(semantic_model), DEFAULT_POLICY)
    assert report.grounded and report.faithful and report.robust
    assert report.trustworthy
    assert report.passed == 3


def test_hallucinated_evidence_fails_grounding():
    model = StubBackend(
        '{"label": "suspicious", "confidence": 0.9, "evidence": ["wire the money now"]}'
    )
    report = audit(DOC, model, DEFAULT_POLICY)
    assert not report.grounded  # the cited phrase is not in the document
    assert not report.trustworthy
