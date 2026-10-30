"""Turn reports into plain JSON-serializable dicts for ``--json`` output.

Every ``*_dict`` here includes the computed verdicts (``grounded``, ``robust``,
``trustworthy``, ``ece`` …) alongside the raw fields, so a downstream pipeline can
gate on a single boolean without re-deriving it. Kept separate from the dataclasses
so the core types stay presentation-free.
"""

from __future__ import annotations

from .audit import AuditReport
from .batch import BatchItem, BatchReport
from .calibration import CalibrationReport
from .consistency import ConsistencyReport
from .faithfulness import FaithfulnessReport
from .schema import Citation, Verdict
from .whitebox import LensResult


def citation_dict(c: Citation) -> dict:
    return {"quote": c.quote, "start": c.start, "end": c.end, "verified": c.verified}


def verdict_dict(v: Verdict) -> dict:
    return {
        "label": v.label,
        "confidence": v.confidence,
        "rationale": v.rationale,
        "grounded": v.grounded,
        "citations": [citation_dict(c) for c in v.citations],
        "hallucinated_count": len(v.hallucinated_citations),
    }


def faithfulness_dict(r: FaithfulnessReport) -> dict:
    return {
        "load_bearing": r.load_bearing,
        "testable": r.testable,
        "comprehensive_label": r.comprehensive_label,
        "comprehensive_flipped": r.comprehensive_flipped,
        "necessary": [c.quote for c in r.necessary],
        "decorative": [c.quote for c in r.decorative],
        "probes": [
            {
                "quote": p.citation.quote,
                "flipped": p.flipped,
                "new_label": p.new_label,
                "new_confidence": p.new_confidence,
            }
            for p in r.probes
        ],
    }


def consistency_dict(r: ConsistencyReport) -> dict:
    return {
        "robust": r.robust,
        "testable": r.testable,
        "stability": r.stability,
        "flips": [v.perturbation.name for v in r.flips],
        "variants": [
            {
                "name": v.perturbation.name,
                "changed": v.changed,
                "label": v.label,
                "confidence": v.confidence,
                "agreed": v.agreed,
            }
            for v in r.variants
        ],
    }


def calibration_dict(r: CalibrationReport) -> dict:
    return {
        "n": r.n,
        "errors": r.errors,
        "accuracy": r.accuracy,
        "mean_confidence": r.mean_confidence,
        "gap": r.gap,
        "ece": r.ece,
        "brier": r.brier,
        "overconfident": r.overconfident,
        "underconfident": r.underconfident,
        "reliability": [
            {
                "low": b.low,
                "high": b.high,
                "count": b.count,
                "avg_confidence": b.avg_confidence,
                "accuracy": b.accuracy,
            }
            for b in r.reliability()
        ],
    }


def audit_dict(r: AuditReport) -> dict:
    return {
        "verdict": verdict_dict(r.verdict),
        "grounded": r.grounded,
        "faithful": r.faithful,
        "robust": r.robust,
        "trustworthy": r.trustworthy,
        "passed": r.passed,
        "checks": r.checks,
        "faithfulness": faithfulness_dict(r.faithfulness),
        "consistency": consistency_dict(r.consistency),
    }


def batch_item_dict(item: BatchItem) -> dict:
    if item.report is not None:
        return {
            "document": item.document,
            "grounded": item.report.grounded,
            "faithful": item.report.faithful,
            "robust": item.report.robust,
            "trustworthy": item.report.trustworthy,
        }
    return {"document": item.document, "error": item.error}


def batch_dict(r: BatchReport) -> dict:
    return {
        "n": r.n,
        "errors": r.errors,
        "grounded_rate": r.grounded_rate,
        "faithful_rate": r.faithful_rate,
        "robust_rate": r.robust_rate,
        "trustworthy_rate": r.trustworthy_rate,
        "items": [batch_item_dict(item) for item in r.items],
    }


def lens_dict(r: LensResult) -> dict:
    return {
        "model": r.model,
        "predicted": r.predicted,
        "predicted_prob": r.predicted_prob,
        "prompt_tokens": r.prompt_tokens,
        "layer_trace": [
            {"layer": s.layer, "token": s.token, "prob": s.prob} for s in r.layer_trace
        ],
        "attributions": [
            {"index": a.index, "token": a.token, "delta": a.delta} for a in r.attributions
        ],
    }
