"""The game engine: a state machine over the beats of game.json, driven by design.json.

Pure functions on plain dicts, so the server, the simulator and tests all run the same rules.

  read     the player reads a beat; at an anchor the pages stop before the choice paragraph
  choice   the anchor's options; options whose conditions fail are shown locked. The canon option
           continues the original text; another option applies its effects and shows what happens
           and how the story still reaches the next scene (soft regression), skipping the rest of
           the original scene
  narrate  those result/bridge pages
  free     after the last beat of each chapter: `slots` actions — a design action, or visiting a
           character where the map places them (then talking is allowed); upkeep is applied first
  end      an ending: a stat with `fail` hit its minimum, an option ended the game, or the slice is over
           and the first ending whose conditions hold is chosen

Talking never moves the story; it changes relations and can reveal intel (see apply_talk)."""
from __future__ import annotations

import copy

TALK_TURNS = 6          # player lines per character per beat (and per visit)
REL_STEP = 3            # largest relation change from one line


def playable(game: dict) -> list[int]:
    return [b["i"] for b in game["beats"] if b["kind"] != "offscreen"]


def located(game: dict, beat_i: int) -> dict[str, str]:
    """Map node where each talkable character was last seen at or before this beat, within the current
    stage: someone seen once long ago should not stand there for the rest of the game."""
    out = {}
    beats = game["beats"]
    stage = beats[beat_i]["stage"]
    hero = game["protagonist"]["id"]
    for cid, trail in game["trail"].items():
        if cid == hero or cid not in game["characters"]:
            continue
        last = None
        for i, node in trail:
            if i > beat_i:
                break
            last = node if beats[i]["stage"] == stage else None
        if last:
            out[cid] = last
    return out


def free_after(game: dict, beat_i: int) -> bool:
    """Free time follows the last playable beat of a chapter, unless it is the last beat of all."""
    ps = playable(game)
    k = ps.index(beat_i)
    return k + 1 < len(ps) and game["beats"][ps[k + 1]]["chapter"] != game["beats"][beat_i]["chapter"]


def askable_from(game: dict, cid: str, beat_i: int) -> int | None:
    """First beat at or after `beat_i` where the player can talk to `cid`: in the scene, or by visiting
    them in the free time after it."""
    for i in playable(game):
        if i < beat_i:
            continue
        if cid in game["beats"][i]["talk"] or (free_after(game, i) and cid in located(game, i)):
            return i
    return None


def _stats(design: dict) -> dict:
    return {s["id"]: s for s in design["stats"]}


def new_state(design: dict, game: dict) -> dict:
    first = playable(game)[0]
    st = {"v": 1, "beat": first, "phase": "read", "p0": None, "stats": {s["id"]: s["start"] for s in design["stats"]},
          "rel": {c: design.get("relations", {}).get(c, 0) for c in game["characters"] if c != game["protagonist"]["id"]},
          "flags": [], "intel": [], "chosen": {}, "log": [], "slots": 0, "visiting": None, "talks": {},
          "narration": [], "ending": None, "turn": 0}
    _enter(design, game, st, first)
    return st


def check(cond: dict, st: dict) -> bool:
    if "stat" in cond or "rel" in cond:
        v = st["stats"].get(cond["stat"], 0) if "stat" in cond else st["rel"].get(cond["rel"], 0)
        t = cond["value"]
        return {">=": v >= t, "<=": v <= t, ">": v > t, "<": v < t, "==": v == t}[cond["op"]]
    if "flag" in cond:
        return cond["flag"] in st["flags"]
    if "not_flag" in cond:
        return cond["not_flag"] not in st["flags"]
    if "intel" in cond:
        return cond["intel"] in st["intel"]
    return False


def all_ok(conds: list[dict], st: dict) -> bool:
    return all(check(c, st) for c in conds or [])


def _apply(design: dict, st: dict, effects: dict, events: list) -> None:
    stats = _stats(design)
    for k, d in (effects or {}).items():
        s = stats[k]
        old = st["stats"][k]
        if d == "-all":
            d = s["min"] - old
        new = max(s["min"], min(s["max"], old + d))
        st["stats"][k] = new
        if new != old or d:
            # clipped: how much of the change did not fit (negative: the stat could not pay for it)
            events.append({"type": "stat", "id": k, "name": s["name"], "delta": new - old, "value": new,
                           "clipped": old + d - new})
    for s in design["stats"]:
        if s.get("fail") and st["stats"][s["id"]] <= s["min"] and st["phase"] != "end":
            _end(st, {"id": f"fail_{s['id']}", "kind": "lose", **s["fail"]}, events)
            return


