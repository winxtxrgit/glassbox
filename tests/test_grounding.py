from __future__ import annotations

from glassbox.grounding import find_span, ground


def test_exact_quote_locates():
    source = "the quick brown fox"
    assert find_span(source, "brown fox") == (10, 19)
    assert source[10:19] == "brown fox"


def test_missing_quote_is_not_found():
    assert find_span("hello world", "goodbye") == (-1, -1)


def test_whitespace_reflow_still_locates_against_original_offsets():
    # The model re-wrapped a newline as a space; offsets must index the source.
    source = "pay the\ninvoice now"
    start, end = find_span(source, "pay the invoice")
    assert (start, end) == (0, 15)
    assert source[start:end] == "pay the\ninvoice"


def test_typographic_punctuation_and_whitespace_keep_source_offsets():
    source = "See: \u201cDon\u2019t\nstop\u2014yet\u201d."
    quote = '"Don\'t stop-yet"'
    assert find_span(source, quote) == (5, len(source) - 1)


def test_straight_source_matches_typographic_quote():
    source = "Now: don't stop."
    assert find_span(source, "don\u2019t stop") == (5, 15)


def test_whitespace_match_takes_precedence_over_normalized_match():
    source = "don\u2019t stop; don't\nstop"
    assert find_span(source, "don't stop") == (12, 22)


def test_ground_marks_verified_and_hallucinated():
    source = "Verify your password within 24 hours."
    citations = ground(source, ["Verify your password", "click the reward link"])
    assert citations[0].verified and citations[0].located
    assert not citations[1].verified
    assert citations[1].start == -1


def test_grounding_locates_thai_spans_by_codepoint():
    # Python strings index by codepoint, so Thai spans ground with correct offsets.
    source = "เรียนลูกค้า กรุณายืนยันรหัสผ่านภายใน 24 ชั่วโมง"
    start, end = find_span(source, "ยืนยันรหัสผ่าน")
    assert start != -1
    assert source[start:end] == "ยืนยันรหัสผ่าน"
    citations = ground(source, ["ยืนยันรหัสผ่าน", "คลิกลิงก์นี้"])
    assert citations[0].verified  # present
    assert not citations[1].verified  # not in the document


def test_ground_skips_empty_and_non_string_entries():
    citations = ground("anything", ["   ", "", 42, None])  # type: ignore[list-item]
    assert citations == []
