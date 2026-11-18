"""Stage 3: whole-book character registry.

Per-chapter entities are merged into book-level characters in three passes:
  1. deterministic: the same proper name across chapters, and chapter aliases that point to only one
     character book-wide;
  2. model review of candidate pairs (substring names, one-character variants, the model's own
     outside_name hints), with descriptions and co-occurrence as evidence;
  3. the agent's per-book overrides in games/<slug>/merge_rules.json (merge / split / generic).
Descriptor names ("老人", "店员") never merge across chapters on their own: they are a different
person from scene to scene unless a pass above ties them to a named character."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .. import vocab
from ..extract.review import load_records

PERSONISH = ("person", "creature")
def is_compound(s: str, known: set[str] | None = None) -> bool:
    """Several people named together ("张三、李四", "王五等", "张三和李四"). They speak or act as a
    group in one scene and must never tie the individuals together."""
    if re.search(r"[、，,/]|等人?$", s):
        return True
    m = re.fullmatch(r"(.{2,4})[和与及同](.{2,4})", s)
    return bool(m and known is not None and m.group(1) in known and m.group(2) in known)


_book_descriptor = None  # is_descriptor from games/<slug>/plugins, set by use_book_plugins


def use_book_plugins(book: Path) -> None:
    """The book's plugins and vocab.json, for every name check in this process."""
    global _book_descriptor
    from ..plugins import load_plugins
    vocab.use_book(book)
    _book_descriptor = load_plugins(book).is_descriptor()


def is_descriptor(s: str) -> bool:
    v = vocab.current
    if s in v.names:
        return False
    if s in v.descriptors:
        return True
    if _book_descriptor is not None:
        verdict = _book_descriptor(s)
        if verdict is not None:
            return bool(verdict)
    if s in v.kinship or s in v.people_nouns or re.match(r"[我你他她它咱这那哪此某每各众诸一两几]", s) or s.endswith("们"):
        return True
    return s.endswith(v.descriptor_tails) and not _looks_named(s)


def _looks_named(s: str) -> bool:
    # "张大夫", "李师傅", "王太太": a name-like head before the role word keeps it proper. We cannot
    # know surnames in general, so only very short heads count as descriptive modifiers.
    for tail in vocab.current.descriptor_tails:
        if s.endswith(tail):
            head = s[: -len(tail)]
            if re.fullmatch(r"[一二三四五六七八九十百]+", head):  # "二经理", "三科长"
                return False
            if head.endswith(vocab.current.organisation_suffixes) and tail not in vocab.current.unique_roles:
                return False  # "某某厂工人": some member of a group
            if re.search(r"[的个位名那这一两三几些众各诸某]", head):
                return False
            return len(head) >= 1 and not any(ch in head for ch in vocab.current.descriptor_modifiers)
    return False


@dataclass
class Mention:
    chapter: int
    name: str
    type: str
    aliases: list[str]
    desc: str
    outside_name: str = ""
    compound: bool = False
    said: int = 0
    scenes: int = 0
    events: int = 0
    facts: int = 0

    @property
    def surfaces(self) -> list[str]:
        return [self.name] + self.aliases


def collect_mentions(book: Path) -> list[Mention]:
    out: list[Mention] = []
    for rec in load_records(book):
        o = rec.get("result")
        if not o:
            continue
        by_surface: dict[str, Mention] = {}
        for e in o.get("entities", []):
            if e.get("unverified") or e.get("type") not in PERSONISH + ("group", "other"):
                continue
            m = Mention(rec["idx"], e["name"], e.get("type", "other"), list(e.get("aliases", [])), e.get("desc", ""),
                        e.get("outside_name", ""))
            out.append(m)
            for s in m.surfaces:
                by_surface.setdefault(s, m)
        hit = lambda x: by_surface.get(x)
        for u in o.get("utterances", []):
            if u[3] in ("said", "thought", "telepathy", "written") and hit(u[1]):
                hit(u[1]).said += 1
        for s in o.get("scenes", []):
            for x in set(s.get("present", [])):
                if hit(x):
                    hit(x).scenes += 1
        for ev in o.get("events", []):
            for x in set(ev.get("who", [])):
                if hit(x):
                    hit(x).events += 1
        for f in o.get("facts", []):
            for x in set(f.get("about", [])):
                if hit(x):
                    hit(x).facts += 1
    # "other" entities only count when they act like people (auto-added speakers, present in scenes)
    out = [m for m in out if m.type in PERSONISH or m.type == "group" or m.said or m.scenes]
    known = {m.name for m in out}
    for m in out:
        if is_compound(m.name, known):
            m.type, m.aliases = "group", []
            m.compound = True
    return out


