"""Game design: what turns the canon replay into a game, build/design.json.

A cheap text model fills a fixed shape and the validators below keep it inside the rules the engine
(engine.py) implements, so a design can always be played:

  stats      2–4 numbers the player manages (start, min, max); one may end the game when it hits min
  goal       what the protagonist wants by the end of the slice, as a stat target
  actions    what a free-time slot can be spent on (effects on stats, conditions); talking to someone
             on the map is the engine's own action and is not listed
  upkeep     applied at the start of each free time
  relations  starting attitude of the main characters towards the protagonist (-100..100)
  endings    checked in order at the end; the last has no conditions
  intel      things a character knows that the player can get out of them by asking; each unlocks an
             option at a later anchor (the fact is in the ledger and the character knows it in time)
  choices    one per anchor: where in the scene it appears (paragraph), the canon option and 2–3 others,
             each with effects, relation changes, flags, conditions, what happens next and how the story
             comes back to the next scene (soft regression)

Three model steps: frame (stats, goal, actions, relations, endings), intel, choices (one call per anchor,
in parallel). Each item is cached by the digest of its prompt. A hand-edited games/<slug>/design.json,
if present, is played instead of the generated one; `n2g design --check` validates either.

book.json "design" (optional): {"guide": "extra instructions for the designer model", "slots": 2}"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path

from ..knowledge.ledger import known_at, load_ledger
from ..text.ingest import load_book, load_chapters
from .build import load_game
from .engine import free_after, located, playable

OPS = (">=", "<=", ">", "<", "==")
ALL = "-all"  # an option that loses all of a stat (the engine drops it to min)
KINDS = ("win", "lose", "neutral")
_ID = re.compile(r"^[a-z][a-z0-9_]{0,15}$")

COND_HELP = ('条件 when / needs 是数组，全部满足才算：{"stat":"属性id","op":">=","value":数} / '
             '{"rel":"人物id","op":">=","value":数} / {"flag":"标记"} / {"not_flag":"标记"} / {"intel":"情报id"}')

FRAME_PROMPT = """你是游戏策划。把小说的一段剧情做成以主角身份经营和抉择的游戏：玩家扮演{hero}，在关键情节做抉择，平时用有限的空闲时间做事、找人打听消息，努力达成目标。
引擎已实现规则，你只填数值和文字：
- stats 2~4 个属性：id（小写英文）、name（两三个汉字）、unit（可空）、start、min、max。数值用书里的单位和数目（钱就用书里的钱数），start 是这段开头主角的实际情况，范围要装得下书里的大起大落。
  可以给一个属性写 fail：{{"title":"","text":""}}，降到 min 时游戏失败。fail 只给耗尽就活不下去的属性（体力、性命之类）；书里主角在这段一直活着，照原著走绝不能触发 fail。
- goal：一句话 text，加一个属性 stat 和阶段末要达到的 target。目标来自主角在书里的愿望。
- 每章结束有 slots 段空闲（1~3）。actions 3~6 种空闲时能做的事（谋生、休息、花销、自我提升之类，按书里的生活写）：id、name（四字以内）、desc（一句）、effects（属性变化）、needs（条件，可空）。
  upkeep：每次空闲开始自动发生的属性变化（如生活开销，可空）。“去找人说话”是引擎自带的，不要写进 actions。
