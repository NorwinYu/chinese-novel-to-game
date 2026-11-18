"""Stage ⑥: turn the build into a playable script, build/game.json.

Nothing here calls a model. The game is a canon replay seen from the protagonist: one beat per ledger
scene in book order; the player reads the scene as a visual novel (the original paragraphs, with the
extracted speaker of every quotation) and, in scenes they take part in, can stop and talk to the main
characters present. Talking does not change the story.

  beats    one per scene: chapter, map node, who is present, whether the protagonist is there,
           the quotations with speakers, the S/A characters one can talk to, events, anchor flag
  anchors  the protagonist's major events (importance 3, protagonist involved or watching)
  map      nodes the protagonist visits, their parents, and moves between them
  trail    where each main character was last seen, by beat, so the map can place them (scenes whose node
           places.json only guessed are left out)
  first_seen  chapter each character name first occurs in the text, for spoiler checks

book.json "game" (all optional):
  {"viewpoint": "hero" | "reader",   hero: scenes without the protagonist are skipped (default unless
                                      the profile says the book has an ensemble cast); reader: shown
                                      as cut-scenes the player only watches
   "talk_tiers": "SA",               who can be talked to (characters with cards)
   "anchor_importance": 3,
   "chapters": [0, 10]}              only this chapter range (0-based, inclusive) is played: a slice of the book"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from ..extract.chapter import number_chapter
from ..extract.review import load_records
from ..knowledge.cards import _raw_first, book_stages
from ..knowledge.ledger import Resolver, load_ledger
from ..text.ingest import load_book, load_chapters

SPOKEN = ("said", "telepathy", "written", "thought", "sound")


def game_settings(book: Path) -> dict:
    meta = load_book(book)
    g = dict(meta.get("game") or {})
    ensemble = (meta.get("profile") or {}).get("protagonist") == "ensemble"
    g.setdefault("viewpoint", "reader" if ensemble else "hero")
    g.setdefault("talk_tiers", "SA")
    g.setdefault("anchor_importance", 3)
    g.setdefault("chapters", None)
    return g


def protagonist_id(book: Path, res: Resolver) -> str:
    hero = load_book(book).get("protagonist")
    if not hero:
        raise ValueError("book.json has no \"protagonist\"; set it in stage ③")
    cid = res.lookup(hero) or next((i for i, c in res.chars.items() if hero in c["names"]), None)
    if not cid:
        raise ValueError(f"protagonist {hero!r} is not in characters.json")
    return cid


def build_game(book: Path) -> dict:
    settings = game_settings(book)
    res = Resolver(book)
    hero = protagonist_id(book, res)
    led = load_ledger(book)
    places = json.loads((book / "build" / "places.json").read_text(encoding="utf-8"))
    parent = {n["name"]: n["parent"] for n in places["nodes"]}
    scene_node = places.get("scene_node", {})
    guessed = set(places.get("scene_guessed", []))
    chapters = load_chapters(book)
    stages = book_stages(book, len(chapters))
    stage_of = lambda ch: next((k for k, s in enumerate(stages) if s["from"] <= ch <= s["to"]), len(stages) - 1)
    cards_dir = book / "build" / "cards"
    has_card = {p.stem for p in cards_dir.glob("*.json")} if cards_dir.exists() else set()
    talkable = {cid for cid, c in res.chars.items() if c["tier"] in settings["talk_tiers"] and cid in has_card}

    utter: dict[int, dict] = {}
    for rec in load_records(book):
        o = rec.get("result")
        if o:
            utter[rec["idx"]] = {u[0]: u for u in o.get("utterances", [])}
    events_by_scene: dict[str, list[dict]] = defaultdict(list)
    for e in led["events"]:
        events_by_scene[e["scene"]].append(e)

    beats, anchors, trail = [], [], defaultdict(list)
    moves: Counter = Counter()
    last_node = None
    quotes_cache: dict[int, dict] = {}
    lo, hi = settings["chapters"] or (0, len(chapters) - 1)
    for s in led["scenes"]:
        ch = s["chapter"]
        if not lo <= ch <= hi:
            continue
        if ch not in quotes_cache:
            quotes_cache[ch] = number_chapter(chapters[ch]["paragraphs"])[1]
        quotes = quotes_cache[ch]
        lines = []
        for qid, (p, text, _) in quotes.items():
            if not (s["from"] <= p <= s["to"]):
                continue
            u = utter.get(ch, {}).get(qid)
            who = res(ch, u[1]) if u else None
            lines.append({"q": f"{ch}.{qid}", "p": p, "who": who, "name": u[1] if u else "", "kind": u[3] if u else "unknown",
                          "text": text})
        present = s["present"]
        here = hero in present
        node = scene_node.get(s["id"])
        evs = events_by_scene.get(s["id"], [])
        anchor = next((e for e in evs if e["importance"] >= settings["anchor_importance"]
                       and (hero in e["who"] or hero in e.get("witnesses", []))), None)
        i = len(beats)
        beat = {"i": i, "id": s["id"], "chapter": ch, "heading": chapters[ch].get("heading", ""), "stage": stage_of(ch),
                "from": s["from"], "to": s["to"], "place": s["place"], "node": node, "time": s.get("time", ""),
                "present": present, "hero": here,
                "kind": "play" if here else ("cutscene" if settings["viewpoint"] == "reader" else "offscreen"),
                "talk": [c for c in present if c in talkable and c != hero] if here else [],
                "lines": lines, "events": [e["id"] for e in evs], "anchor": anchor["id"] if anchor else None}
        beats.append(beat)
        if anchor:
            anchors.append({"beat": i, "event": anchor["id"], "chapter": ch, "summary": anchor["summary"]})
        if node:
            # a guessed node is fine as the backdrop, but would put characters on the map where they were not
            for c in present if s["id"] not in guessed else [hero] if here else []:
                if c in talkable or c == hero:
                    trail[c].append([i, node])
            if here:
                if last_node and last_node != node:
                    moves[(last_node, node)] += 1
                last_node = node

    visited = Counter(b["node"] for b in beats if b["hero"] and b["node"])
    first_beat = {}
    for b in beats:
        if b["node"] and b["hero"]:
            first_beat.setdefault(b["node"], b["i"])
    nodes = {}
    for n in visited:
        m = n
        while m and m not in nodes:
            nodes[m] = {"name": m, "parent": parent.get(m), "visits": visited.get(m, 0), "first_beat": first_beat.get(m)}
            m = parent.get(m)
    names = {n for cid, c in res.chars.items() if c["type"] != "group" and c["tier"] in "SAB" for n in c["names"] if len(n) >= 2}
    game = {"protagonist": {"id": hero, "name": res.name(hero)}, "settings": settings, "stages": stages,
            "beats": beats, "anchors": anchors,
            "map": {"nodes": list(nodes.values()), "moves": [[a, b, k] for (a, b), k in moves.most_common()]},
            "trail": dict(trail), "characters": {cid: {"name": c["name"], "tier": c["tier"]} for cid, c in res.chars.items()
                                                 if cid in talkable or cid == hero},
            "first_seen": _raw_first(chapters, names)}
    (book / "build" / "game.json").write_text(json.dumps(game, ensure_ascii=False), encoding="utf-8")
    return game


def load_game(book: Path) -> dict:
    return json.loads((book / "build" / "game.json").read_text(encoding="utf-8"))


def game_report(book: Path, game: dict | None = None) -> dict:
    g = game or load_game(book)
    beats = g["beats"]
    play = [b for b in beats if b["hero"]]
    chapters = sorted({b["chapter"] for b in beats})
    hero_chapters = {b["chapter"] for b in play}
    return {"viewpoint": g["settings"]["viewpoint"], "beats": len(beats), "play_beats": len(play),
            "play_share": round(len(play) / max(1, len(beats)), 3),
            "node_coverage": round(sum(1 for b in play if b["node"]) / max(1, len(play)), 3),
            "talk_share": round(sum(1 for b in play if b["talk"]) / max(1, len(play)), 3),
            "attributed_line_share": round(sum(1 for b in play for x in b["lines"] if x["who"]) /
                                           max(1, sum(len(b["lines"]) for b in play)), 3),
            "anchors": len(g["anchors"]), "map_nodes": len(g["map"]["nodes"]),
            "chapters_without_protagonist": [c for c in chapters if c not in hero_chapters][:30],
            "talkable_characters": sorted({g["characters"][c]["name"] for b in play for c in b["talk"]}),
            "anchor_examples": [f"ch{a['chapter']} {a['summary']}" for a in g["anchors"][:8]]}
