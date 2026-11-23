"""``glassbox serve``: the audit lenses behind a small HTTP API and web page.

Built for a model that lives on a slow, always-on box (a Raspberry Pi on the
LAN): an audit makes many model calls and can take minutes there, so requests
are **jobs** — ``POST /api/jobs`` queues one and returns at once, the client then
polls ``GET /api/jobs/<id>``. One worker runs jobs in order, because a small
machine can only run one generation at a time anyway.

Standard library only, so the core install stays dependency-free.

Trust boundary
--------------
* Binds to ``127.0.0.1`` unless told otherwise; exposing it is an explicit choice.
* If ``GLASSBOX_TOKEN`` is set, every ``/api/*`` call needs
  ``Authorization: Bearer <token>`` (compared in constant time).
* Only text is accepted — never a path — so nothing on disk is reachable.
* Bodies are capped, the queue is bounded, and finished jobs are evicted.
* Results quote the submitted document, which is untrusted (it may be the very
  phishing email being judged). The page renders everything with
  ``textContent`` under a nonce-based CSP, so quoted markup can never run.
* POST requires ``Content-Type: application/json`` and no CORS headers are sent,
  so another site's page cannot drive the API from a viewer's browser.
* It is not hardened for the open internet: keep it on a trusted LAN.
"""

from __future__ import annotations

import hmac
import json
import secrets
import sys
import threading
import time
import traceback
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .audit import audit
from .backends import Backend, BackendError
from .prompts import DEFAULT_POLICY, PROMPT_STYLES
from .serialize import audit_dict, verdict_dict
from .triage import TriageError, triage

TOKEN_ENV = "GLASSBOX_TOKEN"
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8787
MAX_BODY_BYTES = 64 * 1024
_DRAIN_LIMIT = 1024 * 1024
DEFAULT_MAX_QUEUE = 20
DEFAULT_KEEP = 100
VERBS = ("audit", "triage")

_PENDING = ("queued", "running")


class QueueFull(Exception):
    """Too many jobs are already waiting."""


@dataclass
class Job:
    id: str
    verb: str
    prompt: str
    document: str = field(repr=False)
    status: str = "queued"  # queued -> running -> done | error
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    result: dict | None = None
    error: str | None = None
    chars: int = 0

    def __post_init__(self) -> None:
        self.chars = len(self.document)

    def summary(self) -> dict:
        elapsed = None
        if self.started is not None:
            elapsed = round((self.finished or time.time()) - self.started, 2)
        return {
            "id": self.id,
            "verb": self.verb,
            "prompt": self.prompt,
            "status": self.status,
            "created": self.created,
            "elapsed_s": elapsed,
            "chars": self.chars,
        }


