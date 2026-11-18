"""Offline playthroughs of a design: no model calls, the same engine the page uses.

Policies:
  canon    always the book's option; free time spent on the action that best serves the goal; never asks
  planner  takes the best available option, keeps failing stats away from their minimum, visits whoever
           has intel it can still use and assumes asking reveals it
  random   uniform over legal moves (asking reveals intel half the time), many seeds

The protagonist lives through the slice in the book, so the canon run must reach its last beat (a design
whose stats kill the canon protagonist has numbers that do not match the story). The report also says
whether a win and a loss are both reachable, how often random play wins, whether any run
got stuck, and how much intel the planner could actually collect. `n2g gate design` uses it."""
from __future__ import annotations

import json
import random
from pathlib import Path

from .build import load_game
from .design import check_design, load_design
from .engine import act, apply_talk, available_intel, legal, new_state, option_view

MAX_STEPS = 20000


def _score(design: dict, st: dict, effects: dict, goal_w: float = 2.0, safe: float = 0.5) -> float:
    s = 0.0
    goal = design["goal"]["stat"]
    for x in design["stats"]:
        d = effects.get(x["id"], 0)
        if d == "-all":
            d = x["min"] - st["stats"][x["id"]]
        span = x["max"] - x["min"] or 1
        w = goal_w if x["id"] == goal else 0.3
        if x.get("fail") and (st["stats"][x["id"]] - x["min"]) / span < safe:
            w = 1.5 * goal_w
        s += w * d / span
    return s


def _canon(design, game, st, moves, rng):
    if st["phase"] == "choice":
        c = design["choices"][str(st["beat"])]
        return {"type": "choose", "option": next(o["id"] for o in c["options"] if o["canon"])}
    return _best_free(design, st, moves) if st["phase"] == "free" else moves[0]


def _best_free(design, st, moves, goal_w=2.0, safe=0.5):
    acts = {a["id"]: a for a in design["actions"]}
    dos = [m for m in moves if m["type"] == "do"]
    if not dos:
        return {"type": "leave"}
    return max(dos, key=lambda m: _score(design, st, acts[m["action"]]["effects"], goal_w, safe))


def _planner(design, game, st, moves, rng, goal_w=2.0, safe=0.5):
    if st["phase"] == "choice":
        c = design["choices"][str(st["beat"])]
        opts = {o["id"]: o for o in c["options"]}
        ok = [m for m in moves if not opts[m["option"]].get("ending")] or moves
        return max(ok, key=lambda m: _score(design, st, opts[m["option"]]["effects"], goal_w, safe) + 0.2 * (not opts[m["option"]]["canon"]))
    if st["phase"] == "free":
        for m in moves:
            if m["type"] == "visit" and available_intel(design, st, m["cid"]) and not st.get("visiting"):
                return m
        return _best_free(design, st, moves, goal_w, safe)
    return moves[0]


def _random(design, game, st, moves, rng):
    if st["phase"] == "free" and len(moves) > 1 and st["slots"] > 0:
        moves = [m for m in moves if m["type"] != "leave"]
    return rng.choice(moves)


# the planner is tried with several weightings and the best run is reported
PLANNER_VARIANTS = [(g, s) for g in (1.0, 2.0, 4.0) for s in (0.25, 0.4, 0.55)]
POLICIES = {"canon": (_canon, 0.0), "random": (_random, 0.5),
            **{f"planner{k}": ((lambda g, s: lambda *a: _planner(*a, goal_w=g, safe=s))(g, s), 1.0)
               for k, (g, s) in enumerate(PLANNER_VARIANTS)}}