class DSU:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[max(a, b)] = min(a, b)


@dataclass
class Rules:
    merge: list[list[str]] = field(default_factory=list)     # surfaces that are one character
    split: list[list[str]] = field(default_factory=list)     # surfaces that must stay apart
    generic: list[str] = field(default_factory=list)         # surfaces never used to link chapters
    proper: list[str] = field(default_factory=list)          # descriptor-looking surfaces that are names here


def load_rules(book: Path) -> Rules:
    path = book / "merge_rules.json"
    if not path.exists():
        return Rules()
    d = json.loads(path.read_text(encoding="utf-8"))
    return Rules(**{k: d.get(k, []) for k in ("merge", "split", "generic", "proper")})


def deterministic_clusters(mentions: list[Mention], rules: Rules) -> list[list[int]]:
    generic = set(rules.generic)
    proper = set(rules.proper)
    linkable = lambda s: len(s) >= 2 and s not in generic and (s in proper or not is_descriptor(s))
    kind = _kind
    dsu = DSU(len(mentions))
    alias_links: set[tuple[int, int]] = set()

    # 1. same proper name across chapters (people with people, groups with groups)
    by_name: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, m in enumerate(mentions):
        if linkable(m.name) and not m.compound:
            by_name[(kind(m), m.name)].append(i)
    for ids in by_name.values():
        for j in ids[1:]:
            dsu.union(ids[0], j)

    # 2. aliases: mentions named by an alias that, book-wide, one cluster uses join that cluster, but
    #    only small clusters. Two established clusters linked by an alias (one sister once called by the other's name)
    #    are left for the model to judge.
    for _ in range(2):
        owners: dict[tuple[str, str], Counter] = defaultdict(Counter)
        for i, m in enumerate(mentions):
            for a in m.aliases:
                if linkable(a) and not m.compound:
                    owners[(kind(m), a)][dsu.find(i)] += 1
        size = Counter(dsu.find(i) for i in range(len(mentions)))
        for key, cnt in owners.items():
            (root, uses), total = cnt.most_common(1)[0], sum(cnt.values())
            if uses / total < 0.9:
                continue
            for r in {dsu.find(j) for j in by_name.get(key, [])} - {dsu.find(root)}:
                if size[r] <= max(2, uses // 2):
                    dsu.union(root, r)
                else:
                    alias_links.add((root, r))

    # 3. agent overrides
    surface_ids: dict[str, list[int]] = defaultdict(list)
    for i, m in enumerate(mentions):
        for s in m.surfaces:
            surface_ids[s].append(i)
    for group in rules.merge:
        ids = [i for s in group for i in surface_ids.get(s, []) if mentions[i].name == s or s in mentions[i].aliases]
        for j in ids[1:]:
            dsu.union(ids[0], j)

    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(len(mentions)):
        clusters[dsu.find(i)].append(i)
    out = list(clusters.values())
    for pair in rules.split:
        out = _split(out, mentions, pair)
    where = {i: k for k, c in enumerate(out) for i in c}
    links = {tuple(sorted((where[a], where[b]))) for a, b in alias_links if where[a] != where[b]}
    return out, links


def _split(clusters: list[list[int]], mentions: list[Mention], surfaces: list[str]) -> list[list[int]]:
    """Separate mentions named by different surfaces in `surfaces` that ended up in one cluster."""
    res = []
    for c in clusters:
        named = {s: [i for i in c if mentions[i].name == s] for s in surfaces}
        if sum(1 for v in named.values() if v) < 2:
            res.append(c)
            continue
        taken = set()
        for s, ids in named.items():
            if ids:
                res.append(ids)
                taken.update(ids)
        rest = [i for i in c if i not in taken]
        if rest:
            res[-len([v for v in named.values() if v])] += rest
    return res


def summarize(cluster: list[int], mentions: list[Mention], n_chapters: int) -> dict:
    ms = [mentions[i] for i in cluster]
    names = Counter(m.name for m in ms)
    aliases = Counter(a for m in ms for a in m.aliases)
    chapters = sorted({m.chapter for m in ms})
    proper = [s for s, _ in names.most_common() if not is_descriptor(s)]
    return {
        "name": (proper or [names.most_common(1)[0][0]])[0],
        "type": Counter(m.type for m in ms).most_common(1)[0][0],
        "names": dict(names.most_common()),
        "aliases": dict(aliases.most_common(20)),
        "outside_names": sorted({m.outside_name for m in ms if m.outside_name}),
        "compound": all(m.compound for m in ms),
        "chapters": len(chapters), "first": chapters[0], "last": chapters[-1],
        "said": sum(m.said for m in ms), "scenes": sum(m.scenes for m in ms),
        "events": sum(m.events for m in ms), "facts": sum(m.facts for m in ms),
        "descs": [f"ch{m.chapter}: {m.desc}" for m in _spread(ms, 6) if m.desc],
        "mentions": [(m.chapter, m.name) for m in ms],
    }


def _spread(ms: list[Mention], k: int) -> list[Mention]:
    ms = sorted(ms, key=lambda m: m.chapter)
    if len(ms) <= k:
        return ms
    step = (len(ms) - 1) / (k - 1)
    return [ms[round(i * step)] for i in range(k)]


# ---------------------------------------------------------------- model review of candidate pairs

def _kind(m: Mention) -> str:
    return m.type if m.type in ("creature", "group") else "person"


def candidate_pairs(mentions: list[Mention], clusters: list[list[int]], alias_links: set[tuple[int, int]] = frozenset(),
                    *, min_weight: int = 2) -> list[dict]:
    """Pairs of clusters that may be one character: one proper name contains the other, names differ
    by one character, an outside_name hint names the other, or a descriptor-named speaker sits next
    to a named character's first appearance. Pairs that appear side by side as separate entities in
    two or more chapters are left out (the extractor already told them apart there)."""
    info = []
    for c in clusters:
        ms = [mentions[i] for i in c]
        surfaces = Counter(s for m in ms for s in m.surfaces)
        info.append({"kind": _kind(ms[0]), "chapters": {m.chapter for m in ms},
                     "names": {m.name for m in ms}, "surfaces": set(surfaces),
                     "proper": {s for s in surfaces if len(s) >= 2 and not is_descriptor(s)},
                     "outside": {m.outside_name for m in ms if m.outside_name},
                     "weight": sum(m.said + m.events for m in ms) + len({m.chapter for m in ms}),
                     "descriptor_only": all(is_descriptor(m.name) for m in ms),
                     "said": sum(m.said for m in ms)})
    compound = [all(mentions[i].compound for i in c) for c in clusters]
    heavy = [i for i, x in enumerate(info) if x["weight"] >= min_weight and not compound[i]]
    pairs: dict[tuple[int, int], set[str]] = defaultdict(set)

    def add(a: int, b: int, why: str) -> None:
        if a == b or info[a]["kind"] != info[b]["kind"] or compound[a] or compound[b]:
            return
        if len(info[a]["chapters"] & info[b]["chapters"]) >= 2:
            return
        pairs[(min(a, b), max(a, b))].add(why)

    by_surface: dict[str, set[int]] = defaultdict(set)
    by_wild: dict[str, set[int]] = defaultdict(set)
    for i in heavy:
        for s in info[i]["proper"]:
            by_surface[s].add(i)
            for k in range(len(s)):
                by_wild[s[:k] + "?" + s[k + 1:]].add(i)
    for s, owners in by_surface.items():
        for t in (s[k:k + n] for n in range(2, len(s)) for k in range(len(s) - n + 1)):
            for a in owners:
                for b in by_surface.get(t, ()):
                    add(a, b, f"包含：{s}⊃{t}")
    for key, owners in by_wild.items():
        if len(key) >= 3 or len(owners) <= 6:  # two-character keys like "张?" are too broad
            for a in owners:
                for b in owners:
                    if a < b:
                        add(a, b, f"一字之差：{key}")
    for i in heavy:
        for o in info[i]["outside"]:
            for b in by_surface.get(o, ()):
                add(i, b, f"抽取时的全名提示：{o}")
    for a, b in alias_links:
        add(a, b, "一方的别称是另一方的名字")
    first_seen = {i: min(info[i]["chapters"]) for i in heavy}
    for i in heavy:
        if info[i]["descriptor_only"] and info[i]["said"] >= 5:
            ch = max(info[i]["chapters"])
            for b in heavy:
                # someone first called "老人" is often named a few chapters later
                if not info[b]["descriptor_only"] and info[b]["weight"] >= 10 and 0 <= first_seen[b] - ch <= 3:
                    add(i, b, "无名角色，之后几章出现的有名角色")
    out = []
    for (a, b), why in pairs.items():
        out.append({"a": a, "b": b, "why": sorted(why),
                    "together": sorted(info[a]["chapters"] & info[b]["chapters"])})
    return out


JUDGE_SYSTEM = """你在整理一部小说的人物表。程序把逐章抽出的人物按名字初步合并成了若干“人物条目”，现在给你一批“可能是同一个人”的条目对，请逐对判断。

每个条目给出：在各章里用过的名字和别称、出现的章节范围、说话次数、各章对他的描述。
“same: yes” 只表示 A 和 B 是同一个人（或同一个固定群体）。一个条目是一群人、另一个是其中一员，不算同一个；拿不准就给 unsure。
判断依据只能是这些材料：描述里的身份、关系、经历是否吻合，出现章节是否衔接。名字相近不等于同一人（兄弟、同姓、同辈常常只差一个字），名字不同也可能是同一人（本名和外号、错别字、身份揭晓前的称呼）。“二叔”“太太”“二爷”这类称呼随说话人变化，在不同章可能指不同的人，要看描述。together 列出两个条目在同一章里被当作两个不同的人分别列出的章节，这通常说明是两个人。

输出 JSON：{"decisions": [{"pair": 编号, "same": "yes|no|unsure", "reason": "一句话理由"}]}，每一对都要给出。"""


def _brief(cluster: list[int], mentions: list[Mention]) -> dict:
    s = summarize(cluster, mentions, 0)
    return {"名字": s["names"], "别称": list(s["aliases"])[:10], "章节": f"{s['first']}-{s['last']}（共{s['chapters']}章）",
            "说话": s["said"], "描述": s["descs"][:5]}


async def judge_pairs(llm, mentions: list[Mention], clusters: list[list[int]], pairs: list[dict], *, batch: int = 25) -> list[dict]:
    import asyncio

    async def one(chunk: list[tuple[int, dict]]) -> list[dict]:
        items = [{"pair": k, "A": _brief(clusters[p["a"]], mentions), "B": _brief(clusters[p["b"]], mentions),
                  "together": p["together"], "候选原因": p["why"]} for k, p in chunk]
        ids = {k for k, _ in chunk}

        def validate(obj):
            ds = obj.get("decisions") if isinstance(obj, dict) else None
            if not isinstance(ds, list):
                return obj, ["缺少 decisions 数组"]
            got = {d.get("pair") for d in ds if isinstance(d, dict)}
            missing = ids - got
            for d in ds:
                if d.get("same") not in ("yes", "no", "unsure"):
                    d["same"] = "unsure"
            return obj, [f"缺少这些编号的判断：{sorted(missing)}"] if missing else []

        obj, _ = await llm.chat_json("consolidate", [{"role": "system", "content": JUDGE_SYSTEM},
                                                     {"role": "user", "content": json.dumps(items, ensure_ascii=False)}],
                                     validate=validate, max_tokens=8000, temperature=0.0)
        return [d for d in (obj or {}).get("decisions", []) if d.get("pair") in ids]

    numbered = list(enumerate(pairs))
    chunks = [numbered[i:i + batch] for i in range(0, len(numbered), batch)]
    results = await asyncio.gather(*(one(c) for c in chunks))
    by_id = {d["pair"]: d for r in results for d in r}
    return [{**p, "same": by_id.get(k, {}).get("same", "unsure"), "reason": by_id.get(k, {}).get("reason", "")}
            for k, p in numbered]


# ---------------------------------------------------------------- tiers and output

def _weight(c: dict) -> int:
    return c["said"] + 2 * c["events"] + c["chapters"]


def assign_tiers(chars: list[dict], n_chapters: int, overrides: dict[str, str], limits: dict[str, int] | None = None) -> None:
    """S: carries the story (in ≥20% of chapters and ≥2% of all lines); A: recurring major (≥5% of
    chapters, or ≥1% of lines over at least two chapters); B: recurs or speaks; C: everyone else.
    Groups get G. Thresholds are relative to the book so they scale from 20 to 2000 chapters.
    S and A get role-play cards, so a character who never speaks (a monster, an offstage figure) stays
    at B unless merge_rules.json "tiers" says otherwise: there is no voice to imitate."""
    total_said = sum(c["said"] for c in chars) or 1
    for c in chars:
        share_ch, share_said = c["chapters"] / max(n_chapters, 1), c["said"] / total_said
        if c["type"] == "group":
            tier = "G"
        elif share_ch >= 0.2 and share_said >= 0.02:
            tier = "S"
        elif c["chapters"] >= 2 and (share_ch >= 0.05 or share_said >= 0.01):
            tier = "A"
        elif c["chapters"] >= 2 or c["said"] >= 5:
            tier = "B"
        else:
            tier = "C"
        if tier in "SA" and c["said"] == 0:
            tier = "B"
        c["tier"] = tier
    # book.json "tier_limits" ({"S": 8, "A": 40}) caps a tier by weight; the rest move down one tier.
    # Cards are written for S and A per stage, so this is the main cost control on long books.
    order = "SABC"
    for k, t in enumerate(order[:-1]):
        cap = (limits or {}).get(t)
        if cap is None:
            continue
        ranked = sorted((c for c in chars if c["tier"] == t and c["name"] not in overrides), key=_weight, reverse=True)
        keep = max(0, cap - sum(1 for c in chars if overrides.get(c["name"]) == t))
        for c in ranked[keep:]:
            c["tier"] = order[k + 1]
    for c in chars:
        c["tier"] = overrides.get(c["name"], c["tier"])


async def build_characters(book: Path, llm=None, n_chapters: int | None = None) -> dict:
    use_book_plugins(book)
    rules = load_rules(book)
    mentions = collect_mentions(book)
    clusters, alias_links = deterministic_clusters(mentions, rules)
    decisions: list[dict] = []
    if llm is not None:
        pairs = candidate_pairs(mentions, clusters, alias_links)
        decisions = await judge_pairs(llm, mentions, clusters, pairs) if pairs else []
        blocked = {frozenset(p) for p in rules.split}
        dsu = DSU(len(clusters))
        comp_chapters = {k: {mentions[i].chapter for i in c} for k, c in enumerate(clusters)}
        comp_names = {k: {mentions[i].name for i in c} for k, c in enumerate(clusters)}
        for d in decisions:
            if d["same"] == "yes":
                # Each pair is judged alone, so two "yes" answers can chain different people through a
                # third entry ("A = 灰衣人", "灰衣人 = B"). Check the merged components, not the pair.
                ra, rb = dsu.find(d["a"]), dsu.find(d["b"])
                if ra == rb:
                    continue
                if any(frozenset((x, y)) in blocked for x in comp_names[ra] for y in comp_names[rb]):
                    d["same"] = "blocked_by_rules"
                    continue
                # listed as separate entities in the same chapter twice or more: two people
                if len(comp_chapters[ra] & comp_chapters[rb]) >= 2:
                    d["same"] = "blocked_by_conflict"
                    continue
                dsu.union(ra, rb)
                root = dsu.find(ra)
                comp_chapters[root] = comp_chapters[ra] | comp_chapters[rb]
                comp_names[root] = comp_names[ra] | comp_names[rb]
        merged: dict[int, list[int]] = defaultdict(list)
        for k, c in enumerate(clusters):
            merged[dsu.find(k)].extend(c)
        for d in decisions:
            d["A"] = sorted({mentions[i].name for i in clusters[d["a"]]})
            d["B"] = sorted({mentions[i].name for i in clusters[d["b"]]})
        clusters = list(merged.values())
    chars = [summarize(c, mentions, 0) for c in clusters]
    if n_chapters is None:
        n_chapters = len({m.chapter for m in mentions})
    meta_path = book / "merge_rules.json"
    tiers = json.loads(meta_path.read_text(encoding="utf-8")).get("tiers", {}) if meta_path.exists() else {}
    meta = book / "book.json"
    limits = json.loads(meta.read_text(encoding="utf-8")).get("tier_limits") if meta.exists() else None
    assign_tiers(chars, n_chapters, tiers, limits)
    chars.sort(key=lambda c: ("SABCG".index(c["tier"]), -_weight(c)))
    for k, c in enumerate(chars):
        c["id"] = f"c{k:04d}"
    people = [c for c in chars if c["type"] == "person"]
    protagonist = max(people, key=lambda c: c["scenes"])["name"] if people else None
    return {"protagonist_guess": protagonist, "chapters_seen": n_chapters, "characters": chars,
            "merge_decisions": [{k: v for k, v in d.items() if k not in ("a", "b")} for d in decisions]}
