"""When counterfactual faithfulness applies, and spending no calls when it doesn't.

Found on real runs: for a "benign" verdict, erasing the cited span almost never
flips the label (the reason is an *absence* of signals), so faithfulness failed
by construction; and when a model quoted the whole document, erasing it left an
empty input, and the "flip" that followed was an artifact. Both used to be
scored as faithfulness results.
"""

from __future__ import annotations

import json

import pytest

from glassbox.audit import audit
from glassbox.backends import CallableBackend
from glassbox.batch import run_batch, wilson_interval
from glassbox.consistency import Perturbation, check
from glassbox.faithfulness import analyze
from glassbox.prompts import DEFAULT_POLICY, make_policy
from glassbox.serialize import audit_dict, batch_dict
from glassbox.triage import triage

PHRASE = "Verify your password"


class Counting:
    """Suspicious iff PHRASE is present (cite it); benign otherwise, citing *benign_cite*."""

    def __init__(self, benign_cite: list[str] | None = None, empty_says: str = "benign"):
        self.calls: list[str] = []
        self.benign_cite = benign_cite or []
        self.empty_says = empty_says

    def generate(self, system: str, user: str) -> str:
        document = user.split("DOCUMENT:\n", 1)[-1]
        self.calls.append(document)
        if not document.strip():
            label = self.empty_says
            evidence: list[str] = []
        elif PHRASE.lower() in document.lower():  # case-robust
            label, evidence = "suspicious", [PHRASE] if PHRASE in document else []
        else:
            label = "benign"
            evidence = [q for q in self.benign_cite if q in document]
        return json.dumps({"label": label, "confidence": 0.8, "evidence": evidence})


# --- default-label verdicts --------------------------------------------------------


def test_a_benign_verdict_is_not_tested_for_faithfulness_and_costs_no_calls():
    backend = Counting(benign_cite=["Lunch is at noon"])
    verdict = triage("Lunch is at noon. See you.", backend, DEFAULT_POLICY)
    backend.calls.clear()
    report = analyze("Lunch is at noon. See you.", backend, DEFAULT_POLICY, verdict=verdict)
    assert not report.applicable
    assert "benign" in report.not_applicable
    assert backend.calls == []  # nothing erased, nothing re-asked


def test_audit_of_a_benign_verdict_checks_only_grounding_and_robustness():
    report = audit("Lunch is at noon. See you.", Counting(), DEFAULT_POLICY)
    assert report.faithful is None
    assert set(report.checks) == {"grounded", "robust"}
    # No quote is needed for "nothing here", and it invented none.
    assert report.grounded
    assert report.trustworthy
    data = audit_dict(report)
    assert data["faithful"] is None and data["faithfulness"]["applicable"] is False


def test_a_benign_verdict_that_quotes_something_unreal_is_still_ungrounded():
    def liar(system, user):
        return json.dumps({"label": "benign", "evidence": ["words that are not there"]})

    report = audit("Lunch is at noon.", CallableBackend(liar), DEFAULT_POLICY)
    assert not report.grounded and not report.trustworthy


def test_without_a_default_label_every_verdict_is_tested_as_before():
    policy = make_policy("suspicious,benign", "Threat?")  # no --default-label
    document = "Lunch is at noon. See you there."
    report = audit(document, Counting(benign_cite=["Lunch is at noon."]), policy)
    assert report.faithfulness.applicable
    assert report.faithful is False  # benign survives erasure: the old, strict reading
    assert "faithful" in report.checks


def test_the_suspicious_verdict_is_still_tested():
    report = audit(f"{PHRASE} at http://x.example now", Counting(), DEFAULT_POLICY)
    assert report.faithfulness.applicable and report.faithful is True


# --- evidence that is the whole document -------------------------------------------


def test_erasing_the_whole_document_is_not_a_faithfulness_result():
    # The model cites the entire document; with it gone the input is empty, and
    # this toy then says "benign" -- a flip, but one that means nothing.
    report = analyze(PHRASE, Counting(empty_says="benign"), DEFAULT_POLICY)
    assert report.comprehensive_flipped
    assert not report.applicable
    assert "whole document" in report.not_applicable
    full = audit(PHRASE + " ...", Counting(empty_says="benign"), DEFAULT_POLICY)
    assert full.faithful is None


# --- one model call per distinct text ------------------------------------------------


def test_a_single_citation_costs_one_erasure_call_not_two():
    backend = Counting()
    document = f"{PHRASE} now"
    verdict = triage(document, backend, DEFAULT_POLICY)
    backend.calls.clear()
    analyze(document, backend, DEFAULT_POLICY, verdict=verdict)
    assert len(backend.calls) == 1  # "erase it" and "erase all" are the same text


def test_edits_that_produce_the_same_text_share_one_call():
    backend = Counting()
    document = f"{PHRASE} now"
    original = triage(document, backend, DEFAULT_POLICY)
    backend.calls.clear()
    twice = (
        Perturbation("lower", "", str.lower),
        Perturbation("lower again", "", lambda t: t.lower()),
        Perturbation("no-op", "", lambda t: t),
    )
    report = check(document, backend, DEFAULT_POLICY, perturbations=twice, original=original)
    assert len(backend.calls) == 1
    assert [v.changed for v in report.variants] == [True, True, False]


# --- batch rates use each lens's own denominator -------------------------------------


DOCS = [f"{PHRASE} now", "Lunch is at noon.", "Agenda attached."]


def test_batch_faithful_rate_counts_only_verdicts_it_applies_to(tmp_path):
    report = run_batch(DOCS, Counting(), DEFAULT_POLICY)
    assert report.n == 3
    assert report.applicable("faithful") == 1
    assert report.faithful_rate == 1.0  # 1/1, not 1/3
    assert report.trustworthy_rate == 1.0
    data = batch_dict(report)
    assert data["faithful_applicable"] == 1
    assert [item["faithful"] for item in data["items"]] == [True, None, None]

    # The n/a survives a checkpoint round trip.
    path = tmp_path / "ckpt.jsonl"
    run_batch(DOCS, Counting(), DEFAULT_POLICY, checkpoint=path)
    resumed = run_batch(DOCS, Counting(), DEFAULT_POLICY, checkpoint=path)
    assert resumed.applicable("faithful") == 1 and resumed.faithful_rate == 1.0


def test_wilson_interval():
    low, high = wilson_interval(15, 40)
    assert low == pytest.approx(0.242, abs=0.001) and high == pytest.approx(0.530, abs=0.001)
    assert wilson_interval(0, 10)[0] == 0.0 and wilson_interval(10, 10)[1] == 1.0
    assert wilson_interval(0, 0) == (0.0, 1.0)
