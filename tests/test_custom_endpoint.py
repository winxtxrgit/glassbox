import io
import json

import pytest

from glassbox import cli
from glassbox.backends import BackendError


@pytest.mark.parametrize("key", ["", "test-only-token"])
def test_cli_uses_custom_endpoint_without_contacting_a_model(tmp_path, monkeypatch, capsys, key):
    doc = tmp_path / "message.txt"
    doc.write_text("Lunch at noon.", encoding="utf-8")
    monkeypatch.delenv("OPENAI_COMPAT_API_KEY", raising=False)
    if key:
        monkeypatch.setenv("OPENAI_COMPAT_API_KEY", key)
    requests = []

    def urlopen(request, timeout):
        requests.append(request)
        verdict = json.dumps({"label": "benign", "confidence": 0.9, "evidence": ["Lunch at noon."]})
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": verdict}}]}).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    assert (
        cli.main(
            [
                "triage",
                str(doc),
                "--backend",
                "openai-compatible",
                "--base-url",
                "http://localhost:8080/v1/",
                "--model",
                "local-model",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["label"] == "benign"
    assert len(requests) == 1
    request = requests[0]
    assert request.full_url == "http://localhost:8080/v1/chat/completions"
    assert json.loads(request.data)["model"] == "local-model"
    assert request.get_header("Authorization") == (f"Bearer {key}" if key else None)


@pytest.mark.parametrize(
    "args, message",
    [
        (["--backend", "openai-compatible", "--model", "local"], "--base-url"),
        (["--backend", "openai-compatible", "--base-url", "http://localhost/v1"], "--model"),
        (["--base-url", "http://localhost/v1"], "--host"),
    ],
)
def test_custom_endpoint_configuration_errors(args, message):
    parsed = cli.build_parser().parse_args(["triage", "unused.txt", *args])
    with pytest.raises(BackendError, match=message):
        cli._make_backend(parsed)


def test_existing_provider_can_override_its_endpoint(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-only-token")
    parsed = cli.build_parser().parse_args(
        [
            "triage",
            "unused.txt",
            "--backend",
            "groq",
            "--base-url",
            "http://localhost/v1",
        ]
    )
    backend = cli._make_backend(parsed)
    assert backend.base_url == "http://localhost/v1"
    assert backend.api_key == "test-only-token"
