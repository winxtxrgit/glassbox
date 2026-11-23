from __future__ import annotations

import http.client
import json
import threading
import time
from contextlib import contextmanager

import pytest

from glassbox.backends import BackendError, CallableBackend
from glassbox.server import AuditService, QueueFull, is_loopback, make_server

DOC = "Your account is locked. Verify your password now or lose access."
REPLY = json.dumps(
    {
        "label": "suspicious",
        "confidence": 0.9,
        "rationale": "credential phishing",
        "evidence": ["Verify your password now"],
    }
)


def _model(system: str, user: str) -> str:
    # Load-bearing toy: suspicious only while the cited phrase is present.
    if "Verify your password now" in user:
        return REPLY
    return json.dumps({"label": "benign", "confidence": 0.6, "rationale": "x", "evidence": []})


@contextmanager
def running(backend=None, token=None, max_queue=20):
    service = AuditService(backend or CallableBackend(_model), model="toy", max_queue=max_queue)
    server = make_server(service, "127.0.0.1", 0, token)
    thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
    thread.start()
    try:
        yield server.server_address[1], service
    finally:
        server.shutdown()
        server.server_close()
        service.stop()


def request(port, method, path, body=None, headers=None, raw=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    hdrs = dict(headers or {})
    payload = raw
    if body is not None:
        payload = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=payload, headers=hdrs)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp, data


