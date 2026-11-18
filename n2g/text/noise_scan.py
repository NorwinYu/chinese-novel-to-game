"""Per-book noise discovery: statistics + model proposals + whole-book rule testing.

Nothing here knows about any particular site or book; the output is a proposal the agent reviews
before writing games/<slug>/noise_rules.json."""
from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from pathlib import Path

from ..llm import LLM
from .chapters import HeadingRules, split_chapters
from .clean import NoiseRule, clean_text, normalize_lines
from .quotes import find_quotes

_BRACKET = re.compile(r"[【\[（(][^】\]）)\n]{1,40}[】\]）)]|\*[^*\n]{1,20}\*")
# Latin (with diacritics), Greek and Cyrillic look-alikes are common in watermarks meant to dodge filters.
_LATIN_RUN = re.compile(r"[A-Za-zＡ-Ｚａ-ｚ0-9０-９\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u024f\u0370-\u03ff\u0400-\u04ff/=.\-^~]{2,}")
_SENT_END = "。！？!?…"


def structural_candidates(lines: list[str], n_chapters: int, top: int = 40) -> dict:
    text = "\n".join(lines)
    min_rep = max(5, int(n_chapters * 0.01))
    min_small = 3 if n_chapters < 200 else 5
    repeated = Counter(l for l in lines if len(l) >= 4 and not l.startswith(("“", "「", '"')))
    brackets = Counter(m.group(0) for l in lines for m in _BRACKET.finditer(l))
    latin = Counter(m.group(0) for l in lines for m in _LATIN_RUN.finditer(l) if re.search("[\u4e00-\u9fff]", l))

    injected = injected_chars(text)

    def ctx(s: str, k: int = 2) -> list[str]:
        out = []
        for m in re.finditer(re.escape(s), text):
            out.append(text[max(0, m.start() - 25): m.end() + 25].replace("\n", "⏎"))
            if len(out) >= k:
                break
        return out

    return {
        "repeated_lines": [{"line": l, "count": c} for l, c in repeated.most_common(top) if c >= min_rep],
        "frequent_brackets": [{"text": b, "count": c, "context": ctx(b, 1)} for b, c in brackets.most_common(top) if c >= min_small],
        "latin_runs": [{"text": s, "count": c, "context": ctx(s, 1)} for s, c in latin.most_common(top) if c >= min_small],
        "injected_chars": [{"char": ch, "suspicious": c, "total": t, "context": ctx(ch)} for ch, c, t in injected[:top]],
    }


def injected_chars(text: str, *, min_join: int = 30, side_ratio: float = 0.1) -> list[tuple[str, int, int]]:
    """Characters that keep showing up inside an otherwise common word: removing them leaves a frequent
    bigram while neither side forms a bigram with them (e.g. a filler glyph dropped into words)."""
    cjk = re.compile("[\u4e00-\u9fff]")
    bigrams = Counter(text[i:i + 2] for i in range(len(text) - 1))
    chars = Counter(text)
    hits: Counter = Counter()
    for i in range(1, len(text) - 1):
        a, c, b = text[i - 1], text[i], text[i + 1]
        if not (cjk.match(a) and cjk.match(b) and cjk.match(c)):
            continue
        join = bigrams[a + b]
        if join >= min_join and bigrams[a + c] + bigrams[c + b] <= side_ratio * join:
            hits[c] += 1
    out = [(ch, n, chars[ch]) for ch, n in hits.items() if n >= 5 and n / chars[ch] >= 0.5]
    return sorted(out, key=lambda x: -x[1])


