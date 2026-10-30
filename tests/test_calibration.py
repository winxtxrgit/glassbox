from __future__ import annotations

import json

import pytest

from glassbox.backends import CallableBackend
from glassbox.calibration import (
    CalibrationReport,
    DatasetError,
    Example,
    ExampleResult,
    evaluate,
    load_dataset,
)
from glassbox.prompts import DEFAULT_POLICY


def report_of(pairs, bins=10):
    # pairs: list of (confidence, correct)
    results = [ExampleResult("s", "s", conf, correct) for conf, correct in pairs]
    return CalibrationReport(results=results, bins=bins)


def test_accuracy_mean_confidence_and_gap():
    r = report_of([(0.9, True), (0.9, True), (0.9, False), (0.9, False)])
    assert r.accuracy == 0.5
    assert r.mean_confidence == 0.9
    assert r.gap == pytest.approx(0.4)
    assert r.overconfident
    assert not r.underconfident


def test_single_bin_ece_and_brier():
    # All confidence 0.9, right half the time: one bin, ECE = |0.9 - 0.5| = 0.4.
    r = report_of([(0.9, True), (0.9, True), (0.9, False), (0.9, False)])
    assert r.ece == pytest.approx(0.4)
    # Brier = mean((0.9-1)^2, (0.9-1)^2, (0.9-0)^2, (0.9-0)^2)
    assert r.brier == pytest.approx((0.01 + 0.01 + 0.81 + 0.81) / 4)


def test_perfect_calibration_has_zero_ece():
    # Confidence equals accuracy within each bin -> ECE 0.
    r = report_of([(1.0, True), (1.0, True), (0.0, False), (0.0, False)])
    assert r.ece == pytest.approx(0.0)
    assert not r.overconfident and not r.underconfident


def test_underconfident_model():
    r = report_of([(0.2, True), (0.2, True), (0.2, True), (0.2, True)])
    assert r.accuracy == 1.0
    assert r.underconfident
    assert r.gap == pytest.approx(-0.8)


def test_ece_aggregates_across_bins():
    # Bin 0.9-1.0: one correct (acc 1.0, conf 0.9 -> gap 0.1).
    # Bin 0.5-0.6: one wrong   (acc 0.0, conf 0.5 -> gap 0.5).
    # Equal weight -> ECE = (0.1 + 0.5) / 2 = 0.3.
    r = report_of([(0.9, True), (0.5, False)])
    assert r.ece == pytest.approx(0.3)


def test_reliability_bins_are_sorted_and_scoped():
    r = report_of([(0.9, True), (0.9, False), (0.2, True)])
    bins = r.reliability()
    assert [b.count for b in bins] == [1, 2]  # 0.2 band then 0.9 band
    assert bins[0].low == pytest.approx(0.2)
    assert bins[1].accuracy == pytest.approx(0.5)


def test_evaluate_scores_predictions_against_gold():
    # Toy model: 'suspicious' iff 'bad' in the document, confidence 0.8.
    def model(system, user):
        document = user.split("DOCUMENT:\n", 1)[-1]
        label = "suspicious" if "bad" in document else "benign"
        return json.dumps({"label": label, "confidence": 0.8, "evidence": []})

    dataset = [
        Example("this is bad", "suspicious"),  # correct
        Example("all good here", "benign"),  # correct
        Example("nothing bad at all", "benign"),  # 'bad' substring -> wrong
    ]
    report = evaluate(dataset, CallableBackend(model), DEFAULT_POLICY)
    assert report.n == 3
    assert report.accuracy == pytest.approx(2 / 3)
    assert report.errors == 0


def test_evaluate_counts_unparseable_examples_as_errors():
    def broken(system, user):
        return "not json at all"

    report = evaluate([Example("x", "benign")], CallableBackend(broken), DEFAULT_POLICY)
    assert report.n == 0
    assert report.errors == 1


def test_load_dataset_round_trips(tmp_path):
    path = tmp_path / "data.jsonl"
    path.write_text(
        '{"document": "hello", "label": "benign"}\n'
        "\n"  # blank line tolerated
        '{"document": "verify password", "label": "suspicious"}\n'
    )
    data = load_dataset(path)
    assert [e.gold for e in data] == ["benign", "suspicious"]


def test_load_dataset_rejects_malformed_lines(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"document": "x"}\n')  # missing "label"
    with pytest.raises(DatasetError, match="line 1"):
        load_dataset(path)


def test_load_dataset_rejects_empty(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("\n\n")
    with pytest.raises(DatasetError, match="no examples"):
        load_dataset(path)
