"""Decoding and noise removal for raw novel text.

Only book-independent structural rules live here. Anything specific to a source (site watermarks,
author notes, running headers) is discovered per book by `noise_scan` and stored in that book's
`noise_rules.json`, so no novel text or site names are baked into the code."""
from __future__ import annotations

import codecs
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
_SPACES = re.compile("[\u3000\u00a0\t ]+")
_CJK = re.compile("[\u3400-\u9fff]")
_URL = re.compile(r"(https?://\S+|www\.[\w.\-/]+|\b[\w\-]+\.(?:com|net|org|cn|cc|info|me)\b[\w./\-]*)", re.I)
_HEADING_LIKE_ASCII = re.compile(r"^([0-9０-９]{1,4}|[IVXLCDM]{1,8})[、.．]?$")


@dataclass
class Decoded:
    text: str
    encoding: str
    replaced: int


def decode_bytes(data: bytes) -> Decoded:
    if data.startswith(codecs.BOM_UTF8):
        data = data[len(codecs.BOM_UTF8):]
    if data[:2] in (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE):
        return Decoded(data.decode("utf-16", errors="replace"), "utf-16", 0)
    try:
        return Decoded(data.decode("utf-8"), "utf-8", 0)
    except UnicodeDecodeError:
        pass
    best: Decoded | None = None
    for enc in ("gb18030", "big5", "utf-8"):
        text = data.decode(enc, errors="replace")
        cand = Decoded(text, enc, text.count("\ufffd"))
        if best is None or cand.replaced < best.replaced:
            best = cand
    assert best is not None
    return best


@dataclass
class NoiseRule:
    id: str
    scope: str  # "line": drop the whole line; "inline": delete only the matched text
    pattern: str
    description: str = ""
    enabled: bool = True

    def compiled(self) -> re.Pattern:
        return re.compile(self.pattern)


_PUA = re.compile("[\ue000-\uf8ff\ufffd]")


def _plausibility(text: str, freq: Counter) -> float:
    """Mean log-frequency of the characters under the book's own character distribution; mis-decoded
    text is made of characters the book almost never uses."""
    import math
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return float("-inf")
    return sum(math.log(freq.get(c, 0) + 1) if not _PUA.match(c) else -3.0 for c in chars) / len(chars)


def gbk_shift_repair(span: str, freq: Counter) -> str:
    """Re-decode text that was read one byte out of step in a GBK-family file."""
    try:
        data = span.encode("gb18030")
    except UnicodeEncodeError:
        return span
    best, best_score = span, _plausibility(span, freq)
    for shift in (1, 2):
        cand = data[shift:].decode("gb18030", errors="replace")
        score = _plausibility(cand, freq)
        if score > best_score + 1.0:
            best, best_score = cand, score
    return _PUA.sub("", best) if best is not span else span


REPAIRS = {"gbk_byte_shift": gbk_shift_repair}


@dataclass
class Repair:
    id: str
    kind: str  # key of REPAIRS
    pattern: str  # the span to repair is group 1 (or the whole match); the whole match is replaced
    description: str = ""

    def apply(self, line: str, freq: Counter) -> tuple[str, int]:
        fn = REPAIRS[self.kind]
        pat = re.compile(self.pattern)
        return pat.subn(lambda m: fn(m.group(1) if m.groups() else m.group(0), freq), line)


def load_repairs(path: Path) -> list[Repair]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for r in data.get("repairs", []):
        if r["kind"] not in REPAIRS:
            raise ValueError(f"unknown repair kind {r['kind']!r}; available: {sorted(REPAIRS)}")
        out.append(Repair(r["id"], r["kind"], r["pattern"], r.get("description", "")))
    return out


def load_rules(path: Path) -> list[NoiseRule]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    rules = []
    for r in data.get("rules", []):
        rule = NoiseRule(r["id"], r.get("scope", "inline"), r["pattern"], r.get("description", ""), r.get("enabled", True))
        if rule.enabled:
            rule.compiled()  # fail fast on an invalid pattern
            rules.append(rule)
    return rules