def sample_chapters(chapters, k_even: int = 10, k_odd: int = 4) -> list:
    if not chapters:
        return []
    step = max(1, len(chapters) // k_even)
    picked = {c.idx for c in chapters[::step][:k_even]}

    def oddness(c) -> float:
        body = "\n".join(c.paragraphs)
        return (len(_LATIN_RUN.findall(body)) + len(_BRACKET.findall(body))) / max(1, len(body))

    for c in sorted(chapters, key=oddness, reverse=True)[:k_odd]:
        picked.add(c.idx)
    return [c for c in chapters if c.idx in picked]


SYSTEM = """你是中文电子书的文本清洗助手。你会看到一本小说的若干原文片段和一些统计出的可疑字符串。
任务：找出其中不属于小说正文的内容，并为每一类写一条 Python 正则规则。

属于噪声的：网站水印、广告、下载或阅读提示、作者在正文之外说的话（求票、请假、感谢读者、推荐别的书）、重复出现的页眉（书名加章节名）、乱码碎片。
不属于噪声的：小说的叙述和角色对白，即使其中出现“谢谢”“更新”“收藏”之类的词。拿不准的不要写规则。

规则要求：
- scope 为 "line" 表示整行删除，只用于整行都是噪声的情况；为 "inline" 表示只删除匹配到的片段，用于夹在正文句子里的噪声。
- pattern 必须是合法的 Python 正则，尽量具体，抓住噪声特有的字样，不要只匹配一个普通词。整行规则建议用 ^ 和 $ 锚定或包含明显的噪声标志。
- examples 必须逐字摘自给出的材料，并且能被该 pattern 匹配。

只输出 JSON：{"rules": [{"id": "简短英文标识", "scope": "line|inline", "pattern": "...", "description": "中文说明", "examples": ["..."]}], "notes": "其他发现"}"""


def _validator(material: str):
    def validate(obj):
        problems = []
        rules = obj.get("rules", []) if isinstance(obj, dict) else []
        for r in rules:
            try:
                pat = re.compile(r.get("pattern", ""))
            except re.error as e:
                problems.append(f"规则 {r.get('id')} 的正则无法编译：{e}")
                continue
            if r.get("scope") not in ("line", "inline"):
                problems.append(f"规则 {r.get('id')} 的 scope 必须是 line 或 inline")
            for ex in r.get("examples", [])[:5]:
                if ex not in material:
                    problems.append(f"规则 {r.get('id')} 的例子不在材料中（必须逐字摘录）：{ex[:40]}")
                elif not pat.search(ex):
                    problems.append(f"规则 {r.get('id')} 的正则匹配不到它自己的例子：{ex[:40]}")
        return obj, problems
    return validate


async def propose_rules(llm: LLM, chapters, candidates: dict, *, chars_per_chapter: int = 5000) -> dict:
    sample = sample_chapters(chapters)
    batches = [sample[i::2] for i in range(2)]
    cand_json = json.dumps(candidates, ensure_ascii=False, indent=0)

    async def run(batch):
        material = "\n\n".join(f"【片段：{c.heading}】\n" + "\n".join(c.paragraphs)[:chars_per_chapter] for c in batch)
        user = f"统计出的可疑字符串（含出现次数和上下文）：\n{cand_json}\n\n原文片段：\n{material}"
        obj, problems = await llm.chat_json("audit", [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                                            validate=_validator(material + cand_json), max_tokens=6000)
        return obj or {}, problems

    results = await asyncio.gather(*(run(b) for b in batches if b))
    merged: dict[str, dict] = {}
    notes, unresolved = [], []
    for obj, problems in results:
        unresolved += problems
        if obj.get("notes"):
            notes.append(obj["notes"])
        for r in obj.get("rules", []):
            merged.setdefault(r["pattern"], r)
    return {"rules": list(merged.values()), "notes": notes, "unresolved_problems": unresolved,
            "sampled_chapters": [c.heading for c in sample]}


def test_rules(raw_lines: list[str], rules: list[NoiseRule], samples: int = 6) -> list[dict]:
    report = []
    for rule in rules:
        try:
            pat = rule.compiled()
        except re.error as e:
            report.append({"id": rule.id, "error": str(e)})
            continue
        matched_lines = matches = dialogue_hits = dropped_chars = 0
        examples = []
        for line in raw_lines:
            ms = list(pat.finditer(line))
            if not ms:
                continue
            matched_lines += 1
            matches += len(ms)
            spans = find_quotes(line)
            for m in ms:
                if any(a <= m.start() < b for a, b in spans):
                    dialogue_hits += 1
            dropped_chars += len(line) if rule.scope == "line" else sum(len(m.group(0)) for m in ms)
            if len(examples) < samples:
                m = ms[0]
                examples.append(line[max(0, m.start() - 30): m.end() + 30])
        report.append({
            "id": rule.id, "scope": rule.scope, "pattern": rule.pattern, "description": rule.description,
            "matched_lines": matched_lines, "matches": matches, "dialogue_hits": dialogue_hits,
            "dropped_chars": dropped_chars,
            "share_of_lines": round(matched_lines / max(1, len(raw_lines)), 5),
            "examples": examples,
        })
    return report


async def scan(book: Path, llm: LLM, raw: str) -> dict:
    from .ingest import load_book
    profile = load_book(book).get("profile")
    lines, _ = clean_text(raw, profile=profile)  # structural cleaning only
    split = split_chapters(lines, HeadingRules.load(book / "book.json"))
    candidates = structural_candidates(lines, len(split.chapters))
    proposal = await propose_rules(llm, split.chapters, candidates)
    rules = [NoiseRule(r.get("id", f"rule{i}"), r.get("scope", "inline"), r["pattern"], r.get("description", ""))
             for i, r in enumerate(proposal["rules"])]
    proposal["test"] = test_rules(normalize_lines(raw, profile), rules)
    proposal["candidates"] = candidates
    return proposal