def playthrough(design: dict, game: dict, policy: str, seed: int = 0) -> dict:
    pick, ask = POLICIES[policy]
    rng = random.Random(seed)
    st = new_state(design, game)
    steps, low, clipped = 0, {s["id"]: s["start"] for s in design["stats"]}, []
    while st["phase"] != "end":
        b = game["beats"][st["beat"]]
        who = [st["visiting"]["cid"]] if st["phase"] == "free" and st.get("visiting") else b["talk"] if st["phase"] == "read" else []
        for cid in who:
            for i in available_intel(design, st, cid):
                if rng.random() < ask:
                    st, _ = apply_talk(design, game, st, cid, 1, [i["id"]])
        moves = legal(design, game, st)
        if not moves:
            return {"policy": policy, "seed": seed, "stuck": f"no legal move in phase {st['phase']} at beat {st['beat']}"}
        mv = pick(design, game, st, moves, rng)
        st, ev = act(design, game, st, mv)
        if mv["type"] == "choose":
            clipped += [f"beat {st['beat']} {e['name']} {e['clipped']:g}" for e in ev if e["type"] == "stat" and e["clipped"] < 0]
        for k, v in st["stats"].items():
            low[k] = min(low[k], v)
        steps += 1
        if steps > MAX_STEPS:
            return {"policy": policy, "seed": seed, "stuck": f"more than {MAX_STEPS} moves"}
    e = st["ending"]
    last = [b for b in game["beats"] if b["kind"] != "offscreen"][-1]["i"]
    return {"policy": policy, "seed": seed, "ending": e["id"], "kind": e["kind"], "beat": st["beat"],
            "early": st["beat"] != last or st["phase"] != "end" or e["id"].startswith("fail_"), "stats": st["stats"],
            "lowest": low, "clipped": clipped, "intel": len(st["intel"]), "deviations": sum(not x["canon"] for x in st["log"]), "moves": steps}


def simulate(book: Path, runs: int = 60) -> dict:
    design, game = load_design(book), load_game(book)
    return sim_report(book, design, game, runs)


def _rank(r: dict) -> tuple:
    return ({"win": 2, "neutral": 1}.get(r.get("kind"), 0), r.get("intel", 0))


def sim_report(book: Path | None, design: dict, game: dict, runs: int = 60) -> dict:
    problems = check_design(design, game)
    out = [playthrough(design, game, "canon")]
    plans = [playthrough(design, game, f"planner{k}") for k in range(len(PLANNER_VARIANTS))]
    out += [max((r for r in plans if "stuck" not in r), key=_rank, default=plans[0]) | {"policy": "planner"}]
    out += [r for r in plans if "stuck" in r]
    out += [playthrough(design, game, "random", s) for s in range(runs)]
    stuck = [r for r in out if "stuck" in r]
    done = [r for r in out if "stuck" not in r]
    rnd = [r for r in done if r["policy"] == "random"]
    kinds = lambda rs: {k: sum(r["kind"] == k for r in rs) for k in ("win", "lose", "neutral")}
    ending_counts: dict[str, int] = {}
    for r in done:
        ending_counts[r["ending"]] = ending_counts.get(r["ending"], 0) + 1
    planner = next((r for r in done if r["policy"] == "planner"), {})
    canon = next((r for r in done if r["policy"] == "canon"), {})
    n_intel = len(design.get("intel", []))
    anchors = {a["beat"] for a in game["anchors"]}
    rep = {"design_problems": len(problems), "problems": problems[:10],
           "choice_coverage": round(len(anchors & {int(k) for k in design.get("choices", {})}) / max(1, len(anchors)), 3),
           "deadlocks": len(stuck), "stuck": [r["stuck"] for r in stuck[:3]],
           "canon_survives": int(bool(canon) and not canon.get("early")),
           "canon_clipped": len(canon.get("clipped", [])),
           "win_reachable": int(any(r["kind"] == "win" for r in done)),
           "lose_reachable": int(any(r["kind"] == "lose" for r in done)),
           "random_win_share": round(kinds(rnd)["win"] / max(1, len(rnd)), 3),
           "random_lose_share": round(kinds(rnd)["lose"] / max(1, len(rnd)), 3),
           "intel_obtained_share": round(planner.get("intel", 0) / max(1, n_intel), 3),
           "canon": {k: canon.get(k) for k in ("ending", "kind", "beat", "stats", "lowest", "clipped")},
           "planner": {k: planner.get(k) for k in ("ending", "kind", "stats", "lowest", "intel", "deviations")},
           "endings_seen": ending_counts, "runs": len(out)}
    if book is not None:
        (book / "build" / "sim_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return rep
