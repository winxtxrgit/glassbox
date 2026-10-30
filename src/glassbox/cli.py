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
import json
import os
import sys

from . import __version__
from .audit import AuditReport, audit
from .backends import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    PROVIDERS,
    BackendError,
    CallableBackend,
    OllamaBackend,
    OpenAICompatBackend,
    StubBackend,
)
from .batch import load_documents, run_batch
from .calibration import (
    CalibrationReport,
    DatasetError,
    Example,
    evaluate,
    load_dataset,
)
from .consistency import ConsistencyReport, check
from .faithfulness import FaithfulnessReport, analyze
from .prompts import DEFAULT_POLICY
from .schema import Verdict
from .serialize import (
    audit_dict,
    batch_dict,
    calibration_dict,
    consistency_dict,
    faithfulness_dict,
    lens_dict,
    verdict_dict,
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


def _add_model_args(p: argparse.ArgumentParser, *, demo_help: str | None = None) -> None:
    if demo_help:
        p.add_argument("--demo", action="store_true", help=demo_help)
    p.add_argument(
        "--backend",
        choices=("ollama", *PROVIDERS),
        default="ollama",
        help="where the model runs: ollama (local, default), or a free API tier "
        "(openrouter/groq) that reads <PROVIDER>_API_KEY from the environment",
    )
    p.add_argument("--model", default=None, help="model name (default depends on backend)")
    p.add_argument("--host", default=DEFAULT_HOST, help="Ollama server URL (default: %(default)s)")
    p.add_argument("--json", dest="as_json", action="store_true", help="emit JSON instead of text")


def _make_backend(args):
    """Build the backend named by --backend, or raise BackendError with a hint."""
    if args.backend == "ollama":
        return OllamaBackend(model=args.model or DEFAULT_MODEL, host=args.host)
    base_url, default_model, env_var = PROVIDERS[args.backend]
    key = os.environ.get(env_var)
    if not key:
        raise BackendError(
            f"set {env_var} to use --backend {args.backend} (free key from the provider)"
        )
    return OpenAICompatBackend(base_url, args.model or default_model, key)


def _emit(args, text: str, data_fn) -> None:
    """Print JSON (if --json) or the human-readable text report."""
    if getattr(args, "as_json", False):
        print(json.dumps(data_fn(), indent=2))
    else:
        print(text)


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
        with open(args.file, encoding="utf-8") as handle:
            document = handle.read()
    except OSError as error:
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
    a.add_argument("file", nargs="?", help="document to audit (omit with --demo)")
    _add_model_args(a, demo_help="run a built-in toy model — no model needed")

    t = sub.add_parser("triage", help="classify a document and ground its evidence")
    t.add_argument("file", help="path to the document to triage")
    _add_model_args(t)

    p = sub.add_parser("probe", help="faithfulness: erase each cited span and re-decide")
    p.add_argument("file", nargs="?", help="document to probe (omit with --demo)")
    _add_model_args(p, demo_help="run a built-in toy model — no model needed")

    s = sub.add_parser("stress", help="consistency: re-decide after meaning-preserving edits")
    s.add_argument("file", nargs="?", help="document to stress-test (omit with --demo)")
    _add_model_args(s, demo_help="run a built-in toy model — no model needed")

    c = sub.add_parser("calibrate", help="do the confidence numbers mean anything? (needs labels)")
    c.add_argument(
        "dataset", nargs="?", help="JSON Lines of {'document','label'} (omit with --demo)"
    )
    _add_model_args(c, demo_help="run a built-in labelled set and toy model — no model needed")

    b = sub.add_parser("batch", help="audit a whole corpus and report the pass rates")
    b.add_argument("corpus", nargs="?", help="JSON Lines of {'document'} (omit with --demo)")
    _add_model_args(b, demo_help="run a built-in corpus and toy model — no model needed")

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

    lines.append("")
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
    skipped = f" ({report.errors} skipped: unparseable)" if report.errors else ""
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
        verdict = triage(document, backend, DEFAULT_POLICY)
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
        report = audit(document, backend, DEFAULT_POLICY)
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
        report = analyze(document, backend, DEFAULT_POLICY)
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
        report = check(document, backend, DEFAULT_POLICY)
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

    try:
        report = evaluate(dataset, backend, DEFAULT_POLICY)
    except (BackendError, TriageError) as error:
        print(f"glassbox: {error}", file=sys.stderr)
        return 2
    _emit(args, _render_calibration(report), lambda: calibration_dict(report))
    return 0 if not (report.overconfident or report.underconfident) else 1


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
        report = run_batch(documents, backend, DEFAULT_POLICY)
    except (BackendError, TriageError) as error:
        print(f"glassbox: {error}", file=sys.stderr)
        return 2
    _emit(args, _render_batch(report), lambda: batch_dict(report))
    return 0 if report.n > 0 and report.trustworthy_rate == 1.0 else 1


def _cmd_lens(args) -> int:
    if not whitebox_available():
        print(
            "glassbox: the lens needs the optional white-box dependencies. "
            'Install them with: pip install -e ".[whitebox]"',
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "audit": _cmd_audit,
        "triage": _cmd_triage,
        "probe": _cmd_probe,
        "stress": _cmd_stress,
        "calibrate": _cmd_calibrate,
        "batch": _cmd_batch,
        "lens": _cmd_lens,
        "demo": _cmd_demo,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
