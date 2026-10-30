"""Locate the model's quoted evidence back in the source text.

The model is asked to quote verbatim, but models paraphrase, re-wrap whitespace,
or invent support. :func:`ground` finds each quote's real character span when it
exists and flags it as unverified when it doesn't — turning a free-text rationale
into something a machine can audit.
"""

from __future__ import annotations

import re

from .schema import Citation


def find_span(source: str, quote: str) -> tuple[int, int]:
    """Return the ``(start, end)`` offsets of *quote* in *source*, or ``(-1, -1)``.

    Tries an exact match first, then a whitespace-flexible match so a quote that
    only differs by re-wrapped spaces or newlines still resolves to real offsets
    (the flexible pattern is searched against the original text, so the returned
    span always indexes the untouched source).
    """
    q = quote.strip()
    if not q:
        return (-1, -1)

    idx = source.find(q)
    if idx != -1:
        return (idx, idx + len(q))

    # Whitespace-flexible fallback: match the same words with any run of
    # whitespace between them. Run on the original source to keep offsets honest.
    pattern = re.compile(r"\s+".join(re.escape(word) for word in q.split()))
    match = pattern.search(source)
    if match:
        return (match.start(), match.end())

    return (-1, -1)


def ground(source: str, quotes: list[str]) -> list[Citation]:
    """Resolve each quoted evidence string against *source* into a :class:`Citation`.

    Non-string or empty entries are skipped. A quote that cannot be found becomes
    a citation with ``verified=False`` and offsets of -1 — a recorded instance of
    the model citing something the document never contained.
    """
    citations: list[Citation] = []
    for quote in quotes:
        if not isinstance(quote, str) or not quote.strip():
            continue
        start, end = find_span(source, quote)
        citations.append(Citation(quote=quote, start=start, end=end, verified=start != -1))
    return citations