- relations：主要人物对主角的初始好感 {{人物id: -100~100}}，按书里开头的关系。
- endings 2~5 个，按顺序判定，第一个满足 when 的生效；最后一个 when 为 []，兜底。kind 取 win / lose / neutral，至少一个 win。
{cond}
数值要让结局取决于玩家：每次抉择和空闲安排都有分量，偷懒或乱来会失败，用心经营、问到关键消息才能达成目标。一次行动的变化不超过属性范围的三成。
输出 JSON：{{"stats":[{{"id":"","name":"","unit":"","start":0,"min":0,"max":100}}],"goal":{{"text":"","stat":"","target":0}},
"slots":2,"actions":[{{"id":"","name":"","desc":"","effects":{{}},"needs":[]}}],"upkeep":{{}},"relations":{{}},
"endings":[{{"id":"","kind":"win","title":"","text":"","when":[]}}]}}"""

INTEL_PROMPT = """你为游戏设计“打听情报”。玩家扮演{hero}，空闲时可以去找人聊天，问出对方知道的事；问到的情报会在之后的某个关键抉择解锁一个更好的选项。
给你：关键抉择（beat 编号和事件）、每个可对话的人知道的事（fact id 和这件事出现的 beat）。
选 3~8 条情报，每条：{{"id":"i1","cid":"人物id","fact":"fact id","topic":"玩家该打听的方向，十字以内，不说出答案","answer":"对方会说出的要点，只能来自这条 fact","unlock":抉择的 beat 编号}}
要求：fact 出现的 beat 要早于 unlock；topic 让玩家有线索可循；尽量分给不同的人、不同的抉择。
输出 JSON：{{"intel":[...]}}"""

CHOICE_PROMPT = """你为小说游戏的一个关键情节设计抉择。玩家扮演{hero}。
给你：属性、目标、结局、这一段原文（带段号）、下一段剧情概要、可以在这里用上的情报。
- at_p：抉择出现在哪一段之前（原文段号）。这一段及以后是原著里主角的做法和后果。
- prompt：抉择时的处境和问题，一两句，用“你”称呼玩家。
- options 3~4 个，恰好一个 canon:true，是原著的做法（effects 照原著的得失如实写，数目和属性单位一致；原著里某样东西全部失去时写 "-all"；不写 needs、不写 ending，result 和 bridge 留空）。
  其余是这个处境下合理的其他做法，每个写：
  text 选项文字（十五字以内）；effects 属性变化；rel 好感变化 {{人物id: 数}}；flags 之后可以引用的标记（可空）；
  needs 条件（可空）；needs_intel 情报 id（可空；有可用情报时至少一个选项要用它，而且这个选项更好）；
  result 这样做之后马上发生的事，两三句旁白，用“你”；bridge 之后故事怎样仍然接上“下一段剧情”，一两句；
  ending 可选：只有极端的选择才写一个 kind 为 lose 的结局 id，游戏就此结束。
{cond}
不要写这一段之后才发生的事，不要提还没出场的人；文字口语、简短。
输出 JSON：{{"at_p":0,"prompt":"","options":[{{"id":"canon","text":"","canon":true,"effects":{{}},"rel":{{}},"flags":[]}},
{{"id":"a","text":"","canon":false,"effects":{{}},"rel":{{}},"flags":[],"needs":[],"needs_intel":"","result":"","bridge":"","ending":""}}]}}"""


def _digest(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def design_path(book: Path) -> Path:
    hand = book / "design.json"
    return hand if hand.exists() else book / "build" / "design.json"


def load_design(book: Path) -> dict:
    return json.loads(design_path(book).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- validation

def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _effects(o, stats: dict, p: list[str], where: str, cap: float = 0.34, allow_all: bool = False) -> dict:
    out = {}
    for k, v in (o or {}).items() if isinstance(o, dict) else []:
        if k not in stats:
            p.append(f"{where}: effects 里的 {k!r} 不是属性 id {list(stats)}")
            continue
        if v == ALL:
            if allow_all:
                out[k] = ALL
            else:
                p.append(f"{where}: {ALL!r} 只能用在抉择选项里")
            continue
        s = stats[k]
        lim = max(1.0, (s["max"] - s["min"]) * cap)
        d = _num(v)
        if abs(d) > lim:
            p.append(f"{where}: {k} 变化 {d:g} 太大，最多 ±{lim:g}")
            d = max(-lim, min(lim, d))
        if d:
            out[k] = int(round(d))
    return out


def _conds(o, d: dict, p: list[str], where: str, *, intel: set[str] | None = None) -> list[dict]:
    out = []
    if o in (None, "", {}):
        return out
    if not isinstance(o, list):
        p.append(f"{where}: 条件必须是数组")
        return out
    for c in o:
        if not isinstance(c, dict):
            p.append(f"{where}: 条件 {c!r} 格式不对")
            continue
        if "stat" in c or "rel" in c:
            key = "stat" if "stat" in c else "rel"
            ok = c[key] in (d["stats"] if key == "stat" else d["characters"])
            if not ok or c.get("op") not in OPS:
                p.append(f"{where}: 条件 {json.dumps(c, ensure_ascii=False)} 的 {key} 或 op 不对")
                continue
            out.append({key: c[key], "op": c["op"], "value": _num(c.get("value"))})
        elif "flag" in c or "not_flag" in c:
            k = "flag" if "flag" in c else "not_flag"
            if isinstance(c[k], str) and c[k]:
                out.append({k: c[k]})
        elif "intel" in c and (intel is None or c["intel"] in intel):
            out.append({"intel": c["intel"]})
        else:
            p.append(f"{where}: 不认识的条件 {json.dumps(c, ensure_ascii=False)}")
    return out


def check_frame(o, characters: dict) -> tuple[dict, list[str]]:
    p: list[str] = []
    o = o if isinstance(o, dict) else {}
    stats = {}
    for s in (o.get("stats") or [])[:4]:
        if not isinstance(s, dict) or not _ID.match(str(s.get("id", ""))) or not s.get("name"):
            p.append(f"属性 {s!r} 缺 id（小写英文）或 name")
            continue
        lo, hi = _num(s.get("min")), _num(s.get("max"), 100)
        if hi <= lo:
            p.append(f"属性 {s['id']}: max 必须大于 min")
            continue
        st = {"id": s["id"], "name": str(s["name"])[:6], "unit": str(s.get("unit") or "")[:3], "min": lo, "max": hi,
              "start": max(lo, min(hi, _num(s.get("start"), lo)))}
        if isinstance(s.get("fail"), dict) and s["fail"].get("title"):
            st["fail"] = {"title": str(s["fail"]["title"])[:20], "text": str(s["fail"].get("text") or "")[:300]}
        stats[st["id"]] = st
    if not 2 <= len(stats) <= 4:
        p.append(f"需要 2~4 个属性，现在 {len(stats)} 个")
    d = {"stats": stats, "characters": characters}
    g = o.get("goal") if isinstance(o.get("goal"), dict) else {}
    goal = {"text": str(g.get("text") or "")[:60], "stat": g.get("stat"), "target": _num(g.get("target"))}
    if goal["stat"] not in stats or not goal["text"]:
        p.append("goal 需要 text 和一个属性 id stat")
    elif not stats[goal["stat"]]["min"] < goal["target"] <= stats[goal["stat"]]["max"]:
        # start may already exceed the target when the story spends it early; the simulation judges difficulty
        p.append("goal.target 要在该属性的 min 和 max 之间")
    actions = []
    for a in (o.get("actions") or [])[:6]:
        if not isinstance(a, dict) or not _ID.match(str(a.get("id", ""))) or not a.get("name"):
            p.append(f"行动 {a!r} 缺 id 或 name")
            continue
        where = f"行动 {a['id']}"
        actions.append({"id": a["id"], "name": str(a["name"])[:8], "desc": str(a.get("desc") or "")[:40],
                        "effects": _effects(a.get("effects"), stats, p, where), "needs": _conds(a.get("needs"), d, p, where)})
    if len(actions) < 3:
        p.append(f"需要 3~6 个空闲行动，现在 {len(actions)} 个")
    if actions and not any(any(v > 0 for v in a["effects"].values()) for a in actions):
        p.append("没有任何行动能让属性增加，玩家无事可做")
    endings = []
    for e in (o.get("endings") or [])[:5]:
        if not isinstance(e, dict) or not _ID.match(str(e.get("id", ""))) or e.get("kind") not in KINDS or not e.get("title"):
            p.append(f"结局 {e!r} 缺 id/title 或 kind 不在 {KINDS}")
            continue
        endings.append({"id": e["id"], "kind": e["kind"], "title": str(e["title"])[:20], "text": str(e.get("text") or "")[:400],
                        "when": _conds(e.get("when"), d, p, f"结局 {e['id']}")})
    if not any(e["kind"] == "win" for e in endings):
        p.append("至少要有一个 kind 为 win 的结局")
    if not endings or endings[-1]["when"]:
        p.append("最后一个结局的 when 必须是 []，作兜底")
    if not (any(e["kind"] == "lose" for e in endings) or any("fail" in s for s in stats.values())):
        p.append("需要失败的可能：一个 lose 结局，或一个属性的 fail")
    rel = {}
    for k, v in (o.get("relations") or {}).items() if isinstance(o.get("relations"), dict) else []:
        if k in characters:
            rel[k] = int(max(-100, min(100, _num(v))))
    slots = int(max(1, min(3, _num(o.get("slots"), 2))))
    return {"stats": list(stats.values()), "goal": goal, "slots": slots, "actions": actions,
            "upkeep": _effects(o.get("upkeep"), stats, p, "upkeep"), "relations": rel, "endings": endings}, p


def _frame_ctx(frame: dict, characters: dict) -> dict:
    return {"stats": {s["id"]: s for s in frame["stats"]}, "characters": characters}


def check_intel(o, *, candidates: dict[str, dict], anchors: set[int]) -> tuple[list[dict], list[str]]:
    """candidates: fact id -> {cid -> first beat the player can ask them}."""
    p: list[str] = []
    items = (o.get("intel") if isinstance(o, dict) else None) or []
    out, seen = [], set()
    for k, x in enumerate(items[:8]):
        if not isinstance(x, dict):
            continue
        iid = f"i{k + 1}"
        fact, cid, unlock = x.get("fact"), x.get("cid"), x.get("unlock")
        try:
            unlock = int(unlock)
        except (TypeError, ValueError):
            unlock = None
        if fact not in candidates or cid not in candidates[fact]:
            p.append(f"情报 {x.get('id') or iid}: {cid!r} 不知道 fact {fact!r}（只能从给出的列表里选）")
            continue
        if unlock not in anchors:
            p.append(f"情报 {x.get('id') or iid}: unlock {x.get('unlock')!r} 不是关键抉择的 beat")
            continue
        first = candidates[fact][cid]
        if first >= unlock:
            p.append(f"情报 {x.get('id') or iid}: 最早在 beat {first} 才能问到，晚于抉择 beat {unlock}")
            continue
        if not x.get("topic") or not x.get("answer") or (fact, cid) in seen:
            p.append(f"情报 {x.get('id') or iid}: 缺 topic/answer 或重复")
            continue
        seen.add((fact, cid))
        out.append({"id": iid, "cid": cid, "fact": fact, "topic": str(x["topic"])[:16], "answer": str(x["answer"])[:120],
                    "from_beat": first, "unlock": unlock})
    if len(out) < 3:
        p.append(f"需要至少 3 条有效情报，现在 {len(out)} 条")
    return out, p


def check_choice(o, *, frame: dict, characters: dict, beat: dict, intel: list[dict], future: list[str]) -> tuple[dict, list[str]]:
    p: list[str] = []
    o = o if isinstance(o, dict) else {}
    d = _frame_ctx(frame, characters)
    at = int(_num(o.get("at_p"), beat["from"]))
    if not beat["from"] <= at <= beat["to"]:
        p.append(f"at_p {at} 不在这一段的段号 {beat['from']}~{beat['to']} 里")
        at = beat["from"]
    here = {i["id"] for i in intel if i["unlock"] == beat["i"]}
    lose = {e["id"] for e in frame["endings"] if e["kind"] == "lose"}
    opts, ids = [], set()
    for x in (o.get("options") or [])[:4]:
        if not isinstance(x, dict) or not x.get("text"):
            continue
        oid = re.sub(r"[^a-z0-9_]", "", str(x.get("id") or "")) or f"o{len(opts)}"
        if oid in ids:
            oid = f"{oid}{len(opts)}"
        ids.add(oid)
        canon = bool(x.get("canon"))
        where = f"选项 {oid}"
        op = {"id": oid, "text": str(x["text"])[:24], "canon": canon,
              "effects": _effects(x.get("effects"), d["stats"], p, where, cap=1.0, allow_all=True),
              "rel": {k: int(max(-30, min(30, _num(v)))) for k, v in (x.get("rel") or {}).items() if k in characters}
              if isinstance(x.get("rel"), dict) else {},
              "flags": [str(f)[:16] for f in (x.get("flags") or []) if f][:3] if isinstance(x.get("flags"), list) else []}
        if not canon:
            op["needs"] = _conds(x.get("needs"), d, p, where)
            ni = x.get("needs_intel") or ""
            if ni and ni not in here:
                p.append(f"{where}: needs_intel {ni!r} 不是这里能用的情报 {sorted(here)}")
                ni = ""
            op["needs_intel"] = ni
            op["result"] = str(x.get("result") or "")[:300]
            op["bridge"] = str(x.get("bridge") or "")[:200]
            end = x.get("ending") or ""
            if end and end not in lose:
                p.append(f"{where}: ending {end!r} 不是 lose 结局 {sorted(lose)}")
                end = ""
            op["ending"] = end
            if not op["result"] or not (op["bridge"] or end):
                p.append(f"{where}: 非原著选项要写 result 和 bridge（直接结束游戏的选项可以不写 bridge）")
            hits = [n for n in future if n in op["result"] + op["bridge"]]
            if hits:
                p.append(f"{where}: 提到了还没出场的 {'、'.join(hits[:3])}")
        opts.append(op)
    canon = [x for x in opts if x["canon"]]
    if len(canon) != 1:
        p.append(f"必须恰好一个 canon 选项，现在 {len(canon)} 个")
    if not 3 <= len(opts) <= 4:
        p.append(f"需要 3~4 个选项，现在 {len(opts)} 个")
    if any(v == ALL for x in opts if not x["canon"] for v in x["effects"].values()):
        p.append(f'{ALL!r} 只给原著选项用')
    unused = here - {x.get("needs_intel") for x in opts}
    if unused:
        p.append(f"这里的情报 {sorted(unused)} 没有选项用到：每条可用情报都要有一个用 needs_intel 解锁的选项")
    if sum(1 for x in opts if x.get("ending")) > 1:
        p.append("最多一个选项直接结束游戏")
    if not o.get("prompt"):
        p.append("缺 prompt")
    return {"beat": beat["i"], "at_p": at, "prompt": str(o.get("prompt") or "")[:120], "options": opts}, p


def gate_actions(design: dict, game: dict) -> None:
    """An action that names someone is offered from the chapter that person first appears in."""
    first = game["first_seen"]
    lo = (game["settings"].get("chapters") or (0, 0))[0]
    for a in design["actions"]:
        text = a["name"] + a.get("desc", "")
        a["from_chapter"] = max([lo, *(ch for n, ch in first.items() if len(n) >= 2 and n in text)])


def check_design(d: dict, game: dict) -> list[str]:
    """Whole-design checks, also for a hand-edited design."""
    p: list[str] = []
    chars = {c for c in game["characters"] if c != game["protagonist"]["id"]}
    _, fp = check_frame({**d, "stats": d.get("stats")}, {c: 1 for c in chars})
    p += [f"frame: {x}" for x in fp]
    anchors = {a["beat"] for a in game["anchors"]}
    have = {int(k) for k in d.get("choices", {})}
    if anchors - have:
        p.append(f"这些关键情节没有抉择：{sorted(anchors - have)}")
    ids = {i["id"] for i in d.get("intel", [])}
    used = {o.get("needs_intel") for c in d.get("choices", {}).values() for o in c["options"]}
    if ids - used:
        p.append(f"这些情报没有被任何选项用到：{sorted(ids - used)}")
    return p


# ---------------------------------------------------------------- context

def _hero_story(book: Path, game: dict) -> list[dict]:
    p = book / "build" / "cards" / f"{game['protagonist']['id']}.json"
    if not p.exists():
        return []
    lo, hi = game["settings"].get("chapters") or (0, 10**9)
    out = []
    for s in json.loads(p.read_text(encoding="utf-8"))["stages"]:
        if s.get("card") and s["to"] >= lo and s["from"] <= hi:
            out.append({"章": f"{s['from'] + 1}-{s['to'] + 1}", "目标": s["card"].get("goals"), "处境": s["card"].get("status")})
    return out


def _cast(book: Path, game: dict) -> dict[str, str]:
    hero = game["protagonist"]["id"]
    out = {}
    for cid, c in game["characters"].items():
        if cid == hero:
            continue
        p = book / "build" / "cards" / f"{cid}.json"
        ident = ""
        if p.exists():
            st = [s for s in json.loads(p.read_text(encoding="utf-8"))["stages"] if s.get("card")]
            ident = st[0]["card"].get("identity", "") if st else ""
        out[cid] = f"{c['name']}：{ident}"[:60]
    return out


def _askable(game: dict) -> dict[str, list[int]]:
    """Beats at which the player can talk to each character: in the scene, or by visiting them in the
    free time after the beat."""
    out: dict[str, list[int]] = {}
    for i in playable(game):
        for c in game["beats"][i]["talk"]:
            out.setdefault(c, []).append(i)
        if free_after(game, i):
            for c in located(game, i):
                out.setdefault(c, []).append(i)
    return {c: sorted(set(v)) for c, v in out.items()}


def _intel_candidates(book: Path, game: dict, led: dict) -> tuple[dict, dict]:
    beat_of_scene = {b["id"]: b["i"] for b in game["beats"]}
    hero = game["protagonist"]["id"]
    lo, hi = game["settings"].get("chapters") or (0, 10**9)
    ask = _askable(game)
    cand: dict[str, dict[str, int]] = {}
    shown: dict[str, list[dict]] = {}
    for cid in game["characters"]:
        if cid == hero or cid not in ask:
            continue
        for f in known_at(led, cid, hi)["facts"]:
            if f["chapter"] < lo or f["scene"] not in beat_of_scene:
                continue
            fb = beat_of_scene[f["scene"]]
            first = next((b for b in ask[cid] if b >= fb), None)
            if first is None:
                continue
            cand.setdefault(f["id"], {})[cid] = first
            shown.setdefault(cid, []).append({"fact": f["id"], "beat": first, "text": f["text"]})
    return cand, {c: v[:18] for c, v in shown.items()}


# ---------------------------------------------------------------- generation

BALANCE_PROMPT = """你在给小说游戏调数值。玩家扮演{hero}。下面是当前设计（属性、空闲行动、每次空闲的开销、结局条件、每个抉择各选项的属性变化）和离线模拟的结果。
只改数字，让模拟满足：
1. 照原著选（canon）全程不触发失败，原著里的得失也扣得起（canon_clipped 列出的是属性不够扣的地方：说明开头的数值、上限或收入和书不符）；
2. 用心经营（planner：挑好选项、空闲时做最划算的事、去打听情报）能达成目标，拿到 win 结局；
3. 不能太轻松：随机乱玩（random）的胜率不超过一半，偷懒或乱来会失败。
数字要和书里的数目一致；原著选项的得失尽量不改，除非它和书不符。一次空闲行动的变化不超过属性范围的三成。
可以改：stats 的 start/max、actions 的 effects、upkeep、slots（1~3）、endings 的 when 里的数值、选项的 effects。
{cond}
只输出要改的部分，JSON：{{"stats":[{{"id":"","start":0,"max":0}}],"actions":[{{"id":"","effects":{{}}}}],"upkeep":{{}},"slots":2,
"endings":[{{"id":"","when":[]}}],"options":[{{"beat":0,"id":"","effects":{{}}}}],"why":"一句话说明改了什么"}}"""

BALANCE_KEYS = ("deadlocks", "canon_survives", "canon_clipped", "win_reachable", "lose_reachable", "random_win_share")


def _unbalanced(rep: dict) -> list[str]:
    from ..gates import THRESHOLDS
    out = []
    for k in BALANCE_KEYS:
        how, limit = THRESHOLDS["design"][k]
        v = rep.get(k)
        if v is None or (how == "max" and v > limit) or (how == "min" and v < limit):
            out.append(f"{k} = {v}, needs {'<=' if how == 'max' else '>='} {limit}")
    return out


def _numbers(d: dict) -> dict:
    return {"stats": [{k: s[k] for k in ("id", "name", "start", "min", "max")} | ({"fail": True} if "fail" in s else {}) for s in d["stats"]],
            "goal": d["goal"], "slots": d["slots"], "upkeep": d["upkeep"],
            "actions": [{k: a[k] for k in ("id", "name", "effects", "needs", "from_chapter") if k in a} for a in d["actions"]],
            "endings": [{k: e[k] for k in ("id", "kind", "when")} for e in d["endings"]],
            "choices": {b: [{"id": o["id"], "canon": o["canon"], "text": o["text"], "effects": o["effects"],
                             **({"needs": o["needs"]} if o.get("needs") else {}), **({"intel": o["needs_intel"]} if o.get("needs_intel") else {})}
                            for o in c["options"]] for b, c in d["choices"].items()}}


def apply_balance(design: dict, patch: dict, characters: dict) -> tuple[dict, list[str]]:
    import copy
    d = copy.deepcopy(design)
    p: list[str] = []
    patch = patch if isinstance(patch, dict) else {}
    stats = {s["id"]: s for s in d["stats"]}
    for s in patch.get("stats") or []:
        if isinstance(s, dict) and s.get("id") in stats:
            st = stats[s["id"]]
            if "max" in s and _num(s["max"], st["max"]) > st["min"]:
                st["max"] = _num(s["max"], st["max"])
            if "start" in s:
                st["start"] = max(st["min"], min(st["max"], _num(s["start"], st["start"])))
    ctx = {"stats": stats, "characters": characters}
    acts = {a["id"]: a for a in d["actions"]}
    for a in patch.get("actions") or []:
        if isinstance(a, dict) and a.get("id") in acts and isinstance(a.get("effects"), dict):
            acts[a["id"]]["effects"] = _effects(a["effects"], stats, p, f"行动 {a['id']}")
    if isinstance(patch.get("upkeep"), dict):
        d["upkeep"] = _effects(patch["upkeep"], stats, p, "upkeep")
    if "slots" in patch:
        d["slots"] = int(max(1, min(3, _num(patch["slots"], d["slots"]))))
    ends = {e["id"]: e for e in d["endings"]}
    for e in patch.get("endings") or []:
        if isinstance(e, dict) and e.get("id") in ends and isinstance(e.get("when"), list) and ends[e["id"]] is not d["endings"][-1]:
            ends[e["id"]]["when"] = _conds(e["when"], ctx, p, f"结局 {e['id']}")
    for o in patch.get("options") or []:
        c = d["choices"].get(str(o.get("beat"))) if isinstance(o, dict) else None
        op = next((x for x in c["options"] if x["id"] == o.get("id")), None) if c else None
        if op and isinstance(o.get("effects"), dict):
            op["effects"] = _effects(o["effects"], stats, p, f"选项 {o.get('beat')}:{op['id']}", cap=1.0, allow_all=True)
    g = d["goal"]
    g["target"] = min(g["target"], stats[g["stat"]]["max"])
    return d, p


async def balance(design: dict, game: dict, llm, *, hero: str, extra: str = "", rounds: int = 3, log=lambda m: None):
    """Simulate, show the model what failed, let it change numbers only; keep the best design seen."""
    from .sim import sim_report
    chars = {c: 1 for c in game["characters"] if c != game["protagonist"]["id"]}
    history = []
    rep = sim_report(None, design, game)
    best = (len(_unbalanced(rep)), design)
    for r in range(rounds):
        bad = _unbalanced(rep)
        history.append({"round": r, "unmet": bad, "canon": rep["canon"].get("ending"), "planner": rep["planner"].get("ending"),
                        "random_win_share": rep["random_win_share"]})
        if not bad:
            break
        sim = {k: rep[k] for k in ("canon_survives", "canon_clipped", "win_reachable", "lose_reachable", "random_win_share",
                                   "random_lose_share", "endings_seen")}
        sim |= {"canon": rep["canon"], "planner": rep["planner"], "unmet": bad}
        msgs = [{"role": "system", "content": BALANCE_PROMPT.format(hero=hero, cond=COND_HELP) + extra},
                {"role": "user", "content": json.dumps({"设计": _numbers(design), "模拟": sim}, ensure_ascii=False)}]
        patch, _ = await llm.chat_json("director", msgs, max_attempts=2, temperature=0.3, max_tokens=2500)
        design, left = apply_balance(design, patch, chars)
        history[-1]["why"] = (patch or {}).get("why", "") if isinstance(patch, dict) else ""
        history[-1]["patch_problems"] = left[:5]
        rep = sim_report(None, design, game)
        log(f"balance round {r + 1}: {history[-1]['why'][:80]} → unmet {_unbalanced(rep)}")
        if len(_unbalanced(rep)) <= best[0]:
            best = (len(_unbalanced(rep)), design)
    if best[1] is not design and len(_unbalanced(rep)) > best[0]:
        design = best[1]
    return design, history


async def build_design(book: Path, llm, *, force: bool = False, rounds: int = 3, log=lambda m: None) -> dict:
    game = load_game(book)
    meta = load_book(book)
    guide = (meta.get("design") or {}).get("guide", "")
    led = load_ledger(book)
    chapters = load_chapters(book)
    hero = game["protagonist"]["name"]
    cast = _cast(book, game)
    out_path = book / "build" / "design.json"
    old = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}
    cache = old.get("_source", {})
    src: dict[str, str] = {}
    problems: dict[str, list[str]] = {}
    extra = f"\n补充要求：{guide}" if guide else ""

    async def ask(key, system, user, check, max_tokens):
        msgs = [{"role": "system", "content": system + extra}, {"role": "user", "content": user}]
        dg = _digest(json.dumps(msgs, ensure_ascii=False))
        src[key] = dg
        if not force and cache.get(key) == dg and key in old.get("_raw", {}):
            value, left = check(old["_raw"][key])
            if not left:  # a cached answer that fails the current validator is asked again
                return old["_raw"][key], value, left
        obj, _ = await llm.chat_json("director", msgs, validate=lambda x: (x, check(x)[1]),
                                     max_attempts=3, temperature=0.4, max_tokens=max_tokens)
        value, left = check(obj)
        return obj, value, left

    anchors = [{"beat": a["beat"], "chapter": a["chapter"] + 1, "事件": a["summary"]} for a in game["anchors"]]
    hero_facts = [f["text"] for f in led["facts"] if game["protagonist"]["id"] in f.get("about", [])
                  and (game["settings"].get("chapters") or (0, 10**9))[0] <= f["chapter"] <= (game["settings"].get("chapters") or (0, 10**9))[1]][:60]
    user = json.dumps({"主角": hero, "主角在这段的目标和处境": _hero_story(book, game), "关键情节": anchors,
                       "主要人物": cast, "关于主角的事实": hero_facts}, ensure_ascii=False)
    raw: dict = {}
    raw["frame"], frame, left = await ask("frame", FRAME_PROMPT.format(hero=hero, cond=COND_HELP), user,
                                          lambda o: check_frame(o, cast), 2500)
    if left:
        problems["frame"] = left
    log(f"frame: {len(frame['stats'])} stats, {len(frame['actions'])} actions, {len(frame['endings'])} endings")

    cand, shown = _intel_candidates(book, game, led)
    anchor_beats = {a["beat"] for a in game["anchors"]}
    user = json.dumps({"关键抉择": anchors, "可对话的人知道的事": {f"{cid} {cast.get(cid, '')}": v for cid, v in shown.items()}},
                      ensure_ascii=False)
    raw["intel"], intel, left = await ask("intel", INTEL_PROMPT.format(hero=hero), user,
                                          lambda o: check_intel(o, candidates=cand, anchors=anchor_beats), 2500)
    if left:
        problems["intel"] = left
    log(f"intel: {len(intel)} items")

    scenes = {s["id"]: s for s in led["scenes"]}
    playable = [b for b in game["beats"] if b["kind"] != "offscreen"]
    first_seen = game["first_seen"]
    frame_brief = {"属性": {s["id"]: f"{s['name']} {s['min']:g}~{s['max']:g}，开始 {s['start']:g}" for s in frame["stats"]},
                   "目标": frame["goal"], "结局": [{k: e[k] for k in ("id", "kind", "title")} for e in frame["endings"]]}

    async def one(a):
        b = game["beats"][a["beat"]]
        paras = chapters[b["chapter"]]["paragraphs"]
        nxt = next((x for x in playable if x["i"] > b["i"]), None)
        text = "\n".join(f"[{p}] {paras[p][:160]}" for p in range(b["from"], min(b["to"], len(paras) - 1) + 1))[:4000]
        here = [i for i in intel if i["unlock"] == b["i"]]
        later_ch = nxt["chapter"] if nxt else b["chapter"]
        future = [n for n, ch in first_seen.items() if ch > later_ch]
        user = json.dumps({**frame_brief, "在场的人": {c: cast[c] for c in b["present"] if c in cast},
                           "这一段原文": text, "事件": a["summary"],
                           "下一段剧情": scenes.get(nxt["id"], {}).get("summary", "（本段是最后一段）") if nxt else "（本段是最后一段）",
                           "可用情报": [{"id": i["id"], "内容": i["answer"]} for i in here]}, ensure_ascii=False)
        key = f"choice:{b['i']}"
        r, value, left = await ask(key, CHOICE_PROMPT.format(hero=hero, cond=COND_HELP), user,
                                   lambda o: check_choice(o, frame=frame, characters=cast, beat=b, intel=intel, future=future), 2500)
        raw[key] = r
        if left:
            problems[key] = left
        return value

    choices = await asyncio.gather(*(one(a) for a in game["anchors"]))
    design = {**frame, "intel": intel, "choices": {str(c["beat"]): c for c in choices}}
    gate_actions(design, game)
    design, history = await balance(design, game, llm, hero=hero, extra=extra, rounds=rounds, log=log)
    design["balance"] = history
    design["problems"] = check_design(design, game)
    design["_source"], design["_raw"] = src, raw
    out_path.write_text(json.dumps(design, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"written": str(out_path), "stats": [s["name"] for s in frame["stats"]], "goal": frame["goal"],
            "actions": [a["name"] for a in frame["actions"]], "endings": [f"{e['kind']} {e['title']}" for e in frame["endings"]],
            "intel": len(intel), "choices": len(choices), "with_problems": problems, "design_problems": design["problems"],
            "balance": history}
