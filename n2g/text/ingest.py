"""Stage 1: raw TXT -> cleaned, chapterised book.

Per-book configuration lives next to the build output and is written by the agent running the skill:
  games/<slug>/book.json         source path, title, optional "headings" overrides (HeadingRules)
  games/<slug>/noise_rules.json  source-specific noise rules discovered by `n2g noise-scan`
  games/<slug>/plugins/*.py      optional agent-written code (see n2g.plugins)
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .chapters import HeadingRules, split_chapters
from ..plugins import load_plugins
from .clean import clean_text, decode_bytes, load_repairs, load_rules


def load_book(book: Path) -> dict:
    path = book / "book.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing; run `n2g init` first")
    return json.loads(path.read_text(encoding="utf-8"))


def read_source(book: Path) -> tuple[str, str, int]:
    meta = load_book(book)
    decoded = decode_bytes(Path(meta["source"]).read_bytes())
    return decoded.text, decoded.encoding, decoded.replaced


def ingest(book: Path) -> dict:
    raw, encoding, replaced = read_source(book)
    rules = load_rules(book / "noise_rules.json")
    lines, clean_report = clean_text(raw, encoding, replaced, rules, load_repairs(book / "noise_rules.json"),
                                     repair_line=load_plugins(book).repair_line(), profile=load_book(book).get("profile"))
    result = split_chapters(lines, HeadingRules.load(book / "book.json"))
    build = book / "build"
    build.mkdir(parents=True, exist_ok=True)
    with (build / "chapters.jsonl").open("w", encoding="utf-8") as f:
        for c in result.chapters:
            f.write(json.dumps(c.to_dict(), ensure_ascii=False) + "\n")
    chars = [c.chars for c in result.chapters]
    summary = {
        "clean": clean_report.to_dict(),
        "scheme": result.scheme,
        "front_matter": result.front_matter[:10],
        "chapter_count": len(result.chapters),
        "parts": {str(k): v for k, v in sorted(Counter(c.part for c in result.chapters).items())},
        "total_chars": sum(chars),
        "chapter_chars": {"min": min(chars), "max": max(chars), "mean": round(sum(chars) / len(chars))} if chars else {},
        "flag_counts": dict(Counter(f for c in result.chapters for f in c.flags)),
        "anomaly_counts": dict(Counter(a["type"] for a in result.anomalies)),
        "anomalies": result.anomalies[:300],
        "first_chapters": [c.heading for c in result.chapters[:3]],
        "last_chapters": [c.heading for c in result.chapters[-3:]],
    }
    (build / "ingest_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def load_chapters(book: Path) -> list[dict]:
    with (book / "build" / "chapters.jsonl").open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]
