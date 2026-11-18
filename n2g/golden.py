"""Golden set: hand-verified judgments about one book, rechecked after every rebuild.

Each line of games/<slug>/golden.jsonl is one judgment the agent confirmed against the text. Types:
  speaker  {chapter, quote, speaker}                 who says the spoken line containing `quote`
  same     {a, b, expect, chapter?}                  whether two names are one character
  present  {who, chapter, expect}                    whether `who` is in some scene of the chapter
  knows    {who, chapter, pattern, expect}           whether `who` knows a fact/event matching the regex by then
  card     {who, chapter|stage, must?, must_not?}    regexes the stage card must / must not contain
  tier     {who, expect}                             allowed tiers, e.g. "SA"
  anchor   {chapter, pattern, expect}                whether a major-turning-point beat of the chapter matches
  probe    {who, chapter, ask, must?, must_not?}     one player line to a character; the reply must / must not
                                                     match. Run with `n2g probe` (model calls), not by evaluate()
Every item needs a `note` saying what in the text settles it. Results: pass, fail, vacuous (a negative
check whose subject never appears, so it proves nothing), missing (the build or the name is not there),
known_gap (fails, and the item's `known_gap` field says why it is accepted for now; it turns into pass
by itself once fixed)."""
from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

REQUIRED = {"speaker": ("chapter", "quote", "speaker"), "same": ("a", "b", "expect"),
            "present": ("who", "chapter", "expect"), "knows": ("who", "chapter", "pattern", "expect"),
            "card": ("who",), "tier": ("who", "expect"), "anchor": ("chapter", "pattern", "expect"),
            "probe": ("who", "chapter", "ask")}


def golden_path(book: Path) -> Path:
    return book / "golden.jsonl"


def load_golden(book: Path) -> list[dict]:
    p = golden_path(book)
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def add_golden(book: Path, item: dict) -> dict:
    kind = item.get("type")
    if kind not in REQUIRED:
        raise ValueError(f"type must be one of {sorted(REQUIRED)}")
    missing = [k for k in (*REQUIRED[kind], "note") if k not in item]
    if kind == "card" and not ({"chapter", "stage"} & item.keys() and {"must", "must_not"} & item.keys()):
        missing.append("chapter|stage and must|must_not")
    if missing:
        raise ValueError(f"missing fields: {missing}")
    items = load_golden(book)
    item = {"id": f"g{len(items) + 1:03d}", **item, "added": time.strftime("%Y-%m-%d")}
    with golden_path(book).open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
    return item


class _Book:
    def __init__(self, book: Path):
        self.book = book
        b = book / "build"
        self.chars = {c["id"]: c for c in json.loads((b / "characters.json").read_text(encoding="utf-8"))["characters"]} \
            if (b / "characters.json").exists() else {}
        self._res = self._led = self._recs = None

    @property
    def res(self):
        if self._res is None:
            from .knowledge.ledger import Resolver
            self._res = Resolver(self.book)
        return self._res

    @property
    def led(self):
        if self._led is None:
            from .knowledge.ledger import load_ledger
            self._led = load_ledger(self.book) if (self.book / "build" / "ledger.json").exists() else {}
        return self._led

    def record(self, ch: int) -> dict | None:
        p = self.book / "build" / "extract" / f"{ch:05d}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def ids(self, name: str, chapter: int | None = None) -> set[str]:
        if chapter is not None and self.chars:
            cid = self.res(chapter, name)
            if cid:
                return {cid}
        exact = {i for i, c in self.chars.items() if c["name"] == name}
        return exact or {i for i, c in self.chars.items() if name in c["names"] or name in c.get("aliases", {})}

    def name(self, cid: str) -> str:
        return self.chars.get(cid, {}).get("name", cid)


