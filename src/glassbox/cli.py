"""Command line: ``audit``, ``triage``, ``probe``, ``stress``, ``calibrate``, ``batch``, ``lens``.

``audit`` runs grounding + faithfulness + consistency on one verdict and reports
whether it clears all three. ``triage`` classifies and grounds (phase 1); ``probe``
erases each cited span to test faithfulness (phase 2); ``stress`` re-decides after
meaning-preserving edits to test consistency (phase 3); ``calibrate`` checks the
confidence numbers over a labelled set (phase 4); ``lens`` opens a small local
model up (phase 5, needs the ``whitebox`` extra). ``batch`` audits a whole corpus.

Models run locally on Ollama by default, or on a free API tier with
``--backend openrouter`` / ``--backend groq`` (reads an API key from the
environment). Every analysis verb takes ``--json`` for machine-readable output and
a ``--demo`` that runs a canned backend with no model installed.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sys
import threading

from . import __version__
from .audit import AuditReport, audit
from .backends import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT,
    PROVIDERS,
    BackendError,
    CallableBackend,
    OllamaBackend,
    OpenAICompatBackend,
    StubBackend,
)
from .batch import CheckpointError, load_documents, run_batch
from .calibration import (
    CalibrationReport,
    DatasetError,
    Example,
    evaluate,
    load_dataset,
)
from .consistency import ConsistencyReport, check
from .faithfulness import FaithfulnessReport, analyze
from .prompts import DEFAULT_POLICY, PROMPT_STYLES, make_policy
from .schema import Policy, Verdict
from .serialize import (
    audit_dict,
    batch_dict,
    calibration_dict,
    consistency_dict,
    faithfulness_dict,
    lens_dict,
    verdict_dict,
)
from .server import (
    DEFAULT_BIND,
    DEFAULT_MAX_QUEUE,
    DEFAULT_PORT,
    TOKEN_ENV,
    AuditService,
    is_loopback,
    make_server,
)
from .triage import TriageError, triage
from .whitebox import LensResult
from .whitebox import analyze as lens_analyze
from .whitebox import available as whitebox_available


def _ascii(text: str) -> str:
    """Make model-derived token text safe to print on any console encoding."""
    return text.encode("ascii", "backslashreplace").decode("ascii")


# ----------------------------------------------------------------------------
# Demo fixtures — canned documents and toy models so every verb runs with no
# model installed.
# ----------------------------------------------------------------------------

_DEMO_DOCUMENT = """\
From: it-support@secure-login.example
Subject: Action required: your account will be closed