class AuditService:
    """A bounded job queue with one worker thread that runs the lenses."""

    def __init__(
        self,
        backend: Backend,
        *,
        model: str = "",
        default_prompt: str = "plain",
        max_queue: int = DEFAULT_MAX_QUEUE,
        keep: int = DEFAULT_KEEP,
    ) -> None:
        self.backend = backend
        self.model = model
        self.default_prompt = default_prompt
        self.max_queue = max_queue
        self.keep = max(keep, max_queue + 1)
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._stopping = False
        self._worker = threading.Thread(target=self._run, name="glassbox-worker", daemon=True)
        self._worker.start()

    # -- public API ---------------------------------------------------------
    def submit(self, verb: str, document: str, prompt: str | None = None) -> Job:
        job = Job(
            id=secrets.token_urlsafe(9),
            verb=verb,
            prompt=prompt or self.default_prompt,
            document=document,
        )
        with self._lock:
            if self._pending_count() >= self.max_queue:
                raise QueueFull
            self._jobs[job.id] = job
            self._evict()
            self._wake.notify()
        return job

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            data = job.summary()
            if job.status == "queued":
                data["position"] = self._position(job)
            if job.result is not None:
                data["result"] = job.result
            if job.error is not None:
                data["error"] = job.error
            return data

    def recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            jobs = list(self._jobs.values())[-limit:]
            return [j.summary() for j in reversed(jobs)]

    def queue_length(self) -> int:
        with self._lock:
            return self._pending_count()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._stopping = True
            self._wake.notify_all()
        self._worker.join(timeout)

    # -- internals ----------------------------------------------------------
    def _pending_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j.status in _PENDING)

    def _position(self, job: Job) -> int:
        """1 = next to run."""
        ahead = 0
        for other in self._jobs.values():
            if other is job:
                break
            if other.status == "queued":
                ahead += 1
        return ahead + 1

    def _evict(self) -> None:
        # Drop the oldest *finished* jobs; never a queued or running one.
        excess = len(self._jobs) - self.keep
        if excess <= 0:
            return
        for job_id in [k for k, j in self._jobs.items() if j.status not in _PENDING][:excess]:
            del self._jobs[job_id]

    def _next(self) -> Job | None:
        with self._lock:
            while not self._stopping:
                for job in self._jobs.values():
                    if job.status == "queued":
                        job.status = "running"
                        job.started = time.time()
                        return job
                self._wake.wait()
            return None

    def _run(self) -> None:
        while (job := self._next()) is not None:
            result, error = None, None
            try:
                result = self._execute(job)
            except (BackendError, TriageError) as exc:
                error = str(exc)
            except Exception:  # keep the worker alive whatever a job does
                traceback.print_exc(file=sys.stderr)
                error = "internal error while running the job (see the server log)"
            with self._lock:
                job.result, job.error = result, error
                job.status = "error" if error else "done"
                job.finished = time.time()
                job.document = ""  # don't keep the submitted text around longer than needed

    def _execute(self, job: Job) -> dict:
        policy = (
            DEFAULT_POLICY
            if job.prompt == "plain"
            else replace(DEFAULT_POLICY, prompt_style=job.prompt)
        )
        if job.verb == "triage":
            return verdict_dict(triage(job.document, self.backend, policy))
        return audit_dict(audit(job.document, self.backend, policy))


def _backend_reachable(backend: Backend) -> bool | None:
    """Cheap liveness check for an Ollama backend; None when not applicable."""
    host = getattr(backend, "host", None)
    if not isinstance(host, str):
        return None
    try:
        with urllib.request.urlopen(f"{host}/api/version", timeout=3):
            return True
    except OSError:
        return False


