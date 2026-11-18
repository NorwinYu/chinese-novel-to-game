"""Volume / chapter segmentation with duplicate-heading repair and numbering audit.

The defaults cover common Chinese typesetting conventions only. Anything unusual about a
particular book goes into that book's `book.json` under "headings" (see HeadingRules)."""
from __future__ import annotations

import difflib
import json
import re
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path

_DIGITS = {"零": 0, "〇": 0, "○": 0, "一": 1, "壹": 1, "二": 2, "两": 2, "贰": 2, "三": 3, "叁": 3,
           "四": 4, "肆": 4, "五": 5, "伍": 5, "六": 6, "陆": 6, "七": 7, "柒": 7, "八": 8, "捌": 8,
           "九": 9, "玖": 9}
_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000, "万": 10000}
NUM = r"[0-9０-９零〇○一二三四五六七八九十百千万两壹贰叁肆伍陆柒捌玖拾佰仟]+"


def cn_to_int(s: str) -> int | None:
    s = s.strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if not any(c in _UNITS for c in s):
        if all(c in _DIGITS for c in s):
            return int("".join(str(_DIGITS[c]) for c in s))
        return None
    total = section = num = 0
    for c in s:
        if c in _DIGITS:
            num = _DIGITS[c]
        elif c in _UNITS:
            unit = _UNITS[c]
            if unit == 10000:
                total += (section + num) * unit
                section = 0
            else:
                section += (num if num else 1) * unit
            num = 0
        else:
            return None
    return total + section + num


@dataclass
class HeadingRules:
    chapter_units: str = "章回节集"
    volume_units: str = "卷部"
    # Characters that, right after a unit, mean the line is prose ("第一回合", "第二节课").
    unit_not_followed_by: dict[str, str] = field(default_factory=lambda: {"回": "合", "节": "课"})
    special: list[str] = field(default_factory=lambda: ["楔子", "序章", "序", "引子", "尾声", "后记", "番外"])
    # Extra regexes for chapter headings; optional named groups (?P<num>...) and (?P<title>...).
    # "{NUM}" expands to the Chinese/Arabic numeral class.
    extra_chapter_patterns: list[str] = field(default_factory=list)
    # Lines matching these are never treated as headings.
    ignore_patterns: list[str] = field(default_factory=list)
    max_heading_len: int = 40
    # Allow "<short prefix> 第X章 标题" running headers to be recognised as headings.
    allow_prefix: bool = True

    @classmethod
    def load(cls, book_json: Path | None) -> "HeadingRules":
        if book_json is None or not book_json.exists():
            return cls()
        data = json.loads(book_json.read_text(encoding="utf-8")).get("headings", {})
        rules = cls()
        for k, v in data.items():
            if hasattr(rules, k):
                setattr(rules, k, v)
        return rules

    def compile(self) -> dict[str, list[re.Pattern] | re.Pattern]:
        prefix = r"(?:\S{1,12}\s+)?" if self.allow_prefix else ""
        chapter = re.compile(rf"^{prefix}第\s*(?P<num>{NUM})\s*(?P<unit>[{self.chapter_units}])\s*[:：、.．]?\s*(?P<title>.*)$")
        volume = re.compile(rf"^第\s*(?P<num>{NUM})\s*(?P<unit>[{self.volume_units}])\s*[:：、.．]?\s*(?P<title>.*)$")
        special = re.compile(rf"^(?P<kind>{'|'.join(map(re.escape, self.special))})\s*[:：、]?\s*(?P<title>.{{0,20}})$") if self.special else None
        return {
            "chapter": chapter,
            "volume": volume,
            "special": special,
            "extra": [re.compile(p.replace("{NUM}", NUM)) for p in self.extra_chapter_patterns],
            "ignore": [re.compile(p) for p in self.ignore_patterns],
            "bare": re.compile(rf"^(?P<num>{NUM})[、.．]?$"),
        }


