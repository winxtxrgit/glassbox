"""Command line: ``glassbox triage <file>`` and ``glassbox demo``.

``triage`` runs a real local model (Ollama + Qwen by default). ``demo`` runs a
built-in example through a canned backend so you can see the whole idea — a
verdict, its located evidence, and a *caught* fabricated citation — without
installing a model first.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .backends import DEFAULT_MODEL, BackendError, OllamaBackend, StubBackend
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return {"triage": _cmd_triage, "demo": _cmd_demo}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
