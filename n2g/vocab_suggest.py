"""Candidate vocabulary mined from the book's own extraction, for the agent to review into vocab.json.

Nothing here decides: each list is ranked evidence (word × score, with examples). The agent reads the
examples, keeps what fits the definition in vocab.KEYS, and checks the result with `n2g vocab-test`."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .vocab import KEYS, load_vocab

_HAN = r"[\u4e00-\u9fff]"
_DEMONSTRATIVE = re.compile(r"^(?:那|这|一|该|此|某|另一)(?:个|名|位|群|些|帮|伙|只)?")


def _fmt(counter: Counter, examples: dict, have: set, top: int) -> list[str]:
    out = []
    for w, c in counter.most_common():
        if w in have:
            continue
        ex = "/".join(list(dict.fromkeys(examples.get(w, [])))[:3])
        out.append(f"{w}×{c}" + (f" ({ex})" if ex else ""))
        if len(out) >= top:
            break
    return out


def suggest(book: Path, top: int = 30) -> dict:
    from .consolidate.characters import collect_mentions
    from .extract.review import load_records
    from .knowledge.places import _clean
    from .text.ingest import load_chapters

    v = load_vocab(book)
    mentions = collect_mentions(book)
    surf_ch: dict[str, set] = defaultdict(set)
    for m in mentions:
        for s in m.surfaces:
            surf_ch[s].add(m.chapter)
    surfaces = set(surf_ch)
    chars_path = book / "build" / "characters.json"
    chars = json.loads(chars_path.read_text(encoding="utf-8"))["characters"] if chars_path.exists() else []
    owners: dict[str, set] = defaultdict(set)
    person_names: set[str] = set()
    for c in chars:
        for s in [c["name"], *c.get("names", {}), *c.get("aliases", {})]:
            owners[s].add(c["name"])
        if c.get("type") == "person":
            person_names |= {n for n in [c["name"], *c.get("names", {})] if len(n) >= 2}

    places, groups, knowers, speakers = Counter(), Counter(), Counter(), Counter()
    place_ch: dict[str, set] = defaultdict(set)
    for rec in load_records(book):
        o = rec.get("result") or {}
        for s in o.get("scenes", []):
            p = _clean(s.get("place", ""))
            if len(p) >= 2:
                places[p] += 1
                place_ch[p].add(rec["idx"])
        for e in o.get("entities", []):
            if e.get("type") == "group":
                groups[e.get("name", "")] += 1
            elif e.get("type") == "place":
                places[_clean(e.get("name", ""))] += 0
        for f in o.get("facts", []):
            for k in f.get("knowers", []):
                knowers[k.get("who", "") if isinstance(k, dict) else str(k)] += 1
        for u in o.get("utterances", []):
            speakers[u[1]] += 1

    out: dict[str, dict] = {}

    def put(key: str, counter: Counter, examples: dict, note: str = "", used=None) -> None:
        have = set(getattr(v, key))
        out[key] = {"means": KEYS[key], "in_vocab": len(have), "candidates": _fmt(counter, examples, have, top)}
        if used is not None:
            unused = [w for w in getattr(v, key) if not used(w)]
            if unused:
                out[key]["in_vocab_but_unused"] = unused[:30]
        if note:
            out[key]["how"] = note

    # role words: what follows "那/这/一个…" in aliases, and endings shared by many names that also stand alone
    tails, tail_ex = Counter(), defaultdict(list)
    for s in surfaces:
        m = _DEMONSTRATIVE.match(s)
        if m and 1 <= len(s) - m.end() <= 4:
            r = s[m.end():]
            tails[r] += len(surf_ch[s])
            tail_ex[r].append(s)
    by_end: dict[str, list[str]] = defaultdict(list)
    for s in surfaces:
        for k in (2, 3):
            if len(s) > k:
                by_end[s[-k:]].append(s)
    for t, heads in by_end.items():
        heads = [s for s in heads if len(s) - len(t) <= 3]
        standalone = t in surfaces and len(surf_ch[t]) >= 2
        # an ending that also stands alone is a strong sign; many names sharing it is a weaker one
        if (standalone and len(heads) >= 3) or len(heads) >= 5:
            tails[t] += len(heads) if standalone else len(heads) // 2
            tail_ex[t] += heads
    ends = lambda w: any(s.endswith(w) for s in surfaces)
    put("descriptor_tails", tails, tail_ex, "after a demonstrative in an alias, or a word that stands alone and ends ≥3 names", ends)

    known_tails = set(v.descriptor_tails) | {w for w, c in tails.items() if c >= 2}
    org, org_ex = Counter(), defaultdict(list)
    for g in groups:
        if len(g) >= 2 and g[-1] not in "的们人等":
            org[g[-1]] += 1
            org_ex[g[-1]].append(g)
    roles = set(v.descriptor_tails)
    put("organisation_suffixes", org, org_ex, "last character of group names",
        lambda w: any(s.endswith(t) and s[: -len(t)].endswith(w) for s in surfaces for t in roles))

    # "<organisation><role>": one character per surface across chapters suggests a unique role
    uniq, member, role_ex = Counter(), Counter(), defaultdict(list)
    org_chars = set(v.organisation_suffixes) | {w for w, c in org.items() if c >= 3}
    for s in surfaces:
        for t in known_tails:
            head = s[: -len(t)]
            if s.endswith(t) and len(head) >= 2 and head[-1] in org_chars:
                (uniq if len(owners.get(s, ())) == 1 and len(surf_ch[s]) >= 2 else member)[t] += 1
                role_ex[t].append(s)
    put("unique_roles", Counter({t: c for t, c in uniq.items() if c > member[t]}), role_ex,
        "role words whose '<organisation><role>' names keep pointing at one character", ends)

    mods, mod_ex = Counter(), defaultdict(list)
    for s in surfaces:
        for t in known_tails:
            head = s[: -len(t)]
            # a surname head ("王经理") names one character; a modifier head ("小店员") several or none
            if s.endswith(t) and 1 <= len(head) <= 2 and len(owners.get(s, ())) != 1:
                for chr_ in head:
                    mods[chr_] += 1
                    mod_ex[chr_].append(s)
    put("descriptor_modifiers", mods, mod_ex, "characters in short heads before role words, on names that are not one character's",
        lambda w: any(s.endswith(t) and w in s[: -len(t)] for s in surfaces for t in roles))

    # a word that names different people: the registry gave it to several characters, or chapters attach it
    # as an alias to different main names (it can also have chained them into one wrong character)
    attached: dict[str, set] = defaultdict(set)
    for m in mentions:
        for a in m.aliases:
            if a != m.name:
                attached[a].add(m.name)
    shared, shared_ex = Counter(), {}
    for s in set(owners) | set(attached):
        who = owners.get(s, set()) | attached.get(s, set())
        if len(who) >= 2 and len(s) <= 4:
            shared[s] = len(who)
            shared_ex[s] = sorted(who)[:3]
    put("kinship", shared, shared_ex, "words that name several different people; split them into kinship / people_nouns / "
        "descriptor_tails / descriptors, or names if this book uses one for one person",
        lambda w: w in surfaces)
    put("people_nouns", Counter(), {}, "see kinship candidates", lambda w: w in surfaces)

    from .extract.chapter import SPEAKER_PLACEHOLDERS
    crowd, crowd_ex = Counter(), defaultdict(list)
    who_all = knowers + speakers
    for who, c in who_all.items():
        if who and who not in owners and who not in surfaces and who not in SPEAKER_PLACEHOLDERS and len(who) <= 4:
            crowd[who] += c
    put("crowd_words", crowd, crowd_ex, "knower or speaker strings that match no character",
        lambda w: any(w in who for who in who_all))

    text_paras = [p for c in load_chapters(book) for p in c["paragraphs"]]
    lead, lead_ex, trail, trail_ex = Counter(), defaultdict(list), Counter(), defaultdict(list)
    person_surfaces = sorted((s for s in surfaces if len(s) >= 2 and s in owners), key=len, reverse=True)

    def verb_after_name(clause: str) -> str | None:
        # "张三笑道" / "张三冷笑着问" -> the verb; at most three characters between the name and the colon
        n = next((n for n in person_surfaces if clause.startswith(n)), None)
        if not n or not 1 <= len(clause) - len(n) <= 4:
            return None
        rest = re.sub(r"[着了]$", "", clause[len(n):])
        if len(rest) >= 2 and rest.endswith("道"):
            rest = rest[:-1]
        return rest[-1] if rest else None

    # only clauses the current lists miss: those are the cues extraction cannot check yet
    for p in text_paras:
        for m in re.finditer(rf"(?:^|[，。！？；”\s])({_HAN}{{2,10}})[：:]\s*“", p):
            if not v.speech_cue.search(m.group(1) + "：") and (verb := verb_after_name(m.group(1))):
                lead[verb] += 1
                lead_ex[verb].append(m.group(1))
        for m in re.finditer(rf"”({_HAN}{{2,10}})[。，！？]", p):
            if not v.trail_verb.search(m.group(1)) and (verb := verb_after_name(m.group(1))):
                trail[verb] += 1
                trail_ex[verb].append(m.group(1))
    put("speech_verbs", lead, lead_ex, "verb between a character's name and '：“', where the current list finds no cue")
    put("trailing_speech_verbs", trail, trail_ex, "verb ending a short '”<name>…' clause the current list misses")

    gen, gen_ex = Counter(), defaultdict(list)
    for p in places:
        if 2 <= len(p) <= 3:
            longer = [q for q in places if q != p and q.endswith(p)]
            if len(longer) >= 2:
                gen[p] = len(longer)
                gen_ex[p] = longer[:3]
    put("generic_places", gen, gen_ex, "short place words that end several other place strings", lambda w: w in places)

    dwell, dwell_ex = Counter(), defaultdict(list)
    for p in places:
        q = p.replace("的", "")
        for n in person_names:
            if q.startswith(n) and 1 <= len(q) - len(n) <= 3:
                dwell[q[len(n):]] += 1
                dwell_ex[q[len(n):]].append(p)
    put("dwelling_words", dwell, dwell_ex, "remainder of place strings that start with a person's name", lambda w: dwell[w] > 0)

    suf, suf_ex = Counter(), defaultdict(list)
    for p in places:
        if len(p) >= 3 and p[:-1] in places:
            suf[p[-1]] += 1
            suf_ex[p[-1]].append(f"{p[:-1]}/{p}")
    put("place_suffixes", suf, suf_ex, "c where both 'X' and 'Xc' occur as places", lambda w: suf[w] > 0)
    return out
