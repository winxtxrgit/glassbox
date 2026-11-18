import json

import pytest

from glassbox import consistency
from glassbox.backends import CallableBackend


@pytest.mark.parametrize(
    "text, expected",
    [
        ("This request is urgent.", "This request is time-sensitive."),
        ("Urgent: reply today.", "Time-sensitive: reply today."),
        ("URGENT request", "TIME-SENSITIVE request"),
        ("This is time-sensitive.", "This is urgent."),
        ("Time-sensitive: URGENT", "Urgent: TIME-SENSITIVE"),
        ("urgently insurgent time-sensitively", "urgently insurgent time-sensitively"),
        ("Please review when convenient. 请查收。", "Please review when convenient. 请查收。"),
    ],
)
def test_synonyms_use_whole_terms_and_preserve_case(text, expected):
    assert consistency._swap_synonyms(text) == expected


def test_synonym_surface_dependency_is_detected():
    def model(system, user):
        document = user.split("DOCUMENT:\n", 1)[-1]
        return json.dumps(
            {"label": "suspicious" if "urgent" in document.lower() else "benign", "confidence": 0.9}
        )

    report = consistency.check("An urgent request", CallableBackend(model))
    assert "swap synonyms" in {variant.perturbation.name for variant in report.flips}


def test_synonym_noop_reuses_original_verdict():
    calls = []

    def model(system, user):
        calls.append(user)
        return '{"label": "benign", "confidence": 0.9}'

    perturbation = next(p for p in consistency.DEFAULT_PERTURBATIONS if p.name == "swap synonyms")
    report = consistency.check(
        "No rush, thanks.", CallableBackend(model), perturbations=(perturbation,)
    )
    assert not report.effective
    assert len(calls) == 1
