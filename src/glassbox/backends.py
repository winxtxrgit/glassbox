"""Where the text generation actually comes from.

A backend is anything that turns a (system, user) prompt pair into a raw string.
The default :class:`OllamaBackend` talks to a locally running Ollama server over
HTTP — no API key, no cost, and the open weights stay on your machine, which is
what later makes real interpretability possible. :class:`StubBackend` returns a
canned string so the whole pipeline (and the test suite) runs with nothing
installed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Protocol

# Small enough to sit on a 4 GB GPU, capable enough to follow the JSON contract.
DEFAULT_MODEL = "qwen2.5:3b"
DEFAULT_HOST = "http://localhost:11434"


class BackendError(RuntimeError):
    """The backend could not produce a response (unreachable, timeout, bad reply)."""


class Backend(Protocol):
    def generate(self, system: str, user: str) -> str:
        """Return the model's raw text reply to the system+user prompt."""
        ...


class StubBackend:
    """A backend that always returns the same canned reply — for demos and tests."""

    def __init__(self, response: str) -> None:
        self._response = response

    def generate(self, system: str, user: str) -> str:
        return self._response


class CallableBackend:
    """A backend backed by a function ``(system, user) -> str``.

    Unlike :class:`StubBackend` the reply can depend on the prompt, which is what
    the counterfactual tests need: they stand in a toy model whose decision
    actually changes when evidence is removed from the document.
    """

    def __init__(self, fn: Callable[[str, str], str]) -> None:
        self._fn = fn

    def generate(self, system: str, user: str) -> str:
        return self._fn(system, user)


class OllamaBackend:
    """Generate with a local `Ollama <https://ollama.com>`_ model.

    Requests JSON-formatted output and greedy decoding (``temperature=0``) so the
    verdict is as reproducible as a local model allows. Raises :class:`BackendError`
    with a pointer to the fix when the server isn't running or the model is missing.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout

    def generate(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0},
        }
        request = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode())
        except urllib.error.URLError as error:
            raise BackendError(
                f"cannot reach Ollama at {self.host} ({error}). "
                "Install it from https://ollama.com, then run "
                f"'ollama pull {self.model}' and make sure 'ollama serve' is up."
            ) from None
        except (OSError, json.JSONDecodeError) as error:
            raise BackendError(f"Ollama returned an unreadable reply: {error}") from None

        try:
            return body["message"]["content"]
        except (KeyError, TypeError) as error:
            raise BackendError(f"unexpected Ollama response shape: {error}") from None
