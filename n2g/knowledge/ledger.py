"""Stage 5: knowledge ledger.

Turns per-chapter scenes, events and facts into book-level records keyed by character id, and answers
"what does character X know at chapter N". Nothing here calls a model: knowledge comes only from the
extraction (who was present, who took part, who the extractor listed as knowing a fact).

Rules:
  - a character knows an event if they took part in it or were present in its scene;
  - a character knows a fact if the extractor listed them as a knower; a knower that is a crowd or an
    unresolved name ("众人"), or a fact marked "public", extends to everyone present in the scene where
    the fact is stated;
  - "thought" knowledge stays with the thinker: no scene expansion for how == "self" or "inferred".
Unverified facts (evidence not found in the text) are kept but flagged, so callers can skip them."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from .. import vocab
from ..consolidate.characters import collect_mentions, use_book_plugins
from ..extract.review import load_records



class Resolver:
    """(chapter, surface) -> character id, using the merged character table."""

    def __init__(self, book: Path):
        chars = json.loads((book / "build" / "characters.json").read_text(encoding="utf-8"))["characters"]
        self.chars = {c["id"]: c for c in chars}
        owner = {}
        for c in chars:
            for ch, name in c["mentions"]:
                owner[(ch, name)] = c["id"]
        use_book_plugins(book)
        from ..plugins import load_plugins
        self._adjust = load_plugins(book).adjust_present()
        rank = {t: k for k, t in enumerate("SABCG")}
        self._by_name: dict[str, str] = {}
        for c in sorted(chars, key=lambda c: (rank.get(c["tier"], 9), -len(c["mentions"]))):
            self._by_name.setdefault(c["name"], c["id"])
        from .rules import load_rules
        self.rules = load_rules(book, self.lookup)
        self.by_chapter: dict[int, dict[str, str]] = defaultdict(dict)
        for m in collect_mentions(book):
            cid = owner.get((m.chapter, m.name))
            if cid:
                for s in m.surfaces:
                    self.by_chapter[m.chapter].setdefault(s, cid)

    def __call__(self, chapter: int, surface: str) -> str | None:
        return self.by_chapter.get(chapter, {}).get(surface)

    def lookup(self, name: str) -> str | None:
        """Book-level name (or id) -> id, preferring the most important character with that name."""
        return name if name in self.chars else self._by_name.get(name)

    def present(self, chapter: int, surfaces: list[str]) -> list[str]:
        """Resolved ids of the people present in a scene, after knowledge_rules.json companions and the
        book's adjust_present plugin."""
        ids = {x for x in (self(chapter, s) for s in surfaces) if x}
        if self.rules is not None:
            ids = self.rules.present(chapter, ids)
        if self._adjust is not None:
            names = [self.name(i) for i in sorted(ids)]
            changed = self._adjust(chapter, names)
            if changed is not None:
                # names are not unique ("大经理" is many people), so keep the ids that were already resolved
                # and only look up names the plugin added
                keep = set(changed)
                had = {self.name(i) for i in ids}
                ids = {i for i in ids if self.name(i) in keep}
                ids |= {self._by_name[n] for n in keep - had if n in self._by_name}
        return sorted(ids)

    def name(self, cid: str) -> str:
        return self.chars[cid]["name"] if cid in self.chars else cid

    def is_group(self, cid: str) -> bool:
        return self.chars.get(cid, {}).get("type") == "group"


def _scene_of(scenes: list[dict], p: int | None) -> dict | None:
    if p is None:
        return None
    for s in scenes:
        if s.get("from", -1) <= p <= s.get("to", -1):
            return s
    return None


def build_ledger(book: Path) -> dict:
    res = Resolver(book)
    scenes_out, events_out, facts_out = [], [], []
    index: dict[str, dict[str, list[str]]] = defaultdict(lambda: {"facts": [], "events": []})
    stats = Counter()
    for rec in load_records(book):
        o = rec.get("result")
        if not o:
            continue
        ch = rec["idx"]
        resolve = lambda s: res(ch, s)
        scenes = o.get("scenes", [])
        present_of: dict[str, list[str]] = {}
        for s in scenes:
            sid = f"{ch}.{s['id']}"
            present = res.present(ch, s.get("present", []))
            present_of[s["id"]] = present
            scenes_out.append({"id": sid, "chapter": ch, "from": s.get("from"), "to": s.get("to"), "place": s.get("place", ""),
                               "time": s.get("time", ""), "present": present, "summary": s.get("summary", "")})
        for e in o.get("events", []):
            eid = f"{ch}.{e['id']}"
            who = sorted({x for x in map(resolve, e.get("who", [])) if x})
            witnesses = sorted(set(who) | set(present_of.get(e.get("scene"), [])))
            events_out.append({"id": eid, "chapter": ch, "scene": f"{ch}.{e.get('scene')}", "p": e.get("p", []),
                               "summary": e.get("summary", ""), "who": who, "witnesses": witnesses,
                               "importance": e.get("importance", 1),
                               "effects": [{"target": resolve(x.get("target", "")) or x.get("target", ""), "change": x.get("change", "")}
                                           for x in e.get("effects", [])]})
            for c in witnesses:
                index[c]["events"].append(eid)
        for f in o.get("facts", []):
            fid = f"{ch}.{f['id']}"
            ps = f.get("p") or []
            scene = _scene_of(scenes, ps[0] if ps else None)
            in_scene = present_of.get(scene["id"], []) if scene else []
            knowers: dict[str, str] = {}
            for k in f.get("knowers", []):
                who, how = k.get("who", ""), k.get("how", "")
                cid = resolve(who)
                crowd = cid is None or res.is_group(cid) or any(w in who for w in vocab.current.crowd_words)
                if cid and not res.is_group(cid):
                    knowers.setdefault(cid, how)
                if (crowd or how == "public") and how not in ("self", "inferred"):
                    for c in in_scene:
                        knowers.setdefault(c, "present")
                if cid is None:
                    stats["unresolved_knower"] += 1
            if res.rules is not None:
                stats["shared_by_rules"] += res.rules.share(ch, knowers)
            facts_out.append({"id": fid, "chapter": ch, "scene": f"{ch}.{scene['id']}" if scene else None,
                              "text": f.get("text", ""), "kind": f.get("kind", ""), "quote": f.get("quote", ""),
                              "about": [resolve(x) or x for x in f.get("about", [])], "secret": bool(f.get("secret")),
                              "knowers": knowers, "unverified": bool(f.get("unverified"))})
            for c in knowers:
                index[c]["facts"].append(fid)
            stats["facts"] += 1
            stats["facts_without_knower"] += not knowers
    ledger = {"scenes": scenes_out, "events": events_out, "facts": facts_out,
              "index": dict(index), "stats": dict(stats)}
    out = book / "build" / "ledger.json"
    out.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    return ledger


