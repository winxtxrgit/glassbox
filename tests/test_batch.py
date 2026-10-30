from __future__ import annotations

import json

import pytest

from glassbox.backends import CallableBackend
from glassbox.batch import load_documents, run_batch
from glassbox.calibration import DatasetError
from glassbox.prompts import DEFAULT_POLICY


def case_brittle(system, user):
    # 'suspicious' iff the exact-case credential phrase is present; cite it.
    document = user.split("DOCUMENT:\n", 1)[-1]
    suspicious = "Verify your password" in document
    evidence = ["Verify your password"] if suspicious else []
    return json.dumps(
        {
            "label": "suspicious" if suspicious else "benign",
            "confidence": 0.9 if suspicious else 0.3,
            "evidence": evidence,
        }
    )


def test_run_batch_aggregates_pass_rates():
    docs = ["Verify your password now", "Account: Verify your password today"]
    report = run_batch(docs, CallableBackend(case_brittle), DEFAULT_POLICY)
    assert report.n == 2
    assert report.grounded_rate == 1.0  # both cite a real span
    assert report.faithful_rate == 1.0  # erasing it flips each verdict
    assert report.robust_rate == 0.0  # case-brittle: lowercasing flips both
    assert report.trustworthy_rate == 0.0  # so none clear all three


def test_run_batch_counts_unparseable_as_errors():
    def flaky(system, user):
        document = user.split("DOCUMENT:\n", 1)[-1]
        if "bad" in document:
            return "not json"
        return json.dumps({"label": "benign", "confidence": 0.5, "evidence": []})

    report = run_batch(["a good one", "a bad one"], CallableBackend(flaky), DEFAULT_POLICY)
    assert report.errors == 1
    assert report.n == 1  # only the parseable document is audited


def test_load_documents_round_trips(tmp_path):
    path = tmp_path / "corpus.jsonl"
    path.write_text(
        '{"document": "first"}\n'
        "\n"  # blank line tolerated
        '{"document": "second", "label": "ignored"}\n'
    )
    assert load_documents(path) == ["first", "second"]


def test_load_documents_rejects_missing_document(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"label": "x"}\n')
    with pytest.raises(DatasetError, match="line 1"):
        load_documents(path)


def test_load_documents_rejects_empty(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("\n\n")
    with pytest.raises(DatasetError, match="no documents"):
        load_documents(path)
