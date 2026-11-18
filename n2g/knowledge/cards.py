"""Stage 4: staged character cards.

A card describes one character during one story stage: identity, personality, how they talk, who they
deal with and how, what they want, and their situation. Every card is written by the model from that
stage's evidence only (their own lines, events they took part in, facts about them, per-chapter
descriptions), so a stage-3 card cannot know what happens in stage 5.

Stages come from book.json "stages" ([{"name", "from", "to"}], chapter indices, written by the agent
after reading `timeline`); without them the book is cut into equal spans."""
from __future__ import annotations

import asyncio
import bisect
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from ..consolidate.characters import collect_mentions, is_descriptor, use_book_plugins
from ..extract.chapter import number_chapter
from ..extract.review import load_records
from ..text.ingest import load_book, load_chapters
from ..llm import LLMError
from .ledger import Resolver, build_ledger, load_ledger

SPEECH = ("said", "telepathy", "written")


def book_stages(book: Path, n_chapters: int) -> list[dict]:
    meta = load_book(book)
    if meta.get("stages"):
        return [{"name": s.get("name", f"阶段{k + 1}"), "from": s["from"], "to": s["to"]} for k, s in enumerate(meta["stages"])]
    k = min(8, max(1, n_chapters // 15))
    step = n_chapters / k
    return [{"name": f"阶段{i + 1}", "from": round(i * step), "to": round((i + 1) * step) - 1} for i in range(k)]


def timeline(book: Path, *, min_importance: int = 3, start: int = 0, end: int | None = None) -> list[str]:
    out = []
    for rec in load_records(book):
        o = rec.get("result")
        if not o or rec["idx"] < start or (end is not None and rec["idx"] > end):
            continue
        for e in o.get("events", []):
            if e.get("importance", 1) >= min_importance:
                out.append(f"ch{rec['idx']} {e.get('summary', '')}")
    return out


def _raw_first(chapters: list[dict], names: set[str]) -> dict[str, int]:
    """First chapter whose raw text contains each name. A reader has seen a name from that point on, even
    when the character is only mentioned there, and common strings ("大了", "师傅") count from where they
    first occur as plain words, so they never look like a later character."""
    starts, parts, pos = [], [], 0
    for ch in chapters:
        t = "\n".join(ch["paragraphs"]) + "\n"
        starts.append(pos)
        parts.append(t)
        pos += len(t)
    text = "".join(parts)
    out = {}
    for n in names:
        i = text.find(n)
        if i >= 0:
            out[n] = bisect.bisect_right(starts, i) - 1
    return out


def _overlap(a: str, b: str) -> float:
    """Share of a's character bigrams that also occur in b."""
    ga = {a[i:i + 2] for i in range(len(a) - 1)}
    return len(ga & {b[i:i + 2] for i in range(len(b) - 1)}) / len(ga) if ga else 0.0


def _strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)


def _digest(mat: dict) -> str:
    # keys that are empty are left out so that adding a new kind of material does not invalidate old cards
    mat = {k: v for k, v in mat.items() if v != []}
    return hashlib.sha1(json.dumps(mat, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def _spread(items: list, k: int) -> list:
    if len(items) <= k:
        return items
    step = (len(items) - 1) / (k - 1)
    return [items[round(i * step)] for i in range(k)]


class Evidence:
    """Per-chapter material for every character, built once per book."""

    def __init__(self, book: Path):
        self.res = Resolver(book)
        self.chars = self.res.chars
        self.first = {cid: c["first"] for cid, c in self.chars.items()}
        chapters = load_chapters(book)
        self.raw_first = _raw_first(chapters, {n for c in self.chars.values() if c["type"] != "group"
                                              for n in c["names"] if len(n) >= 2})
        self.lines: dict[str, list[tuple]] = defaultdict(list)    # cid -> (ch, qid, text, listeners)
        self.events: dict[str, list[tuple]] = defaultdict(list)   # cid -> (ch, importance, summary)
        self.facts: dict[str, list[tuple]] = defaultdict(list)    # cid -> (ch, kind, text, hidden, other knowers)
        self.descs: dict[str, list[tuple]] = defaultdict(list)    # cid -> (ch, desc)
        self.together: dict[str, Counter] = defaultdict(Counter)  # cid -> (ch-bucket, other) counts
        self.scene_chapters: dict[str, list[tuple[int, list[str]]]] = defaultdict(list)
        ledger_path = book / "build" / "ledger.json"
        knowers = {f["id"]: f["knowers"] for f in (load_ledger(book) if ledger_path.exists() else build_ledger(book))["facts"]}
        for m in collect_mentions(book):
            cid = self.res(m.chapter, m.name)
            if cid and m.desc:
                self.descs[cid].append((m.chapter, m.desc))
        for rec in load_records(book):
            o = rec.get("result")
            if not o:
                continue
            ch = rec["idx"]
            r = lambda s: self.res(ch, s)
            _, quotes = number_chapter(chapters[ch]["paragraphs"])
            for q, spk, to, how in o.get("utterances", []):
                cid = r(spk)
                if cid and how in SPEECH and q in quotes:
                    self.lines[cid].append((ch, f"{ch}.{q}", quotes[q][1], [x for x in to if isinstance(x, str)]))
            for e in o.get("events", []):
                for cid in {r(x) for x in e.get("who", [])} - {None}:
                    self.events[cid].append((ch, e.get("importance", 1), e.get("summary", "")))
            for f in o.get("facts", []):
                if f.get("unverified"):
                    continue
                kn = knowers.get(f"{ch}.{f.get('id')}", {})
                private = f.get("secret") or f.get("kind") in ("secret", "plan")
                for cid in {r(x) for x in f.get("about", [])} - {None}:
                    # a secret or plan about someone that they are not told of must not reach their card:
                    # whatever the card says, the character will know
                    text = f.get("text", "")
                    # knower lists are often incomplete; someone who is the subject of the sentence
                    # ("甲化名乙", "甲决定……") knows it whether or not they were listed
                    aware = cid in kn or any(text.startswith(n) for n in self.chars[cid]["names"] if len(n) >= 2)
                    others = [] if aware else [self.chars[k]["name"] for k in kn if k in self.chars][:4]
                    self.facts[cid].append((ch, f.get("kind", ""), text, bool(private and not aware), others))
            for s in o.get("scenes", []):
                present = self.res.present(ch, s.get("present", []))
                for cid in present:
                    self.scene_chapters[cid].append((ch, present))

    def material(self, cid: str, lo: int, hi: int, *, hide_unknown: bool = True) -> dict:
        inside = lambda xs: [x for x in xs if lo <= x[0] <= hi]
        lines = inside(self.lines[cid])
        events = inside(self.events[cid])
        major = sorted(events, key=lambda e: -e[1])
        events = sorted(major[:25] + _spread([e for e in events if e not in major[:25]], 15), key=lambda e: e[0])
        partners = Counter()
        for ch, present in inside(self.scene_chapters[cid]):
            for other in present:
                if other != cid and self.chars.get(other, {}).get("type") != "group":
                    partners[other] += 1
        return {
            "chapters": sorted({x[0] for x in inside(self.scene_chapters[cid])} | {x[0] for x in lines} | {x[0] for x in events}),
            "lines": _spread(lines, 45),
            "events": events,
            "facts": _spread([x for x in inside(self.facts[cid]) if not (hide_unknown and x[3])], 40),
            "unknown": _spread([x[2] for x in inside(self.facts[cid]) if x[3]], 15) if hide_unknown else [],
            "descs": _spread(inside(self.descs[cid]), 12),
            "partners": [(self.chars[o]["name"], n) for o, n in partners.most_common(15)],
        }


CARD_SYSTEM = """你在为一个由小说改编的游戏写角色卡。游戏里这个角色由 AI 扮演，玩家会和他对话，所以角色卡要让扮演者说话像他、知道他该知道的、不知道他不该知道的。

你只拿到这个角色在某一个故事阶段的材料：他说过的话（带编号）、他参与的事件、关于他的事实、各章对他的描述、和他同场最多的人。只根据这些材料写，不要用你对这本书的已有印象补充，也不要写这个阶段之后才发生的事。材料没体现的项写空字符串或空数组，不要写“未提及”之类的说明。

扮演者会把角色卡上的一切当成角色自己知道的。事实后面标了“（知情：……；他不在其中）”的，是别人知道、他未必知道的：除非他的台词或行为表明他知道，否则不要写进 secrets、goals、status，也不要写成他的看法；他不知道的事一律不要写进卡里（不要写“他不知道某事”）。

输出 JSON：
{"identity": "这个阶段的身份、处境，一两句",
 "appearance": "外貌和衣着，材料里没有就空",
 "personality": ["性格特点，每条一句，附一个体现它的具体行为"],
 "speech": {"style": "说话的语气和用词习惯", "self_reference": "常用的自称", "habits": ["口头禅、语气词、句式习惯"], "examples": ["最能代表他说话方式的台词编号，5~8 个"]},
 "relationships": [{"with": "对方的名字（必须用材料里出现过的名字）", "relation": "关系", "attitude": "他对对方的态度和原因", "address": "他怎么称呼对方，材料里没有就空"}],
 "goals": ["这个阶段想要什么、在做什么"],
 "status": "实力、身份、处境、拥有的重要物品等这个阶段末尾的状态",
 "secrets": ["他自己知道、而别人多数不知道的事"],
 "changes": "和上一阶段相比有什么变化；没有上一阶段就空"}"""


def _render(c: dict, stage: dict, mat: dict, prev: dict | None) -> str:
    lines = [f"⟨{qid}⟩ ch{ch}{'（对' + '、'.join(to) + '）' if to else ''}：{text[:120]}" for ch, qid, text, to in mat["lines"]]
    parts = [f"角色：{c['name']}（其他称呼：{'、'.join(list(c['names'])[:6])}）",
             f"阶段：{stage['name']}，第 {stage['from']}~{stage['to']} 章（他在其中 {len(mat['chapters'])} 章出现）",
             "各章描述：\n" + "\n".join(f"ch{ch}：{d}" for ch, d in mat["descs"]),
             "他说过的话：\n" + "\n".join(lines),
             "他参与的事件：\n" + "\n".join(f"ch{ch} ★{imp} {s}" for ch, imp, s in mat["events"]),
             "关于他的事实：\n" + "\n".join(f"ch{ch} [{k}] {t}" + (f"（知情：{'、'.join(o)}；他不在其中）" if o else "")
                                     for ch, k, t, _, o in mat["facts"]),
             "同场最多的人：" + "、".join(f"{n}({k})" for n, k in mat["partners"])]
    if mat.get("unknown"):
        parts.append("他此时不知道的事（别人瞒着他或他不在场）。只用来避免写错：卡里任何地方都不要出现这些内容，也不要写“他不知道……”：\n"
                     + "\n".join(mat["unknown"]))
    if prev:
        parts.append("上一阶段的角色卡（只作衔接参考，以本阶段材料为准）：\n" + json.dumps(prev, ensure_ascii=False))
    return "\n\n".join(parts)


_NOTE = re.compile(r"^[（(][^）)]*(未|没有|无)[^）)]*[）)]$")
_INLINE_NOTE = re.compile(r"[（(][^）)]*材料[^）)]*[）)]")
# talk about the prompt ("材料中未提及") as opposed to the word itself, which many books use (药材、炼制材料)
_IGNORANT = re.compile(r"(他|她)(并|还|尚|仍)?(不知|未察觉|没有察觉|蒙在鼓里)")
_META = re.compile(r"材料(中|里|未|没|显示|所示|提及|提到|给出|表明)")


def _blank_notes(x):
    """The model sometimes writes "（材料中未出现）" where the format asks for an empty string."""
    if isinstance(x, dict):
        for k, v in x.items():
            if isinstance(v, str) and _NOTE.match(v.strip()):
                x[k] = ""
            elif isinstance(v, str):
                x[k] = _INLINE_NOTE.sub("", v).strip()
            else:
                _blank_notes(v)
    elif isinstance(x, list):
        for v in x:
            _blank_notes(v)


def _validator(mat: dict, known_names: set[str], future_names: set[str], prompt: str):
    qids = {x[1] for x in mat["lines"]}

    def validate(obj):
        problems = []
        if not isinstance(obj, dict):
            return obj, ["输出必须是 JSON 对象"]
        _blank_notes(obj)
        sp = obj.get("speech") or {}
        bad = [q for q in sp.get("examples", []) if q not in qids]
        if bad:
            problems.append(f"speech.examples 里这些编号不在材料中：{bad[:8]}，只能用 ⟨⟩ 里给出的编号")
        if qids and not sp.get("examples"):
            problems.append("speech.examples 不能为空，请从材料里挑 5~8 个台词编号")
        for r in obj.get("relationships", []):
            w = r.get("with") or ""
            if w not in known_names and w not in prompt:
                problems.append(f"relationships 里的“{w}”在材料和此前的故事里都没出现过，换成材料里的名字")
        blob = json.dumps(obj, ensure_ascii=False)
        segs = [g for s in _strings({k: v for k, v in obj.items() if k != "speech"}) for g in re.split("[。；;！？]", s) if len(g) >= 10]
        leaks = [(g, t) for g in segs for t in mat.get("unknown", []) if _overlap(g, t) >= 0.6]
        if leaks:
            problems.append(f"卡里写了他此时不知道的事：“{leaks[0][0][:40]}”（对应：{leaks[0][1][:40]}），删掉或改写")
        ignorant = [s for s in obj.get("secrets", []) if isinstance(s, str) and _IGNORANT.search(s)]
        if ignorant:
            problems.append(f"secrets 只写他知道的事，不要写他不知道什么：{ignorant[:2]}")
        meta = [m.group(0) for m in _META.finditer(blob) if m.group(0) not in prompt]
        if meta:
            problems.append("角色卡是给扮演者看的，不要提“材料”，直接陈述内容；没有内容的项留空")
        leaked = sorted(n for n in future_names if n in blob)
        if leaked:
            problems.append(f"角色卡提到了这个阶段之后才出场的人物：{leaked[:6]}，删掉相关内容")
        return obj, problems
    return validate


def _prepare(ev: "Evidence", c: dict, st: dict, surfaces: dict, prev: dict | None):
    """(material, prompt, validator, digest) for one character in one stage, or None without enough material."""
    mat = ev.material(c["id"], st["from"], st["to"])
    if len(mat["chapters"]) < min(3, st["to"] - st["from"] + 1) and not mat["lines"]:
        return None
    prompt = _render(c, st, mat, prev)
    seen = {n for n, ch in ev.raw_first.items() if ch <= st["to"]}
    known = (seen & {n for ns in surfaces.values() for n in ns}) | set(c["names"])
    future = {n for cid, ns in surfaces.items() if cid != c["id"] and ev.chars[cid]["tier"] in "SAB"
              for n in ns if n not in seen and n not in prompt and not is_descriptor(n)}
    return mat, prompt, _validator(mat, known, future, prompt), _digest(mat)


def _reuse(ev: "Evidence", c: dict, st: dict, kept: dict | None, digest: str, check) -> str:
    """Why a stored card must be rewritten, or "" when it can be kept."""
    if not kept or not kept.get("card"):
        return "new"
    if (kept["from"], kept["to"]) != (st["from"], st["to"]):
        return "range_changed"
    same = kept.get("material") == digest or (
        # cards written before digests existed: same material under the old rule
        "material" not in kept and _digest(ev.material(c["id"], st["from"], st["to"], hide_unknown=False)) == digest)
    if not same:
        return "material_changed"
    problems = check(kept["card"])[1]
    return f"fails_check: {problems[0][:60]}" if problems else ""


def _targets(ev: "Evidence", tiers: str, names: list[str] | None) -> list[dict]:
    return [c for c in ev.chars.values() if c["type"] != "group" and (c["name"] in names if names else c["tier"] in tiers)]


def plan_cards(book: Path, *, tiers: str = "SA", names: list[str] | None = None, force: bool = False) -> dict:
    """What `cards` would do, without calling the model: which cards it keeps, which it rewrites and why,
    and a cost estimate from earlier card runs of this book (or from prompt size)."""
    ev = Evidence(book)
    stages = book_stages(book, len(load_chapters(book)))
    surfaces = {cid: {n for n in c["names"] if len(n) >= 2} for cid, c in ev.chars.items() if c["type"] != "group"}
    reasons, rewrite, chars_out, prompt_chars = Counter(), [], 0, 0
    for c in _targets(ev, tiers, names):
        path = book / "build" / "cards" / f"{c['id']}.json"
        old = {} if force or not path.exists() else {s["stage"]: s for s in json.loads(path.read_text(encoding="utf-8"))["stages"]}
        prev = None
        for k, st in enumerate(stages):
            job = _prepare(ev, c, st, surfaces, prev)
            if job is None:
                reasons["no_material"] += 1
                continue
            mat, prompt, check, digest = job
            why = "new" if force else _reuse(ev, c, st, old.get(k), digest, check)
            kind = why.split(":")[0] or "keep"
            reasons[kind] += 1
            if why:
                rewrite.append(f"{c['name']} {st['name']}: {why}")
                prompt_chars += len(prompt) + len(CARD_SYSTEM)
            prev = (old.get(k) or {}).get("card") or prev
        chars_out += 1
    n = len(rewrite)
    est = _estimate(book, n, prompt_chars)
    return {"characters": chars_out, "stages": len(stages), "cards": dict(reasons), "rewrite": n,
            "estimate": est, "rewrite_list": rewrite[:40]}


def _estimate(book: Path, n: int, prompt_chars: int) -> dict:
    """Cost of n card calls: average of earlier `cards` runs in spend.jsonl, else prompt size priced at
    this book's average cost per token over its cached calls (or N2G_PRICE_PER_MTOK)."""
    if not n:
        return {"usd": 0.0, "basis": "nothing to rewrite"}
    log = book / "build" / "spend.jsonl"
    cost = calls = 0.0
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("label") == "cards":
                cost += rec["cost"]
                calls += rec["usage"].get("director", {}).get("calls", 0)
    if calls:
        per = cost / calls
        # validator feedback makes some cards take a second call
        return {"usd": round(per * n * 1.3, 3), "basis": f"{calls:.0f} earlier card calls at ${per:.4f} each, x1.3 for retries"}
    tokens_in, tokens_out = prompt_chars / 1.4, n * 1500
    from ..config import get_settings
    price = get_settings().price
    if price:
        usd = (tokens_in * price[0] + tokens_out * price[1]) / 1e6
        basis = "N2G_PRICE_PER_MTOK"
    else:
        spent = tokens = 0.0
        for p in list((book / "build" / "llm_cache").glob("*/*.json"))[:3000]:
            u = json.loads(p.read_text(encoding="utf-8")).get("usage") or {}
            nested = next((v for v in u.values() if isinstance(v, dict) and v.get("list_cost")), {})
            c = nested.get("list_cost") or u.get("cost") or 0
            if c:
                spent += c
                tokens += u.get("prompt_tokens", 0) + u.get("completion_tokens", 0)
        if not tokens:
            return {"usd": None, "basis": f"no price known; about {tokens_in / 1e6:.2f}M input and {tokens_out / 1e6:.2f}M output tokens"}
        usd = (tokens_in + tokens_out) * spent / tokens
        basis = f"average ${spent / tokens * 1e6:.2f} per million tokens over this book's cached calls"
    return {"usd": round(usd * 1.3, 3), "basis": f"prompt size at about 1.4 characters per token, 1500 output tokens per card, {basis}, x1.3 for retries"}

async def build_cards(book: Path, llm, *, tiers: str = "SA", names: list[str] | None = None, force: bool = False) -> dict:
    ev = Evidence(book)
    n_chapters = len(load_chapters(book))
    stages = book_stages(book, n_chapters)
    out_dir = book / "build" / "cards"
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = _targets(ev, tiers, names)
    surfaces = {cid: {n for n in c["names"] if len(n) >= 2} for cid, c in ev.chars.items() if c["type"] != "group"}
    report = {"characters": 0, "cards": 0, "kept": 0, "unresolved": [], "stopped": []}
    llm.job("cards", len(targets) * len(stages))

    async def one(c: dict) -> None:
        path = out_dir / f"{c['id']}.json"
        old = {}
        if path.exists() and not force:
            old = {s["stage"]: s for s in json.loads(path.read_text(encoding="utf-8"))["stages"]}
        cards, prev, changed = [], None, False
        for k, st in enumerate(stages):
            llm.tick()
            job = _prepare(ev, c, st, surfaces, prev)
            if job is None:
                continue
            mat, prompt, check, digest = job
            kept = old.get(k)
            if not _reuse(ev, c, st, kept, digest, check):
                changed |= "material" not in kept
                kept["material"] = digest
                # cards that still pass the current checks are not paid for twice
                kept["problems"] = []
                cards.append(kept)
                prev = kept["card"]
                report["kept"] += 1
                continue
            changed = True
            msgs = [{"role": "system", "content": CARD_SYSTEM}, {"role": "user", "content": prompt}]
            try:
                card, problems = await llm.chat_json("director", msgs, validate=check,
                                                     max_tokens=4000, temperature=0.3)
            except LLMError as e:
                # later stages build on this one, so stop here; finished stages are saved and kept next run
                report["stopped"].append(f"{c['name']} {st['name']}: {e}")
                cards.extend(old[j] for j in sorted(old) if j >= k)
                break
            if problems:
                report["unresolved"].append(f"{c['name']} {st['name']}: {problems[:2]}")
            quotes = {x[1]: x[2] for x in mat["lines"]}
            cards.append({"stage": k, "name": st["name"], "from": st["from"], "to": st["to"], "card": card, "material": digest,
                          "example_lines": [quotes[q] for q in (card or {}).get("speech", {}).get("examples", []) if q in quotes],
                          "evidence": {"chapters": len(mat["chapters"]), "lines": len(mat["lines"]), "events": len(mat["events"]),
                                       "facts": len(mat["facts"])},
                          "problems": problems})
            prev = card
        if not changed and len(cards) == len(old):
            return
        path.write_text(json.dumps({"id": c["id"], "name": c["name"], "tier": c["tier"], "stages": cards},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
        report["characters"] += 1
        report["cards"] += len(cards)

    await asyncio.gather(*(one(c) for c in targets))
    report["stages"] = stages
    return report


def review_card(book: Path, name: str, stage: int | None = None) -> dict:
    chars = json.loads((book / "build" / "characters.json").read_text(encoding="utf-8"))["characters"]
    c = next((c for c in chars if c["name"] == name), None) or next(c for c in chars if name in c["names"])
    data = json.loads((book / "build" / "cards" / f"{c['id']}.json").read_text(encoding="utf-8"))
    stages = data["stages"] if stage is None else [s for s in data["stages"] if s["stage"] == stage]
    return {"name": data["name"], "tier": data["tier"], "stages": stages}