def load_ledger(book: Path) -> dict:
    return json.loads((book / "build" / "ledger.json").read_text(encoding="utf-8"))


def known_at(ledger: dict, cid: str, chapter: int, *, include_unverified: bool = False) -> dict:
    """Facts and events character `cid` knows by the end of `chapter`."""
    idx = ledger["index"].get(cid, {"facts": [], "events": []})
    facts = {f["id"]: f for f in ledger["facts"]}
    events = {e["id"]: e for e in ledger["events"]}
    fs = [facts[i] for i in idx["facts"] if facts[i]["chapter"] <= chapter and (include_unverified or not facts[i]["unverified"])]
    es = [events[i] for i in idx["events"] if events[i]["chapter"] <= chapter]
    return {"facts": fs, "events": es}


def ledger_report(book: Path, *, name: str | None = None, chapter: int | None = None, show: int = 12) -> dict:
    """Whole-book numbers, or what one character knows at a chapter and what they do not."""
    led = load_ledger(book)
    chars = {c["id"]: c for c in json.loads((book / "build" / "characters.json").read_text(encoding="utf-8"))["characters"]}
    if name is None:
        main = [c for c in chars.values() if c["tier"] in "SA"]
        # a knower who learned by witnessing or overhearing should be in the scene; listing those who are
        # not is the cheapest check on the extractor's knowers
        scenes = {s["id"]: s for s in led["scenes"]}
        odd = [f for f in led["facts"] if f["scene"] in scenes and any(
            how in ("witnessed", "overheard") and c not in scenes[f["scene"]]["present"] for c, how in f["knowers"].items())]
        secrets = [f for f in led["facts"] if f["secret"]]
        by_char = Counter(chars.get(c, {}).get("name", c) for f in odd for c, how in f["knowers"].items()
                          if how in ("witnessed", "overheard") and c not in scenes[f["scene"]]["present"])
        return {**led["stats"], "events": len(led["events"]), "scenes": len(led["scenes"]),
                "secret_facts": len(secrets),
                "witness_not_present": len(odd),
                "witness_not_present_ratio": round(len(odd) / max(1, len(led["facts"])), 4),
                "witness_not_present_by_character": dict(by_char.most_common(10)),
                "witness_not_present_examples": [f"{f['id']} {f['text']} | 知情={ {chars.get(c, {}).get('name', c): h for c, h in f['knowers'].items()} }" for f in odd[:show]],
                "main_characters": {c["name"]: {"facts": len(led["index"].get(c["id"], {}).get("facts", [])),
                                                "events": len(led["index"].get(c["id"], {}).get("events", []))} for c in main}}
    c = next((c for c in chars.values() if c["name"] == name), None) or next(c for c in chars.values() if name in c["names"])
    ch = chapter if chapter is not None else c["last"]
    k = known_at(led, c["id"], ch)
    nm = lambda i: chars.get(i, {}).get("name", i)
    hidden = [f for f in led["facts"] if f["chapter"] <= ch and f["secret"] and c["id"] not in f["knowers"] and not f["unverified"]]
    about_me_hidden = [f for f in hidden if c["id"] in f["about"]]
    return {"character": c["name"], "id": c["id"], "chapter": ch, "knows_facts": len(k["facts"]), "knows_events": len(k["events"]),
            "latest_facts": [f"ch{f['chapter']} {f['text']}（{f['knowers'][c['id']]}）" for f in k["facts"][-show:]],
            "latest_events": [f"ch{e['chapter']} {e['summary']}" for e in k["events"][-show:]],
            "secrets_not_known": len(hidden),
            "secrets_about_them_not_known": [f"ch{f['chapter']} {f['text']} | 知情={[nm(x) for x in f['knowers']]}" for f in about_me_hidden[-show:]],
            "other_secrets_not_known": [f"ch{f['chapter']} {f['text']} | 知情={[nm(x) for x in f['knowers']]}" for f in hidden[-show:]]}
