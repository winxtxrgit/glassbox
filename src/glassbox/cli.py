"""Command line: ``triage``, ``probe``, ``stress``, ``demo``.

``triage`` classifies a document and grounds its evidence (phase 1). ``probe``
erases each cited span and re-decides, revealing which evidence drives the verdict
and which is decoration (phase 2). ``stress`` re-decides after meaning-preserving
edits, exposing a verdict that tracks surface form rather than meaning (phase 3).
The first three run a real local model (Ollama + Qwen by default); ``demo`` and
the ``--demo`` flags run built-in examples through canned backends, no model needed.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .backends import (
    DEFAULT_MODEL,
    BackendError,
    CallableBackend,
    OllamaBackend,
    StubBackend,
)
from .consistency import ConsistencyReport, check
from .faithfulness import FaithfulnessReport, analyze
from .prompts import DEFAULT_POLICY
from .schema import Verdict
from .triage import TriageError, triage

# A small worked example: a phishing lure. The canned reply cites one real span
# and one fabricated one, so `glassbox demo` shows the grounding check earning
# its keep — the invented quote is flagged, and the verdict reads as ungrounded.
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

# For `probe --demo`: a document and a toy model whose decision depends ONLY on
# the credential phrase, yet which cites a second, harmless phrase too. The
# counterfactual check exposes the difference — one citation is load-bearing,
# the other is decoration.
_PROBE_DEMO_DOCUMENT = (
    "Your mailbox is over quota. Verify your password within 24 hours to avoid suspension."
)
_PROBE_DEMO_TRIGGER = "Verify your password"
_PROBE_DEMO_CANDIDATES = ("Verify your password", "mailbox is over quota")


def _probe_demo_model(system: str, user: str) -> str:
    """A stand-in model: 'suspicious' iff the credential phrase is still present.

    It cites whichever candidate phrases currently appear in the document — so
    once the credential phrase is erased it both flips its label and drops that
    citation, exactly as a faithful (if simplistic) model would.
    """
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="glassbox",
        description="Explainable LLM triage whose evidence is checked against the source.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    t = sub.add_parser("triage", help="classify a document with a local model")
    t.add_argument("file", help="path to the document to triage")
    t.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Ollama model tag (default: %(default)s)",
    )
    t.add_argument(
        "--host",
        default="http://localhost:11434",
        help="Ollama server URL (default: %(default)s)",
    )

    p = sub.add_parser(
        "probe",
        help="counterfactual faithfulness: erase each cited span and re-decide",
    )
    p.add_argument("file", nargs="?", help="document to probe (omit with --demo)")
    p.add_argument(
        "--demo",
        action="store_true",
        help="run a built-in toy model instead of Ollama — no model needed",
    )
    p.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model tag (default: %(default)s)")
    p.add_argument(
        "--host",
        default="http://localhost:11434",
        help="Ollama server URL (default: %(default)s)",
    )

    s = sub.add_parser(
        "stress",
        help="consistency: re-decide after meaning-preserving edits",
    )
    s.add_argument("file", nargs="?", help="document to stress-test (omit with --demo)")
    s.add_argument(
        "--demo",
        action="store_true",
        help="run a built-in toy model instead of Ollama — no model needed",
    )
    s.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model tag (default: %(default)s)")
    s.add_argument(
        "--host",
        default="http://localhost:11434",
        help="Ollama server URL (default: %(default)s)",
    )

    sub.add_parser("demo", help="run a built-in example — no model needed")

    return parser


def _render(verdict: Verdict, document: str) -> str:
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


def _cmd_triage(args) -> int:
    try:
        with open(args.file, encoding="utf-8") as handle:
            document = handle.read()
    except OSError as error:
        print(f"glassbox: {error}", file=sys.stderr)
        return 2

    backend = OllamaBackend(model=args.model, host=args.host)
    try:
        verdict = triage(document, backend, DEFAULT_POLICY)
    except (BackendError, TriageError) as error:
        print(f"glassbox: {error}", file=sys.stderr)
        return 2

    print(_render(verdict, document))
    return 0 if verdict.grounded else 1


def _cmd_demo(_args) -> int:
    print("Document:\n" + _DEMO_DOCUMENT)
    verdict = triage(_DEMO_DOCUMENT, StubBackend(_DEMO_REPLY), DEFAULT_POLICY)
    print(_render(verdict, _DEMO_DOCUMENT))
    print(
        "\nThe second quote was never in the email - glassbox caught the model "
        "inventing its own evidence."
    )
    return 0


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


def _cmd_probe(args) -> int:
    if args.demo:
        document = _PROBE_DEMO_DOCUMENT
        backend = CallableBackend(_probe_demo_model)
    elif args.file:
        try:
            with open(args.file, encoding="utf-8") as handle:
                document = handle.read()
        except OSError as error:
            print(f"glassbox: {error}", file=sys.stderr)
            return 2
        backend = OllamaBackend(model=args.model, host=args.host)
    else:
        print("glassbox: give a file to probe, or pass --demo", file=sys.stderr)
        return 2

    if args.demo:
        print("Document:\n" + document + "\n")
    try:
        report = analyze(document, backend, DEFAULT_POLICY)
    except (BackendError, TriageError) as error:
        print(f"glassbox: {error}", file=sys.stderr)
        return 2

    print(_render_faithfulness(report))
    return 0 if report.load_bearing else 1


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


def _cmd_stress(args) -> int:
    if args.demo:
        document = _PROBE_DEMO_DOCUMENT
        backend = CallableBackend(_probe_demo_model)
    elif args.file:
        try:
            with open(args.file, encoding="utf-8") as handle:
                document = handle.read()
        except OSError as error:
            print(f"glassbox: {error}", file=sys.stderr)
            return 2
        backend = OllamaBackend(model=args.model, host=args.host)
    else:
        print("glassbox: give a file to stress-test, or pass --demo", file=sys.stderr)
        return 2

    if args.demo:
        print("Document:\n" + document + "\n")
    try:
        report = check(document, backend, DEFAULT_POLICY)
    except (BackendError, TriageError) as error:
        print(f"glassbox: {error}", file=sys.stderr)
        return 2

    print(_render_consistency(report))
    return 0 if report.robust else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "triage": _cmd_triage,
        "probe": _cmd_probe,
        "stress": _cmd_stress,
        "demo": _cmd_demo,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