def _check(bk: _Book, g: dict) -> tuple[str, str]:
    kind = g["type"]
    if not bk.chars:
        return "missing", "characters.json not built"
    if kind == "speaker":
        from .extract.chapter import number_chapter
        from .text.ingest import load_chapters
        rec = bk.record(g["chapter"])
        if not rec or not rec.get("result"):
            return "missing", "chapter not extracted"
        _, quotes = number_chapter(load_chapters(bk.book)[g["chapter"]]["paragraphs"])
        qids = [q for q, (_, t, _) in quotes.items() if g["quote"] in t]
        if not qids:
            return "missing", "quote not found in chapter"
        said = {u[0]: u[1] for u in rec["result"].get("utterances", [])}
        got = said.get(qids[0], "")
        want = bk.ids(g["speaker"], g["chapter"])
        ok = got == g["speaker"] or bool(bk.ids(got, g["chapter"]) & want) if got else False
        return ("pass" if ok else "fail"), f"extracted speaker: {got or '(none)'}"
    if kind == "same":
        ch = g.get("chapter")
        a, b = bk.ids(g["a"], ch), bk.ids(g["b"], ch)
        if not a or not b:
            return "missing", f"not found: {[n for n, s in ((g['a'], a), (g['b'], b)) if not s]}"
        same = bool(a & b)
        return ("pass" if same == g["expect"] else "fail"), \
            f"{g['a']}→{sorted(bk.name(i) for i in a)} {g['b']}→{sorted(bk.name(i) for i in b)}"
    if kind == "tier":
        ids = bk.ids(g["who"])
        if not ids:
            return "missing", "name not found"
        tiers = sorted(bk.chars[i]["tier"] for i in ids)
        return ("pass" if any(t in g["expect"] for t in tiers) else "fail"), f"tiers: {tiers}"
    if kind == "anchor":
        from .game.build import load_game
        try:
            gm = load_game(bk.book)
        except FileNotFoundError:
            return "missing", "game.json not built"
        pat = re.compile(g["pattern"])
        mine = [a for a in gm["anchors"] if a["chapter"] == g["chapter"]]
        hits = [a["summary"] for a in mine if pat.search(a["summary"])]
        if not g["expect"] and not hits and not any(pat.search(a["summary"]) for a in gm["anchors"]):
            return "vacuous", "no anchor in the game matches the pattern"
        return ("pass" if bool(hits) == g["expect"] else "fail"), \
            f"anchors in ch{g['chapter']}: {len(mine)}; matching: {hits[:2] or 'none'}"
    if kind == "probe":
        return "missing", "probes need a model: run `n2g probe`"
    ids = bk.ids(g["who"], g.get("chapter"))
    if not ids:
        return "missing", "name not found"
    if kind == "present":
        if not bk.led:
            return "missing", "ledger not built"
        scenes = [s for s in bk.led["scenes"] if s["chapter"] == g["chapter"]]
        hit = [s["id"] for s in scenes if ids & set(s["present"])]
        return ("pass" if bool(hit) == g["expect"] else "fail"), f"scenes with them: {hit[:5]} of {len(scenes)}"
    if kind == "knows":
        from .knowledge.ledger import known_at
        if not bk.led:
            return "missing", "ledger not built"
        pat = re.compile(g["pattern"])
        k = {"facts": [], "events": []}
        for cid in ids:
            got = known_at(bk.led, cid, g["chapter"])
            k["facts"] += got["facts"]
            k["events"] += got["events"]
        hits = [f"ch{f['chapter']} {f['text']}" for f in k["facts"] if pat.search(f["text"])] + \
               [f"ch{e['chapter']} {e['summary']}" for e in k["events"] if pat.search(e["summary"])]
        if not g["expect"] and not hits:
            anywhere = any(pat.search(f["text"]) for f in bk.led["facts"] if f["chapter"] <= g["chapter"]) or \
                any(pat.search(e["summary"]) for e in bk.led["events"] if e["chapter"] <= g["chapter"])
            if not anywhere:
                return "vacuous", "nothing in the ledger matches the pattern yet"
        return ("pass" if bool(hits) == g["expect"] else "fail"), "; ".join(hits[:3]) or "no matching knowledge"
    if kind == "card":
        cid = next(iter(sorted(ids, key=lambda i: "SABCG".find(bk.chars[i]["tier"]))))
        p = bk.book / "build" / "cards" / f"{cid}.json"
        if not p.exists():
            return "missing", "no cards for this character"
        stages = json.loads(p.read_text(encoding="utf-8"))["stages"]
        st = next((s for s in stages if (s["stage"] == g["stage"] if "stage" in g else s["from"] <= g["chapter"] <= s["to"])), None)
        if not st or not st["card"]:
            return "missing", "no card for that stage"
        blob = json.dumps(st["card"], ensure_ascii=False)
        lacking = [r for r in g.get("must", []) if not re.search(r, blob)]
        found = [m.group(0) for r in g.get("must_not", []) for m in [re.search(r, blob)] if m]
        return ("pass" if not lacking and not found else "fail"), \
            f"stage {st['stage']} {st['name']}" + (f"; lacks {lacking}" if lacking else "") + (f"; contains {found}" if found else "")
    return "missing", f"unknown type {kind}"


def evaluate(book: Path, kind: str | None = None) -> dict:
    items = [g for g in load_golden(book) if kind is None or g["type"] == kind]
    bk = _Book(book)
    out, by_type = [], defaultdict(Counter)
    for g in items:
        try:
            status, detail = _check(bk, g)
        except Exception as e:  # a broken item should not hide the others
            status, detail = "missing", f"{type(e).__name__}: {e}"
        if status == "fail" and g.get("known_gap"):
            status, detail = "known_gap", f"{detail} | {g['known_gap']}"
        by_type[g["type"]][status] += 1
        out.append({"id": g["id"], "type": g["type"], "status": status, "detail": detail, "note": g.get("note", "")})
    total = Counter(r["status"] for r in out)
    checked = total["pass"] + total["fail"]
    return {"items": len(out), **dict(total), "accuracy": round(total["pass"] / checked, 4) if checked else None,
            "by_type": {k: dict(v) for k, v in by_type.items()},
            "not_passing": [r for r in out if r["status"] != "pass"]}