@dataclass
class Heading:
    line_no: int
    kind: str  # chapter | volume | special | bare
    number: int | None
    title: str
    raw: str


@dataclass
class Chapter:
    idx: int
    part: int
    volume: str
    number: int | None
    title: str
    heading: str
    paragraphs: list[str]
    flags: list[str] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return sum(len(p) for p in self.paragraphs)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["chars"] = self.chars
        return d


def detect_headings(lines: list[str], rules: HeadingRules) -> list[Heading]:
    pats = rules.compile()
    out: list[Heading] = []
    for i, line in enumerate(lines):
        if len(line) > rules.max_heading_len or line.count("，") > 2 or line.startswith(("“", "「", '"')):
            continue
        if any(p.search(line) for p in pats["ignore"]):
            continue
        if m := pats["chapter"].match(line):
            title = m.group("title").strip()
            if title[:1] and rules.unit_not_followed_by.get(m.group("unit"), "") and title[0] in rules.unit_not_followed_by[m.group("unit")]:
                continue
            out.append(Heading(i, "chapter", cn_to_int(m.group("num")), title, line))
        elif m := pats["volume"].match(line):
            out.append(Heading(i, "volume", cn_to_int(m.group("num")), m.group("title").strip(), line))
        elif pats["special"] is not None and (m := pats["special"].match(line)):
            out.append(Heading(i, "special", None, (m.group("kind") + " " + m.group("title")).strip(), line))
        elif m := next((p.match(line) for p in pats["extra"] if p.match(line)), None):
            gd = m.groupdict()
            out.append(Heading(i, "chapter", cn_to_int(gd.get("num") or ""), (gd.get("title") or "").strip(), line))
        elif m := pats["bare"].match(line):
            out.append(Heading(i, "bare", cn_to_int(m.group("num")), "", line))
    return out


def _choose_scheme(heads: list[Heading]) -> set[str]:
    n_chapter = sum(h.kind == "chapter" for h in heads)
    bare = [h.number for h in heads if h.kind == "bare" and h.number is not None]
    if n_chapter >= 3:
        return {"chapter", "volume", "special"}
    increasing = sum(1 for a, b in zip(bare, bare[1:]) if b == a + 1)
    if len(bare) >= 3 and increasing >= 0.6 * (len(bare) - 1):
        return {"bare", "volume", "special"}
    return set()


def _norm_title(t: str) -> str:
    t = re.sub(r"[【\[（(][^】\]）)]*[】\]）)]", "", t)
    return re.sub(r"[\s！!。，,：:？?…\.·]", "", t)


def _similar_title(a: str, b: str) -> bool:
    a, b = _norm_title(a), _norm_title(b)
    if not a or not b:
        return True  # an untitled repeat of a heading counts as the same heading
    if a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.5


_NON_CJK = re.compile(r"[^\u4e00-\u9fff]")


def _para_keys(paragraphs: list[str], n: int = 10, mod: int = 16) -> list[str]:
    """Sampled character n-grams of the CJK-only text; insensitive to punctuation, line wrapping and
    small insertions, so two copies of a chapter share most keys."""
    s = _NON_CJK.sub("", "".join(paragraphs))
    keys = {s[i:i + n] for i in range(len(s) - n + 1)}
    return [k for k in keys if zlib.crc32(k.encode()) % mod == 0]


