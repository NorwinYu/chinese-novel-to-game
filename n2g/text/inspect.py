"""Evidence and inspection helpers for the agent running the skill.

These commands never decide anything; they show the agent what the text looks like so it can form
hypotheses, write per-book rules, and check them against the whole book."""
from __future__ import annotations

import random
import re
from pathlib import Path

from .chapters import HeadingRules, split_chapters
from ..plugins import load_plugins
from .clean import clean_text, load_repairs, load_rules, normalize_lines
from .ingest import load_book, load_chapters, read_source
from .noise_scan import sample_chapters, structural_candidates
from .quotes import find_quotes


def evidence(book: Path, *, n_tails: int = 30, head_chars: int = 600, tail_chars: int = 1200, seed: int = 0) -> dict:
    """Statistics plus excerpts of the text after the current rules, written for the agent to read."""
    raw, _, _ = read_source(book)
    lines, report = clean_text(raw, rules=load_rules(book / "noise_rules.json"), repairs=load_repairs(book / "noise_rules.json"),
                               repair_line=load_plugins(book).repair_line(), profile=load_book(book).get("profile"))
    split = split_chapters(lines, HeadingRules.load(book / "book.json"))
    cands = structural_candidates(lines, len(split.chapters))
    rng = random.Random(seed)
    chapters = split.chapters
    tails = []
    for c in rng.sample(chapters, min(n_tails, len(chapters))):
        tails.append({"chapter": c.heading, "first_lines": c.paragraphs[:2], "last_lines": c.paragraphs[-3:]})
    parts = ["# 抽样原文（已应用当前规则）\n"]
    for c in sample_chapters(chapters):
        body = "\n".join(c.paragraphs)
        parts.append(f"\n## [{c.idx}] {c.heading}  ({c.chars}字)\n\n### 开头\n{body[:head_chars]}\n\n### 结尾\n{body[-tail_chars:]}\n")
    build = book / "build"
    build.mkdir(parents=True, exist_ok=True)
    (build / "evidence_samples.md").write_text("".join(parts), encoding="utf-8")
    out = {"front_matter": split.front_matter[:15], "candidates": cands, "chapter_edges": tails,
           "removed_by_current_rules": report.to_dict()}
    return out


def grep(book: Path, pattern: str, *, raw_text: bool = False, limit: int = 20, width: int = 40, seed: int = 0) -> dict:
    """Count and show a pattern in the raw text (before any noise rule) or in the built chapters."""
    pat = re.compile(pattern)
    if raw_text:
        src, _, _ = read_source(book)
        units = [("raw", i, line) for i, line in enumerate(normalize_lines(src, load_book(book).get("profile")))]
    else:
        units = [(c["heading"], i, p) for c in load_chapters(book) for i, p in enumerate(c["paragraphs"])]
    hits = []
    for where, i, line in units:
        quotes = find_quotes(line)
        for m in pat.finditer(line):
            hits.append({"where": where, "para": i, "in_dialogue": any(a <= m.start() < b for a, b in quotes),
                         "whole_line": m.group(0) == line,
                         "context": line[max(0, m.start() - width): m.end() + width]})
    shown = hits if len(hits) <= limit else random.Random(seed).sample(hits, limit)
    return {"pattern": pattern, "source": "raw" if raw_text else "chapters", "hits": len(hits),
            "in_dialogue": sum(h["in_dialogue"] for h in hits), "whole_line": sum(h["whole_line"] for h in hits),
            "shown": shown}


def show(book: Path, idx: int, *, start: int = 0, count: int = 40) -> dict:
    chapters = load_chapters(book)
    c = chapters[idx]
    return {"idx": c["idx"], "heading": c["heading"], "part": c["part"], "volume": c["volume"], "flags": c["flags"],
            "chars": c["chars"], "paragraphs": len(c["paragraphs"]),
            "text": [f"{i}: {p}" for i, p in enumerate(c["paragraphs"][start:start + count], start)]}


def long_chapters(book: Path, *, factor: float = 1.8, max_len: int = 30) -> list[dict]:
    """Unusually long chapters with their short, punctuation-free lines: the usual place where a heading in
    an unexpected format was missed and two chapters got merged."""
    chapters = load_chapters(book)
    sizes = sorted(c["chars"] for c in chapters)
    median = sizes[len(sizes) // 2] if sizes else 0
    plain = re.compile("[“”「」『』？！…。，、；：]")
    out = []
    for c in chapters:
        if c["chars"] < factor * median:
            continue
        short = [f"{i}: {p}" for i, p in enumerate(c["paragraphs"]) if len(p) <= max_len and not plain.search(p)]
        out.append({"idx": c["idx"], "heading": c["heading"], "chars": c["chars"], "median": median, "short_lines": short[:12]})
    return out


def anomalies(book: Path, kind: str | None = None, *, context: int = 2) -> list[dict]:
    """Chapter-numbering anomalies with the neighbouring headings, so the agent can judge each one."""
    chapters = load_chapters(book)
    out = []
    for k, c in enumerate(chapters):
        flags = [f for f in c["flags"] if f != "duplicate_heading_merged"]
        if not flags or (kind and kind not in flags):
            continue
        neigh = [f"[{j}] {chapters[j]['heading']} ({chapters[j]['chars']}字)"
                 for j in range(max(0, k - context), min(len(chapters), k + context + 1))]
        out.append({"idx": k, "flags": flags, "neighbours": neigh,
                    "opening": "".join(c["paragraphs"][:1])[:80]})
    return out
