"""Views of extraction output for the agent to check against the text."""
from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

from .. import vocab
from ..text.ingest import load_book, load_chapters
from .chapter import cue_speaker, narrator_for, number_chapter, out_path, trailing_cue_speaker


def load_records(book: Path) -> list[dict]:
    d = book / "build" / "extract"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))] if d.exists() else []


def _window(paras: list[str], q: tuple, before: int = 40, after: int = 30) -> str:
    p, text, start = q
    para = paras[p]
    return para[max(0, start - before): start] + "【" + text[:after] + ("…" if len(text) > after else "") + "】"


def review_chapter(book: Path, idx: int, *, n_utt: int = 12, seed: int = 0) -> dict:
    """One chapter's extraction next to the text it cites, with a random sample of speaker attributions."""
    ch = load_chapters(book)[idx]
    rec = json.loads(out_path(book, idx).read_text(encoding="utf-8"))
    o = rec.get("result") or {}
    _, quotes = number_chapter(ch["paragraphs"])
    utt = o.get("utterances", [])
    sample = random.Random(seed).sample(utt, min(n_utt, len(utt)))
    paras = ch["paragraphs"]
    return {
        "heading": rec["heading"], "attempts": rec.get("attempts"), "attempt_problems": rec.get("attempt_problems"),
        "problems": rec.get("problems"),
        "non_story": [{**x, "text": paras[x["p"]][:80]} for x in o.get("non_story", [])],
        "entities": [f"{e['name']} [{e['type']}] 别名={e.get('aliases', [])} {e.get('desc', '')}" for e in o.get("entities", [])],
        "scenes": [f"{s['id']} p{s['from']}-{s['to']} @{s.get('place')} {s.get('time', '')} 在场={s.get('present')}：{s.get('summary')}" for s in o.get("scenes", [])],
        "utterance_sample": [f"{u[0]} {u[1]}→{u[2]} [{u[3]}] …{_window(paras, quotes[u[0]])}…" for u in sample if u[0] in quotes],
        "cue_mismatches_kept": o.get("cue_mismatches", []),
        "speaker_counts": Counter(u[1] for u in utt).most_common(),
        "events": [f"{e['id']} ★{e.get('importance')} {e.get('summary')} → {e.get('effects')}" for e in o.get("events", [])],
        "facts": [f"{f['id']} [{f.get('kind')}] {f.get('text')} | 知情={[k.get('who') + ':' + k.get('how', '') for k in f.get('knowers', [])]}"
                  f"{' 秘密' if f.get('secret') else ''} | 证据「{f.get('quote')}」" for f in o.get("facts", [])],
    }


def _speaker_names(o: dict, narrator: str | None = None) -> dict[str, str]:
    canon: dict[str, str] = {"我": narrator} if narrator else {}
    for e in o.get("entities", []):
        if e.get("type") in ("person", "creature", "group", "other"):
            canon.setdefault(e["name"], e["name"])
            for a in e.get("aliases", []):
                canon.setdefault(a, e["name"])
    return canon


def cue_check(book: Path, recs: list[dict], *, show: int = 20) -> dict:
    """Agreement between the model's speakers and the speakers named by "X道：“…”" / "“…”X道" cues.
    The cues are written independently of the model, so the disagreement rate approximates the
    attribution error rate on cued lines (uncued lines in rapid exchanges are usually harder)."""
    chapters = load_chapters(book)
    profile = load_book(book).get("profile")
    vocab.use_book(book)
    said = cued = 0
    agree: Counter = Counter()
    disagree: list[str] = []
    for r in recs:
        o = r.get("result")
        if not o:
            continue
        paras = chapters[r["idx"]]["paragraphs"]
        _, quotes = number_chapter(paras)
        canon = _speaker_names(o, narrator_for(profile, r["idx"]))
        for q, speaker, _to, how in o.get("utterances", []):
            if how not in ("said", "unknown") or q not in quotes:
                continue
            said += 1
            p, body, start = quotes[q]
            for side, cue in (("pre", cue_speaker(paras[p], start, canon)),
                              ("post", trailing_cue_speaker(paras[p], start, start + len(body), canon))):
                if not cue:
                    continue
                cued += 1
                if cue == canon.get(speaker, speaker):
                    agree[side] += 1
                else:
                    disagree.append(f"ch{r['idx']} {q} {side} 模型={speaker} 线索={cue} "
                                    f"…{paras[p][max(0, start - 20): start]}【{body[:20]}…】{paras[p][start + len(body): start + len(body) + 20]}")
                break
    return {"said_lines": said, "cued": cued, "agree": dict(agree), "disagree": len(disagree),
            "disagree_rate": round(len(disagree) / cued, 4) if cued else None, "disagreements": disagree[:show]}


def uncued_sample(book: Path, n: int = 30, *, seed: int = 0, only: set[int] | None = None) -> list[str]:
    """Random spoken lines that no "X道" cue covers, with the end of the previous paragraph, for
    checking attribution by hand where the automatic check cannot reach."""
    chapters = load_chapters(book)
    profile = load_book(book).get("profile")
    vocab.use_book(book)
    pool: list[str] = []
    for r in load_records(book):
        o = r.get("result")
        if not o or (only is not None and r["idx"] not in only):
            continue
        paras = chapters[r["idx"]]["paragraphs"]
        _, quotes = number_chapter(paras)
        canon = _speaker_names(o, narrator_for(profile, r["idx"]))
        for q, speaker, to, how in o.get("utterances", []):
            if how != "said" or q not in quotes:
                continue
            p, body, start = quotes[q]
            if cue_speaker(paras[p], start, canon) or trailing_cue_speaker(paras[p], start, start + len(body), canon):
                continue
            prev = paras[p - 1][-70:] if p else ""
            pool.append(f"ch{r['idx']} {q} 模型={speaker}→{to}\n  上段…{prev}\n  本段 {paras[p][max(0, start - 60): start]}"
                        f"【{body[:50]}】{paras[p][start + len(body): start + len(body) + 40]}")
    return random.Random(seed).sample(pool, min(n, len(pool)))


def extract_report(book: Path) -> dict:
    recs = load_records(book)
    ok = [r for r in recs if "result" in r]
    attempts = Counter(r.get("attempts") for r in ok)
    problem_kinds: Counter = Counter()
    for r in ok:
        for p in r.get("problems") or []:
            problem_kinds[p.split("：")[0][:30]] += 1
    first_try: Counter = Counter()
    for r in ok:
        for p in (r.get("attempt_problems") or [[]])[0]:
            first_try[p.split("：")[0][:30]] += 1
    tot = lambda k: sum(len((r["result"] or {}).get(k, [])) for r in ok)
    return {
        "chapters": len(recs), "errors": [(r["idx"], r["error"][:120]) for r in recs if "error" in r],
        "attempts": dict(attempts), "unresolved_problem_kinds": problem_kinds.most_common(15),
        "first_attempt_problem_kinds": first_try.most_common(15),
        "chapters_with_unresolved": [r["idx"] for r in ok if r.get("problems")],
        "totals": {k: tot(k) for k in ("non_story", "entities", "scenes", "utterances", "events", "facts")},
        "quotes": sum(r.get("quotes", 0) for r in ok),
        "cue_check": cue_check(book, ok),
    }