class _Handler(BaseHTTPRequestHandler):
    server_version = f"glassbox/{__version__}"
    sys_version = ""
    service: AuditService
    token: str | None

    # -- plumbing -----------------------------------------------------------
    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        # Method + path + status only: never log bodies (they are user documents).
        sys.stderr.write(f"{self.address_string()} {format % args}\n")

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: object, extra: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", extra)

    def _error(self, status: int, message: str, extra: dict | None = None) -> None:
        self._json(status, {"error": message}, extra)

    def _authorized(self) -> bool:
        if not self.token:
            return True
        header = self.headers.get("Authorization", "")
        scheme, _, supplied = header.partition(" ")
        ok = scheme.lower() == "bearer" and hmac.compare_digest(
            supplied.strip().encode(), self.token.encode()
        )
        if not ok:
            self._error(
                HTTPStatus.UNAUTHORIZED,
                "missing or wrong token",
                {"WWW-Authenticate": 'Bearer realm="glassbox"'},
            )
        return ok

    # -- routes -------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        path = self.path.split("?", 1)[0]
        if path == "/":
            nonce = secrets.token_urlsafe(16)
            csp = (
                "default-src 'none'; "
                f"script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                "connect-src 'self'; img-src 'self' data:; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            )
            page = _PAGE.replace("__NONCE__", nonce).replace("__VERSION__", __version__)
            self._send(
                HTTPStatus.OK,
                page.encode("utf-8"),
                "text/html; charset=utf-8",
                {"Content-Security-Policy": csp, "X-Frame-Options": "DENY"},
            )
        elif path == "/healthz":
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "version": __version__,
                    "model": self.service.model,
                    "backend_reachable": _backend_reachable(self.service.backend),
                    "queue": self.service.queue_length(),
                    "auth": bool(self.token),
                },
            )
        elif path == "/api/jobs":
            if self._authorized():
                self._json(HTTPStatus.OK, {"jobs": self.service.recent()})
        elif path.startswith("/api/jobs/"):
            if self._authorized():
                job = self.service.get(path.removeprefix("/api/jobs/"))
                if job is None:
                    self._error(HTTPStatus.NOT_FOUND, "no such job (finished jobs expire)")
                else:
                    self._json(HTTPStatus.OK, job)
        else:
            self._error(HTTPStatus.NOT_FOUND, "not found")

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def _drain(self, length: int) -> None:
        # Discard an unread request body before replying. On Windows, closing a
        # socket with data still in the receive buffer makes the OS send a RST, so
        # the client sees a connection reset instead of our status code. Drain a
        # bounded amount; past that just close the connection.
        if length <= 0:
            return
        if length > _DRAIN_LIMIT:
            self.close_connection = True
            return
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 64 * 1024))
            if not chunk:
                break
            remaining -= len(chunk)

    def _declared_length(self) -> int:
        try:
            return int(self.headers.get("Content-Length", ""))
        except ValueError:
            return -1

    def do_POST(self) -> None:  # noqa: N802
        length = self._declared_length()
        if self.path.split("?", 1)[0] != "/api/jobs":
            self._drain(length)
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        if not self._authorized():
            self._drain(length)
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._drain(length)
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "send Content-Type: application/json")
            return
        if length < 0:
            self._error(HTTPStatus.LENGTH_REQUIRED, "Content-Length required")
            return
        if length > MAX_BODY_BYTES:
            self._drain(length)
            self._error(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"body over {MAX_BODY_BYTES // 1024} KB"
            )
            return
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "body is not valid UTF-8 JSON")
            return
        if not isinstance(data, dict):
            self._error(HTTPStatus.BAD_REQUEST, "body must be a JSON object")
            return

        verb = data.get("verb", "audit")
        prompt = data.get("prompt")
        document = data.get("document")
        if verb not in VERBS:
            self._error(HTTPStatus.BAD_REQUEST, f"verb must be one of {list(VERBS)}")
            return
        if prompt is not None and prompt not in PROMPT_STYLES:
            self._error(HTTPStatus.BAD_REQUEST, f"prompt must be one of {list(PROMPT_STYLES)}")
            return
        if not isinstance(document, str) or not document.strip():
            self._error(HTTPStatus.BAD_REQUEST, "document must be a non-empty string")
            return

        try:
            job = self.service.submit(verb, document, prompt)
        except QueueFull:
            self._error(
                HTTPStatus.TOO_MANY_REQUESTS,
                "queue is full, try again later",
                {"Retry-After": "30"},
            )
            return
        self._json(
            HTTPStatus.ACCEPTED,
            {"id": job.id, "status": job.status, "poll": f"/api/jobs/{job.id}"},
            {"Location": f"/api/jobs/{job.id}"},
        )


class GlassboxServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(
    service: AuditService,
    bind: str = DEFAULT_BIND,
    port: int = DEFAULT_PORT,
    token: str | None = None,
) -> GlassboxServer:
    """Build (but don't start) the HTTP server. ``port=0`` picks a free port."""
    handler = type("Handler", (_Handler,), {"service": service, "token": token or None})
    return GlassboxServer((bind, port), handler)


def is_loopback(bind: str) -> bool:
    return bind in ("127.0.0.1", "::1", "localhost") or bind.startswith("127.")


