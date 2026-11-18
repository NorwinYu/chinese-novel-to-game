"""Place normalisation for the node map.

Scene places come from the extractor as free text ("张家后院的井边", "张家后院"), thousands of variants
in a long book. Here they become nodes:

- position words are stripped ("…的门口", "…附近");
- a name and the same name plus a vocab `place_suffixes` ending ("青石" / "青石城") are one place, under the
  commoner form;
- a name that starts with a known place ("青石城广场" ⊃ "青石城") becomes its sub-location;
- "<character><vocab dwelling_words>" ("张三处", "张三的房间") is that character's 住处;
- a generic word (vocab `generic_places`, or a two-character word that ends several other names) is not one
  place: each occurrence becomes "<region>·<word>" under the place the chapter is set in;
- rare sub-locations fold into their parent.

Book-level corrections live in games/<slug>/place_rules.json:

  {"same": {"变体": "规范名"}, "parent": {"子地点": "父地点"}, "ignore": ["梦中"],
   "regions": [{"name": "青石城", "chapters": [3, 7]}]}

regions name the area a chapter range is set in, for books whose scenes rarely name it (a long stay
in one building: "房间", "走廊"): generic words there go under it, and so does every top-level node seen only there.

Nothing calls a model; the agent reviews `n2g places` and writes rules for what the heuristics miss."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .. import vocab as V
from ..extract.review import load_records

_POSITION = re.compile(r"(?:的)?(?:内部|外部|之中|之外|之内|之上|附近|周围|四周|外围|一带|深处|边缘|门口|门前|门外|入口|"
                       r"上空|下方|中央|中心|里面|外面|某处|一角|内|外|中|里|上|下|前|后|旁|边)$")
_SPLIT = re.compile(r"[、，,/；;→]")
_JOIN = re.compile(r"及|和|与|到|至|或")
_OF = re.compile(r"(?<=[\u4e00-\u9fff]{2})的(?=[\u4e00-\u9fff]{2})")
_PAREN = re.compile(r"[（(][^）)]*[）)]")
# a leading "从…" / "在…", a time before 的 ("夜晚的街上"), "距…不远" say where, not which place
_LEAD = re.compile(r"^(?:从|在|于)(?=[\u4e00-\u9fff]{2})")
_TIME = re.compile(r"^(?:[初深晚]?[春夏秋冬][天日季]?|清晨|早晨|早上|上午|中午|午后|下午|傍晚|黄昏|晚上|夜晚|夜里|深夜|黑夜|"
                   r"半夜|夜间|白天|当晚|当夜|次日|雨夜|雪夜|雨中|雪中)+(?:中|里)?的(?=[\u4e00-\u9fff])")
_NEAR = re.compile(r"^(?:距|离)([\u4e00-\u9fff]{2,}?)不远(?:的|处)?[\u4e00-\u9fff]*$")
_NOT_PLACE = re.compile(r"^(?:无|未知|不详|不明|不定|多处|各处|某处|叙述.*|旁白.*|回忆.*|背景.*|梦.*|心.*)$")


def _clean(s: str, keep: frozenset[str] = frozenset()) -> str:
    """`keep`: names known to contain a joining character, which must not be split there."""
    s = _PAREN.sub("", s or "").strip()
    s = _SPLIT.split(s, 1)[0].strip()
    s = _LEAD.sub("", s)
    s = _TIME.sub("", s)
    if (m := _NEAR.match(s)):
        s = m.group(1)
    # "A及B" is two places, but 和 and 与 also occur inside names ("长和街", "和平村")
    spans = [(i, i + len(k)) for k in keep for i in [s.find(k)] if i >= 0]
    for m in _JOIN.finditer(s):
        if any(a <= m.start() < b for a, b in spans):
            continue
        if m.start() >= 2 and len(s) - m.end() >= 2:
            s = s[:m.start()]
            break
    s = _OF.sub("", s)
    for _ in range(3):
        t = _POSITION.sub("", s)
        t = t[:-1] if len(t) > 2 and t.endswith("之") else t
        if len(t) < 2 or t == s:
            break
        s = t
    return s


def _joined_names(places: list[str]) -> frozenset[str]:
    """Places named twice or more whose name holds a joining character ("长和街")."""
    c = Counter(_clean(p) for p in places)
    return frozenset(n for n, k in c.items() if k >= 2 and _JOIN.search(n))


def load_place_rules(book: Path) -> dict:
    p = book / "place_rules.json"
    rules = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    unknown = set(rules) - {"same", "parent", "ignore", "regions", "note"}
    if unknown:
        raise ValueError(f"place_rules.json: unknown keys {sorted(unknown)}")
    return rules


def _person_names(book: Path) -> dict[str, str]:
    """Proper names (not aliases, which are often shared titles) of people → canonical name."""
    p = book / "build" / "characters.json"
    if not p.exists():
        return {}
    out: dict[str, str] = {}
    for c in json.loads(p.read_text(encoding="utf-8"))["characters"]:
        if c.get("type", "person") == "person":
            for n in [c["name"], *c.get("names", {})]:
                if len(n) >= 2:
                    out.setdefault(n, c["name"])
    return out


def build_places(book: Path, *, min_scenes: int = 3, write: bool = True) -> dict:
    vocab = V.load_vocab(book)
    rules = load_place_rules(book)
    same, parent_rule, ignore = dict(rules.get("same", {})), rules.get("parent", {}), set(rules.get("ignore", []))
    entity_count: Counter = Counter()
    raw: list[tuple[int, str]] = []
    sids: list[str] = []
    entity_names: list[str] = []
    for rec in load_records(book):
        o = rec.get("result") or {}
        for e in o.get("entities", []):
            if e.get("type") == "place" and len(e.get("name", "")) >= 2:
                entity_names.append(e["name"])
        for s in o.get("scenes", []):
            raw.append((rec["idx"], s.get("place", "")))
            sids.append(f"{rec['idx']}.{s.get('id')}")
    keep = _joined_names(entity_names + [p for _, p in raw])
    clean = lambda s: _clean(s, keep)
    for n in entity_names:
        entity_count[clean(n)] += 1

    person = _person_names(book)
    dwell = sorted(vocab.dwelling_words, key=len, reverse=True)

    def dwelling(n: str) -> str:
        for w in dwell:
            if n.endswith(w) and (who := person.get(n[:-len(w)])):
                return f"{who}住处"
        return n

    names: list[tuple[int, str, str]] = []
    name_sids: list[str] = []
    for (ch, p), sid in zip(raw, sids):
        name = same.get(p) or same.get(clean(p)) or dwelling(clean(p))
        name = same.get(name, name)
        if len(name) >= 2 and p not in ignore and name not in ignore and not _NOT_PLACE.match(name):
            names.append((ch, p, name))
            name_sids.append(sid)
    freq = Counter(n for _, _, n in names) + entity_count

    # "青石" / "青石城" and a doubled ending ("青石城城") are one place; keep the form the book uses more.
    # Other endings ("张家大" / "张家大堂") usually mark a different place.
    alias: dict[str, str] = {}
    for b in freq:
        a = b[:-1]
        if len(a) < 2 or a not in freq or a in same or b in same:
            continue
        if b[-1] == a[-1]:
            alias[b] = a
        elif b[-1] in vocab.place_suffixes and (entity_count[a] or freq[a] >= min_scenes):
            keep, drop = (a, b) if freq[a] > freq[b] else (b, a)
            alias[drop] = keep
    canon = lambda n: alias.get(n, n)
    names = [(ch, p, canon(n)) for ch, p, n in names]
    freq2: Counter = Counter()
    for n, c in freq.items():
        freq2[canon(n)] += c
    distinct = {n for _, _, n in names}

    generic = {n for n in distinct if n in vocab.generic_places}
    generic |= {n for n in distinct if len(n) == 2 and entity_count[n] == 0
                and sum(1 for m in distinct if m != n and m.endswith(n)) >= 3}
    anchors = sorted((a for a, c in freq2.items() if a not in generic and len(a) >= 2
                      and (entity_count[a] >= 2 or c >= min_scenes)), key=len, reverse=True)

    def container(s: str) -> str | None:
        # "青石城广场" lies in "青石城"; "张三正屋" is not inside "正屋"
        if s in parent_rule:
            return parent_rule[s]
        # a one-character remainder ("青石山" / "青石山脉") is a different place, not a part of it
        if (a := next((a for a in anchors if len(a) + 2 <= len(s) and s.startswith(a)), None)):
            return a
        # "<street><named place>" ("长安路青石茶馆") is that place; only for names the extractor listed as places
        return next((a for a in anchors if len(a) >= 3 and entity_count[a] >= 2 and len(a) + 2 <= len(s)
                     and s.endswith(a)), None)

    def top(s: str) -> str:
        seen = {s}
        while (c := container(s)) and c not in seen:
            seen.add(c)
            s = c
        return s

    region_votes: dict[int, Counter] = defaultdict(Counter)
    for ch, _, n in names:
        if n not in generic:
            region_votes[ch][top(n)] += 1

    ranged = [(r["name"], r["chapters"][0], r["chapters"][1]) for r in rules.get("regions", [])]
    in_range = lambda ch: next((name for name, a, b in ranged if a <= ch <= b), None)

    def region(ch: int) -> str | None:
        if (r := in_range(ch)):
            return r
        for d in (0, -1, 1, -2, 2):
            if region_votes.get(ch + d):
                return region_votes[ch + d].most_common(1)[0][0]
        return None

    parent: dict[str, str] = dict(parent_rule)
    node_of: dict[str, str] = {}
    count, chapters, variants = Counter(), defaultdict(set), defaultdict(Counter)
    regionless = 0
    scene_raw: dict[str, str] = {}
    guessed: list[str] = []
    for (ch, p, name), sid in zip(names, name_sids):
        if name in generic:
            r = region(ch)
            if r and not in_range(ch):
                guessed.append(sid)
            if r:
                parent.setdefault(f"{r}·{name}", r)
                name = f"{r}·{name}"
            else:
                regionless += 1
        node_of.setdefault(p, name)
        scene_raw[sid] = name
        count[name] += 1
        chapters[name].add(ch)
        variants[name][p] += 1
    for name in list(count):
        if name not in parent and (c := container(name)):
            parent[name] = c
    for name in list(count):
        if name not in parent and chapters[name]:
            owners = {in_range(ch) for ch in chapters[name]}
            if len(owners) == 1 and (r := owners.pop()) and r != name:
                parent[name] = r
    for name in list(parent):
        if parent[name] not in count:
            count[parent[name]] += 0
    # fold rare sub-locations into their parent so the map keeps places the story returns to
    folded: dict[str, str] = {}
    for name in sorted(count, key=lambda n: count[n]):
        if count[name] < min_scenes and name in parent:
            up = parent[name]
            while up in folded:
                up = folded[up]
            folded[name] = up
            count[up] += count[name]
            chapters[up] |= chapters[name]
            variants[up].update(variants[name])
            del count[name]
    nodes = [{"name": n, "parent": parent.get(n), "scenes": c,
              "chapters": [min(chapters[n]), max(chapters[n])] if chapters[n] else None,
              "chapter_count": len(chapters[n]), "variants": [v for v, _ in variants[n].most_common(6)]}
             for n, c in count.most_common() if c]
    resolve = lambda n: resolve(folded[n]) if n in folded else n
    out = {"nodes": nodes, "generic_words": sorted(generic), "aliases": alias,
           "scene_to_node": {p: resolve(n) for p, n in node_of.items()},
           # per scene, since a generic word belongs to a different region in each chapter
           "scene_node": {sid: resolve(n) for sid, n in scene_raw.items()},
           # scenes whose node is only the chapter's most common place, guessed for a generic word
           "scene_guessed": guessed}
    if write:
        (book / "build" / "places.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    kept = [n for n in nodes if n["scenes"] >= min_scenes]
    covered = sum(n["scenes"] for n in kept)
    roots = [n for n in kept if not n["parent"]]
    return {"raw_strings": len({p for _, p in raw}), "scenes": len(raw), "placed_scenes": len(names),
            "nodes": len(nodes), f"nodes_with_{min_scenes}+_scenes": len(kept), "root_nodes": len(roots),
            "scene_share_on_those_nodes": round(covered / max(1, len(raw)), 3),
            "generic_scenes_without_region": regionless,
            "generic_words": sorted(generic)[:40], "aliases": dict(list(alias.items())[:20]),
            "top": [f"{n['name']}({n['scenes']}) ⊂ {n['parent']}" if n["parent"] else f"{n['name']}({n['scenes']})"
                    for n in nodes[:40]],
            "unattached_examples": [n["name"] for n in nodes if not n["parent"] and n["scenes"] < min_scenes][:20]}