def _drop_duplicate_chapters(chapters: list[Chapter], anomalies: list[dict], threshold: float = 0.6) -> list[Chapter]:
    """Drop chapters whose text was already seen (re-posted chapters, possibly out of order, merged with a
    neighbour, or with slightly different noise). If an earlier copy turns out to be a truncated version of
    a later one, the later text replaces it at the earlier position."""
    owner: dict[str, int] = {}
    kept: list[Chapter] = []
    kept_keys: list[list[str]] = []
    for c in chapters:
        keys = _para_keys(c.paragraphs)
        votes: dict[int, int] = {}
        for k in keys:
            if k in owner:
                votes[owner[k]] = votes.get(owner[k], 0) + 1
        seen = sum(votes.values())
        best = max(votes, key=votes.get) if votes else None
        if keys and seen / len(keys) >= threshold:
            anomalies.append({"type": "duplicate_chapter_dropped", "heading": c.heading,
                              "duplicate_of": [kept[i].heading for i in sorted(votes, key=votes.get, reverse=True)[:3]],
                              "overlap": round(seen / len(keys), 2)})
            if len(votes) == 1 and c.chars > kept[best].chars * 1.05:
                kept[best].paragraphs = c.paragraphs
                kept[best].flags.append("replaced_by_longer_duplicate")
            continue
        if best is not None and kept_keys[best] and votes[best] / len(kept_keys[best]) >= threshold:
            target = kept[best]
            anomalies.append({"type": "truncated_copy_replaced", "heading": target.heading, "by": c.heading,
                              "old_chars": target.chars, "new_chars": c.chars})
            target.paragraphs = c.paragraphs
            target.flags.append("replaced_by_longer_duplicate")
            for k in keys:
                owner.setdefault(k, best)
            kept_keys[best] = keys
            continue
        for k in keys:
            owner.setdefault(k, len(kept))
        kept.append(c)
        kept_keys.append(keys)
    for i, c in enumerate(kept):
        c.idx = i
    return kept


@dataclass
class SplitResult:
    chapters: list[Chapter]
    front_matter: list[str]
    scheme: list[str]
    anomalies: list[dict]


def _synthetic(lines: list[str], fallback_chars: int) -> SplitResult:
    chapters: list[Chapter] = []
    buf: list[str] = []
    size = 0
    for p in lines:
        buf.append(p)
        size += len(p)
        if size >= fallback_chars:
            chapters.append(Chapter(len(chapters), 0, "", len(chapters) + 1, "", "", buf, ["synthetic"]))
            buf, size = [], 0
    if buf:
        chapters.append(Chapter(len(chapters), 0, "", len(chapters) + 1, "", "", buf, ["synthetic"]))
    return SplitResult(chapters, [], ["synthetic"], [{"type": "no_headings"}])


