"""Declarative knowledge rules for one book: games/<slug>/knowledge_rules.json.

Some books have presence or knowledge the extractor cannot see scene by scene. The common shapes are
data, not code:

  {"companions": [{"who": "B", "with": "A", "chapters": [[0, 262], [361, 610]],
                   "shares_knowledge": true, "note": "why, with chapter evidence"}],
   "shared_knowledge": [{"from": "A", "to": "B", "chapters": [[0, 99]], "how": ["told", "witnessed"],
                         "note": "..."}]}

companions        B is in every scene A is in, during the chapter ranges (inclusive): a spirit in a ring,
                  a familiar, a voice in the head. With shares_knowledge (default true) B also learns what
                  A learns by witnessing, hearing or being told there.
shared_knowledge  B learns what A learns in those chapters (how: which of A's ways of knowing pass on;
                  default witnessed, told, overheard, public, present). For telepathic links, diaries read
                  later, a spy who reports everything.
Names are book-level character names (or ids). Anything these shapes cannot express goes into a plugin."""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_HOW = ("witnessed", "told", "overheard", "public", "present")


def _in(ranges: list, ch: int) -> bool:
    return any(a <= ch <= b for a, b in ranges)


class KnowledgeRules:
    def __init__(self, spec: dict, lookup):
        unknown = set(spec) - {"companions", "shared_knowledge", "note"}
        if unknown:
            raise ValueError(f"knowledge_rules.json: unknown keys {sorted(unknown)}")
        self.companions, self.shared, missing = [], [], []

        def cid(name: str) -> str | None:
            got = lookup(name)
            if got is None:
                missing.append(name)
            return got

        for c in spec.get("companions", []):
            who, host = cid(c["who"]), cid(c["with"])
            if who and host:
                self.companions.append((who, host, c["chapters"]))
                if c.get("shares_knowledge", True):
                    self.shared.append((host, who, c["chapters"], tuple(DEFAULT_HOW)))
        for s in spec.get("shared_knowledge", []):
            a, b = cid(s["from"]), cid(s["to"])
            if a and b:
                self.shared.append((a, b, s["chapters"], tuple(s.get("how", DEFAULT_HOW))))
        if missing:
            raise ValueError(f"knowledge_rules.json: no character named {sorted(set(missing))}")

    def present(self, chapter: int, ids: set[str]) -> set[str]:
        out = set(ids)
        for who, host, ranges in self.companions:
            if host in ids and _in(ranges, chapter):
                out.add(who)
        return out

    def share(self, chapter: int, knowers: dict[str, str]) -> int:
        """Add knowers that the rules pass knowledge to; returns how many were added."""
        added = 0
        for a, b, ranges, hows in self.shared:
            if b not in knowers and knowers.get(a) in hows and _in(ranges, chapter):
                knowers[b] = "shared"
                added += 1
        return added


def load_rules(book: Path, lookup) -> KnowledgeRules | None:
    path = book / "knowledge_rules.json"
    if not path.exists():
        return None
    return KnowledgeRules(json.loads(path.read_text(encoding="utf-8")), lookup)

def test_rules(book: Path, *, samples: int = 8, seed: int = 0) -> dict:
    """What knowledge_rules.json changes over the whole book, with samples, before the ledger is rebuilt."""
    import random
    from collections import Counter
    from ..extract.review import load_records
    from .ledger import Resolver
    res = Resolver(book)
    if res.rules is None:
        return {"error": "no knowledge_rules.json"}
    rules, rng = res.rules, random.Random(seed)
    scenes, facts, added, shared = [], [], Counter(), Counter()
    for rec in load_records(book):
        o, ch = rec.get("result") or {}, rec["idx"]
        for s in o.get("scenes", []):
            res.rules = None
            before = set(res.present(ch, s.get("present", [])))
            res.rules = rules
            after = set(res.present(ch, s.get("present", [])))
            if after != before:
                added.update(res.name(i) for i in after - before)
                scenes.append(f"ch{ch} {s.get('place', '')}: +{[res.name(i) for i in after - before]}")
        for f in o.get("facts", []):
            kn = {}
            for k in f.get("knowers", []):
                cid = res(ch, k.get("who", ""))
                if cid:
                    kn.setdefault(cid, k.get("how", ""))
            before = set(kn)
            if rules.share(ch, kn):
                shared.update(res.name(i) for i in set(kn) - before)
                facts.append(f"ch{ch} {f.get('text', '')[:50]} → +{[res.name(i) for i in set(kn) - before]}")
    return {"scenes_changed": len(scenes), "added_to_scenes": dict(added), "facts_shared": len(facts),
            "shared_to": dict(shared), "scene_samples": rng.sample(scenes, min(samples, len(scenes))),
            "fact_samples": rng.sample(facts, min(samples, len(facts)))}
