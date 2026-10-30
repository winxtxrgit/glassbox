from __future__ import annotations

import json

from glassbox.audit import audit
from glassbox.backends import CallableBackend
from glassbox.batch import run_batch
from glassbox.calibration import CalibrationReport, ExampleResult
from glassbox.prompts import DEFAULT_POLICY
from glassbox.serialize import audit_dict, batch_dict, calibration_dict

DOC = "Your mailbox is over quota. Verify your password within 24 hours to avoid suspension."


def toy(system, user):
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


def test_audit_dict_is_json_serializable_with_the_headline_flags():
    report = audit(DOC, CallableBackend(toy), DEFAULT_POLICY)
    data = audit_dict(report)
    json.dumps(data)  # must not raise — every value is a JSON primitive
    assert set(data) >= {"verdict", "grounded", "faithful", "robust", "trustworthy", "checks"}
    assert data["grounded"] is True
    assert isinstance(data["verdict"]["citations"], list)
    assert data["verdict"]["citations"][0]["verified"] is True


def test_batch_dict_round_trips():
    report = run_batch([DOC, DOC], CallableBackend(toy), DEFAULT_POLICY)
    data = batch_dict(report)
    json.dumps(data)
    assert data["n"] == 2
    assert data["grounded_rate"] == 1.0
    assert len(data["items"]) == 2


def test_calibration_dict_round_trips():
    report = CalibrationReport(results=[ExampleResult("s", "s", 0.9, True)])
    data = calibration_dict(report)
    json.dumps(data)
    assert data["accuracy"] == 1.0
    assert "reliability" in data
