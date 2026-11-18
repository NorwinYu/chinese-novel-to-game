"""Stage 3: book lexicon for word segmentation.

Names, places, items, techniques and titles from the extraction become a jieba user dictionary, so
retrieval (BM25 over memories and facts) treats a three-character name or a coined technique name as one word. The evaluation
segments real sentences from the book that contain each term, with and without the dictionary, and
reports how often the term comes out as a single token, plus how much other segmentation changed."""
from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from ..extract.review import load_records
from ..text.ingest import load_chapters
from .characters import is_compound, is_descriptor, use_book_plugins

TAGS = {"person": "nr", "creature": "nr", "group": "nt", "place": "ns", "item": "nz", "technique": "nz",
        "title": "nz", "other": "nz"}
_CLAUSE = re.compile(r"[^，。！？；：、“”‘’「」『』（）()\s…—]+")
# Phrases the extractor sometimes lists as entities ("我们家", "两个人", "三十块"): pronoun, demonstrative or
# numeral heads and plural tails make them phrases, not words.
_PHRASE = re.compile(r"^[我你他她它咱这那哪此该某每各一二两三四五六七八九十百千万几半整好些]|[们的了着过]$")


def collect_terms(book: Path, *, min_chapters: int = 1) -> dict[str, dict]:
    """term -> {type, chapters}: every verified entity name and alias from the extraction."""
    terms: dict[str, dict] = {}
    chapters: dict[str, set[int]] = defaultdict(set)
    types: dict[str, Counter] = defaultdict(Counter)
    for rec in load_records(book):
        o = rec.get("result") or {}
        for e in o.get("entities", []):
            if e.get("unverified"):
                continue
            for s in [e["name"]] + e.get("aliases", []):
                chapters[s].add(rec["idx"])
                types[s][e.get("type", "other")] += 1
    for s, chs in chapters.items():
        t = types[s].most_common(1)[0][0]
        if len(s) < 2 or len(s) > 12 or is_compound(s) or (t in ("person", "creature") and is_descriptor(s)):
            continue
        if not re.fullmatch(r"[\u4e00-\u9fff·]+", s):
            continue
        if _PHRASE.search(s) and len(chs) < 3:
            continue
        if len(chs) >= min_chapters:
            terms[s] = {"type": t, "chapters": len(chs)}
    return terms


def _tokenizer(userdict: list[tuple[str, int, str]] | None = None):
    import jieba
    tk = jieba.Tokenizer()
    tk.initialize()
    for w, f, tag in userdict or []:
        tk.add_word(w, f, tag)
    return tk


def build_userdict(terms: dict[str, dict], text_counts: Counter) -> list[tuple[str, int, str]]:
    base = _tokenizer()
    out = []
    for w, info in sorted(terms.items(), key=lambda kv: (-len(kv[0]), kv[0])):
        need = base.suggest_freq(w, tune=False)
        out.append((w, max(need, text_counts.get(w, 0), 5), TAGS.get(info["type"], "nz")))
    return out


def evaluate(book: Path, userdict: list[tuple[str, int, str]], *, per_term: int = 3, max_terms: int = 1500,
             seed: int = 0) -> dict:
    """Single-token rate of dictionary terms in real clauses before and after, and how many tokens not
    covered by any term changed (collateral damage of the dictionary)."""
    rng = random.Random(seed)
    clauses = [c for ch in load_chapters(book) for p in ch["paragraphs"] for c in _CLAUSE.findall(p) if len(c) >= 4]
    words = [w for w, _, _ in userdict]
    vocab = set(words)
    picked = rng.sample(words, min(max_terms, len(words)))
    by_term: dict[str, list[str]] = defaultdict(list)
    wanted = set(picked)
    # one pass over clauses; terms are matched by trying every substring up to 12 chars
    order = list(range(len(clauses)))
    rng.shuffle(order)
    for k in order:
        c = clauses[k]
        for n in range(2, min(12, len(c)) + 1):
            for i in range(len(c) - n + 1):
                w = c[i:i + n]
                if w in wanted and len(by_term[w]) < per_term:
                    by_term[w].append(c)
        if len(by_term) >= len(wanted) and all(len(v) >= per_term for v in by_term.values()):
            break
    before, after = _tokenizer(), _tokenizer(userdict)
    stats = Counter()
    bad_before: Counter = Counter()
    bad_after: Counter = Counter()
    changed_other = 0
    other_tokens = 0
    for w, cs in by_term.items():
        for c in cs:
            tb, ta = list(before.cut(c, HMM=True)), list(after.cut(c, HMM=True))
            stats["samples"] += 1
            # inside a longer dictionary word ("李四" in "李四海") also counts as kept whole
            whole = lambda toks: any(t == w or (w in t and t in vocab) for t in toks)
            if whole(tb):
                stats["single_before"] += 1
            else:
                bad_before[w] += 1
            if whole(ta):
                stats["single_after"] += 1
            else:
                bad_after[w] += 1
            cover = set()
            for x in words:
                if len(x) >= 2 and x in c:
                    s = c.find(x)
                    cover.update(range(s, s + len(x)))
            sb = _spans(tb)
            sa = set(_spans(ta))
            for span in sb:
                if not cover.intersection(range(*span)):
                    other_tokens += 1
                    if span not in sa:
                        changed_other += 1
    n = stats["samples"] or 1
    return {"terms_tested": len(by_term), "samples": stats["samples"],
            "single_token_before": round(stats["single_before"] / n, 3),
            "single_token_after": round(stats["single_after"] / n, 3),
            "other_tokens_changed": round(changed_other / max(other_tokens, 1), 4),
            "still_split_after": [w for w, _ in bad_after.most_common(30)],
            "split_before_examples": [w for w, _ in bad_before.most_common(30)]}


def _spans(tokens: list[str]) -> list[tuple[int, int]]:
    out, pos = [], 0
    for t in tokens:
        out.append((pos, pos + len(t)))
        pos += len(t)
    return out


def build_lexicon(book: Path, *, seed: int = 0) -> dict:
    use_book_plugins(book)
    terms = collect_terms(book)
    rules = book / "lexicon_rules.json"
    if rules.exists():
        r = json.loads(rules.read_text(encoding="utf-8"))
        for w in r.get("drop", []):
            terms.pop(w, None)
        for w, t in r.get("add", {}).items():
            terms[w] = {"type": t, "chapters": 0}
    text = "".join(p for ch in load_chapters(book) for p in ch["paragraphs"])
    counts = Counter({w: text.count(w) for w in terms})
    terms = {w: v for w, v in terms.items() if counts[w] > 0}
    userdict = build_userdict(terms, counts)
    path = book / "build" / "userdict.txt"
    path.write_text("".join(f"{w} {f} {t}\n" for w, f, t in userdict), encoding="utf-8")
    report = evaluate(book, userdict, seed=seed)
    report.update({"written": str(path), "terms": len(userdict),
                   "by_type": dict(Counter(t for _, _, t in userdict))})
    return report
