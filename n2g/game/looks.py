"""Looks: structured appearance parameters the browser turns into low-poly 3D (static/scene.js).

No image service is needed. A cheap text-model call per item reads what the book already says and fills a
fixed schema; the validator below keeps every value inside it, so the renderer never meets an unknown word.

  character  from the earliest stage card's identity and appearance: sex, age, build, skin, hair, clothes,
             hat, accessories (colours as #rrggbb)
  place      per map node, from its name, the place strings mapped to it and scenes there: kind of place,
             indoor or not, architecture, palette, default time and weather, props, background crowd
  event      per anchor: who is in the picture, each person's pose, mood and facing, camera distance,
             extra props and background figures (soldiers, onlookers) as coloured silhouettes

  n2g looks --slug S            → build/looks.json (unchanged inputs are not asked again)
  n2g looks --slug S --show 李四  print one item
  games/<slug>/looks.json        hand corrections, same shape, only the fields to change; wins over the build

Nothing book-specific lives here: the enums are kinds of things, not things from any book."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path

from ..knowledge.ledger import load_ledger
from .build import load_game

MOODS = ("平静", "高兴", "生气", "难过", "惊讶")
CHAR = {
    "sex": ("male", "female"),
    "age": ("child", "teen", "young", "adult", "middle", "old"),
    "build": ("thin", "normal", "stout", "muscular"),
    "hair_style": ("bald", "crew", "short", "messy", "long", "bun", "braid", "ponytail", "topknot", "bob"),
    "facial_hair": ("none", "mustache", "beard", "goatee", "stubble"),
    "top": ("shirt", "jacket", "longrobe", "vest", "gown", "coat", "uniform", "armor"),
    "bottom": ("trousers", "skirt", "shorts"),
    "hat": ("none", "cap", "brimmed", "bowler", "straw", "scarf", "hood", "headband", "crown", "helmet"),
    "accessories": ("glasses", "pipe", "fan", "bag", "sword", "staff", "book", "towel", "umbrella", "basket",
                    "jewelry", "belt", "cane"),
}
PLACE = {
    "kind": ("street", "alley", "courtyard", "room", "hall", "shop", "teahouse", "yard", "village", "field",
             "road", "mountain", "forest", "river", "lake", "sea", "desert", "camp", "cave", "underground",
             "temple", "palace", "garden", "station", "sky"),
    "architecture": ("old_chinese", "western", "modern", "rustic", "none"),
    "time": ("day", "dusk", "night"),
    "weather": ("clear", "rain", "snow", "fog", "dust"),
    "props": ("cart", "table", "chair", "bed", "shelf", "crate", "barrel", "lantern", "tree", "bush", "rock",
              "well", "fence", "stall", "sign", "stairs", "cabinet", "stove", "pillar", "statue", "boat", "tent",
              "fire", "flag", "door"),
}
POSES = ("stand", "sit", "kneel", "walk", "run", "lie", "point", "raise", "bow", "fight", "hold", "cry")
CAMERAS = ("wide", "medium", "close")


def _enum_lines(d: dict) -> str:
    return "\n".join(f"- {k}: {' | '.join(v)}" for k, v in d.items())


CHAR_PROMPT = f"""你把小说人物的外貌描写转成 3D 低多边形人偶的参数。只能用下面列出的取值，颜色写 #rrggbb。
书里没写的按身份、年龄、处境和时代合理补全，不要和描写矛盾。
{_enum_lines(CHAR)}
输出 JSON：{{"sex":"","age":"","build":"","height":1.0,"skin":"#","hair_style":"","hair_color":"#",
"facial_hair":"","top":"","top_color":"#","trim_color":"#","bottom":"","bottom_color":"#","shoes_color":"#",
"hat":"","hat_color":"#","accessories":[]}}  height 在 0.85~1.15 之间（相对常人）；accessories 最多 3 个。"""

PLACE_PROMPT = f"""你把小说里的一个地点转成 3D 低多边形场景的参数。只能用下面列出的取值，颜色写 #rrggbb。
按时代背景和在这里发生的场景判断。
{_enum_lines(PLACE)}
输出 JSON：{{"kind":"","indoor":false,"architecture":"","ground":"#","wall":"#","roof":"#","accent":"#",
"time":"","weather":"","props":[],"crowd":0}}  props 2~6 个；crowd 是背景路人数 0~6。"""

EVENT_PROMPT = f"""你为小说的一个关键情节摆一个 3D 定格画面。在场人物只能从给出的名单里选（用 id），
每人选一个姿势和情绪，朝向写另一个人的 id 或 "camera"。名单外的人（如一群兵、围观者）写进 extras，只给称呼、人数、衣服颜色。
- pose: {' | '.join(POSES)}
- mood: {' | '.join(MOODS)}
- camera: {' | '.join(CAMERAS)}
- props: {' | '.join(PLACE['props'])}
- time: {' | '.join(PLACE['time'])}
只画这一刻，不画之后的事。
输出 JSON：{{"camera":"","time":"","people":[{{"id":"","pose":"","mood":"","facing":""}}],"props":[],
"extras":[{{"label":"","count":0,"color":"#"}}]}}  people 1~4 人；extras 最多 2 组、每组最多 8 人。"""

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _digest(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def _pick(v, allowed, default, problems, key):
    if v in allowed:
        return v
    problems.append(f"{key} 取值 {v!r} 不在允许列表 {list(allowed)} 里")
    return default


def _color(v, default, problems, key):
    if isinstance(v, str) and _HEX.match(v):
        return v.lower()
    problems.append(f"{key} 应为 #rrggbb 颜色，现在是 {v!r}")
    return default


def check_character(o) -> tuple[dict, list[str]]:
    p: list[str] = []
    o = o if isinstance(o, dict) else {}
    out = {k: _pick(o.get(k), CHAR[k], CHAR[k][1] if k != "hat" else "none", p, k)
           for k in ("sex", "age", "build", "hair_style", "facial_hair", "top", "bottom", "hat")}
    try:
        out["height"] = min(1.15, max(0.85, float(o.get("height", 1.0))))
    except (TypeError, ValueError):
        out["height"] = 1.0
        p.append("height 应为数字")
    for k, d in (("skin", "#c8a07a"), ("hair_color", "#222222"), ("top_color", "#556677"), ("trim_color", "#333333"),
                 ("bottom_color", "#333333"), ("shoes_color", "#222222"), ("hat_color", "#444444")):
        out[k] = _color(o.get(k, d), d, p, k)
    acc = o.get("accessories") or []
    out["accessories"] = [a for a in acc if a in CHAR["accessories"]][:3] if isinstance(acc, list) else []
    if isinstance(acc, list) and any(a not in CHAR["accessories"] for a in acc):
        p.append(f"accessories 只能用 {list(CHAR['accessories'])}")
    return out, p


def check_place(o) -> tuple[dict, list[str]]:
    p: list[str] = []
    o = o if isinstance(o, dict) else {}
    out = {k: _pick(o.get(k), PLACE[k], PLACE[k][0], p, k) for k in ("kind", "architecture", "time", "weather")}
    out["indoor"] = bool(o.get("indoor"))
    for k, d in (("ground", "#7a6e5e"), ("wall", "#9a8f80"), ("roof", "#4a4a50"), ("accent", "#a04030")):
        out[k] = _color(o.get(k, d), d, p, k)
    props = o.get("props") or []
    out["props"] = [x for x in props if x in PLACE["props"]][:6] if isinstance(props, list) else []
    if isinstance(props, list) and any(x not in PLACE["props"] for x in props):
        p.append(f"props 只能用 {list(PLACE['props'])}")
    try:
        out["crowd"] = int(min(6, max(0, int(o.get("crowd", 0)))))
    except (TypeError, ValueError):
        out["crowd"] = 0
    return out, p


def check_event(o, allowed_ids: set[str]) -> tuple[dict, list[str]]:
    p: list[str] = []
    o = o if isinstance(o, dict) else {}
    out = {"camera": _pick(o.get("camera"), CAMERAS, "medium", p, "camera"),
           "time": _pick(o.get("time"), PLACE["time"], "day", p, "time")}
    people = []
    for x in (o.get("people") or [])[:4]:
        if not isinstance(x, dict) or x.get("id") not in allowed_ids:
            p.append(f"people 里的 id {x.get('id') if isinstance(x, dict) else x!r} 不在名单 {sorted(allowed_ids)} 里")
            continue
        facing = x.get("facing") if x.get("facing") in allowed_ids | {"camera"} else "camera"
        people.append({"id": x["id"], "pose": _pick(x.get("pose"), POSES, "stand", p, "pose"),
                       "mood": _pick(x.get("mood"), MOODS, "平静", p, "mood"), "facing": facing})
    if not people:
        p.append("people 至少要有一个名单里的人")
    out["people"] = people
    out["props"] = [x for x in (o.get("props") or []) if x in PLACE["props"]][:6]
    extras = []
    for x in (o.get("extras") or [])[:2]:
        if isinstance(x, dict) and x.get("label"):
            extras.append({"label": str(x["label"])[:8], "count": int(min(8, max(1, int(x.get("count") or 1)))),
                           "color": _color(x.get("color", "#555555"), "#555555", p, "extras.color")})
    out["extras"] = extras
    return out, p


def load_looks(book: Path, *, raw: bool = False) -> dict:
    """build/looks.json with games/<slug>/looks.json laid over it field by field (same shape, only the
    fields to change). Overridden items go through the same validators, so a typo falls back to a default
    instead of reaching the renderer."""
    p = book / "build" / "looks.json"
    out = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"characters": {}, "places": {}, "events": {}}
    hand = book / "looks.json"
    if raw or not hand.exists():
        return out
    over = json.loads(hand.read_text(encoding="utf-8"))
    people = set(out["characters"]) | set(over.get("characters") or {})
    checks = {"characters": check_character, "places": check_place,
              "events": lambda o: check_event(o, people)}
    for section, check in checks.items():
        for key, fields in (over.get(section) or {}).items():
            base = {k: v for k, v in out[section].get(key, {}).items() if k != "source"}
            value, _ = check({**base, **fields})
            out[section][key] = {**value, "override": True}
    return out


def _first_card(book: Path, cid: str) -> dict:
    p = book / "build" / "cards" / f"{cid}.json"
    if not p.exists():
        return {}
    stages = [s for s in json.loads(p.read_text(encoding="utf-8"))["stages"] if s.get("card")]
    return stages[0]["card"] if stages else {}


def _tasks(book: Path, setting: str) -> list[tuple[str, str, list[dict], object]]:
    """(section, key, messages, checker) for everything the game shows."""
    g = load_game(book)
    led = load_ledger(book)
    places = json.loads((book / "build" / "places.json").read_text(encoding="utf-8"))
    variants = {n["name"]: n.get("variants", []) for n in places["nodes"]}
    scene_node = places.get("scene_node", {})
    summaries: dict[str, list[str]] = {}
    for s in led["scenes"]:
        if (n := scene_node.get(s["id"])):
            summaries.setdefault(n, []).append(s["summary"])
    head = f"时代背景：{setting}\n" if setting else ""
    out = []
    shown = {c for b in g["beats"] for c in b["present"]}
    for cid, c in g["characters"].items():
        if cid not in shown:
            continue
        card = _first_card(book, cid)
        body = {"身份": card.get("identity", ""), "外貌": card.get("appearance", "")}
        out.append(("characters", cid, [{"role": "system", "content": CHAR_PROMPT},
                                        {"role": "user", "content": head + json.dumps(body, ensure_ascii=False)}],
                    check_character))
    for n in g["map"]["nodes"]:
        body = {"地点": n["name"], "所属": n["parent"] or "", "书中写法": variants.get(n["name"], [])[:6],
                "在这里发生的场景": summaries.get(n["name"], [])[:4]}
        out.append(("places", n["name"], [{"role": "system", "content": PLACE_PROMPT},
                                          {"role": "user", "content": head + json.dumps(body, ensure_ascii=False)}],
                    check_place))
    events = {e["id"]: e for e in led["events"]}
    for an in g["anchors"]:
        b = g["beats"][an["beat"]]
        ids = [c for c in dict.fromkeys(events.get(an["event"], {}).get("who", []) + b["present"]) if c in g["characters"]]
        roster = {c: g["characters"][c]["name"] for c in ids}
        body = {"事件": an["summary"], "地点": b["node"] or b["place"], "名单": roster}
        out.append(("events", str(an["beat"]), [{"role": "system", "content": EVENT_PROMPT},
                                                {"role": "user", "content": head + json.dumps(body, ensure_ascii=False)}],
                    (lambda allowed: lambda o: check_event(o, allowed))(set(roster))))
    return out


async def build_looks(book: Path, llm, *, setting: str = "", force: bool = False) -> dict:
    old = load_looks(book, raw=True)
    tasks = _tasks(book, setting)
    new: dict = {"characters": {}, "places": {}, "events": {}}
    problems: dict[str, list[str]] = {}

    async def one(section, key, msgs, check):
        src = _digest(json.dumps(msgs, ensure_ascii=False))
        prev = old.get(section, {}).get(key)
        if not force and prev and prev.get("source") == src:
            new[section][key] = prev
            return
        obj, left = await llm.chat_json("director", msgs, validate=check, max_attempts=2, temperature=0.3, max_tokens=700)
        value, more = check(obj)
        new[section][key] = {**value, "source": src}
        if left or more:
            problems[f"{section}:{key}"] = (left or more)[:3]

    await asyncio.gather(*(one(*t) for t in tasks))
    (book / "build" / "looks.json").write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
    return {k: len(v) for k, v in new.items()} | {"with_problems": problems}
