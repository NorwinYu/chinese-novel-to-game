"""Locate quotations in a paragraph, tolerating nested and mis-directed quotation marks."""
from __future__ import annotations

import re

_CORNER = re.compile(r"「[^「」]*」|『[^『』]*』")
_MARKS = re.compile("[“”]")
_OPEN_AFTER = "：:"            # 道：“ — a mark right after a colon opens a quotation
_CLOSE_BEFORE = "，,。；;、"     # ”， — a mark right before clause punctuation closes one
_SENTENCE_END = re.compile(r"[。！？!?…]+")


def _role(paragraph: str, pos: int) -> int:
    """+1 if the context says this mark opens a quotation, -1 if it closes one, 0 if unclear."""
    head, tail = paragraph[:pos].rstrip(), paragraph[pos + 1:].lstrip()
    opens = not head or head[-1] in _OPEN_AFTER
    closes = not tail or tail[0] in _CLOSE_BEFORE
    return (opens and not closes) - (closes and not opens)


def _by_direction(paragraph: str, marks: list[tuple[int, bool]]) -> list[tuple[int, int]] | None:
    """Spans from properly nested marks; None if they do not nest."""
    curly: list[tuple[int, int]] = []
    depth, start = 0, 0
    for i, (pos, is_open) in enumerate(marks):
        if is_open:
            if depth == 0:
                start = pos
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0:
                curly.append((start, pos + 1))
        elif i == 0 and paragraph[:pos].strip():
            curly.append((0, pos + 1))  # paragraph starts inside a quotation carried over
        else:
            return None
    if depth > 1:
        return None
    if depth == 1:
        curly.append((start, len(paragraph)))
    return curly


def _by_context(paragraph: str, marks: list[tuple[int, bool]], roles: list[int]) -> list[tuple[int, int]]:
    """Ignore mark direction: context decides where it can, otherwise marks alternate open/close."""
    first = next((i for i, r in enumerate(roles) if r), None)
    carried = (first is not None and (roles[first] > 0) == (first % 2 == 1)
               and bool(paragraph[:marks[0][0]].strip()))
    curly: list[tuple[int, int]] = []
    open_at: int | None = 0 if carried else None
    for (pos, _), r in zip(marks, roles):
        if open_at is None:
            if r >= 0:
                open_at = pos
        elif r > 0:
            # A new quotation starts while one is open: the closing mark is missing. End the open one
            # at its last sentence break, since the words before this mark are the narrator's cue.
            ends = [m.end() for m in _SENTENCE_END.finditer(paragraph, open_at + 1, pos)]
            curly.append((open_at, ends[-1] if ends else pos))
            open_at = pos
        else:
            curly.append((open_at, pos + 1))
            open_at = None
    if open_at is not None:
        curly.append((open_at, len(paragraph)))
    return curly


def find_quotes(paragraph: str) -> list[tuple[int, int]]:
    """Top-level quotation spans (start, end) in a paragraph.

    Scanned and converted texts often have curly marks pointing the wrong way, for a whole paragraph
    or from some point on. Mark direction is trusted only when the marks nest and no mark contradicts
    its context (after a colon it opens; before a comma or at the paragraph end it closes). Otherwise
    the context and simple alternation decide. A paragraph may start inside a quotation carried over
    from the previous one; an unclosed last quotation runs to the end of the paragraph."""
    spans = [(m.start(), m.end()) for m in _CORNER.finditer(paragraph)]
    marks = [(m.start(), m.group(0) == "“") for m in _MARKS.finditer(paragraph)]
    roles = [_role(paragraph, pos) for pos, _ in marks]
    curly = None
    if all(r == 0 or (r > 0) == is_open for r, (_, is_open) in zip(roles, marks)):
        curly = _by_direction(paragraph, marks)
    if curly is None:
        curly = _by_context(paragraph, marks, roles)
    curly = [(a, b) for a, b in curly if paragraph[a:b].strip("“”").strip()]
    inside = lambda p: any(a <= p < b for a, b in curly)
    spans = [s for s in spans if not inside(s[0])] + curly
    return sorted(spans)
