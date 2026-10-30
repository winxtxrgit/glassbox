from __future__ import annotations

import json

from glassbox.backends import CallableBackend, StubBackend
from glassbox.faithfulness import ablate, analyze
from glassbox.prompts import DEFAULT_POLICY

# A toy model: "suspicious" iff the credential phrase survives in the document,
# and it cites whichever of two candidate phrases are currently present. So one
# citation is load-bearing (removing it flips the label) and the other is not.
DOC = "Your mailbox is over quota. Verify your password within 24 hours to avoid suspension."
TRIGGER = "Verify your password"
CANDIDATES = ("Verify your password", "mailbox is over quota")


def toy_model(system: str, user: str) -> str:
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


def test_ablate_removes_spans_and_keeps_offsets_valid():
    # Remove "mailbox" and "quota"; deleting right-to-left keeps earlier offsets sane.
    source = "Your mailbox is over quota."
    assert source[5:12] == "mailbox"
    assert source[21:26] == "quota"
    assert ablate(source, [(5, 12), (21, 26)]) == "Your  is over ."


def test_ablate_ignores_out_of_range_spans():
    assert ablate("abc", [(0, 99)]) == "abc"


def test_load_bearing_citation_flips_decorative_one_does_not():
    report = analyze(DOC, CallableBackend(toy_model), DEFAULT_POLICY)
    assert report.verdict.label == "suspicious"
    assert report.testable

    necessary = [c.quote for c in report.necessary]
    decorative = [c.quote for c in report.decorative]
    assert necessary == ["Verify your password"]
    assert decorative == ["mailbox is over quota"]


def test_comprehensive_erasure_flips_so_verdict_is_load_bearing():
    report = analyze(DOC, CallableBackend(toy_model), DEFAULT_POLICY)
    assert report.comprehensive_flipped
    assert report.comprehensive_label == "benign"
    assert report.load_bearing


def test_post_hoc_rationalization_is_caught_as_unfaithful():
    # A model that always says "suspicious" no matter what the document contains,
    # yet cites a real span. Erasing the span can't change the label => unfaithful.
    always = StubBackend(
        '{"label": "suspicious", "confidence": 0.9, "evidence": ["mailbox is over quota"]}'
    )
    report = analyze(DOC, always, DEFAULT_POLICY)
    assert report.testable  # the citation was grounded...
    assert not report.load_bearing  # ...but the verdict never depended on it
    assert report.necessary == []


def test_untestable_when_no_grounded_evidence():
    none_grounded = StubBackend(
        '{"label": "benign", "confidence": 0.5, "evidence": ["not in the document at all"]}'
    )
    report = analyze(DOC, none_grounded, DEFAULT_POLICY)
    assert not report.testable
    assert not report.load_bearing
