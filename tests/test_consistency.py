from __future__ import annotations

import json

from glassbox.backends import CallableBackend, StubBackend
from glassbox.consistency import Perturbation, check
from glassbox.prompts import DEFAULT_POLICY

DOC = "Your mailbox is over quota. Verify your password within 24 hours to avoid suspension."
TRIGGER = "Verify your password"  # exact case


def case_sensitive_model(system: str, user: str) -> str:
    # Only fires on the exact-case trigger, so lowercasing must flip the label.
    document = user.split("DOCUMENT:\n", 1)[-1]
    suspicious = TRIGGER in document
    return json.dumps(
        {
            "label": "suspicious" if suspicious else "benign",
            "confidence": 0.9 if suspicious else 0.3,
            "evidence": [],
        }
    )


def test_lowercasing_flip_is_caught_as_not_robust():
    report = check(DOC, CallableBackend(case_sensitive_model), DEFAULT_POLICY)
    assert report.original.label == "suspicious"
    assert not report.robust

    flipped = {v.perturbation.name for v in report.flips}
    assert "lowercase" in flipped
    assert report.stability < 1.0


def test_pad_boilerplate_keeps_a_content_based_verdict():
    report = check(DOC, CallableBackend(case_sensitive_model), DEFAULT_POLICY)
    pad = next(v for v in report.variants if v.perturbation.name == "pad boilerplate")
    assert pad.changed and pad.agreed  # surrounding a signal doesn't remove it


def test_robust_model_passes_all_edits():
    # A model that ignores case is stable under every meaning-preserving edit.
    def semantic_model(system: str, user: str) -> str:
        document = user.split("DOCUMENT:\n", 1)[-1]
        suspicious = "verify your password" in document.lower()
        return json.dumps({"label": "suspicious" if suspicious else "benign", "confidence": 0.9})

    report = check(DOC, CallableBackend(semantic_model), DEFAULT_POLICY)
    assert report.robust
    assert report.stability == 1.0
    assert report.flips == []


def test_noop_edits_are_excluded_from_the_score():
    # Against an all-lowercase, single-line document, lowercase and collapse
    # whitespace are no-ops and must not count toward stability.
    doc = "verify your password now"
    report = check(doc, CallableBackend(case_sensitive_model), DEFAULT_POLICY)
    names = {v.perturbation.name for v in report.variants if not v.changed}
    assert "lowercase" in names  # already lower case -> unchanged
    assert all(v.changed for v in report.effective)


def test_untestable_when_no_edit_changes_the_document():
    noop = Perturbation("noop", "identity edit", lambda t: t)
    report = check(
        DOC,
        StubBackend('{"label": "benign", "confidence": 0.5, "evidence": []}'),
        DEFAULT_POLICY,
        perturbations=(noop,),
    )
    assert not report.testable
    assert report.robust is False


def test_custom_perturbation():
    shout = Perturbation("shout", "append an exclamation", lambda t: t + "!!!")
    report = check(
        DOC,
        CallableBackend(case_sensitive_model),
        DEFAULT_POLICY,
        perturbations=(shout,),
    )
    assert len(report.variants) == 1
    assert report.variants[0].perturbation.name == "shout"
    assert report.robust  # adding '!!!' keeps the exact-case trigger intact