# The whole UI: one page, no external requests, every untrusted string rendered
# via textContent (never innerHTML). __NONCE__ is filled per response for the CSP.
_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>glassbox</title>
<style nonce="__NONCE__">
:root { --bg:#f7f7f5; --fg:#1d1d1b; --muted:#6b6b66; --card:#fff; --line:#e2e2dc;
        --ok:#1f7a3d; --bad:#b3261e; --warn:#8a5a00; --accent:#2f5bd3; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#151514; --fg:#ececea; --muted:#a0a09a; --card:#1f1f1d; --line:#33332f;
          --ok:#5cc583; --bad:#ff7a70; --warn:#e0b050; --accent:#8aa8ff; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font:16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 20px 16px 48px; }
h1 { font-size: 1.4rem; margin: 0 0 4px; }
.sub { color: var(--muted); margin: 0 0 20px; font-size: .95rem; }
.card { background: var(--card); border:1px solid var(--line); border-radius: 12px;
        padding: 16px; margin-bottom: 16px; }
textarea { width:100%; min-height: 180px; font: 14px/1.45 ui-monospace, Consolas, monospace;
           padding:10px; border:1px solid var(--line); border-radius:8px;
           background:var(--bg); color:var(--fg); resize: vertical; }
.row { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-top:12px; }
select, input, button { font: inherit; padding: 8px 10px; border-radius: 8px;
           border:1px solid var(--line); background:var(--bg); color:var(--fg); }
input { flex: 1 1 160px; min-width: 0; }
button { background: var(--accent); color: #fff; border: none; cursor: pointer;
         padding: 9px 18px; }
button:disabled { opacity: .55; cursor: default; }
.status { color: var(--muted); margin-top: 10px; min-height: 1.5em; }
.badges { display:flex; flex-wrap:wrap; gap:8px; margin: 8px 0 12px; }
.badge { padding: 3px 10px; border-radius: 999px; font-size: .85rem; font-weight: 600;
         border:1px solid currentColor; }
.ok { color: var(--ok); } .bad { color: var(--bad); } .warn { color: var(--warn); }
.label { font-size: 1.25rem; font-weight: 700; }
.muted { color: var(--muted); }
ul { padding-left: 1.1rem; margin: 6px 0; }
li { margin: 4px 0; overflow-wrap: anywhere; }
q { font-family: ui-monospace, Consolas, monospace; font-size: .92em; }
h2 { font-size: 1rem; margin: 16px 0 4px; }
</style>
</head>
<body>
<main>
  <h1>glassbox</h1>
  <p class="sub">Paste a document. glassbox checks whether the model's verdict is grounded,
  faithful and robust. <span id="health" class="muted"></span></p>
  <div class="card">
    <textarea id="doc" placeholder="Paste an email or document here..."></textarea>
    <div class="row">
      <select id="verb" aria-label="check">
        <option value="audit">audit (all three lenses, slower)</option>
        <option value="triage">triage (grounding only, fast)</option>
      </select>
      <select id="prompt" aria-label="prompt style">
        <option value="">default prompt</option>
        <option value="plain">plain</option>
        <option value="few-shot">few-shot</option>
      </select>
    </div>
    <div class="row">
      <input id="token" type="password" autocomplete="off" placeholder="token (if required)">
      <button id="go">Run</button>
    </div>
    <div id="status" class="status" role="status"></div>
  </div>
  <div id="out"></div>
  <p class="muted">glassbox __VERSION__</p>
</main>
<script nonce="__NONCE__">
"use strict";
const $ = (id) => document.getElementById(id);
function store(key, value) { try { localStorage.setItem(key, value); } catch (e) {} }
function load(key) { try { return localStorage.getItem(key) || ""; } catch (e) { return ""; } }
$("token").value = load("glassbox-token");

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}
function badge(name, pass) {
  return el("span", (pass ? "PASS " : "FAIL ") + name, "badge " + (pass ? "ok" : "bad"));
}
function headers() {
  const h = { "Content-Type": "application/json" };
  const t = $("token").value.trim();
  if (t) h["Authorization"] = "Bearer " + t;
  return h;
}

function renderVerdict(card, v) {
  const head = el("div");
  head.append(el("span", v.label, "label"),
              el("span", "  confidence " + Number(v.confidence).toFixed(2), "muted"));
  card.append(head, el("p", v.rationale));
  card.append(el("h2", "Evidence"));
  const list = el("ul");
  for (const c of v.citations) {
    const li = el("li");
    li.append(el("span", c.verified ? "[found] " : "[NOT IN DOCUMENT] ",
                 c.verified ? "ok" : "bad"), el("q", c.quote));
    list.append(li);
  }
  if (!v.citations.length) list.append(el("li", "(no evidence cited)", "muted"));
  card.append(list);
}