Your mailbox is over quota. Verify your password within 24 hours at
http://secure-login.example/verify or your account will be permanently closed.
"""

_DEMO_REPLY = """\
{
  "label": "suspicious",
  "confidence": 0.93,
  "rationale": "Phishing: false urgency plus a credential-harvesting link on a look-alike domain.",
  "evidence": [
    "Verify your password within 24 hours",
    "click here to claim your reward"
  ]
}"""

# A document and a case-brittle toy model: 'suspicious' iff the exact-case
# credential phrase is present; it cites whichever candidate phrases are present.
_PROBE_DEMO_DOCUMENT = (
    "Your mailbox is over quota. Verify your password within 24 hours to avoid suspension."
)
_PROBE_DEMO_TRIGGER = "Verify your password"
_PROBE_DEMO_CANDIDATES = ("Verify your password", "mailbox is over quota")

_BATCH_DEMO_DOCS = (
    "Your mailbox is over quota. Verify your password within 24 hours.",
    "Account alert: Verify your password now to keep access.",
    "Reminder from IT: Verify your password before Friday.",
)


def _probe_demo_model(system: str, user: str) -> str:
    document = user.split("DOCUMENT:\n", 1)[-1]
    suspicious = _PROBE_DEMO_TRIGGER in document
    evidence = [phrase for phrase in _PROBE_DEMO_CANDIDATES if phrase in document]
    return json.dumps(
        {
            "label": "suspicious" if suspicious else "benign",
            "confidence": 0.9 if suspicious else 0.3,
            "rationale": "toy keyword model",
            "evidence": evidence,
        }
    )


# For `calibrate --demo`: a labelled set and a keyword model that stamps 0.90 on
# everything it flags. It gets 6 of 10 right — overconfident.
_CALIB_DEMO_KEYWORDS = ("password", "verify", "urgent", "wire")
_CALIB_DEMO_DATA: tuple[tuple[str, str], ...] = (
    ("Please verify your password immediately.", "suspicious"),
    ("Urgent action required on your account.", "suspicious"),
    ("Complete the wire transfer by 5pm.", "suspicious"),
    ("Reset the password using this link.", "suspicious"),
    ("Lunch at the usual place tomorrow?", "benign"),
    ("The quarterly report is attached.", "benign"),
    ("Your account shows unusual sign-in activity.", "suspicious"),  # no keyword -> missed
    ("Click the secure link to restore access.", "suspicious"),  # no keyword -> missed
    ("Please verify the delivery address below.", "benign"),  # 'verify' -> false alarm
    ("Can you confirm the wire diagram in the spec?", "benign"),  # 'wire' -> false alarm
)


def _calib_demo_model(system: str, user: str) -> str:
    document = user.split("DOCUMENT:\n", 1)[-1].lower()
    suspicious = any(keyword in document for keyword in _CALIB_DEMO_KEYWORDS)
    return json.dumps(
        {
            "label": "suspicious" if suspicious else "benign",
            "confidence": 0.9,  # the tell: the same confidence regardless of the document
            "evidence": [],
        }
    )


# ----------------------------------------------------------------------------
# Shared plumbing: backend selection, output format, document resolution.
# ----------------------------------------------------------------------------


# Point every command at a remote Ollama (e.g. a Pi on the LAN) without typing --host.
HOST_ENV = "GLASSBOX_HOST"


def _rate(value: str) -> float:
    """A pass-rate threshold in 0-1, rejected by argparse with a clear message otherwise."""
    try:
        rate = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid rate {value!r}: expected a number in 0-1"
        ) from None
    if not 0.0 <= rate <= 1.0:
        raise argparse.ArgumentTypeError(f"rate must be between 0 and 1, got {value}")
    return rate


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from None
    if not seconds > 0:  # also rejects nan
        raise argparse.ArgumentTypeError(f"must be > 0, got {value!r}")
    return seconds


def _add_model_args(
    p: argparse.ArgumentParser, *, demo_help: str | None = None, json_flag: bool = True
) -> None:
    if demo_help:
        p.add_argument("--demo", action="store_true", help=demo_help)
    p.add_argument(
        "--backend",
        choices=("ollama", "openai-compatible", *PROVIDERS),
        default="ollama",
        help="where the model runs: ollama (local, default), or a free API tier "
        "(openrouter/groq), or openai-compatible with --base-url and --model",
    )
    p.add_argument("--model", default=None, help="model name (default depends on backend)")
    p.add_argument(
        "--host",
        default=os.environ.get(HOST_ENV) or DEFAULT_HOST,
        help=f"Ollama server URL, e.g. a Raspberry Pi on your LAN (default: ${HOST_ENV} "
        "if set, else %(default)s)",
    )
    p.add_argument(
        "--base-url", help="OpenAI-compatible API URL including /v1; use --host for Ollama"
    )
    p.add_argument(
        "--timeout",
        type=_positive_seconds,
        default=DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help="how long to wait for each model reply (default: %(default)gs); raise it "
        "for slow hardware such as CPU-only machines or a Raspberry Pi",
    )
    if json_flag:
        p.add_argument(
            "--json", dest="as_json", action="store_true", help="emit JSON instead of text"
        )
    p.add_argument(
        "--prompt",
        choices=PROMPT_STYLES,
        default="plain",
        help="instruction style: plain (default), or few-shot which adds worked "
        "verbatim-quoting examples (helps a model that paraphrases its evidence)",
    )
    task = p.add_argument_group(
        "your own task (default: security triage, suspicious/benign)",
        "Audit any classification, e.g. --labels spam,ham --question 'Is this spam?' "
        "--default-label ham",
    )
    task.add_argument(
        "--labels", metavar="A,B,...", help="comma-separated labels the model must choose from"
    )
    task.add_argument("--question", metavar="TEXT", help="the question the model answers")
    task.add_argument(
        "--default-label",
        metavar="LABEL",
        help="the label for 'nothing relevant here' (like benign or ham): its verdicts rest "
        "on an absence, so there is no evidence to erase for faithfulness",
    )


def _build_policy(args) -> Policy:
    """Validate the task flags into a Policy (ValueError on a bad combination)."""
    custom = any(
        getattr(args, name, None) is not None for name in ("labels", "question", "default_label")
    )
    if custom and getattr(args, "demo", False):
        raise ValueError(
            "--demo runs a built-in toy model with the default labels; "
            "drop --labels/--question/--default-label"
        )
    return make_policy(
        labels=getattr(args, "labels", None),
        question=getattr(args, "question", None),
        default_label=getattr(args, "default_label", None),
        prompt_style=getattr(args, "prompt", "plain"),
    )


def _policy(args) -> Policy:
    """The triage policy for this invocation (built and validated in ``main``)."""
    policy = getattr(args, "policy", None)
    return policy if policy is not None else _build_policy(args)


def _make_backend(args):
    """Build the backend named by --backend, or raise BackendError with a hint."""
    base_url_override = getattr(args, "base_url", None)
    if args.backend == "openai-compatible":
        if not base_url_override or not args.model:
            raise BackendError("--backend openai-compatible requires --base-url and --model")
        return OpenAICompatBackend(
            base_url_override,
            args.model,
            os.environ.get("OPENAI_COMPAT_API_KEY", ""),
            timeout=args.timeout,
        )
    if args.backend == "ollama":
        if base_url_override:
            raise BackendError(
                "use --host for Ollama, or --backend openai-compatible with --base-url"
            )
        return OllamaBackend(
            model=args.model or DEFAULT_MODEL, host=args.host, timeout=args.timeout
        )
    base_url, default_model, env_var = PROVIDERS[args.backend]
    key = os.environ.get(env_var)
    if not key:
        raise BackendError(
            f"set {env_var} to use --backend {args.backend} (free key from the provider)"
        )
    return OpenAICompatBackend(
        base_url_override or base_url, args.model or default_model, key, timeout=args.timeout
    )


def _emit(args, text: str, data_fn) -> None:
    """Print JSON (if --json) or the human-readable text report."""
    if getattr(args, "as_json", False):
        print(json.dumps(data_fn(), indent=2))
    else:
        print(text)


def _read_document(path):
    """Read the document at ``path``, or from stdin when ``path`` is ``-``.

    Stdin is decoded as UTF-8 like a file, rather than in the console's code
    page, so piped Thai or Chinese text survives on Windows.
    """
    if path == "-":
        buffer = getattr(sys.stdin, "buffer", None)
        if buffer is None:  # a text stream, e.g. io.StringIO in tests
            return sys.stdin.read()
        return buffer.read().decode("utf-8-sig")
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _resolve(args, demo_doc, demo_model, verb):
    """Return (document, backend) for a document-taking command, or (None, None).

    On error the message is already printed; the caller returns exit code 2.
    """
    if getattr(args, "demo", False):
        if not getattr(args, "as_json", False):
            print("Document:\n" + demo_doc + "\n")
        return demo_doc, CallableBackend(demo_model)
    if not getattr(args, "file", None):
        print(f"glassbox: give a file to {verb}, or pass --demo", file=sys.stderr)
        return None, None
    try:
        document = _read_document(args.file)
    except (OSError, UnicodeDecodeError) as error:
        print(f"glassbox: {error}", file=sys.stderr)
        return None, None
    try:
        return document, _make_backend(args)
    except BackendError as error:
        print(f"glassbox: {error}", file=sys.stderr)
        return None, None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="glassbox",
        description="Explainable LLM triage whose evidence is checked against the source.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    a = sub.add_parser("audit", help="all-in-one: grounding + faithfulness + consistency")
    a.add_argument("file", nargs="?", help="document to audit, or - for stdin (omit with --demo)")
    _add_model_args(a, demo_help="run a built-in toy model — no model needed")

    t = sub.add_parser("triage", help="classify a document and ground its evidence")
    t.add_argument("file", help="document to triage, or - for stdin")
    _add_model_args(t)

    p = sub.add_parser("probe", help="faithfulness: erase each cited span and re-decide")
    p.add_argument("file", nargs="?", help="document to probe, or - for stdin (omit with --demo)")
    _add_model_args(p, demo_help="run a built-in toy model — no model needed")

    s = sub.add_parser("stress", help="consistency: re-decide after meaning-preserving edits")
    s.add_argument(
        "file", nargs="?", help="document to stress-test, or - for stdin (omit with --demo)"
    )
    _add_model_args(s, demo_help="run a built-in toy model — no model needed")

    c = sub.add_parser("calibrate", help="do the confidence numbers mean anything? (needs labels)")
    c.add_argument(
        "dataset", nargs="?", help="JSON Lines of {'document','label'} (omit with --demo)"
    )
    _add_model_args(c, demo_help="run a built-in labelled set and toy model — no model needed")

    b = sub.add_parser("batch", help="audit a whole corpus and report the pass rates")
    b.add_argument("corpus", nargs="?", help="JSON Lines of {'document'} (omit with --demo)")
    _add_model_args(b, demo_help="run a built-in corpus and toy model — no model needed")
    b.add_argument(
        "--min-trustworthy",
        type=_rate,
        default=1.0,
        metavar="RATE",
        help="exit 0 when at least this fraction of documents is trustworthy, "
        "0-1 (default: %(default)s)",
    )
    b.add_argument(
        "--checkpoint",
        metavar="PATH",
        help="append each finished document to this JSON Lines file and skip documents "
        "already in it, so an interrupted run resumes where it stopped",
    )
    b.add_argument(
        "--skip-errors",
        action="store_true",
        help="record a timed-out or dropped request as an error and keep going "
        "(stops anyway after 3 in a row: the server is down, not slow)",
    )

    lens = sub.add_parser(
        "lens",
        help="white-box: logit lens + occlusion attribution (needs the whitebox extra)",
    )
    lens.add_argument("prompt", help="text to run through the model")
    lens.add_argument(
        "--model", default="gpt2", help="Hugging Face causal-LM (default: %(default)s)"
    )
    lens.add_argument(
        "--json", dest="as_json", action="store_true", help="emit JSON instead of text"
    )

    sub.add_parser("demo", help="run a built-in example — no model needed")

    sv = sub.add_parser(
        "serve",
        help="HTTP API + web page for audits (e.g. on a Raspberry Pi on your LAN)",
        description="Serve audit/triage over HTTP: a web page at / and a job API at "
        f"/api/jobs. Set ${TOKEN_ENV} to require a bearer token (recommended whenever "
        "--bind is not localhost).",
    )
    sv.add_argument(
        "--bind",
        default=DEFAULT_BIND,
        help="address to listen on (default: %(default)s, this machine only; "
        "use 0.0.0.0 to serve your LAN)",
    )
    sv.add_argument("--port", type=int, default=DEFAULT_PORT, help="port (default: %(default)s)")
    sv.add_argument(
        "--max-queue",
        type=int,
        default=DEFAULT_MAX_QUEUE,
        help="jobs allowed to wait before new ones get HTTP 429 (default: %(default)s)",
    )
    _add_model_args(sv, demo_help="serve the built-in toy model — no model needed", json_flag=False)

    return parser


# ----------------------------------------------------------------------------
# Renderers (text) — JSON goes through serialize.py.
# ----------------------------------------------------------------------------


def _render(verdict: Verdict) -> str:
    lines = [
        f"label:      {verdict.label}",
        f"confidence: {verdict.confidence:.2f}",
        f"rationale:  {verdict.rationale}",
        "",
        "evidence:",
    ]
    if not verdict.citations:
        lines.append("  (none offered)")
    for citation in verdict.citations:
        mark = "ok  " if citation.verified else "MISS"
        where = f"chars {citation.start}-{citation.end}" if citation.located else "not in source"
        lines.append(f"  [{mark}] {citation.quote!r}  ({where})")

    lines.append("")
    if verdict.grounded:
        lines.append("grounding:  OK - every cited span exists in the document.")
    elif verdict.hallucinated_citations:
        n = len(verdict.hallucinated_citations)
        lines.append(f"grounding:  UNFAITHFUL - {n} cited span(s) are not in the document.")
    else:
        lines.append("grounding:  WEAK - the verdict cited no evidence.")
    return "\n".join(lines)


def _render_faithfulness(report: FaithfulnessReport) -> str:
    v = report.verdict
    lines = [
        f"verdict:    {v.label} (confidence {v.confidence:.2f})",
        "",
        "evidence faithfulness (erase each cited span, then re-decide):",
    ]
    if not report.testable:
        lines.append("  (no grounded evidence to test)")
    for probe in report.probes:
        if probe.flipped:
            tag = "load-bearing"
            effect = f"removing it flips the verdict to '{probe.new_label}'"
        else:
            tag = "decorative  "
            effect = f"verdict holds at '{v.label}' without it"
        lines.append(f"  [{tag}] {probe.citation.quote!r}")
        lines.append(f"               -> {effect}")

    lines.append("")
    if not report.testable:
        lines.append("faithful:   UNTESTABLE - the verdict cited nothing that exists.")
    elif report.load_bearing:
        lines.append(
            "faithful:   YES - erasing all cited evidence flips the verdict to "
            f"'{report.comprehensive_label}'. The decision depends on what it cited."
        )
    else:
        lines.append(
            "faithful:   NO - the verdict survives with all cited evidence erased. "
            "Its stated reasons are post-hoc, not load-bearing."
        )
    return "\n".join(lines)


def _render_consistency(report: ConsistencyReport) -> str:
    v = report.original
    lines = [
        f"verdict:    {v.label} (confidence {v.confidence:.2f})",
        "",
        "consistency (re-decide after meaning-preserving edits):",
    ]
    for variant in report.effective:
        if variant.agreed:
            lines.append(f"  [stable] {variant.perturbation.name:<22} -> still '{variant.label}'")
        else:
            lines.append(
                f"  [FLIP  ] {variant.perturbation.name:<22} -> changes to '{variant.label}'"
            )
    skipped = [var.perturbation.name for var in report.variants if not var.changed]
    if skipped:
        lines.append(f"  (no change to this document: {', '.join(skipped)})")

    lines.append("")
    if not report.testable:
        lines.append("robust:     UNTESTABLE - no edit changed this document.")
    elif report.robust:
        n = len(report.effective)
        lines.append(
            f"robust:     YES - the verdict held under all {n} meaning-preserving edit(s)."
        )
    else:
        n = len(report.flips)
        total = len(report.effective)
        lines.append(
            f"robust:     NO - {n} of {total} edit(s) flipped the verdict. The decision "
            "tracks surface form, not meaning."
        )
    return "\n".join(lines)


def _render_reliability_diagram(report: CalibrationReport) -> str:
    """Place observed accuracy and mean confidence on a shared 0-100% scale."""
    width = 20
    lines = [
        "reliability diagram (C = mean confidence, A = accuracy, * = both):",
        "             0%        50%       100%",
    ]
    for row in report.reliability():
        confidence = round(row.avg_confidence * width)
        accuracy = round(row.accuracy * width)
        scale = ["."] * (width + 1)
        scale[confidence] = "C"
        scale[accuracy] = "*" if confidence == accuracy else "A"
        lines.append(f"  {row.low:.2f}-{row.high:.2f} |{''.join(scale)}|")
    return "\n".join(lines)


def _render_calibration(report: CalibrationReport) -> str:
    skipped = f" ({report.errors} skipped: unparseable)" if report.errors else ""
    lines = [f"calibration over {report.n} example(s){skipped}:", ""]
    if report.n == 0:
        lines.append("  (no scored predictions)")
        return "\n".join(lines)

    sign = "+" if report.gap >= 0 else "-"
    lines += [
        f"accuracy:        {report.accuracy:.2f}",
        f"mean confidence: {report.mean_confidence:.2f}",
        f"gap:             {sign}{abs(report.gap):.2f}",
        f"ECE:             {report.ece:.2f}   (0 = perfectly calibrated)",
        f"Brier:           {report.brier:.2f}   (0 = perfect)",
        "",
        "reliability (per confidence band):",
    ]
    for row in report.reliability():
        lines.append(
            f"  conf {row.low:.2f}-{row.high:.2f} | n={row.count:<3d} "
            f"| said {row.avg_confidence:.2f} | right {row.accuracy:.2f}"
        )

    lines.extend(["", _render_reliability_diagram(report), ""])
    if report.overconfident:
        lines.append(
            f"verdict:    OVERCONFIDENT - it claims {report.mean_confidence:.2f} but is right "
            f"{report.accuracy:.2f} of the time. The confidence number is unreliable."
        )
    elif report.underconfident:
        lines.append(
            f"verdict:    UNDERCONFIDENT - it is right {report.accuracy:.2f} of the time but only "
            f"claims {report.mean_confidence:.2f}."
        )
    else:
        lines.append("verdict:    WELL CALIBRATED - stated confidence tracks actual accuracy.")
    return "\n".join(lines)


def _render_audit(report: AuditReport) -> str:
    v = report.verdict
    lines = [f"verdict:    {v.label} (confidence {v.confidence:.2f})", ""]

    if report.grounded:
        g = "PASS - every cited span exists in the document"
    elif v.hallucinated_citations:
        g = f"FAIL - {len(v.hallucinated_citations)} cited span(s) are not in the document"
    else:
        g = "FAIL - the verdict cited no evidence"
    lines.append(f"grounded:   {g}")

    if not report.faithfulness.testable:
        f = "FAIL - no grounded evidence to test"
    elif report.faithful:
        f = "PASS - erasing the cited evidence flips the verdict"
    else:
        f = "FAIL - the verdict survives with its evidence erased (post-hoc)"
    lines.append(f"faithful:   {f}")

    if not report.consistency.testable:
        r = "FAIL - no edit changed the document"
    elif report.robust:
        r = "PASS - holds under every meaning-preserving edit"
    else:
        names = ", ".join(flip.perturbation.name for flip in report.consistency.flips)
        r = f"FAIL - a meaning-preserving edit flipped the decision ({names})"
    lines.append(f"robust:     {r}")

    lines.append("")
    total = len(report.checks)
    if report.trustworthy:
        lines.append(f"trust:      {report.passed}/{total} checks passed - this verdict holds up.")
    else:
        lines.append(
            f"trust:      {report.passed}/{total} checks passed - treat this verdict with caution."
        )
    return "\n".join(lines)


def _render_batch(report) -> str:
    reasons = []
    if report.parse_errors:
        reasons.append(f"{report.parse_errors} unparseable")
    if report.backend_errors:
        reasons.append(f"{report.backend_errors} timed out / no reply")
    skipped = f" ({report.errors} skipped: {', '.join(reasons)})" if report.errors else ""
    lines = [f"batch audit over {report.n} document(s){skipped}:", ""]
    if report.n == 0:
        lines.append("  (no documents audited)")
        return "\n".join(lines)

    def row(label: str, rate: float) -> str:
        passed = round(rate * report.n)
        return f"  {label:<13} {passed}/{report.n}  ({rate * 100:.0f}%)"

    lines += [
        row("grounded:", report.grounded_rate),
        row("faithful:", report.faithful_rate),
        row("robust:", report.robust_rate),
        row("trustworthy:", report.trustworthy_rate),
    ]
    return "\n".join(lines)


def _render_lens(result: LensResult) -> str:
    tokens = [_ascii(t) for t in result.prompt_tokens]
    lines = [
        f"model:      {result.model}",
        f"tokens:     {tokens}",
        f"predicted:  {_ascii(result.predicted)!r}  (p={result.predicted_prob:.3f})",
        "",
        "logit lens (top token as the prediction forms, by layer):",
    ]
    for step in result.layer_trace:
        lines.append(f"  L{step.layer:02d}  {_ascii(step.token)!r:<14} {step.prob:.3f}")

    lines += ["", "occlusion attribution (mask each token -> drop in P(predicted)):"]
    for attribution in result.attributions:
        lines.append(
            f"  {attribution.delta:+.3f}  {_ascii(attribution.token)!r} (token {attribution.index})"
        )
    lines.append("")
    lines.append(
        "A large positive drop means the model leaned on that token to make its "
        "prediction - the inside-the-model echo of `probe`'s erasure test."
    )
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------


def _cmd_triage(args) -> int:
    document, backend = _resolve(args, None, None, "triage")
    if document is None:
        return 2
    try:
        verdict = triage(document, backend, _policy(args))
    except (BackendError, TriageError) as error:
        print(f"glassbox: {error}", file=sys.stderr)
        return 2
    _emit(args, _render(verdict), lambda: verdict_dict(verdict))
    return 0 if verdict.grounded else 1


def _cmd_audit(args) -> int:
    document, backend = _resolve(args, _PROBE_DEMO_DOCUMENT, _probe_demo_model, "audit")
    if document is None:
        return 2
    try:
        report = audit(document, backend, _policy(args))
    except (BackendError, TriageError) as error:
        print(f"glassbox: {error}", file=sys.stderr)
        return 2
    _emit(args, _render_audit(report), lambda: audit_dict(report))
    return 0 if report.trustworthy else 1


def _cmd_probe(args) -> int:
    document, backend = _resolve(args, _PROBE_DEMO_DOCUMENT, _probe_demo_model, "probe")
    if document is None:
        return 2
    try:
        report = analyze(document, backend, _policy(args))
    except (BackendError, TriageError) as error:
        print(f"glassbox: {error}", file=sys.stderr)
        return 2
    _emit(args, _render_faithfulness(report), lambda: faithfulness_dict(report))
    return 0 if report.load_bearing else 1


def _cmd_stress(args) -> int:
    document, backend = _resolve(args, _PROBE_DEMO_DOCUMENT, _probe_demo_model, "stress-test")
    if document is None:
        return 2
    try:
        report = check(document, backend, _policy(args))
    except (BackendError, TriageError) as error:
        print(f"glassbox: {error}", file=sys.stderr)
        return 2
    _emit(args, _render_consistency(report), lambda: consistency_dict(report))
    return 0 if report.robust else 1


def _cmd_calibrate(args) -> int:
    if args.demo:
        dataset = [Example(doc, gold) for doc, gold in _CALIB_DEMO_DATA]
        backend = CallableBackend(_calib_demo_model)
    elif args.dataset:
        try:
            dataset = load_dataset(args.dataset)
        except (OSError, DatasetError) as error:
            print(f"glassbox: {error}", file=sys.stderr)
            return 2
        try:
            backend = _make_backend(args)
        except BackendError as error:
            print(f"glassbox: {error}", file=sys.stderr)
            return 2
    else:
        print("glassbox: give a labelled dataset, or pass --demo", file=sys.stderr)
        return 2

    policy = _policy(args)
    allowed = {label.lower() for label in policy.labels}
    unknown = sorted({ex.gold for ex in dataset if ex.gold.lower() not in allowed})
    if unknown:
        print(
            f"glassbox: the dataset uses label(s) {unknown} that are not in "
            f"{list(policy.labels)}; pass the task's labels with --labels",
            file=sys.stderr,
        )
        return 2
    try:
        report = evaluate(dataset, backend, policy)
    except (BackendError, TriageError) as error:
        print(f"glassbox: {error}", file=sys.stderr)
        return 2
    _emit(args, _render_calibration(report), lambda: calibration_dict(report))
    return 0 if not (report.overconfident or report.underconfident) else 1


def _batch_progress(index: int, total: int, item) -> None:
    # On stderr so `batch ... > report.txt` still captures only the report.
    if item.backend_error:
        status = "no reply (skipped)"
    elif item.error is not None:
        status = "unparseable"
    else:
        status = "trustworthy" if item.trustworthy else "not trustworthy"
    print(f"  [{index}/{total}] {status}", file=sys.stderr, flush=True)


def _cmd_batch(args) -> int:
    if args.demo:
        documents = list(_BATCH_DEMO_DOCS)
        backend = CallableBackend(_probe_demo_model)
    elif args.corpus:
        try:
            documents = load_documents(args.corpus)
        except (OSError, DatasetError) as error:
            print(f"glassbox: {error}", file=sys.stderr)
            return 2
        try:
            backend = _make_backend(args)
        except BackendError as error:
            print(f"glassbox: {error}", file=sys.stderr)
            return 2
    else:
        print(
            "glassbox: give a corpus (JSON Lines of {'document'}), or pass --demo", file=sys.stderr
        )
        return 2

    try:
        report = run_batch(
            documents,
            backend,
            _policy(args),
            skip_backend_errors=getattr(args, "skip_errors", False),
            checkpoint=None if args.demo else getattr(args, "checkpoint", None),
            progress=_batch_progress if not getattr(args, "as_json", False) else None,
        )
    except CheckpointError as error:
        print(f"glassbox: {error}", file=sys.stderr)
        return 2
    except (BackendError, TriageError) as error:
        print(f"glassbox: {error}", file=sys.stderr)
        return 2
    _emit(args, _render_batch(report), lambda: batch_dict(report))
    return 0 if report.n > 0 and report.trustworthy_rate >= args.min_trustworthy else 1


def _cmd_lens(args) -> int:
    if not whitebox_available():
        print(
            "glassbox: the lens needs the optional white-box dependencies. "
            'Install them with: pip install "glassbox-audit[whitebox]"',
            file=sys.stderr,
        )
        return 2
    try:
        result = lens_analyze(args.prompt, model_name=args.model)
    except Exception as error:  # model download / load failures vary by backend
        print(f"glassbox: could not run the model {args.model!r}: {error}", file=sys.stderr)
        return 2
    _emit(args, _render_lens(result), lambda: lens_dict(result))
    return 0


def _cmd_demo(_args) -> int:
    print("Document:\n" + _DEMO_DOCUMENT)
    verdict = triage(_DEMO_DOCUMENT, StubBackend(_DEMO_REPLY), DEFAULT_POLICY)
    print(_render(verdict))
    print(
        "\nThe second quote was never in the email - glassbox caught the model "
        "inventing its own evidence."
    )
    return 0


def _cmd_serve(args) -> int:
    if not 0 <= args.port <= 65535:
        print("glassbox: --port must be 0-65535", file=sys.stderr)
        return 2
    if args.max_queue < 1:
        print("glassbox: --max-queue must be at least 1", file=sys.stderr)
        return 2
    if args.demo:
        backend, model = CallableBackend(_probe_demo_model), "built-in toy model (--demo)"
    else:
        try:
            backend = _make_backend(args)
        except BackendError as error:
            print(f"glassbox: {error}", file=sys.stderr)
            return 2
        model = getattr(backend, "model", "")

    token = os.environ.get(TOKEN_ENV, "").strip() or None
    service = AuditService(
        backend,
        model=model,
        default_prompt=args.prompt,
        max_queue=args.max_queue,
        policy=_policy(args),
    )
    try:
        server = make_server(service, args.bind, args.port, token)
    except OSError as error:
        service.stop()
        print(f"glassbox: cannot listen on {args.bind}:{args.port} ({error})", file=sys.stderr)
        return 2

    host, port = server.server_address[:2]
    print(f"glassbox serve: http://{host}:{port}/  model: {model}", file=sys.stderr)
    if not args.demo and args.backend == "ollama":
        print(f"  model server: {args.host}  (timeout {args.timeout:g}s)", file=sys.stderr)
    print(f"  token: {'required' if token else 'NOT required'}", file=sys.stderr)
    if not is_loopback(args.bind) and not token:
        print(
            f"  WARNING: listening beyond this machine with no token. Anyone who can reach "
            f"port {port} can submit jobs. Set {TOKEN_ENV} to require one.",
            file=sys.stderr,
        )

    def _shutdown(*_):
        # shutdown() blocks until serve_forever exits, so call it off the main thread.
        threading.Thread(target=server.shutdown, daemon=True).start()

    with contextlib.suppress(ValueError, AttributeError):  # not main thread / no SIGTERM
        signal.signal(signal.SIGTERM, _shutdown)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.stop()
        print("glassbox serve: stopped", file=sys.stderr)
    return 0


def _force_utf8_output() -> None:
    """Emit UTF-8 regardless of the console code page.

    On a Thai Windows console (cp874) the evidence quotes for a Thai document
    come out as mojibake, because Python encodes stdout with the locale code
    page. Reconfiguring to UTF-8 makes non-ASCII evidence render correctly on
    any modern terminal. Guarded so a captured/replaced stream (e.g. under
    pytest) is simply left alone.
    """
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    args = build_parser().parse_args(argv)
    if hasattr(args, "labels"):  # every command that classifies with a model
        try:
            args.policy = _build_policy(args)
        except ValueError as error:
            print(f"glassbox: {error}", file=sys.stderr)
            return 2
    handlers = {
        "audit": _cmd_audit,
        "triage": _cmd_triage,
        "probe": _cmd_probe,
        "stress": _cmd_stress,
        "calibrate": _cmd_calibrate,
        "batch": _cmd_batch,
        "lens": _cmd_lens,
        "demo": _cmd_demo,
        "serve": _cmd_serve,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
