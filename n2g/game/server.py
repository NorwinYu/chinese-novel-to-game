"""`n2g play`: a local web page to play one book.

The page draws the story in 3D from build/looks.json (static/scene.js) or, where AI images were rendered
(stage ⑦ optional), shows those instead. The game state lives on the server in build/saves/<slot>.json
and every move goes through engine.py, the same rules the simulator checks; the page only renders the
view it is sent. Only /api/talk calls a model."""
from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from pathlib import Path

from pydantic import BaseModel

from ..art.build import MOOD_KEYS, art_dir, load_plan, portrait_for
from ..text.ingest import load_chapters
from . import engine as E
from .design import load_design
from .looks import load_looks
from .npc import World, located, reply

STATIC = Path(__file__).parent / "static"


# Module level on purpose: FastAPI resolves the string annotation of a body parameter through module
# globals; a model defined inside create_app is not found and the body is misread as a query parameter.
class Turn(BaseModel):
    cid: str
    beat: int
    upto_p: int | None = None
    visit: str | None = None
    history: list[dict] = []
    text: str


class Move(BaseModel):
    slot: str = "default"
    move: dict = {}


class Say(BaseModel):
    slot: str = "default"
    cid: str
    text: str
    upto_p: int | None = None


def create_app(book: Path, llm_factory):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    w = World(book)
    chapters = load_chapters(book)
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app):
        state["llm"] = llm_factory()
        yield
        await state["llm"].aclose()

    app = FastAPI(lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    g = w.game
    hero = g["protagonist"]["id"]
    hero_view = g["settings"]["viewpoint"] == "hero"
    playable = E.playable(g)
    planned = load_plan(book)["items"]
    try:
        design = load_design(book)
    except FileNotFoundError:
        design = None
    names = {c: v["name"] for c, v in g["characters"].items()}
    stats_by = {s["id"]: s for s in (design or {}).get("stats", [])}
    saves = book / "build" / "saves"

    def art_items():
        # images may still be rendering while the server runs; pick up new files as they appear
        return [i for i in planned if (art_dir(book) / f"{i['id']}.png").exists()]

    def slot_path(slot: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", slot):
            raise HTTPException(400, "bad slot name")
        return saves / f"{slot}.json"

    def load_state(slot: str) -> dict:
        p = slot_path(slot)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        st = E.new_state(design, g)
        save_state(slot, st)
        return st

    def save_state(slot: str, st: dict) -> None:
        saves.mkdir(parents=True, exist_ok=True)
        slot_path(slot).write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")

    def need_design():
        if design is None:
            raise HTTPException(409, "no design yet: run `n2g design --slug ...` first")

    # ------------------------------------------------------------ view

    def cond_text(c: dict) -> str:
        if "stat" in c:
            s = stats_by.get(c["stat"], {})
            return f"{s.get('name', c['stat'])} {c['op']} {c['value']:g}{s.get('unit', '')}"
        if "rel" in c:
            return f"{names.get(c['rel'], c['rel'])}的好感 {c['op']} {c['value']:g}"
        if "intel" in c:
            i = next((x for x in design.get("intel", []) if x["id"] == c["intel"]), {})
            return f"打听到：{i.get('topic', c['intel'])}"
        return "之前的某个选择"

    def page_list(b: dict, lo: int, hi: int, items: list[dict]) -> list[dict]:
        paras = chapters[b["chapter"]]["paragraphs"]
        speaker = {}
        for x in b["lines"]:
            if x["kind"] in ("said", "telepathy", "written", "thought") and x["name"]:
                speaker.setdefault(x["p"], x["name"])
        out = []
        for p in range(lo, min(hi, len(paras) - 1) + 1):
            s = speaker.get(p, "")
            cid = w.by_name.get(s) if s else None
            out.append({"p": p, "text": paras[p], "speaker": s, "cid": cid if cid in g["characters"] else None,
                        "art": portrait_for(items, cid, b["stage"]) if cid in g["characters"] else None})
        return out

    def view(st: dict) -> dict:
        b = g["beats"][st["beat"]]
        items = art_items()
        ids = {i["id"] for i in items}
        ch = design["choices"].get(str(b["i"])) if design else None
        pending = ch and str(b["i"]) not in st["chosen"]
        lo = st["p0"] if st["p0"] is not None else b["from"]
        hi = ch["at_p"] - 1 if pending else b["to"]
        pages = page_list(b, lo, hi, items) if st["phase"] == "read" else []
        if st["phase"] == "narrate":
            pages = [{"p": None, "text": t, "speaker": "", "cid": None, "art": None, "narration": True} for t in st["narration"]]
        at = located(w, b["i"])

        def person(c):
            por = portrait_for(items, c, b["stage"])
            return {"id": c, "name": names.get(c, c), "portrait": por,
                    "expressions": {m: f"{por}_{k}" for m, k in MOOD_KEYS.items() if por and f"{por}_{k}" in ids},
                    "rel": st["rel"].get(c)}
        v = {"phase": st["phase"], "turn": st["turn"],
             "beat": {"i": b["i"], "chapter": b["chapter"], "heading": b["heading"], "node": b["node"], "place": b["place"],
                      "time": b["time"], "stage": b["stage"], "stage_name": (g["stages"][b["stage"]] or {}).get("name", "")
                      if b["stage"] < len(g["stages"]) else "", "anchor": bool(b["anchor"]), "kind": b["kind"],
                      "backdrop": next((x["id"] for x in items if x["kind"] == "backdrop" and x.get("node") == b["node"]), None),
                      "event_art": f"event_b{b['i']:04d}" if f"event_b{b['i']:04d}" in ids else None},
             "pages": pages,
             "cast": [person(c) for c in b["present"] if c in g["characters"]],
             "talk": [person(c) | {"left": E.TALK_TURNS - st["talks"].get(f"{c}@{b['i']}", 0)} for c in b["talk"]]
             if st["phase"] in ("read", "choice") else [],
             "hud": {"stats": [{**{k: s[k] for k in ("id", "name", "unit", "min", "max")}, "value": st["stats"][s["id"]],
                                "danger": bool(s.get("fail"))} for s in design["stats"]],
                     "goal": {**design["goal"], "value": st["stats"][design["goal"]["stat"]]},
                     "intel": [{"topic": i["topic"], "answer": i["answer"], "who": names.get(i["cid"])}
                               for i in design.get("intel", []) if i["id"] in st["intel"]],
                     "clues": [{"topic": i["topic"], "who": names.get(i["cid"])} for i in design.get("intel", [])
                               if i["id"] not in st["intel"] and i["from_beat"] <= b["i"] < i["unlock"]],
                     "relations": [person(c) for c in sorted({c for x in st["log"] for c in x["witnesses"]} | set(b["present"]))
                                   if c in st["rel"] and c in g["characters"]],
                     "log": [{"beat": x["beat"], "text": x["text"], "canon": x["canon"]} for x in st["log"]]},
             "map": {"nodes": [n for n in g["map"]["nodes"] if n["first_beat"] is not None and n["first_beat"] <= b["i"]],
                     "moves": g["map"]["moves"], "here": b["node"],
                     "people": {node: [person(c) for c, n in at.items() if n == node] for node in set(at.values())}},
             "progress": {"k": playable.index(b["i"]), "n": len(playable)}}
        if st["phase"] == "choice":
            v["choice"] = {"prompt": ch["prompt"], "options": [
                {**ov, "needs_text": [cond_text(c) for c in ov["needs"]] + ([cond_text({"intel": ov["needs_intel"]})] if ov["needs_intel"] else [])}
                for ov in (E.option_view(design, st, o) for o in ch["options"])]}
        if st["phase"] == "free":
            v["free"] = {"slots": st["slots"], "visiting": st.get("visiting"),
                         "actions": [{**{k: a[k] for k in ("id", "name", "desc", "effects")}, "ok": E.all_ok(a["needs"], st),
                                      "needs_text": [cond_text(c) for c in a["needs"]]} for a in E.offered(design, g, st)],
                         "visit": [person(c) | {"node": n} for c, n in at.items()]}
        if st["phase"] == "end":
            v["ending"] = st["ending"]
        if st.get("visiting"):
            v["chat"] = {"cid": st["visiting"]["cid"], "left": E.TALK_TURNS - st["visiting"]["turns"]}
        return v

    # ------------------------------------------------------------ routes

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/looks")
    def looks_page():
        return FileResponse(STATIC / "looks.html")

    @app.get("/api/looks")
    def looks():
        return load_looks(book)

    @app.get("/api/art/{name}")
    def art(name: str):
        if not name.replace("_", "").replace("-", "").isalnum():
            raise HTTPException(404)
        p = art_dir(book) / f"{name}.png"
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p)

    @app.get("/api/meta")
    def meta():
        return {"slug": w.meta.get("slug"), "title": w.meta.get("title") or Path(w.meta.get("source") or "").stem or w.meta.get("slug"),
                "chapter_of": [b["chapter"] for b in g["beats"]], "protagonist": g["protagonist"],
                "viewpoint": g["settings"]["viewpoint"], "stages": g["stages"], "beats": len(g["beats"]),
                "playable": playable, "characters": g["characters"], "beat_nodes": [b["node"] for b in g["beats"]],
                "map": g["map"], "anchors": [{"beat": a["beat"], "chapter": a["chapter"]} for a in g["anchors"]],
                "has_design": design is not None, "has_art": bool(art_items())}

    @app.get("/api/game")
    def game_get(slot: str = "default"):
        need_design()
        st = load_state(slot)
        return {"view": view(st), "chats": st.get("chats", {})}

    @app.post("/api/game/new")
    def game_new(m: Move):
        need_design()
        st = E.new_state(design, g)
        save_state(m.slot, st)
        return {"view": view(st), "events": [], "chats": {}}

    @app.post("/api/game/act")
    def game_act(m: Move):
        need_design()
        st = load_state(m.slot)
        try:
            st, events = E.act(design, g, st, m.move)
        except ValueError as e:
            raise HTTPException(400, str(e))
        save_state(m.slot, st)
        return {"view": view(st), "events": events}

    @app.post("/api/game/talk")
    async def game_talk(s: Say):
        need_design()
        st = load_state(s.slot)
        why = E.can_talk(g, st, s.cid)
        if why:
            raise HTTPException(400, why)
        visit = st["visiting"]["node"] if st["phase"] == "free" else None
        key = f"{s.cid}@{st['beat']}" + (f"@{visit}" if visit else "")
        chats = st.setdefault("chats", {})
        hist = chats.get(key, [])
        play = {"rel": st["rel"].get(s.cid, 0), "deeds": E.deeds_known_by(st, s.cid, names),
                "intel": [{k: i[k] for k in ("id", "topic", "answer")} for i in E.available_intel(design, st, s.cid)]}
        r = await reply(w, state["llm"], s.cid, st["beat"], None if visit else s.upto_p, hist, s.text[:500], visit=visit, play=play)
        st, events = E.apply_talk(design, g, st, s.cid, r["rel"], r["told"])
        st.setdefault("chats", {})[key] = hist + [{"role": "player", "text": s.text[:500]},
                                                  {"role": "npc", "text": r["say"], "act": r["act"], "mood": r["mood"]}]
        save_state(s.slot, st)
        return {"reply": r, "events": events, "key": key, "view": view(st)}

    # canon-replay endpoints, kept for tools that read one beat without a game state
    @app.get("/api/beat/{i}")
    def beat(i: int):
        if not 0 <= i < len(g["beats"]):
            raise HTTPException(404)
        b = g["beats"][i]
        if hero_view and b["kind"] == "offscreen":
            raise HTTPException(403, "the protagonist is not in this scene")
        return {"i": i, "chapter": b["chapter"], "node": b["node"], "stage": b["stage"],
                "pages": page_list(b, b["from"], b["to"], art_items()), "talk": [{"id": c, "name": names.get(c)} for c in b["talk"]]}

    @app.post("/api/talk")
    async def talk(t: Turn):
        b = g["beats"][t.beat]
        if t.visit:
            if located(w, t.beat).get(t.cid) != t.visit:
                raise HTTPException(400, "that character is not at this place now")
        elif t.cid not in b["talk"]:
            raise HTTPException(400, "that character cannot be talked to in this scene")
        return await reply(w, state["llm"], t.cid, t.beat, t.upto_p, t.history, t.text[:500], visit=t.visit)

    return app
