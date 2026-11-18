"""The NPC runtime: one character answering the protagonist at one point of the story.

What the character may use is decided before the model sees anything:
  persona    the card of the last stage that ended before this chapter (a card describes the end of its
             stage, so the current stage's card would tell the future), the per-chapter descriptions
             up to now, and the speech style of the current stage; voice samples are lines they really
             said up to now
  memory     ledger facts and events they know by this chapter, ranked by relevance to what was just
             said (see retrieve.py), plus the latest few they witnessed
  scene      where they are, who is there, and the lines of this scene the player has already read
After the model answers, a name that first occurs later in the book (and that the player did not say)
sends the answer back once with the problem; if it persists the turn is flagged `future_name`."""
from __future__ import annotations

import json
import re
from functools import cached_property
from pathlib import Path

from ..knowledge.ledger import known_at, load_ledger
from ..text.ingest import load_book
from .build import load_game
from .engine import located as engine_located
from .retrieve import Memory

SYSTEM = """你在扮演小说里的一个角色，和玩家对话。玩家扮演{hero}。
规则：
1. 你只知道下面“你知道的事”里列出的内容，以及你的身份和经历。没列出的事你不知道：被问到时按你的性格表示不知道、猜测或岔开，不要编造情节，不要提你没听说过的人。
2. 现在是故事的这个时刻，之后会发生什么你一概不知，不要预言或暗示。
3. 说话要像你：用你的自称、语气和口头禅；一般一到三句，口语，不要旁白，不要解释规则，不要说自己是 AI。
4. 玩家说的话可能和原著不同，你按自己的性格和知道的事自然回应。
5. mood 是你说这句话时的神情，只能是 平静 / 高兴 / 生气 / 难过 / 惊讶；rel 是玩家这句话让你对他的好感变了多少，-3 到 3 的整数，多数时候是 0。
6. 如果下面有“{hero}可能向你打听的事”：只有聊到了、你也愿意说时才说（按你的性格和对他的好感决定）。在 say 里把要点明明白白说出来了，才把编号写进 told；含糊其辞、卖关子、只说“以后再说”都不算，不要写。
输出 JSON：{{"say": "你说的话", "act": "说话时的动作或神情，很短，可以为空", "mood": "平静", "rel": 0, "told": []}}"""

MOODS = ("平静", "高兴", "生气", "难过", "惊讶")


def _attitude(v: int) -> str:
    return "很亲近" if v >= 50 else "亲近" if v >= 20 else "一般" if v > -20 else "冷淡" if v > -50 else "厌恶"


def _chapter_of(qid: str) -> int:
    return int(qid.split(".", 1)[0])


class World:
    """Everything the runtime reads for one book, loaded once."""

    def __init__(self, book: Path):
        self.book = book
        self.meta = load_book(book)
        self.game = load_game(book)
        self.led = load_ledger(book)
        chars = json.loads((book / "build" / "characters.json").read_text(encoding="utf-8"))["characters"]
        self.chars = {c["id"]: c for c in chars}
        self.hero = self.game["protagonist"]["id"]
        self.by_name = {}
        for c in chars:
            for n in [c["name"], *c["names"]]:
                self.by_name.setdefault(n, c["id"])
        self.lines_by: dict[str, list[dict]] = {}
        for b in self.game["beats"]:
            for x in b["lines"]:
                if x["who"] and x["kind"] in ("said", "telepathy", "written"):
                    self.lines_by.setdefault(x["who"], []).append(x)

    @cached_property
    def memory(self) -> Memory:
        items = [{"id": f["id"], "chapter": f["chapter"], "text": f["text"], "kind": "fact"}
                 for f in self.led["facts"] if not f["unverified"]]
        items += [{"id": e["id"], "chapter": e["chapter"], "text": e["summary"], "kind": "event"} for e in self.led["events"]]
        return Memory(self.book, items)

    def card_stages(self, cid: str) -> list[dict]:
        p = self.book / "build" / "cards" / f"{cid}.json"
        return json.loads(p.read_text(encoding="utf-8"))["stages"] if p.exists() else []

    def name(self, cid: str) -> str:
        return self.chars.get(cid, {}).get("name", cid)

    def future_names(self, chapter: int) -> dict[str, int]:
        return {n: ch for n, ch in self.game["first_seen"].items() if ch > chapter}


