"""Split long text into sentence-sized chunks for pipelined synthesis.

A small, dependency-free splitter: it breaks on sentence terminators
(``.`` ``!`` ``?`` ``…``) while guarding common abbreviations and initials, and
hard-splits any run-on longer than ``max_chars`` at a whitespace boundary so one
pathological "sentence" cannot defeat the point of pipelining. It is
intentionally simple — good enough to start speaking a paragraph sooner, never a
linguistics engine — and pure (no I/O, no state), so the queue worker can call
it and unit tests can pin every rule.

The gateway only reaches for this when a text is long enough to be worth
pipelining (``speech.chunking``); the *decision* to split lives in the service,
this module just does the splitting.
"""

from __future__ import annotations

import re

#: Longest chunk handed to a provider as a single unit. A sentence (or a run-on
#: with no terminator) beyond this is hard-split at whitespace, so a wall of text
#: without punctuation still pipelines instead of blocking on one huge clip.
MAX_CHUNK_CHARS = 400

#: Words that are almost never sentence-final, so a period after one is not a
#: boundary. Lower-cased, without the trailing dot. Kept deliberately
#: conservative: guarding a title (``Dr.``) or initial avoids an audibly-bad
#: split before a name, but guarding an ambiguous abbreviation like ``etc.`` (as
#: often sentence-final as not) would wrongly *glue* sentences together — so
#: those are left out and allowed to split. Single-letter initials (``e.g.``,
#: ``J. R.``) are handled separately, not listed here.
_ABBREVIATIONS = frozenset(
    {
        "mr", "mrs", "ms", "dr", "prof", "rev", "hon", "sr", "jr", "st", "vs",
        "gov", "gen", "sen", "rep", "col", "capt", "sgt", "lt", "cmdr",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
        "nov", "dec",
    }
)  # fmt: skip

#: A run of sentence terminators that is followed by whitespace or end-of-text.
#: Terminators mid-token (``U.S.A``, ``3.14``) are not followed by space and so
#: never match; the run is kept with the sentence it closes.
_BOUNDARY = re.compile(r"[.!?…]+(?=\s|$)")

#: The alphanumeric word immediately preceding a boundary (for the guards).
_TRAILING_WORD = re.compile(r"(\w+)$")


def split_into_chunks(text: str, *, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Break ``text`` into a list of sentence-sized, non-empty chunks.

    Whitespace around each chunk is stripped. Text with no sentence boundaries
    (and short enough) comes back as a single-element list, so the caller can
    treat "one chunk" as "don't pipeline". ``max_chars`` bounds any single
    chunk, hard-splitting at the last space before the limit (or mid-word only
    when there is no space at all).
    """
    if not text.strip():
        return []

    pieces: list[str] = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        if not _is_real_boundary(text, match.start()):
            continue
        piece = text[start : match.end()].strip()
        if piece:
            pieces.append(piece)
        start = match.end()
    tail = text[start:].strip()
    if tail:
        pieces.append(tail)

    chunks: list[str] = []
    for piece in pieces:
        if len(piece) > max_chars:
            chunks.extend(_hard_split(piece, max_chars))
        else:
            chunks.append(piece)
    return [chunk for chunk in chunks if chunk]


def _is_real_boundary(text: str, terminator_start: int) -> bool:
    """Whether the terminator run at ``terminator_start`` ends a sentence.

    Rejects a period that merely closes an abbreviation (``etc.``) or a single
    initial (``J.``, and by extension ``e.g.`` / ``i.e.``).
    """
    word_match = _TRAILING_WORD.search(text[:terminator_start])
    if word_match is None:
        return True
    word = word_match.group(1)
    if word.lower() in _ABBREVIATIONS:
        return False
    # A lone letter before the dot is an initial (``J.``) or the tail of an
    # abbreviation like ``e.g.`` / ``i.e.`` — not a sentence end.
    return not (len(word) == 1 and word.isalpha())


def _hard_split(piece: str, max_chars: int) -> list[str]:
    """Split an over-long ``piece`` into <= ``max_chars`` parts at whitespace."""
    parts: list[str] = []
    while len(piece) > max_chars:
        cut = piece.rfind(" ", 0, max_chars)
        if cut <= 0:  # a single unbroken token longer than the limit
            cut = max_chars
        head = piece[:cut].strip()
        if head:
            parts.append(head)
        piece = piece[cut:].strip()
    if piece:
        parts.append(piece)
    return parts
