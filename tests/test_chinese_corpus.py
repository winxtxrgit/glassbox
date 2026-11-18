import json
from collections import Counter
from pathlib import Path

from glassbox.backends import StubBackend
from glassbox.calibration import load_dataset
from glassbox.triage import triage

CORPUS = Path(__file__).resolve().parents[1] / "examples" / "chinese.jsonl"


def test_chinese_corpus_is_balanced_and_loadable():
    rows = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 6
    assert Counter(row["label"] for row in rows) == {"suspicious": 3, "benign": 3}
    assert len({row["id"] for row in rows}) == len(rows)
    assert all({"id", "document", "label", "category"} <= row.keys() for row in rows)
    assert all(
        row["category"] and any("\u4e00" <= c <= "\u9fff" for c in row["document"]) for row in rows
    )
    examples = load_dataset(CORPUS)
    assert [example.document for example in examples] == [row["document"] for row in rows]
    assert [example.gold for example in examples] == [row["label"] for row in rows]


def test_chinese_example_supports_verbatim_evidence_grounding():
    example = load_dataset(CORPUS)[0]
    quote = "输入密码"
    backend = StubBackend(
        json.dumps({"label": example.gold, "confidence": 0.9, "evidence": [quote]})
    )
    verdict = triage(example.document, backend)
    assert verdict.grounded
    citation = verdict.citations[0]
    assert example.document[citation.start : citation.end] == quote