def persona(w: World, cid: str, chapter: int) -> dict:
    c = w.chars[cid]
    stages = w.card_stages(cid)
    past = [s for s in stages if s["card"] and s["to"] < chapter]
    now = next((s for s in stages if s["from"] <= chapter <= s["to"] and s["card"]), None)
    prev = past[-1]["card"] if past else None
    descs = [d for d in c.get("descs", []) if (m := re.match(r"ch(\d+)", d)) and int(m.group(1)) <= chapter]
    said = [x for x in w.lines_by.get(cid, []) if _chapter_of(x["q"]) <= chapter]
    step = max(1, len(said) // 8)
    samples = [x["text"] for x in said[::step]][-8:]
    speech = (now or {}).get("card", {}).get("speech") or (prev or {}).get("speech") or {}
    out = {"name": c["name"], "other_names": [n for n in c["names"] if n != c["name"]][:5],
           "descriptions": [re.sub(r"^ch\d+[:：]\s*", "", d) for d in descs[-4:]],
           "speech": {k: speech.get(k) for k in ("style", "self_reference", "habits") if speech.get(k)},
           "voice_samples": samples}
    if prev:
        hero = w.name(w.hero)
        out["earlier"] = {k: prev.get(k) for k in ("identity", "personality", "status", "goals", "secrets") if prev.get(k)}
        out["relationships"] = [r for r in prev.get("relationships", []) if r.get("with")][:8]
        out["toward_player"] = next((r for r in prev.get("relationships", []) if r.get("with") in w.chars[w.hero]["names"] or r.get("with") == hero), None)
    return out


def knowledge(w: World, cid: str, chapter: int, query: str, *, k: int = 12, recent: int = 5) -> dict:
    kn = known_at(w.led, cid, chapter)
    ids = [f["id"] for f in kn["facts"]] + [e["id"] for e in kn["events"]]
    ranked = w.memory.rank(query, ids, chapter, k=k)
    seen = {x["id"] for x in ranked}
    latest = [{"id": e["id"], "chapter": e["chapter"], "text": e["summary"], "kind": "event"} for e in kn["events"][-recent:]
              if e["id"] not in seen]
    return {"known_facts": len(kn["facts"]), "known_events": len(kn["events"]), "relevant": ranked, "latest": latest,
            "backend": w.memory.backend}


def scene_context(w: World, beat_i: int, upto_p: int | None, visit: str | None = None, cid: str | None = None) -> dict:
    """The scene as the player has seen it: lines up to paragraph `upto_p` (the whole beat when None).
    With `visit`, the player walked to that map node to find the character there instead."""
    b = w.game["beats"][beat_i]
    if visit:
        return {"chapter": b["chapter"], "heading": b["heading"], "place": visit, "time": "",
                "present": [w.name(w.hero), w.name(cid)], "lines_so_far": []}
    upto = [x for x in b["lines"] if upto_p is None or x["p"] <= upto_p]
    return {"chapter": b["chapter"], "heading": b["heading"], "place": b["node"] or b["place"], "time": b["time"],
            "present": [w.name(x) for x in b["present"]],
            "lines_so_far": [f"{x['name'] or '某人'}：{x['text']}" for x in upto][-12:]}


def build_messages(w: World, cid: str, beat_i: int, upto_p: int | None, history: list[dict], message: str,
                   visit: str | None = None, play: dict | None = None) -> tuple[list[dict], dict]:
    """play (optional, from the game engine): {"rel": int, "deeds": [str], "intel": [{"id","topic","answer"}]}"""
    b = w.game["beats"][beat_i]
    ch = b["chapter"]
    hero = w.name(w.hero)
    query = message + " " + " ".join(h["text"] for h in history[-4:])
    per, mem, sc = persona(w, cid, ch), knowledge(w, cid, ch, query), scene_context(w, beat_i, upto_p, visit, cid)
    fmt = lambda xs: "\n".join(f"- 第{x['chapter'] + 1}章：{x['text']}" for x in xs) or "（无）"
    ctx = "\n\n".join([
        f"【你是】{per['name']}" + (f"（也被叫作：{'、'.join(per['other_names'])}）" if per["other_names"] else ""),
        "【你的情况】\n" + "\n".join(f"- {d}" for d in per["descriptions"]) +
        (("\n此前：" + json.dumps(per["earlier"], ensure_ascii=False)) if per.get("earlier") else ""),
        ("【你和其他人】" + json.dumps(per.get("relationships", []), ensure_ascii=False)) if per.get("relationships") else "",
        "【说话方式】" + json.dumps(per["speech"], ensure_ascii=False) + "\n你说过的话：" + " / ".join(per["voice_samples"]),
        f"【此刻】第{sc['chapter'] + 1}章 {sc['heading']}，地点：{sc['place']}" + (f"，{sc['time']}" if sc["time"] else "") +
        f"。在场：{'、'.join(sc['present'])}\n刚才的对话：\n" + ("\n".join(sc["lines_so_far"]) or "（无）"),
        "【你知道的事（和眼下的话最相关的）】\n" + fmt(mem["relevant"]),
        "【你最近经历的事】\n" + fmt(mem["latest"]),
        *(_play_context(play, hero) if play else []),
    ])
    msgs = [{"role": "system", "content": SYSTEM.format(hero=hero) + "\n\n" + ctx}]
    for h in history[-12:]:
        if h["role"] == "player":
            msgs.append({"role": "user", "content": f"{hero}：{h['text']}"})
        else:
            msgs.append({"role": "assistant", "content": json.dumps({"say": h["text"], "act": h.get("act", "")}, ensure_ascii=False)})
    msgs.append({"role": "user", "content": f"{hero}：{message}"})
    return msgs, {"persona": per, "memory": mem, "scene": sc}


def _bigrams(s: str, drop: list[str]) -> set[str]:
    for n in drop:
        s = s.replace(n, " ")
    s = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", " ", s)
    return {s[i:i + 2] for i in range(len(s) - 1) if " " not in s[i:i + 2]}


def said_enough(say: str, answer: str, names: list[str]) -> bool:
    """The model's claim that it told a piece of intel only counts if the line shares enough of its
    wording; teasing ("you'll see") is not telling."""
    a = _bigrams(answer, names)
    hit = a & _bigrams(say, names)
    return len(hit) >= 2 and len(hit) >= 0.15 * len(a)


def _play_context(play: dict, hero: str) -> list[str]:
    out = [f"【你对{hero}的看法】{_attitude(play.get('rel', 0))}（好感 {play.get('rel', 0)}，-100~100）"]
    if play.get("deeds"):
        out.append(f"【{hero}做过的、和你原以为的不一样的事（你亲眼见到的）】\n" + "\n".join(f"- {d}" for d in play["deeds"]))
    if play.get("intel"):
        out.append(f"【{hero}可能向你打听的事】\n" + "\n".join(f"- {i['id']}：{i['topic']} —— 要点：{i['answer']}" for i in play["intel"]))
    return out


def _validator(w: World, chapter: int, allowed_text: str):
    future = {n: c for n, c in w.future_names(chapter).items() if n not in allowed_text}

    def check(obj):
        if not isinstance(obj, dict) or not isinstance(obj.get("say"), str) or not obj["say"].strip():
            return obj, ["输出必须是 {\"say\": \"...\", \"act\": \"...\"}，say 不能为空"]
        text = obj["say"] + (obj.get("act") or "")
        hits = [n for n in future if n in text]
        if hits:
            return obj, [f"你提到了此刻你还不知道的名字：{'、'.join(hits)}。去掉它们，只说你知道的。"]
        return obj, []
    return check


async def reply(w: World, llm, cid: str, beat_i: int, upto_p: int | None, history: list[dict], message: str,
                visit: str | None = None, play: dict | None = None) -> dict:
    msgs, ctx = build_messages(w, cid, beat_i, upto_p, history, message, visit, play)
    ch = w.game["beats"][beat_i]["chapter"]
    allowed = message + " ".join(h["text"] for h in history) + " ".join(ctx["scene"]["lines_so_far"])
    obj, problems = await llm.chat_json("dialogue", msgs, validate=_validator(w, ch, allowed), max_attempts=2,
                                        temperature=0.7, max_tokens=600)
    obj = obj if isinstance(obj, dict) else {}
    say = obj.get("say", "") or ""
    try:
        rel = max(-3, min(3, int(obj.get("rel") or 0)))
    except (TypeError, ValueError):
        rel = 0
    offered = {i["id"]: i for i in (play or {}).get("intel", [])}
    claimed = [t for t in obj.get("told") or [] if t in offered] if isinstance(obj.get("told"), list) else []
    names = [w.name(cid), w.name(w.hero)]
    told = [t for t in claimed if said_enough(say, offered[t]["answer"], names)]
    return {"say": say, "act": obj.get("act") or "", "mood": obj.get("mood") if obj.get("mood") in MOODS else "平静",
            "rel": rel, "told": told, "told_claimed": claimed,
            "flags": ["future_name"] if any("还不知道的名字" in p for p in problems) else ([] if say else ["empty"]),
            "used": [x["id"] for x in ctx["memory"]["relevant"]], "backend": ctx["memory"]["backend"]}


def located(w: World, beat_i: int) -> dict[str, str]:
    return engine_located(w.game, beat_i)


def find_character(w: World, name: str) -> str:
    cid = w.by_name.get(name)
    if not cid:
        raise ValueError(f"no character named {name!r}")
    return cid


def beat_for(w: World, cid: str, chapter: int) -> int:
    """The last playable beat at or before the end of `chapter` where the character can be talked to."""
    best = None
    for b in w.game["beats"]:
        if b["chapter"] > chapter:
            break
        if cid in b["talk"]:
            best = b["i"]
    if best is None:
        raise ValueError(f"{w.name(cid)} cannot be talked to by chapter {chapter}")
    return best
