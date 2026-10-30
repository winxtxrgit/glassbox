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
