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

# OpenAI-compatible providers with a free tier, for when you'd rather not run a
# model locally. Each: (base URL, a sensible default model, the env var holding
# the API key). OpenRouter's default is a free Qwen; Groq's is a fast free Llama.
PROVIDERS: dict[str, tuple[str, str, str]] = {
    "openrouter": (
        "https://openrouter.ai/api/v1",
        "qwen/qwen-2.5-72b-instruct:free",
        "OPENROUTER_API_KEY",
    ),
    "groq": (
        "https://api.groq.com/openai/v1",
        "llama-3.3-70b-versatile",
        "GROQ_API_KEY",
    ),
}


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


class OpenAICompatBackend:
    """Generate via any OpenAI-compatible ``/chat/completions`` endpoint.

    Works with Groq, OpenRouter, Together, and friends — anything that speaks the
    OpenAI chat API. Requests JSON-object output and greedy decoding. The API key
    is read from the caller (typically an environment variable the user set); this
    class never prompts for or stores it beyond the instance.
    """

    def __init__(self, base_url: str, model: str, api_key: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def generate(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:200]
            raise BackendError(f"{self.base_url} returned HTTP {error.code}: {detail}") from None
        except urllib.error.URLError as error:
            raise BackendError(f"cannot reach {self.base_url}: {error}") from None
        except (OSError, json.JSONDecodeError) as error:
            raise BackendError(f"unreadable reply from {self.base_url}: {error}") from None

        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise BackendError(f"unexpected response shape from {self.base_url}: {error}") from None