def split_chapters(lines: list[str], rules: HeadingRules | None = None, *, fallback_chars: int = 6000) -> SplitResult:
    rules = rules or HeadingRules()
    all_heads = detect_headings(lines, rules)
    scheme = _choose_scheme(all_heads)
    heads = [h for h in all_heads if h.kind in scheme]
    if not heads:
        return _synthetic(lines, fallback_chars)
    anomalies: list[dict] = []

    def gap_chars(a: Heading, b: Heading) -> int:
        return sum(len(p) for p in lines[a.line_no + 1: b.line_no])

    # Pass 1: collapse runs of repeated headings with (almost) nothing between them, e.g.
    # "第十章 夜雨" / "第十章 夜語" / "某书 第十章 夜雨" / "第十章". The run may disagree on the number.
    groups: list[list[Heading]] = []
    for h in heads:
        if groups:
            g = groups[-1]
            last = g[-1]
            if (last.kind == h.kind and gap_chars(last, h) < 80
                    and ((h.number is not None and h.number == last.number) or _similar_title(last.title, h.title))):
                g.append(h)
                continue
        groups.append([h])

    collapsed: list[tuple[Heading, list[Heading]]] = []
    for g in groups:
        prev_num = next((c.number for c, _ in reversed(collapsed) if c.number is not None), None)
        fitting = [h for h in g if prev_num is not None and h.number == prev_num + 1]
        num_src = fitting[0] if fitting else g[-1]
        title_src = max(g, key=lambda h: len(_norm_title(h.title)))
        rep = Heading(g[-1].line_no, g[-1].kind, num_src.number, title_src.title, title_src.raw)
        collapsed.append((rep, g))

    # Pass 2: a number that jumps backwards while the next heading continues the sequence is either a
    # typo in a real heading (a chapter-sized body follows) or a prose line that looks like a heading.
    sizes = sorted(gap_chars(collapsed[k][1][-1], collapsed[k + 1][1][0]) for k in range(len(collapsed) - 1))
    median = sizes[len(sizes) // 2] if sizes else 0
    prose_punct = re.compile("[，。；]")
    punct_share = sum(bool(prose_punct.search(h.raw)) for h, _ in collapsed) / max(1, len(collapsed))
    filtered: list[tuple[Heading, list[Heading]]] = []
    for k, (h, g) in enumerate(collapsed):
        last = next((c.number for c, _ in reversed(filtered) if c.number is not None and c.kind == h.kind), None)
        nxt = collapsed[k + 1] if k + 1 < len(collapsed) else None
        if (h.kind in ("chapter", "bare") and h.number is not None and last is not None
                and h.number < last and not (h.number <= 3 and last > 10)
                and nxt is not None and nxt[0].number is not None and nxt[0].number in (last + 1, last + 2)):
            looks_like_prose = punct_share < 0.1 and bool(prose_punct.search(h.raw))
            if gap_chars(g[-1], nxt[1][0]) >= 0.3 * median and not looks_like_prose:
                anomalies.append({"type": "heading_number_typo", "line": h.raw, "assumed": last + 1})
                h = Heading(h.line_no, h.kind, last + 1, h.title, h.raw)
            else:
                anomalies.append({"type": "false_heading_ignored", "line": h.raw})
                continue
        filtered.append((h, g))

    heading_lines = {x.line_no for _, g in filtered for x in g}
    front = lines[: filtered[0][1][0].line_no]
    chapters: list[Chapter] = []
    volume = ""
    for k, (h, g) in enumerate(filtered):
        start = g[0].line_no
        end = filtered[k + 1][1][0].line_no if k + 1 < len(filtered) else len(lines)
        body = [lines[i] for i in range(start + 1, end) if i not in heading_lines]
        chars = sum(len(p) for p in body)
        if h.kind == "volume":
            volume = h.raw
            if chars < 200:
                continue
        flags = ["duplicate_heading_merged"] if len(g) > 1 else []
        n = h.number if h.kind != "volume" else None
        chapters.append(Chapter(len(chapters), 0, volume, n, h.title if h.kind != "bare" else "", h.raw, body, flags))

    chapters = _drop_duplicate_chapters(chapters, anomalies)
    _audit_numbers(chapters, anomalies)
    return SplitResult(chapters, front, sorted(scheme), anomalies)


def _audit_numbers(chapters: list[Chapter], anomalies: list[dict]) -> None:
    part, prev = 0, None
    for c in chapters:
        n, flags = c.number, c.flags
        if n is not None and prev is not None:
            if n == prev:
                flags.append("number_repeated")
                anomalies.append({"type": "number_repeated", "idx": c.idx, "heading": c.heading})
            elif n < prev and n <= 3 and prev > 10:
                part += 1
                anomalies.append({"type": "numbering_restart", "idx": c.idx, "heading": c.heading, "after": prev})
            elif n > prev + 1:
                flags.append("gap_before")
                anomalies.append({"type": "gap", "idx": c.idx, "heading": c.heading, "missing": [prev + 1, n - 1]})
            elif n < prev:
                flags.append("number_backwards")
                anomalies.append({"type": "backwards", "idx": c.idx, "heading": c.heading, "after": prev})
        if n is not None:
            prev = n
        c.part = part
        if c.chars < 50:
            flags.append("empty_body")
            anomalies.append({"type": "empty_body", "idx": c.idx, "heading": c.heading})
    if part:
        sizes: dict[int, int] = {}
        for c in chapters:
            sizes[c.part] = sizes.get(c.part, 0) + c.chars
        main = max(sizes, key=sizes.get)
        for c in chapters:
            if c.part != main:
                c.flags.append("extra_part")
