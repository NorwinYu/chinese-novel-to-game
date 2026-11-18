"""Word lists the pipeline relies on: an interface the framework defines and each book fills in.

The framework knows only Chinese grammar (CORE below: speech verbs, crowd pronouns). Every list that
depends on genre, era or setting (role words, kinship terms, organisation and place endings, kinds of
place) is empty here and lives in games/<slug>/vocab.json, which the agent writes from evidence:
`n2g vocab-suggest` mines candidates from the book's own extraction, `n2g vocab-test` shows which names
a change flips before anything is rebuilt.

  {"descriptor_tails": ["老人", "店员"],    # role words: "X老人" describes someone, does not name them
   "descriptors": ["黑衣人"],                # exact strings that are always descriptions
   "names": ["阿福"],                        # exact strings that are always names, whatever they end with
   "remove": {"speech_verbs": ["笑"]},       # CORE words that are wrong for this book
   "note": "why these words"}

KEYS says what each list does; the code reads only those keys."""
from __future__ import annotations

import json
import re
from pathlib import Path

KEYS: dict[str, str] = {
    "descriptor_tails": "role words; a name ending in one describes someone (\"X老人\") unless the head looks like a name",
    "unique_roles": "role words held by one person per organisation; \"<organisation><role>\" then names someone",
    "organisation_suffixes": "endings of organisation names; \"<organisation><role>\" is a member, not a name",
    "descriptor_modifiers": "characters that make a head descriptive (colours, clothing, age): \"<modifier><role>\" is a description",
    "kinship": "kinship and teacher terms used as address; always descriptions, never merged across chapters",
    "people_nouns": "common nouns for people (\"客人\", \"路人\"); always descriptions",
    "crowd_words": "words in a knower or speaker meaning everyone present",
    "speech_verbs": "verbs before a colon that introduce a quotation (\"张三道：\")",
    "trailing_speech_verbs": "verbs that end the clause after a quotation (\"”张三笑问。\")",
    "generic_places": "kinds of place rather than places (\"房间\"); on the map each belongs to the chapter's region",
    "dwelling_words": "\"<person><word>\" is where that person lives or stays (\"张三家\", \"张三处\")",
    "place_suffixes": "endings that turn a short place name into the same place's full form (\"某\" / \"某城\")",
}
# grammar, not setting: true of any modern or classical Chinese prose
CORE: dict[str, tuple[str, ...]] = {
    "crowd_words": ("众人", "大家", "所有人", "们"),
    "speech_verbs": ("道", "说", "问", "答", "叫", "喊"),
    "trailing_speech_verbs": ("说", "问", "叫", "喊"),
}
EXACT = ("descriptors", "names")


class Vocab:
    def __init__(self, spec: dict | None = None):
        spec = spec or {}
        unknown = set(spec) - set(KEYS) - set(EXACT) - {"remove", "note"}
        if unknown:
            raise ValueError(f"vocab.json: unknown keys {sorted(unknown)}; known: {sorted([*KEYS, *EXACT, 'remove'])}")
        remove = spec.get("remove", {})
        for key in KEYS:
            words = [w for w in CORE.get(key, ()) if w not in remove.get(key, [])]
            words += [w for w in spec.get(key, []) if w not in words]
            setattr(self, key, tuple(words))
        self.descriptors = frozenset(spec.get("descriptors", []))
        self.names = frozenset(spec.get("names", []))
        alt = lambda ws: "|".join(map(re.escape, sorted(ws, key=len, reverse=True))) or "(?!)"
        self.speech_cue = re.compile(rf"(?:{alt(self.speech_verbs)})[道着了]?[：:]\s*$")
        self.trail_verb = re.compile(rf"(?:道|(?<!听)(?:{alt(self.trailing_speech_verbs)})[着了]?)$")

    def changed_from_core(self) -> dict:
        out = {k: [w for w in getattr(self, k) if w not in CORE.get(k, ())] for k in KEYS}
        out["removed_from_core"] = [f"{k}:{w}" for k, ws in CORE.items() for w in ws if w not in getattr(self, k)]
        out.update({k: sorted(getattr(self, k)) for k in EXACT})
        return {k: v for k, v in out.items() if v}


current = Vocab()


def load_vocab(book: Path) -> Vocab:
    path = book / "vocab.json"
    return Vocab(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else Vocab()


def test_vocab(book: Path, spec: dict | None = None) -> dict:
    """Names whose descriptor verdict the book's vocab.json (or `spec`) flips against CORE alone."""
    global current
    from collections import Counter
    from .consolidate import characters as ch
    names = Counter(m.name for m in ch.collect_mentions(book))
    saved, plugin = current, ch._book_descriptor
    try:
        ch._book_descriptor = None
        current = Vocab()
        before = {n: ch.is_descriptor(n) for n in names}
        current = Vocab(spec) if spec is not None else load_vocab(book)
        flips = [(n, c, before[n]) for n, c in names.most_common() if ch.is_descriptor(n) != before[n]]
        changes = current.changed_from_core()
    finally:
        current, ch._book_descriptor = saved, plugin
    return {"changes": changes, "names": len(names), "flipped": len(flips),
            "to_descriptor": [f"{n}×{c}" for n, c, was in flips if not was][:80],
            "to_name": [f"{n}×{c}" for n, c, was in flips if was][:80]}


def use_book(book: Path) -> Vocab:
    """Make the book's vocabulary the one every stage uses in this process."""
    global current
    current = load_vocab(book)
    return current
