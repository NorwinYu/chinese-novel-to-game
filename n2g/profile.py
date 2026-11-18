"""Stage 0: book profile.

Cheap text statistics that decide how later stages should treat the book: narrative person, quotation
style, script, scale, how much dialogue carries speech cues, and hints about time structure. The numbers
are measured; the decisions (`person`, `narrator`, ...) are written by the agent after reading samples,
because a statistic cannot tell an anonymous narrator from a diary inside a third-person novel."""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from .text.quotes import find_quotes

# characters that differ between the two scripts and are common in fiction
_TRAD = "們這說來時為會對個裡麼還沒間後過見問聽讓開門頭臉樣樂經點覺發現無與從當進應機"
_SIMP = "们这说来时为会对个里么还没间后过见问听让开门头脸样乐经点觉发现无与从当进应机"
_CUE = re.compile(r"(?:道|说|问|答|喝|叫|喊|笑|骂|叹|嚷|吼)[道着了]?[：:，,]?\s*$")
_POST_CUE = re.compile(r"[^“”「」。！？]{1,12}?(?:道|说|问)")
# "某某说，……" with no quotation mark after the verb: dialogue written into the narration
_UNQUOTED = re.compile(r"[^，。！？\s]{1,6}(?:说|讲|问|答|叫)[，：:](?![“\"「])")
_RECALL = re.compile(r"回忆|想起|当年|那年|多年前|多年以前|小时候|从前|往事|记得")
_YEAR = re.compile(r"(?:19|20)\d\d年|[一二三四五六七八九〇零]{4}年")
_FIRST = re.compile(r"我")
_THIRD = re.compile(r"[他她]")

DECISIONS = {
    "person": "first | third | mixed（多个第一人称叙述者轮换，或第一人称与第三人称交替）",
    "narrator": "第一人称叙述者在本书里的名字；无名就写“叙述者”。mixed 时写 {\"起-止\": \"名字\"}（章号含两端，\"5-\" 到结尾）",
    "time": "linear | flashback | multi_line（多条时间线交替）| frame（故事套故事）",
    "protagonist": "single | ensemble",
    "special_presence": "需要特殊在场规则的设定，如“只有主角看得见、听得见的同伴”“隔空传话”；没有就空",
    "quotes": "curly（“”或「」，默认）| straight（ASCII 双引号，① 会转成弯引号）",
    "wrap": "none（默认）| hard（导出时按固定宽度断行，① 会把句中断开的行接回去）",
    "dialogue": "quoted（默认）| unquoted（对话不用引号，如“某某说，……”；目前抽取只认引号里的话，需要先处理）",
}


