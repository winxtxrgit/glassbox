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
