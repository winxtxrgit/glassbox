from __future__ import annotations

import urllib.error

import pytest

from glassbox import backends
from glassbox.backends import BackendError, OllamaBackend, OpenAICompatBackend


def _raise(error: BaseException):
    def fake_urlopen(*_args, **_kwargs):
        raise error

    return fake_urlopen


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("timed out"),  # read timeout: raised raw by the socket
        urllib.error.URLError(TimeoutError("timed out")),  # connect timeout: wrapped
    ],
)
@pytest.mark.parametrize(
    "backend",
    [
        OllamaBackend(host="http://pi:11434", timeout=7),
        OpenAICompatBackend("https://api.example", "m", "key", timeout=7),
    ],
)
def test_timeout_is_reported_as_a_timeout_with_the_fix(monkeypatch, backend, error):
    monkeypatch.setattr(backends.urllib.request, "urlopen", _raise(error))
    with pytest.raises(BackendError) as exc:
        backend.generate("system", "user")
    message = str(exc.value)
    assert "within 7s" in message
    assert "--timeout" in message
    assert "unreadable" not in message


def test_unreachable_server_is_not_mislabelled_as_a_timeout(monkeypatch):
    refused = urllib.error.URLError(ConnectionRefusedError("refused"))
    monkeypatch.setattr(backends.urllib.request, "urlopen", _raise(refused))
    with pytest.raises(BackendError) as exc:
        OllamaBackend(host="http://pi:11434").generate("system", "user")
    assert "cannot reach Ollama at http://pi:11434" in str(exc.value)
    assert "--timeout" not in str(exc.value)


def test_backends_default_to_the_shared_timeout():
    assert OllamaBackend().timeout == backends.DEFAULT_TIMEOUT
    assert OpenAICompatBackend("https://x", "m", "k").timeout == backends.DEFAULT_TIMEOUT