function render(job) {
  const out = $("out");
  out.replaceChildren();
  const card = el("div", null, "card");
  const r = job.result;
  if (job.verb === "triage") {
    const b = el("div", null, "badges");
    b.append(badge("grounded", r.grounded));
    card.append(b);
    renderVerdict(card, r);
  } else {
    const b = el("div", null, "badges");
    b.append(badge("grounded", r.grounded), badge("faithful", r.faithful),
             badge("robust", r.robust));
    const verdictLine = r.trustworthy ? "TRUSTWORTHY" : "NOT TRUSTWORTHY";
    const total = Object.keys(r.checks).length;
    card.append(el("div", verdictLine + " (" + r.passed + "/" + total + " lenses)",
                   r.trustworthy ? "label ok" : "label bad"), b);
    renderVerdict(card, r.verdict);
    const f = r.faithfulness;
    card.append(el("h2", "Faithfulness: does the verdict depend on its evidence?"));
    const fl = el("ul");
    for (const p of f.probes) {
      const li = el("li");
      li.append(el("span", p.flipped ? "[load-bearing] " : "[decorative] ",
                   p.flipped ? "ok" : "warn"), el("q", p.quote));
      fl.append(li);
    }
    if (!f.probes.length) fl.append(el("li", "(nothing testable)", "muted"));
    fl.append(el("li", "erase all evidence: label " +
                 (f.comprehensive_flipped ? "changes" : "stays " + f.comprehensive_label),
                 f.comprehensive_flipped ? "ok" : "warn"));
    card.append(fl);
    const c = r.consistency;
    card.append(el("h2", "Consistency: same meaning, same verdict?"));
    const cl = el("ul");
    for (const v of c.variants) {
      if (!v.changed) continue;
      cl.append(el("li", (v.agreed ? "[stable] " : "[FLIPPED] ") + v.name + " -> " + v.label,
                   v.agreed ? "ok" : "bad"));
    }
    card.append(cl);
  }
  card.append(el("p", "took " + job.elapsed_s + " s", "muted"));
  out.append(card);
}

async function poll(id) {
  for (;;) {
    const res = await fetch("/api/jobs/" + encodeURIComponent(id), { headers: headers() });
    const job = await res.json();
    if (!res.ok) throw new Error(job.error || res.statusText);
    if (job.status === "done") { render(job); return; }
    if (job.status === "error") throw new Error(job.error);
    $("status").textContent = job.status === "queued"
      ? "queued, position " + job.position + "..."
      : "running (" + (job.elapsed_s || 0) + " s) - small machines can take a few minutes...";
    await new Promise((r) => setTimeout(r, 2000));
  }
}

$("go").addEventListener("click", async () => {
  const doc = $("doc").value;
  if (!doc.trim()) { $("status").textContent = "paste a document first"; return; }
  store("glassbox-token", $("token").value.trim());
  $("go").disabled = true;
  $("out").replaceChildren();
  $("status").textContent = "submitting...";
  try {
    const body = { verb: $("verb").value, document: doc };
    if ($("prompt").value) body.prompt = $("prompt").value;
    const res = await fetch("/api/jobs", { method: "POST", headers: headers(),
                                           body: JSON.stringify(body) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    await poll(data.id);
    $("status").textContent = "";
  } catch (e) {
    $("status").textContent = "error: " + e.message;
  } finally {
    $("go").disabled = false;
  }
});

fetch("/healthz").then((r) => r.json()).then((h) => {
  const up = h.backend_reachable === false ? " - MODEL SERVER UNREACHABLE" : "";
  $("health").textContent = "Model: " + (h.model || "?") + (h.auth ? " - token required" : "") + up;
}).catch(() => {});
</script>
</body>
</html>
"""