def measure(text: str, n_chapters: int | None = None) -> dict:
    from .text.clean import _TERMINAL, _curly
    marks = Counter()
    for ch in "“”「」『』‘’\"'":
        marks[ch] = text.count(ch)
    curly = sum(marks[c] for c in "“”「」『』")
    straight = marks['"'] / max(1, marks['"'] + curly)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if straight > 0.5:  # measure the text the way ① will see it once quotes are converted
        lines = [_curly(l) for l in lines]
    total = sum(len(l) for l in lines)
    # an even sample keeps very long books fast while still covering beginning, middle and end
    step = max(1, len(lines) // 6000)
    sample = lines[::step]
    inside = 0
    out_text, cues, quotes = [], 0, 0
    for l in sample:
        last = 0
        for a, b in find_quotes(l):
            out_text.append(l[last:a])
            inside += b - a
            quotes += 1
            cues += bool(_CUE.search(l[max(0, a - 12):a]) or _POST_CUE.match(l[b:b + 16]))
            last = b
        out_text.append(l[last:])
    narr = "".join(out_text)
    nn = max(1, len(narr))
    sized = Counter(len(l) for l in lines if len(l) >= 10)
    width = sized.most_common(1)[0][0] if sized else 0
    ends = _TERMINAL + ('"',)
    near = [l for l in lines if width >= 20 and len(l) >= 0.8 * width]
    hard = sum(1 for l in near if not l.endswith(ends)) / max(1, len(lines))
    trad = sum(text.count(c) for c in _TRAD)
    simp = sum(text.count(c) for c in _SIMP)
    first = len(_FIRST.findall(narr)) * 1000 / nn
    third = len(_THIRD.findall(narr)) * 1000 / nn
    return {
        "chars": total, "chapters": n_chapters,
        "avg_chapter_chars": round(total / n_chapters) if n_chapters else None,
        "script": "traditional" if trad > simp else "simplified", "traditional_share": round(trad / max(1, trad + simp), 3),
        "quote_marks": {k: v for k, v in marks.most_common() if v},
        "straight_quote_share": round(straight, 3),
        "line_width_mode": width, "lines_broken_mid_sentence": round(hard, 3),
        "dialogue_share": round(inside / max(1, inside + len(narr)), 3),
        "quotes_per_1000_chars": round(quotes * 1000 / max(1, sum(len(l) for l in sample)), 2),
        "unquoted_speech_per_1000": round(len(_UNQUOTED.findall(narr)) * 1000 / nn, 2),
        "cued_quote_share": round(cues / max(1, quotes), 3),
        "narration_wo_per_1000": round(first, 2), "narration_ta_per_1000": round(third, 2),
        "recall_words_per_10000": round(len(_RECALL.findall(narr)) * 10000 / nn, 2),
        "year_mentions": len(_YEAR.findall(text)),
    }


def suggest(m: dict) -> dict:
    """First guesses for the decisions, with the reason; the agent confirms them on samples."""
    s = {}
    ratio = m["narration_wo_per_1000"] / max(0.1, m["narration_ta_per_1000"])
    unquoted = m["unquoted_speech_per_1000"] > max(0.5, m["quotes_per_1000_chars"])
    if unquoted:
        s["dialogue"] = ("unquoted", f"“某某说，……”这种不带引号的对话每千字 {m['unquoted_speech_per_1000']}，引语只有 {m['quotes_per_1000_chars']}；"
                                     "抽取目前只认引号里的话")
    if unquoted and m["narration_wo_per_1000"] >= 2:
        s["person"] = ("check", "对话写在叙述里，“我”的次数里混着对话，人称要抽样判断")
    elif m["narration_wo_per_1000"] >= 4 and ratio >= 0.35:
        s["person"] = ("first", f"叙述里“我”每千字 {m['narration_wo_per_1000']}，“他/她” {m['narration_ta_per_1000']}")
    elif m["narration_wo_per_1000"] >= 2 and ratio >= 0.15:
        s["person"] = ("mixed?", "叙述里“我”不少，可能有第一人称段落（书信、日记、多叙述者），抽样确认")
    else:
        s["person"] = ("third", f"叙述里“我”每千字只有 {m['narration_wo_per_1000']}")
    if m["straight_quote_share"] > 0.5:
        s["quotes"] = ("straight", f"{m['straight_quote_share']:.0%} 的引号是 ASCII 双引号，不转换的话引语一句都找不到")
    if m["lines_broken_mid_sentence"] > 0.3:
        s["wrap"] = ("hard", f"{m['lines_broken_mid_sentence']:.0%} 的行在句子中间断开（常见行宽 {m['line_width_mode']} 字）")
    if "dialogue" in s:
        pass
    elif m["unquoted_speech_per_1000"] > m["quotes_per_1000_chars"] / 2:
        s["dialogue"] = ("partly_unquoted?", f"不带引号的“某某说，”每千字 {m['unquoted_speech_per_1000']}，抽样看是转述还是对话")
    elif m["quotes_per_1000_chars"] < 0.5:
        s["dialogue"] = ("unquoted?", "引号很少：对话可能不用引号，抽样确认")
    elif m["cued_quote_share"] < 0.3:
        s["dialogue"] = ("few_cues", f"只有 {m['cued_quote_share']:.0%} 的引语前后有“某某道/说”，说话人多靠上下文")
    if m["traditional_share"] > 0.5:
        s["script"] = ("traditional", "繁体字：检查词表、正则和分词是否需要繁体版本")
    if m["recall_words_per_10000"] > 8 or m["year_mentions"] > 50:
        s["time"] = ("check", f"回忆类词每万字 {m['recall_words_per_10000']}，年份 {m['year_mentions']} 处：抽样看是否有倒叙或多时间线")
    fp = m.get("first_person_chapters")
    if fp and m["chapters"] and 0 < fp["count"] < 0.8 * m["chapters"]:
        s["person"] = ("mixed?", f"{fp['count']}/{m['chapters']} 章的叙述以“我”为主（{fp['ranges'][:6]}）：可能多个叙述者或部分章节第一人称")
    if m["chars"] > 2_000_000:
        s["scale"] = ("long", "超长篇：先做 30 章试点，费用先估算再全书跑")
    return {k: {"value": v[0], "why": v[1]} for k, v in s.items()}


def first_person_chapters(chapters: list[dict], threshold: float = 8.0) -> dict:
    """Chapters whose narration (outside quotes) uses 我 at least `threshold` times per 1000 characters."""
    hits = []
    for c in chapters:
        narr = "".join(_outside(p) for p in c["paragraphs"])
        if narr and len(_FIRST.findall(narr)) * 1000 / len(narr) >= threshold:
            hits.append(c["idx"])
    ranges, start = [], None
    for k, i in enumerate(hits):
        if start is None:
            start = i
        if k + 1 == len(hits) or hits[k + 1] != i + 1:
            ranges.append(f"{start}:{i + 1}")
            start = None
    return {"count": len(hits), "ranges": ranges}


def _outside(p: str) -> str:
    out, last = [], 0
    for a, b in find_quotes(p):
        out.append(p[last:a])
        last = b
    return "".join(out) + p[last:]


def profile(book: Path) -> dict:
    from .text.ingest import load_book, load_chapters, read_source
    meta = load_book(book)
    raw, _, _ = read_source(book)
    built = (book / "build" / "chapters.jsonl").exists()
    chapters = load_chapters(book) if built else []
    m = measure(raw, len(chapters) if built else None)
    if chapters:
        m["first_person_chapters"] = first_person_chapters(chapters)
    prof = meta.get("profile", {})
    return {"measured": m, "suggested": suggest(m), "decided": {k: prof[k] for k in DECISIONS if k in prof},
            "undecided": {k: v for k, v in DECISIONS.items() if k not in prof}}


def set_profile(book: Path, pairs: list[str]) -> dict:
    path = book / "book.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    prof = meta.setdefault("profile", {})
    for pair in pairs:
        key, value = pair.split("=", 1)
        if key not in DECISIONS:
            raise ValueError(f"unknown profile key {key}; known: {sorted(DECISIONS)}")
        try:
            prof[key] = json.loads(value)
        except json.JSONDecodeError:
            prof[key] = value
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return prof
