"""Long runs on slow hardware: skipping dead requests, resuming, reasoning models.

These cover what broke during the first Raspberry Pi benchmark: one timed-out
request (the Pi browned out and rebooted) threw away 100 minutes of audits, and a
reasoning model (qwen3) spent most of its time thinking before answering.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from glassbox import backends
from glassbox.backends import BackendError, CallableBackend, OllamaBackend
from glassbox.batch import CheckpointError, run_batch
from glassbox.calibration import Example, evaluate
from glassbox.prompts import DEFAULT_POLICY
from glassbox.serialize import batch_dict
from glassbox.triage import triage

PHRASE = "Verify your password"


def toy(system: str, user: str) -> str:
    """Suspicious iff the exact phrase is present, citing it (faithful, case-brittle)."""
    document = user.split("DOCUMENT:\n", 1)[-1]
    suspicious = PHRASE in document
    return json.dumps(
        {
            "label": "suspicious" if suspicious else "benign",
            "confidence": 0.9 if suspicious else 0.3,
            "evidence": [PHRASE] if suspicious else [],
        }
    )


class Flaky:
    """Times out on any document containing a marker; counts calls."""

    def __init__(self, dead_marker: str = "DEAD") -> None:
        self.dead_marker = dead_marker
        self.calls = 0
        self.model = "toy"

    def generate(self, system: str, user: str) -> str:
        self.calls += 1
        if self.dead_marker in user:
            raise BackendError("no reply from http://pi:11434 within 900s.")
        return toy(system, user)


DOCS = [f"{PHRASE} now", "DEAD letter", f"Account: {PHRASE} today"]


# --- skipping dead requests -----------------------------------------------------


def test_backend_error_aborts_by_default():
    with pytest.raises(BackendError):
        run_batch(DOCS, Flaky(), DEFAULT_POLICY)


def test_backend_error_is_skipped_and_counted_when_asked():
    report = run_batch(DOCS, Flaky(), DEFAULT_POLICY, skip_backend_errors=True)
    assert report.n == 2  # the two live documents were still audited
    assert report.errors == 1
    assert report.backend_errors == 1
    assert report.parse_errors == 0
    assert report.grounded_rate == 1.0
    data = batch_dict(report)
    assert data["backend_errors"] == 1
    assert data["items"][1]["backend_error"] is True


def test_consecutive_backend_errors_mean_the_server_is_down():
    docs = ["DEAD 1", "DEAD 2", "DEAD 3", f"{PHRASE} never reached"]
    backend = Flaky()
    with pytest.raises(BackendError):
        run_batch(docs, backend, DEFAULT_POLICY, skip_backend_errors=True)
    assert backend.calls == 3  # stopped instead of timing out on every document


def test_calibration_can_skip_backend_errors():
    dataset = [
        Example(document=f"{PHRASE} now", gold="suspicious"),
        Example(document="DEAD letter", gold="benign"),
    ]
    with pytest.raises(BackendError):
        evaluate(dataset, Flaky(), DEFAULT_POLICY)
    report = evaluate(dataset, Flaky(), DEFAULT_POLICY, skip_backend_errors=True)
    assert report.n == 1 and report.errors == 1
    assert report.accuracy == 1.0


def test_progress_is_reported_per_document():
    seen = []
    run_batch(
        DOCS,
        Flaky(),
        DEFAULT_POLICY,
        skip_backend_errors=True,
        progress=lambda i, total, item: seen.append((i, total, item.backend_error)),
    )
    assert seen == [(1, 3, False), (2, 3, True), (3, 3, False)]


# --- checkpoint / resume ----------------------------------------------------------


def test_checkpoint_resumes_without_calling_the_model_again(tmp_path):
    path = tmp_path / "ckpt.jsonl"
    first = Flaky()
    report1 = run_batch(DOCS, first, DEFAULT_POLICY, skip_backend_errors=True, checkpoint=path)

    second = Flaky()
    report2 = run_batch(DOCS, second, DEFAULT_POLICY, skip_backend_errors=True, checkpoint=path)
    # Only the document whose request died is retried; the rest come from disk.
    assert second.calls == 1
    assert report2.n == report1.n == 2
    assert report2.trustworthy_rate == report1.trustworthy_rate
    assert report2.robust_rate == report1.robust_rate


def test_a_backend_error_is_retried_on_resume_not_remembered(tmp_path):
    path = tmp_path / "ckpt.jsonl"
    run_batch(DOCS, Flaky(), DEFAULT_POLICY, skip_backend_errors=True, checkpoint=path)
    # The server is healthy now: the previously dead document gets audited.
    recovered = run_batch(DOCS, Flaky(dead_marker="\x00"), DEFAULT_POLICY, checkpoint=path)
    assert recovered.errors == 0 and recovered.n == 3


def test_unparseable_replies_are_remembered(tmp_path):
    path = tmp_path / "ckpt.jsonl"
    calls = []

    def junk(system, user):
        calls.append(1)
        return "not json"

    run_batch(["a"], CallableBackend(junk), DEFAULT_POLICY, checkpoint=path)
    report = run_batch(["a"], CallableBackend(junk), DEFAULT_POLICY, checkpoint=path)
    assert len(calls) == 1  # deterministic failure: not asked again
    assert report.parse_errors == 1


def test_checkpoint_for_another_model_is_refused(tmp_path):
    path = tmp_path / "ckpt.jsonl"
    run_batch(DOCS[:1], Flaky(), DEFAULT_POLICY, checkpoint=path)
    other = Flaky()
    other.model = "a-different-model"
    with pytest.raises(CheckpointError):
        run_batch(DOCS[:1], other, DEFAULT_POLICY, checkpoint=path)


def test_a_torn_last_line_is_ignored(tmp_path):
    path = tmp_path / "ckpt.jsonl"
    run_batch(DOCS[:1], Flaky(), DEFAULT_POLICY, checkpoint=path)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"key": "abc", "err')  # killed mid-write
    backend = Flaky()
    report = run_batch(DOCS[:1], backend, DEFAULT_POLICY, checkpoint=path)
    assert backend.calls == 0 and report.n == 1


# --- reasoning ("thinking") models ---------------------------------------------


def test_a_think_block_before_the_json_is_ignored():
    reply = "<think>The user wants JSON. The phrase is suspicious.</think>\n" + toy("", PHRASE)
    verdict = triage(PHRASE, CallableBackend(lambda s, u: reply), DEFAULT_POLICY)
    assert verdict.label == "suspicious" and verdict.grounded


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _ok(content: str = '{"label": "benign"}'):
    return _Response(json.dumps({"message": {"content": content}}).encode())


def _http_error(code: int, message: str) -> urllib.error.HTTPError:
    body = io.BytesIO(json.dumps({"error": message}).encode())
    return urllib.error.HTTPError("http://pi/api/chat", code, "err", {}, body)  # type: ignore[arg-type]


def test_ollama_asks_reasoning_models_not_to_think(monkeypatch):
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(json.loads(request.data))
        return _ok()

    monkeypatch.setattr(backends.urllib.request, "urlopen", fake_urlopen)
    OllamaBackend(model="qwen3:1.7b").generate("s", "u")
    assert sent[0]["think"] is False
    OllamaBackend(model="qwen3:1.7b", think=None).generate("s", "u")
    assert "think" not in sent[1]


def test_ollama_drops_the_think_flag_if_the_server_rejects_it(monkeypatch):
    sent = []

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data)
        sent.append(payload)
        if "think" in payload:
            raise _http_error(400, "invalid option: think")
        return _ok()

    monkeypatch.setattr(backends.urllib.request, "urlopen", fake_urlopen)
    backend = OllamaBackend()
    assert backend.generate("s", "u") == '{"label": "benign"}'
    backend.generate("s", "u")
    assert ["think" in p for p in sent] == [True, False, False]  # remembered


def test_ollama_http_error_says_what_ollama_said(monkeypatch):
    def fake_urlopen(request, timeout):
        raise _http_error(404, 'model "nope:1b" not found, try pulling it first')

    monkeypatch.setattr(backends.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(BackendError) as exc:
        OllamaBackend(model="nope:1b", host="http://pi:11434").generate("s", "u")
    message = str(exc.value)
    assert "HTTP 404" in message and "not found" in message
    assert "ollama pull nope:1b" in message
    assert "cannot reach" not in message  # it answered: it is reachable
