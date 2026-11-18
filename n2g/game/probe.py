"""Stage ⑧: probes — playing against the built game to check it holds up.

A probe is a golden item {"type": "probe", "who", "chapter", "ask", "must"?, "must_not"?}: one thing the
player says to one character at one point of the story. The reply (words + action) must match every `must`
regex and no `must_not` regex. Two families:

  spoiler   the player asks about something that has not happened yet; a passing reply does not confirm,
            predict, or use names/facts from later chapters (validator flags count as failures too)
  memory    the player asks about something the character knows by then; a passing reply contains it

`n2g probe` runs every probe item (model calls, budget-capped) and writes build/probe_report.json, which
`n2g gate accept` reads. Running a subset (`--only g003,g005`) keeps earlier results for the rest."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from ..golden import load_golden
from .npc import World, beat_for, find_character, reply


def probe_report(book: Path) -> dict:
    p = book / "build" / "probe_report.json"
    if not p.exists():
        raise FileNotFoundError(str(p))
    return json.loads(p.read_text(encoding="utf-8"))


async def run_probes(book: Path, llm, *, only: list[str] | None = None, log=print) -> dict:
    w = World(book)
    items = [g for g in load_golden(book) if g["type"] == "probe"]
    if only:
        items = [g for g in items if g["id"] in only]
    results = []
    for g in items:
        cid = find_character(w, g["who"])
        beat = beat_for(w, cid, g["chapter"])
        r = await reply(w, llm, cid, beat, None, [], g["ask"])
        text = r["say"] + " " + r["act"]
        lacking = [p for p in g.get("must", []) if not re.search(p, text)]
        leaked = [m.group(0) for p in g.get("must_not", []) for m in [re.search(p, text)] if m]
        bad = {"future_name"} & set(r["flags"])
        passed = not lacking and not leaked and not bad
        results.append({"id": g["id"], "who": g["who"], "chapter": g["chapter"], "ask": g["ask"],
                        "say": r["say"], "act": r["act"], "flags": r["flags"], "passed": passed,
                        "detail": (f"lacks {lacking} " if lacking else "") + (f"says {leaked} " if leaked else "")
                        + (f"flags {sorted(bad)}" if bad else "") or "ok"})
        log(f"[probe] {g['id']} {g['who']} ch{g['chapter']} {'PASS' if passed else 'FAIL'}: {r['say'][:60]}")
    try:
        old = {r["id"]: r for r in probe_report(book)["results"]}
    except FileNotFoundError:
        old = {}
    old.update({r["id"]: r for r in results})
    out = {"ran_at": time.strftime("%Y-%m-%d %H:%M:%S"), "results": list(old.values())}
    (book / "build" / "probe_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ran": len(results), "passed": sum(r["passed"] for r in results),
            "failed": [r["id"] for r in results if not r["passed"]]}