def _rel(st: dict, deltas: dict, events: list, names: dict) -> None:
    for c, d in (deltas or {}).items():
        if c not in st["rel"] or not d:
            continue
        old = st["rel"][c]
        st["rel"][c] = max(-100, min(100, old + d))
        if st["rel"][c] != old:
            events.append({"type": "rel", "id": c, "name": names.get(c, c), "delta": st["rel"][c] - old, "value": st["rel"][c]})


def _end(st: dict, ending: dict, events: list) -> None:
    st["phase"] = "end"
    st["ending"] = ending
    events.append({"type": "end", "ending": ending})


def _final(design: dict, st: dict, events: list) -> None:
    e = next((e for e in design["endings"] if all_ok(e["when"], st)), design["endings"][-1])
    _end(st, e, events)


def _choice(design: dict, beat_i: int) -> dict | None:
    return design.get("choices", {}).get(str(beat_i))


def _enter(design: dict, game: dict, st: dict, beat_i: int) -> None:
    st.update(beat=beat_i, phase="read", p0=None, narration=[], visiting=None)


def _advance(design: dict, game: dict, st: dict, events: list) -> None:
    ps = playable(game)
    k = ps.index(st["beat"])
    if k + 1 >= len(ps):
        _final(design, st, events)
        return
    if st["phase"] != "free" and free_after(game, st["beat"]):
        st.update(phase="free", slots=design.get("slots", 2), visiting=None, narration=[])
        events.append({"type": "free", "slots": st["slots"]})
        _apply(design, st, design.get("upkeep"), events)
        return
    _enter(design, game, st, ps[k + 1])
    events.append({"type": "beat", "beat": ps[k + 1]})


def offered(design: dict, game: dict, st: dict) -> list[dict]:
    """Free-time actions open at this point of the story."""
    ch = game["beats"][st["beat"]]["chapter"]
    return [a for a in design["actions"] if a.get("from_chapter", 0) <= ch]


def option_view(design: dict, st: dict, op: dict) -> dict:
    locked = []
    if not op["canon"]:
        if op.get("needs") and not all_ok(op["needs"], st):
            locked.append("needs")
        if op.get("needs_intel") and op["needs_intel"] not in st["intel"]:
            locked.append("intel")
    return {**{k: op[k] for k in ("id", "text", "canon")}, "locked": locked, "needs": op.get("needs", []),
            "needs_intel": op.get("needs_intel", ""), "effects": op["effects"]}


def legal(design: dict, game: dict, st: dict) -> list[dict]:
    """Every move the player can make now (the simulator picks from these)."""
    ph = st["phase"]
    if ph == "end":
        return []
    if ph in ("read", "narrate"):
        return [{"type": "next"}]
    if ph == "choice":
        c = _choice(design, st["beat"])
        return [{"type": "choose", "option": o["id"]} for o in c["options"] if not option_view(design, st, o)["locked"]]
    moves = [{"type": "leave"}]
    if st["slots"] > 0:
        moves += [{"type": "do", "action": a["id"]} for a in offered(design, game, st) if all_ok(a["needs"], st)]
        moves += [{"type": "visit", "cid": c} for c in located(game, st["beat"])]
    return moves


