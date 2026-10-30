from __future__ import annotations

from glassbox.cli import main


def test_demo_runs_and_flags_the_fabricated_citation(capsys):
    # The built-in demo must exit 1 (ungrounded) and name the invented evidence.
    assert main(["demo"]) == 0  # demo itself succeeds as a command
    out = capsys.readouterr().out
    assert "UNFAITHFUL" in out
    assert "inventing its own evidence" in out


def test_triage_grounded_document_exits_zero(tmp_path, capsys, monkeypatch):
    doc = tmp_path / "mail.txt"
    doc.write_text("Verify your password now or lose access.")

    import glassbox.cli as cli

    reply = (
        '{"label": "suspicious", "confidence": 0.9, "rationale": "phishing",'
        ' "evidence": ["Verify your password now"]}'
    )
    # Swap the real Ollama backend for a canned one so the CLI path is testable.
    monkeypatch.setattr(cli, "OllamaBackend", lambda **kwargs: cli.StubBackend(reply))

    assert main(["triage", str(doc)]) == 0
    assert "grounding:  OK" in capsys.readouterr().out


def test_triage_missing_file_errors(capsys):
    assert main(["triage", "does_not_exist.txt"]) == 2
    assert "glassbox:" in capsys.readouterr().err


def test_probe_demo_separates_load_bearing_from_decorative(capsys):
    assert main(["probe", "--demo"]) == 0  # the demo verdict is load-bearing
    out = capsys.readouterr().out
    assert "[load-bearing]" in out
    assert "[decorative" in out
    assert "faithful:   YES" in out


def test_probe_without_file_or_demo_errors(capsys):
    assert main(["probe"]) == 2
    assert "give a file to probe, or pass --demo" in capsys.readouterr().err


def test_probe_catches_post_hoc_rationalization(tmp_path, capsys, monkeypatch):
    doc = tmp_path / "note.txt"
    doc.write_text("The weather is mild and the meeting is at noon.")

    import glassbox.cli as cli

    # A model that always says "suspicious" and cites a real span — but the
    # verdict can never flip, so erasing the evidence proves it was decorative.
    reply = '{"label": "suspicious", "confidence": 0.9, "evidence": ["meeting is at noon"]}'
    monkeypatch.setattr(cli, "OllamaBackend", lambda **kwargs: cli.StubBackend(reply))

    assert main(["probe", str(doc)]) == 1  # unfaithful -> non-zero exit
    assert "faithful:   NO" in capsys.readouterr().out


def test_stress_demo_detects_surface_sensitivity(capsys):
    assert main(["stress", "--demo"]) == 1  # the toy model is case-brittle
    out = capsys.readouterr().out
    assert "[FLIP  ]" in out
    assert "lowercase" in out
    assert "robust:     NO" in out


def test_stress_without_file_or_demo_errors(capsys):
    assert main(["stress"]) == 2
    assert "give a file to stress-test, or pass --demo" in capsys.readouterr().err


def test_calibrate_demo_flags_overconfidence(capsys):
    assert main(["calibrate", "--demo"]) == 1  # overconfident -> non-zero
    out = capsys.readouterr().out
    assert "accuracy:        0.60" in out
    assert "ECE:             0.30" in out
    assert "OVERCONFIDENT" in out


def test_calibrate_without_dataset_or_demo_errors(capsys):
    assert main(["calibrate"]) == 2
    assert "give a labelled dataset, or pass --demo" in capsys.readouterr().err


def test_calibrate_reads_a_dataset_file(tmp_path, capsys, monkeypatch):
    data = tmp_path / "d.jsonl"
    data.write_text(
        '{"document": "verify your password", "label": "suspicious"}\n'
        '{"document": "lunch at noon", "label": "benign"}\n'
    )
    import glassbox.cli as cli

    # A perfectly-calibrated toy: right every time and says so (confidence 1.0).
    def model(system, user):
        import json

        document = user.split("DOCUMENT:\n", 1)[-1]
        label = "suspicious" if "password" in document else "benign"
        return json.dumps({"label": label, "confidence": 1.0, "evidence": []})

    monkeypatch.setattr(cli, "OllamaBackend", lambda **kwargs: cli.CallableBackend(model))

    assert main(["calibrate", str(data)]) == 0  # well calibrated -> exit 0
    assert "WELL CALIBRATED" in capsys.readouterr().out
