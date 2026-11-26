"""Auditing your own classification task: --labels / --question / --default-label."""

from __future__ import annotations

import json

import pytest

from glassbox import cli
from glassbox.audit import audit
from glassbox.backends import CallableBackend
from glassbox.prompts import DEFAULT_POLICY, build_prompts, make_policy
from glassbox.triage import TriageError, triage

SPAM = make_policy("spam, ham", "Is this message unsolicited bulk advertising?", "ham")


def spam_model(system: str, user: str) -> str:
    """Spam iff 'FREE' appears (exact case), citing it."""
    document = user.split("DOCUMENT:\n", 1)[-1]
    spam = "FREE" in document
    return json.dumps(
        {
            "label": "Spam" if spam else "ham",
            "confidence": 0.9,
            "evidence": ["FREE"] if spam else [],
        }
    )


# --- make_policy ----------------------------------------------------------------


def test_no_arguments_is_the_default_policy():
    assert make_policy() == DEFAULT_POLICY


def test_custom_labels_are_parsed_and_normalized():
    assert SPAM.labels == ("spam", "ham")
    assert SPAM.default_label == "ham"
    assert SPAM.question == "Is this message unsolicited bulk advertising?"
    # The default label is matched case-insensitively but stored as spelled in --labels.
    assert make_policy(["Spam", "Ham"], "q?", "ham").default_label == "Ham"


def test_question_alone_keeps_the_default_labels():
    policy = make_policy(question="Is this a scam?")
    assert policy.labels == DEFAULT_POLICY.labels
    assert policy.default_label == "benign"
    assert policy.question == "Is this a scam?"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"labels": "spam", "question": "q"}, "at least two"),
        ({"labels": "spam,,ham", "question": "q"}, "empty label"),
        ({"labels": "spam,Spam", "question": "q"}, "distinct"),
        ({"labels": "spam,ham"}, "needs --question"),
        ({"labels": "spam,ham", "question": "  "}, "needs --question"),
        ({"labels": "spam,ham", "question": "q", "default_label": "eggs"}, "not one of"),
        ({"default_label": "ham"}, "needs --labels"),
        ({"question": " "}, "must not be empty"),
        ({"labels": "spam,ham", "question": "q", "prompt_style": "few-shot"}, "few-shot"),
        ({"prompt_style": "chatty"}, "unknown prompt style"),
    ],
)
def test_bad_policies_are_rejected_with_a_useful_message(kwargs, message):
    with pytest.raises(ValueError, match=message):
        make_policy(**kwargs)


def test_few_shot_still_works_with_the_default_labels():
    system, _ = build_prompts("x", make_policy(prompt_style="few-shot"))
    assert "EXAMPLE 1" in system


def test_prompt_lists_the_custom_labels_and_question():
    system, user = build_prompts("Buy now", SPAM)
    assert "spam, ham" in system
    assert "suspicious" not in system
    assert user.startswith("QUESTION: Is this message unsolicited bulk advertising?")


# --- the lenses run unchanged on a custom task -----------------------------------


def test_triage_matches_custom_labels_case_insensitively():
    verdict = triage("Get it FREE today", CallableBackend(spam_model), SPAM)
    assert verdict.label == "spam" and verdict.grounded


def test_a_label_outside_the_custom_set_is_an_error():
    with pytest.raises(TriageError, match="not one of"):
        triage("x", CallableBackend(lambda s, u: '{"label": "suspicious"}'), SPAM)


def test_audit_runs_all_lenses_on_a_custom_task():
    report = audit("Get it FREE today", CallableBackend(spam_model), SPAM)
    assert report.grounded and report.faithful  # erasing FREE flips spam -> ham
    assert not report.robust  # the toy is case-brittle: lowercasing loses FREE


# --- CLI ------------------------------------------------------------------------


def test_cli_rejects_a_bad_policy_before_touching_the_model(capsys):
    assert cli.main(["triage", "doc.txt", "--labels", "spam"]) == 2
    assert "at least two" in capsys.readouterr().err


def test_cli_rejects_custom_labels_with_demo(capsys):
    assert cli.main(["audit", "--demo", "--labels", "a,b", "--question", "q"]) == 2
    assert "--demo" in capsys.readouterr().err


def test_cli_builds_the_policy_from_flags():
    args = cli.build_parser().parse_args(
        [
            "batch",
            "c.jsonl",
            "--labels",
            "spam,ham",
            "--question",
            "Spam?",
            "--default-label",
            "ham",
        ]
    )
    policy = cli._build_policy(args)
    assert policy.labels == ("spam", "ham") and policy.default_label == "ham"


def test_calibrate_refuses_a_dataset_with_labels_outside_the_policy(tmp_path, capsys, monkeypatch):
    data = tmp_path / "d.jsonl"
    data.write_text('{"document": "x", "label": "spam"}\n', encoding="utf-8")
    monkeypatch.setattr(cli, "_make_backend", lambda args: CallableBackend(spam_model))
    assert cli.main(["calibrate", str(data)]) == 2  # default labels: suspicious/benign
    assert "['spam']" in capsys.readouterr().err
    args = ["calibrate", str(data), "--labels", "spam,ham", "--question", "Spam?"]
    assert cli.main(args) in (0, 1)
