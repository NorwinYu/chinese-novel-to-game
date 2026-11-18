"""Per-book plugins written by the agent.

Every `games/<slug>/plugins/*.py` file is loaded in file-name order. A plugin defines any of these
functions; the pipeline calls them at fixed points and keeps the generic behaviour when none is
defined or every plugin returns None:

  repair_line(line: str) -> str | None     ① each line after whitespace normalisation, before noise
                                              rules; plugins are chained in order
  is_descriptor(name: str) -> bool | None   ③ whether a character name is a role word ("老人") that must
                                              not link chapters; the first non-None answer wins
  adjust_present(chapter: int, present: list[str]) -> list[str] | None
                                           ④⑤ the character names present in a scene (book-level names);
                                              e.g. a companion who perceives everything the hero does
                                              during some chapters; plugins are chained in order

Anything that holds for one book only belongs in a plugin or a rule file, never in the package. Test a
plugin with `n2g plugin-test` before relying on it."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

HOOKS = ("repair_line", "is_descriptor", "adjust_present")


class Plugins:
    def __init__(self, modules: list[tuple[str, ModuleType]] | None = None):
        self.modules = modules or []

    def names(self) -> list[str]:
        return [n for n, _ in self.modules]

    def functions(self, hook: str) -> list:
        return [getattr(m, hook) for _, m in self.modules if callable(getattr(m, hook, None))]

    def repair_line(self):
        fns = self.functions("repair_line")
        if not fns:
            return None

        def chained(line: str) -> str:
            for fn in fns:
                out = fn(line)
                if out is not None:
                    line = out
            return line
        return chained

    def adjust_present(self):
        fns = self.functions("adjust_present")
        if not fns:
            return None

        def chained(chapter: int, present: list[str]) -> list[str]:
            for fn in fns:
                out = fn(chapter, present)
                if out is not None:
                    present = list(out)
            return present
        return chained

    def is_descriptor(self):
        fns = self.functions("is_descriptor")
        if not fns:
            return None

        def first(name: str):
            for fn in fns:
                v = fn(name)
                if v is not None:
                    return v
            return None
        return first


def load_plugins(book: Path, only: list[str] | None = None) -> Plugins:
    folder = book / "plugins"
    if not folder.is_dir():
        return Plugins()
    mods = []
    for path in sorted(folder.glob("*.py")):
        if path.name.startswith("_") or (only and path.stem not in only and path.name not in only):
            continue
        spec = importlib.util.spec_from_file_location(f"n2g_plugin_{book.name}_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        mods.append((path.name, module))
    return Plugins(mods)

def test_plugins(book: Path, hook: str, only: list[str] | None = None, *, samples: int = 12, seed: int = 0) -> dict:
    """Run plugins over the whole book and report what they change, for the agent to judge."""
    import difflib
    import random
    from collections import Counter

    plugins = load_plugins(book, only)
    if not plugins.functions(hook):
        return {"error": f"no plugin defines {hook}", "plugins": plugins.names()}
    rng = random.Random(seed)
    if hook == "repair_line":
        from .text.clean import normalize_lines
        from .text.ingest import load_book, read_source
        from .text.quotes import find_quotes
        fn = plugins.repair_line()
        raw, _, _ = read_source(book)
        lines = normalize_lines(raw, load_book(book).get("profile"))
        changed, edits, in_quote = [], Counter(), 0
        for line in lines:
            out = fn(line)
            if out == line:
                continue
            changed.append((line, out))
            for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, line, out, autojunk=False).get_opcodes():
                if op != "equal":
                    edits[f"{line[a1:a2] or '∅'}→{out[b1:b2] or '∅'}"] += 1
        quotes_before = sum(len(find_quotes(a)) for a, _ in changed)
        quotes_after = sum(len(find_quotes(b)) for _, b in changed)
        return {"plugins": plugins.names(), "lines": len(lines), "changed_lines": len(changed),
                "edit_kinds": dict(edits.most_common(25)),
                "quotes_in_changed_lines": {"before": quotes_before, "after": quotes_after},
                "samples": [{"before": a[:160], "after": b[:160]} for a, b in rng.sample(changed, min(samples, len(changed)))]}
    if hook == "is_descriptor":
        from .consolidate import characters as ch
        ch._book_descriptor = None
        names = Counter(m.name for m in ch.collect_mentions(book))
        generic = {n: ch.is_descriptor(n) for n in names}
        ch._book_descriptor = plugins.is_descriptor()
        flips = [(n, c, generic[n]) for n, c in names.most_common() if ch.is_descriptor(n) != generic[n]]
        return {"plugins": plugins.names(), "names": len(names), "flipped": len(flips),
                "to_descriptor": [f"{n}×{c}" for n, c, g in flips if not g][:80],
                "to_name": [f"{n}×{c}" for n, c, g in flips if g][:80]}
    if hook == "adjust_present":
        from .knowledge.ledger import Resolver
        from .extract.review import load_records
        res = Resolver(book)
        adjust, res._adjust = res._adjust, None
        changed, added, removed = [], Counter(), Counter()
        for rec in load_records(book):
            for s in (rec.get("result") or {}).get("scenes", []):
                before = [res.name(i) for i in res.present(rec["idx"], s.get("present", []))]
                after = adjust(rec["idx"], list(before))
                if sorted(after) != sorted(before):
                    changed.append(f"ch{rec['idx']} {s.get('place', '')}: {before} -> {after}")
                    added.update(set(after) - set(before))
                    removed.update(set(before) - set(after))
        return {"plugins": plugins.names(), "scenes_changed": len(changed), "added": dict(added.most_common(20)),
                "removed": dict(removed.most_common(20)), "samples": rng.sample(changed, min(samples, len(changed)))}
    return {"error": f"unknown hook {hook}; available: {list(HOOKS)}"}