@dataclass
class CleanReport:
    encoding: str
    replaced_chars: int
    removed_lines: Counter = field(default_factory=Counter)
    inline_removed: Counter = field(default_factory=Counter)
    samples: dict[str, list[str]] = field(default_factory=dict)
    masked_spans: int = 0
    rules_applied: int = 0

    def note(self, rule: str, line: str, *, inline: bool = False) -> None:
        (self.inline_removed if inline else self.removed_lines)[rule] += 1
        bucket = self.samples.setdefault(rule, [])
        if len(bucket) < 6:
            bucket.append(line[:100])

    def to_dict(self) -> dict:
        return {
            "encoding": self.encoding,
            "replaced_chars": self.replaced_chars,
            "rules_applied": self.rules_applied,
            "removed_lines": dict(self.removed_lines),
            "inline_removed": dict(self.inline_removed),
            "samples": self.samples,
            "masked_spans": self.masked_spans,
        }


def structural_noise(line: str) -> str | None:
    if "\ufffd" in line and len(line.replace("\ufffd", "").strip()) <= 3:
        return "garbled"
    if not _CJK.search(line) and len(line) <= 40 and not _HEADING_LIKE_ASCII.match(line):
        return "no_cjk_short_line"
    return None


_TERMINAL = tuple("。！？!?…”」』）)：:")


def _curly(line: str) -> str:
    """ASCII double quotes to curly ones by alternation; quotes.find_quotes repairs direction from context."""
    parts = line.split('"')
    return "".join(p + ("“" if i % 2 == 0 else "”") for i, p in enumerate(parts[:-1])) + parts[-1]


def _unwrap(lines: list[str]) -> list[str]:
    """Join lines that a fixed-width export broke mid-sentence: a line near the common full width that
    does not end a sentence continues on the next line."""
    lengths = Counter(len(l) for l in lines if len(l) >= 10)
    if not lengths:
        return lines
    width = lengths.most_common(1)[0][0]
    out: list[str] = []
    carry = False
    for line in lines:
        if carry and out:
            out[-1] += line
        else:
            out.append(line)
        carry = len(line) >= 0.8 * width and not line.endswith(_TERMINAL)
    return out


def normalize_lines(raw: str, profile: dict | None = None) -> list[str]:
    """Lines without blanks and odd spacing. `profile` is book.json "profile": quotes == "straight"
    turns ASCII double quotes into curly ones, wrap == "hard" joins hard-wrapped lines."""
    profile = profile or {}
    text = _ZERO_WIDTH.sub("", raw.replace("\r\n", "\n").replace("\r", "\n"))
    out = []
    for line in text.split("\n"):
        line = _SPACES.sub(" ", line).strip()
        if line:
            out.append(_curly(line) if profile.get("quotes") == "straight" and '"' in line else line)
    return _unwrap(out) if profile.get("wrap") == "hard" else out


def clean_text(raw: str, encoding: str = "utf-8", replaced: int = 0,
               rules: list[NoiseRule] | None = None, repairs: list[Repair] | None = None,
               repair_line=None, profile: dict | None = None) -> tuple[list[str], CleanReport]:
    report = CleanReport(encoding=encoding, replaced_chars=replaced, rules_applied=len(rules or []))
    compiled = [(r, r.compiled()) for r in rules or []]
    out: list[str] = []
    freq = Counter(raw) if repairs else Counter()
    for line in normalize_lines(raw, profile):
        original = line
        if repair_line is not None:
            fixed = repair_line(line)
            if fixed is not None and fixed != line:
                report.note("plugin.repair_line", line, inline=True)
                line = fixed
        for rep in repairs or []:
            line, n = rep.apply(line, freq)
            if n:
                report.note(rep.id, line, inline=True)
        line, n = _URL.subn("", line)
        if n:
            report.note("url", original, inline=True)
        dropped = False
        for rule, pat in compiled:
            if rule.scope == "line":
                if pat.search(line):
                    report.note(rule.id, original)
                    dropped = True
                    break
            else:
                line, n = pat.subn("", line)
                if n:
                    report.note(rule.id, original, inline=True)
        line = line.strip()
        if dropped or not line:
            continue
        kind = structural_noise(line)
        if kind:
            report.note(kind, line)
            continue
        report.masked_spans += len(re.findall(r"\*{2,}", line))
        out.append(line)
    return out, report