def act(design: dict, game: dict, st: dict, move: dict) -> tuple[dict, list[dict]]:
    """Apply one move to a copy of the state; returns (new state, events). Raises ValueError on an
    illegal move."""
    st = copy.deepcopy(st)
    events: list[dict] = []
    names = {c: v["name"] for c, v in game["characters"].items()}
    t = move.get("type")
    ph = st["phase"]
    st["turn"] += 1
    if ph == "end":
        raise ValueError("the game is over")
    if t == "next" and ph in ("read", "narrate"):
        c = _choice(design, st["beat"])
        if ph == "read" and c and str(st["beat"]) not in st["chosen"]:
            st["phase"] = "choice"
            events.append({"type": "choice", "beat": st["beat"]})
        else:
            _advance(design, game, st, events)
    elif t == "choose" and ph == "choice":
        c = _choice(design, st["beat"])
        op = next((o for o in c["options"] if o["id"] == move.get("option")), None)
        if not op or option_view(design, st, op)["locked"]:
            raise ValueError("that option is not available")
        st["chosen"][str(st["beat"])] = op["id"]
        st["flags"] += [f for f in op["flags"] if f not in st["flags"]]
        present = game["beats"][st["beat"]]["present"]
        st["log"].append({"beat": st["beat"], "option": op["id"], "canon": op["canon"], "text": op["text"],
                          "result": op.get("result", ""), "witnesses": present})
        _rel(st, op["rel"], events, names)
        _apply(design, st, op["effects"], events)
        if st["phase"] == "end":
            pass
        elif op.get("ending"):
            _end(st, next(e for e in design["endings"] if e["id"] == op["ending"]), events)
        elif op["canon"]:
            st.update(phase="read", p0=c["at_p"])
        else:
            st.update(phase="narrate", narration=[x for x in (op["result"], op["bridge"]) if x])
    elif ph == "free" and t == "do":
        a = next((a for a in offered(design, game, st) if a["id"] == move.get("action")), None)
        if not a or st["slots"] <= 0 or not all_ok(a["needs"], st):
            raise ValueError("that action is not available")
        st["slots"] -= 1
        st["visiting"] = None
        events.append({"type": "did", "action": a["id"], "name": a["name"]})
        _apply(design, st, a["effects"], events)
    elif ph == "free" and t == "visit":
        at = located(game, st["beat"])
        if st["slots"] <= 0 or move.get("cid") not in at:
            raise ValueError("that character cannot be visited now")
        st["slots"] -= 1
        st["visiting"] = {"cid": move["cid"], "node": at[move["cid"]], "turns": 0}
        events.append({"type": "visit", "cid": move["cid"], "node": at[move["cid"]], "name": names.get(move["cid"])})
    elif ph == "free" and t == "leave":
        _advance(design, game, st, events)
    else:
        raise ValueError(f"move {t!r} is not allowed in phase {ph!r}")
    return st, events


def can_talk(game: dict, st: dict, cid: str) -> str | None:
    """None if the player may say one more line to `cid` now, else why not."""
    if st["phase"] == "free":
        v = st.get("visiting")
        if not v or v["cid"] != cid:
            return "visit them on the map first (it takes a free-time slot)"
        return None if v["turns"] < TALK_TURNS else "this visit is over"
    if st["phase"] not in ("read", "choice") or cid not in game["beats"][st["beat"]]["talk"]:
        return "they are not in this scene"
    return None if st["talks"].get(f"{cid}@{st['beat']}", 0) < TALK_TURNS else "you have talked enough for now"


def available_intel(design: dict, st: dict, cid: str) -> list[dict]:
    return [i for i in design.get("intel", []) if i["cid"] == cid and i["id"] not in st["intel"]
            and i["from_beat"] <= st["beat"] < i["unlock"]]


def apply_talk(design: dict, game: dict, st: dict, cid: str, rel: int, told: list[str]) -> tuple[dict, list[dict]]:
    st = copy.deepcopy(st)
    events: list[dict] = []
    if st["phase"] == "free" and st.get("visiting"):
        st["visiting"]["turns"] += 1
    else:
        k = f"{cid}@{st['beat']}"
        st["talks"][k] = st["talks"].get(k, 0) + 1
    _rel(st, {cid: max(-REL_STEP, min(REL_STEP, int(rel or 0)))}, events, {c: v["name"] for c, v in game["characters"].items()})
    ok = {i["id"]: i for i in available_intel(design, st, cid)}
    for t in told or []:
        if t in ok:
            st["intel"].append(t)
            events.append({"type": "intel", "id": t, "topic": ok[t]["topic"], "answer": ok[t]["answer"]})
    return st, events


def deeds_known_by(st: dict, cid: str, names: dict) -> list[str]:
    """What the protagonist did differently from the book, as far as `cid` saw it."""
    return [f"{x['text']}：{x['result']}" for x in st["log"] if not x["canon"] and cid in x["witnesses"]][-5:]
