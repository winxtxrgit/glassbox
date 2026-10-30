"""Command line: ``triage``, ``probe``, ``stress``, ``calibrate``, ``demo``.

``triage`` classifies a document and grounds its evidence (phase 1). ``probe``
erases each cited span and re-decides, revealing which evidence drives the verdict
and which is decoration (phase 2). ``stress`` re-decides after meaning-preserving
edits, exposing a verdict that tracks surface form rather than meaning (phase 3).
``calibrate`` runs a labelled set and checks whether the confidence numbers match
how often the model is actually right (phase 4). ``lens`` opens a small local model
up — logit lens and occlusion attribution (phase 5, needs the ``whitebox`` extra).
The black-box verbs run a real local model (Ollama + Qwen by default); ``demo`` and
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
from .triage import TriageError, triage
from .whitebox import LensResult
from .whitebox import analyze as lens_analyze
from .whitebox import available as whitebox_available


def _ascii(text: str) -> str:
    """Make model-derived token text safe to print on any console encoding."""
    return text.encode("ascii", "backslashreplace").decode("ascii")


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


# For `calibrate --demo`: a labelled set and a keyword model that stamps 0.90 on
# everything it flags. It gets 6 of 10 right — so calibration exposes an
# overconfident model whose confidence number carries no information.
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

    c = sub.add_parser(
        "calibrate",
        help="do the confidence numbers mean anything? (needs a labelled set)",
    )
    c.add_argument(
        "dataset",
        nargs="?",
        help="JSON Lines file of {'document','label'} (omit with --demo)",
    )
    c.add_argument(
        "--demo",
        action="store_true",
        help="run a built-in labelled set and toy model — no model needed",
    )
    c.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model tag (default: %(default)s)")
    c.add_argument(
        "--host",
        default="http://localhost:11434",
        help="Ollama server URL (default: %(default)s)",
    )

    lens = sub.add_parser(
        "lens",
        help="white-box: logit lens + occlusion attribution (needs the whitebox extra)",
    )
    lens.add_argument("prompt", help="text to run through the model")
    lens.add_argument(
        "--model",
        default="gpt2",
        help="Hugging Face causal-LM name (default: %(default)s)",
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
        backend = OllamaBackend(model=args.model, host=args.host)
    else:
        print("glassbox: give a labelled dataset, or pass --demo", file=sys.stderr)
        return 2

    try:
        report = evaluate(dataset, backend, DEFAULT_POLICY)
    except (BackendError, TriageError) as error:
        print(f"glassbox: {error}", file=sys.stderr)
        return 2

    print(_render_calibration(report))
    # Non-zero when the confidence is miscalibrated enough to be misleading.
    return 0 if not (report.overconfident or report.underconfident) else 1


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
    print(_render_lens(result))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "triage": _cmd_triage,
        "probe": _cmd_probe,
        "stress": _cmd_stress,
        "calibrate": _cmd_calibrate,
        "lens": _cmd_lens,
        "demo": _cmd_demo,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
