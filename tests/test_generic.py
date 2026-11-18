"""Framework code, tests and skill docs must not carry words from a particular book.

Scans for S/A character names and aliases and frequent map places of every locally built book. Words
that are ordinary Chinese address terms or common nouns, and so turn up as aliases in many books, are
allowed. Skips when no book has been built."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMMON = {"二哥", "大姐", "姑娘", "老人", "伙计", "兄弟", "太太", "二叔", "二爷", "小伙子", "孩子", "老头", "先生",
          "老板", "大夫", "师傅", "姐姐", "妹妹", "哥哥", "弟弟", "父亲", "母亲", "老爷", "夫人", "少爷", "大哥",
          "新生"}  # "新生" only as part of "重新生成"


# examples/demo-novel.txt is written to reuse the neutral names the tests already use (张三/李四/青石城),
# so that one book is never evidence of a leak; every other built book counts.
EXAMPLES = {"demo"}


def _book_words() -> dict[str, set[str]]:
    words: dict[str, set[str]] = {}
    built = [d for d in ROOT.glob("games/*") if d.name not in EXAMPLES]
    for d in built:
        for f in (d / "build").glob("characters.json"):
            for c in json.loads(f.read_text(encoding="utf-8"))["characters"]:
                if c.get("tier") in ("S", "A"):
                    for w in (c["name"], *c.get("names", {}), *c.get("aliases", {})):
                        if len(w) >= 2:
                            words.setdefault(w, set()).add(d.name)
        for f in (d / "build").glob("places.json"):
            nodes = json.loads(f.read_text(encoding="utf-8")).get("nodes", {})
            items = nodes.items() if isinstance(nodes, dict) else ((n.get("name"), n) for n in nodes)
            for name, n in items:
                if name and len(name) >= 2 and "·" not in name and isinstance(n, dict) and n.get("scenes", 0) >= 5:
                    words.setdefault(name, set()).add(d.name)
    return {w: s for w, s in words.items() if w not in COMMON}


def test_framework_has_no_book_words():
    words = _book_words()
    if not words:
        pytest.skip("no built book to compare against")
    files = [*ROOT.glob("n2g/**/*.py"), *ROOT.glob("tests/*.py"), *ROOT.glob("*.md"),
             *(f for f in ROOT.glob("n2g/**/static/**/*") if f.suffix in (".html", ".js") and "vendor" not in f.parts)]
    hits = sorted(f"{f.relative_to(ROOT)}: {w} ({', '.join(sorted(words[w]))})"
                  for f in files if f.name != Path(__file__).name
                  for w in words if w in f.read_text(encoding="utf-8"))
    assert not hits, "book words in framework files:\n" + "\n".join(hits)