def wait_done(port, job_id, headers=None, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp, data = request(port, "GET", f"/api/jobs/{job_id}", headers=headers)
        job = json.loads(data)
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_audit_job_round_trip():
    with running() as (port, _):
        resp, data = request(port, "POST", "/api/jobs", {"verb": "audit", "document": DOC})
        assert resp.status == 202
        job_id = json.loads(data)["id"]
        assert resp.getheader("Location") == f"/api/jobs/{job_id}"

        job = wait_done(port, job_id)
        assert job["status"] == "done"
        result = job["result"]
        assert result["verdict"]["label"] == "suspicious"
        assert result["grounded"] and result["faithful"]
        assert {"faithfulness", "consistency", "trustworthy"} <= result.keys()
        assert job["elapsed_s"] is not None


def test_triage_job_returns_a_verdict():
    with running() as (port, _):
        _, data = request(port, "POST", "/api/jobs", {"verb": "triage", "document": DOC})
        job = wait_done(port, json.loads(data)["id"])
        assert job["result"]["grounded"] is True
        assert job["result"]["citations"][0]["quote"] == "Verify your password now"


def test_verb_defaults_to_audit_and_jobs_are_listed():
    with running() as (port, _):
        _, data = request(port, "POST", "/api/jobs", {"document": DOC})
        job = wait_done(port, json.loads(data)["id"])
        assert job["verb"] == "audit"
        _, listing = request(port, "GET", "/api/jobs")
        jobs = json.loads(listing)["jobs"]
        assert jobs[0]["id"] == job["id"]
        assert "document" not in jobs[0] and "result" not in jobs[0]


def test_backend_failure_marks_the_job_and_keeps_the_worker_alive():
    calls = {"n": 0}

    def flaky(system, user):
        calls["n"] += 1
        if calls["n"] == 1:
            raise BackendError("no reply within 1s")
        return _model(system, user)

    with running(CallableBackend(flaky)) as (port, _):
        _, d1 = request(port, "POST", "/api/jobs", {"verb": "triage", "document": DOC})
        first = wait_done(port, json.loads(d1)["id"])
        assert first["status"] == "error"
        assert "no reply" in first["error"]

        _, d2 = request(port, "POST", "/api/jobs", {"verb": "triage", "document": DOC})
        assert wait_done(port, json.loads(d2)["id"])["status"] == "done"


def test_unexpected_exception_does_not_kill_the_worker(capsys):
    calls = {"n": 0}

    def buggy(system, user):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ZeroDivisionError
        return _model(system, user)

    with running(CallableBackend(buggy)) as (port, _):
        _, d1 = request(port, "POST", "/api/jobs", {"verb": "triage", "document": DOC})
        assert wait_done(port, json.loads(d1)["id"])["error"].startswith("internal error")
        _, d2 = request(port, "POST", "/api/jobs", {"verb": "triage", "document": DOC})
        assert wait_done(port, json.loads(d2)["id"])["status"] == "done"


@pytest.mark.parametrize(
    "body, fragment",
    [
        ({"verb": "rm -rf", "document": DOC}, "verb must be"),
        ({"verb": "audit", "document": "   "}, "non-empty"),
        ({"verb": "audit", "document": 42}, "non-empty"),
        ({"verb": "audit"}, "non-empty"),
        ({"document": DOC, "prompt": "evil"}, "prompt must be"),
        (["not", "an", "object"], "JSON object"),
    ],
)
def test_bad_requests_are_rejected(body, fragment):
    with running() as (port, _):
        resp, data = request(port, "POST", "/api/jobs", body)
        assert resp.status == 400
        assert fragment in json.loads(data)["error"]


def test_invalid_json_and_wrong_content_type():
    with running() as (port, _):
        resp, _ = request(
            port, "POST", "/api/jobs", raw=b"{nope", headers={"Content-Type": "application/json"}
        )
        assert resp.status == 400
        # A cross-site <form> can only send form/text types -- refuse them.
        resp, _ = request(
            port, "POST", "/api/jobs", raw=b"document=x", headers={"Content-Type": "text/plain"}
        )
        assert resp.status == 415


def test_oversized_body_is_refused():
    with running() as (port, _):
        resp, _ = request(port, "POST", "/api/jobs", {"document": "x" * (70 * 1024)})
        assert resp.status == 413


def test_unknown_routes_and_jobs_are_404():
    with running() as (port, _):
        assert request(port, "GET", "/etc/passwd")[0].status == 404
        assert request(port, "GET", "/api/jobs/doesnotexist")[0].status == 404
        assert request(port, "POST", "/elsewhere", {"document": DOC})[0].status == 404


def test_token_is_required_on_the_api_when_configured():
    with running(token="s3cret") as (port, _):
        assert request(port, "POST", "/api/jobs", {"document": DOC})[0].status == 401
        wrong = {"Authorization": "Bearer nope"}
        assert request(port, "POST", "/api/jobs", {"document": DOC}, wrong)[0].status == 401
        assert request(port, "GET", "/api/jobs")[0].status == 401

        good = {"Authorization": "Bearer s3cret"}
        resp, data = request(port, "POST", "/api/jobs", {"document": DOC}, good)
        assert resp.status == 202
        assert wait_done(port, json.loads(data)["id"], good)["status"] == "done"

        # The page and health check stay open (they reveal no job data).
        assert request(port, "GET", "/")[0].status == 200
        health = json.loads(request(port, "GET", "/healthz")[1])
        assert health["auth"] is True


def test_queue_is_bounded():
    gate = threading.Event()

    def blocked(system, user):
        gate.wait(5)
        return _model(system, user)

    with running(CallableBackend(blocked), max_queue=2) as (port, _):
        statuses = [
            request(port, "POST", "/api/jobs", {"verb": "triage", "document": DOC})[0].status
            for _ in range(3)
        ]
        gate.set()
    assert statuses == [202, 202, 429]


def test_queued_jobs_report_their_position():
    gate = threading.Event()

    def blocked(system, user):
        gate.wait(5)
        return _model(system, user)

    service = AuditService(CallableBackend(blocked), max_queue=5)
    try:
        first = service.submit("triage", DOC)
        second = service.submit("triage", DOC)
        deadline = time.monotonic() + 5
        while service.get(first.id)["status"] != "running" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert service.get(second.id)["position"] == 1
        gate.set()
    finally:
        gate.set()
        service.stop()


def test_finished_jobs_are_evicted_but_pending_ones_never():
    service = AuditService(CallableBackend(_model), max_queue=1, keep=2)
    try:
        ids = []
        for _ in range(4):
            job = service.submit("triage", DOC)
            ids.append(job.id)
            deadline = time.monotonic() + 5
            while service.get(job.id)["status"] != "done" and time.monotonic() < deadline:
                time.sleep(0.01)
        assert service.get(ids[0]) is None
        assert service.get(ids[-1]) is not None
        with pytest.raises(QueueFull):
            gate = threading.Event()
            service.backend = CallableBackend(lambda s, u: gate.wait(5) or REPLY)
            service.submit("triage", DOC)
            service.submit("triage", DOC)
            service.submit("triage", DOC)
    finally:
        service.stop(timeout=0.1)


def test_page_is_locked_down_against_injected_markup():
    with running() as (port, _):
        resp, data = request(port, "GET", "/")
        page = data.decode()
        csp = resp.getheader("Content-Security-Policy")
        assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
        nonce = csp.split("'nonce-", 1)[1].split("'", 1)[0]
        assert f'<script nonce="{nonce}">' in page
        assert "__NONCE__" not in page
        # Untrusted text (quotes from the document) must only ever hit textContent.
        assert "innerHTML" not in page and "insertAdjacentHTML" not in page
        assert resp.getheader("X-Content-Type-Options") == "nosniff"
        assert "Access-Control-Allow-Origin" not in dict(resp.getheaders())


def test_healthz_reports_model_and_queue():
    with running() as (port, _):
        health = json.loads(request(port, "GET", "/healthz")[1])
        assert health == {
            "ok": True,
            "version": health["version"],
            "model": "toy",
            "backend_reachable": None,  # not an Ollama backend
            "queue": 0,
            "auth": False,
        }


def test_submitted_document_is_dropped_after_the_job():
    service = AuditService(CallableBackend(_model))
    try:
        job = service.submit("triage", DOC)
        deadline = time.monotonic() + 5
        while service.get(job.id)["status"] != "done" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert job.document == ""
        assert service.get(job.id)["chars"] == len(DOC)
    finally:
        service.stop()


def test_is_loopback():
    assert is_loopback("127.0.0.1") and is_loopback("localhost") and is_loopback("::1")
    assert not is_loopback("0.0.0.0") and not is_loopback("192.168.1.62")


def test_cli_serve_rejects_bad_port(capsys):
    from glassbox.cli import main

    assert main(["serve", "--demo", "--port", "70000"]) == 2
    assert "--port" in capsys.readouterr().err
