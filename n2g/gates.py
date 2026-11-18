"""Stage gates: measurable conditions a stage must meet before the next one starts.

`n2g gate <stage>` computes the stage's metrics from build outputs (no model calls), compares them with
thresholds (defaults below, per-book overrides in book.json "gates": {"<stage>": {"<metric>": value}}),
reruns the golden items that belong to the stage, and records the verdict in build/state.json together
with fingerprints of the stage's inputs. `n2g status` shows, per stage, whether the gate passed and
whether any input changed since (stale), so a rebuild is never mistaken for a checked result."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .config import ROOT

STAGES = ("clean", "extract", "characters", "ledger", "cards", "game", "design", "accept")

# metric -> (comparison, default threshold). "max": value must be <= threshold, "min": >=.
THRESHOLDS = {
    "clean": {"chapters": ("min", 1)},
    "extract": {"coverage": ("min", 1.0), "error_chapters": ("max", 0), "unresolved_problem_rate": ("max", 0.005),
                "cue_disagree_rate": ("max", 0.02), "unverified_fact_ratio": ("max", 0.02)},
    "characters": {"s_tier": ("min", 1), "protagonist_found": ("min", 1)},
    "ledger": {"witness_not_present_ratio": ("max", 0.05)},
    "cards": {"coverage": ("min", 1.0), "rewrite_needed": ("max", 0)},
    "game": {"node_coverage": ("min", 0.5), "play_share": ("min", 0.3), "anchors": ("min", 3)},
    "design": {"design_problems": ("max", 0), "choice_coverage": ("min", 1.0), "deadlocks": ("max", 0),
               "canon_survives": ("min", 1), "canon_clipped": ("max", 0), "win_reachable": ("min", 1), "lose_reachable": ("min", 1), "random_win_share": ("max", 0.9),
               "intel_obtained_share": ("min", 1.0)},
    "accept": {"probes": ("min", 2), "probe_failures": ("max", 0)},
}
MIN_GOLDEN = 2  # a stage whose golden set is empty would pass on metrics alone
GOLDEN_TYPES = {"extract": ("speaker",), "characters": ("same", "tier"), "ledger": ("present", "knows"),
                "cards": ("card",), "game": ("anchor",)}

# what each stage reads; a change in any of these makes its recorded gate stale
INPUTS = {
    "clean": ("source", "book.json:source,headings,extra_chapter_patterns", "noise_rules.json", "plugins/", "code:n2g/text/"),
    "extract": ("build/chapters.jsonl", "extract_guide.md", "book.json:profile,pilot", "code:n2g/extract/chapter.py"),
    "characters": ("build/extract/", "merge_rules.json", "vocab.json", "book.json:protagonist", "code:n2g/consolidate/"),
    "ledger": ("build/characters.json", "build/extract/", "plugins/", "knowledge_rules.json", "vocab.json",
               "code:n2g/knowledge/ledger.py"),
    "cards": ("build/ledger.json", "build/characters.json", "book.json:stages,tier_limits", "vocab.json",
              "code:n2g/knowledge/cards.py"),
    "game": ("build/ledger.json", "build/characters.json", "build/places.json", "build/cards/",
             "book.json:game,protagonist,stages", "code:n2g/game/build.py"),
    "design": ("build/game.json", "build/design.json", "design.json", "book.json:design", "code:n2g/game/design.py",
               "code:n2g/game/engine.py", "code:n2g/game/sim.py"),
    "accept": ("build/game.json", "golden.jsonl", "code:n2g/game/"),
}


def _hash_path(p: Path) -> str:
    if not p.exists():
        return "absent"
    h = hashlib.sha1()
    if p.is_dir():
        for f in sorted(x for x in p.rglob("*") if x.is_file() and "__pycache__" not in x.parts):
            st = f.stat()
            h.update(f"{f.relative_to(p)}:{st.st_size}:{st.st_mtime_ns}\n".encode())
    elif p.stat().st_size < 8_000_000:
        h.update(p.read_bytes())
    else:
        st = p.stat()
        h.update(f"{st.st_size}:{st.st_mtime_ns}".encode())
    return h.hexdigest()[:12]


def fingerprint(book: Path, stage: str) -> dict[str, str]:
    meta = json.loads((book / "book.json").read_text(encoding="utf-8"))
    out = {}
    for spec in INPUTS[stage]:
        if spec == "source":
            out[spec] = _hash_path(Path(meta["source"]))
        elif spec.startswith("book.json:"):
            keys = spec.split(":", 1)[1].split(",")
            blob = json.dumps({k: meta.get(k) for k in keys}, ensure_ascii=False, sort_keys=True)
            out[spec] = hashlib.sha1(blob.encode()).hexdigest()[:12]
        elif spec.startswith("code:"):
            out[spec] = _hash_path(ROOT / spec[5:])
        else:
            out[spec] = _hash_path(book / spec)
    return out


def _state_path(book: Path) -> Path:
    return book / "build" / "state.json"


def load_state(book: Path) -> dict:
    p = _state_path(book)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def pilot_chapters(book: Path, n: int) -> set[int] | None:
    """book.json "pilot": "0:30" (or "0,5,9") while only a trial range is extracted; delete it for the full run."""
    spec = json.loads((book / "book.json").read_text(encoding="utf-8")).get("pilot")
    if not spec:
        return None
    out: set[int] = set()
    for part in str(spec).split(","):
        if ":" in part:
            a, b = part.split(":")
            out |= set(range(int(a or 0), int(b) if b else n))
        else:
            out.add(int(part))
    return {i for i in out if i < n}


def _metrics(book: Path, stage: str) -> dict:
    b = book / "build"
    if stage == "clean":
        rep = json.loads((b / "ingest_report.json").read_text(encoding="utf-8"))
        return {"chapters": rep["chapter_count"], "anomalies": sum(rep.get("anomaly_counts", {}).values())}
    if stage == "extract":
        from .extract.review import cue_check, load_records
        from .text.ingest import load_chapters
        n = len(load_chapters(book))
        recs = load_records(book)
        pilot = pilot_chapters(book, n)
        if pilot is not None:
            recs, n = [r for r in recs if r["idx"] in pilot], len(pilot)
        ok = [r for r in recs if r.get("result")]
        facts = [f for r in ok for f in r["result"].get("facts", [])]
        cue = cue_check(book, ok)
        # per labelled item, so that one leftover problem in a 20-chapter book weighs what it should
        items = sum(len(r["result"].get(k, [])) for r in ok for k in ("entities", "scenes", "utterances", "events", "facts"))
        return {"chapters": n, **({"pilot": True} if pilot is not None else {}), "coverage": round(len(ok) / max(1, n), 4),
                "error_chapters": sum(1 for r in recs if "error" in r),
                "unresolved_chapter_ratio": round(sum(1 for r in ok if r.get("problems")) / max(1, len(ok)), 4),
                "unresolved_problem_rate": round(sum(len(r.get("problems") or []) for r in ok) / max(1, items), 5),
                "cue_disagree_rate": cue.get("disagree_rate", 0.0), "cued_lines": cue.get("cued", 0),
                "unverified_fact_ratio": round(sum(1 for f in facts if f.get("unverified")) / max(1, len(facts)), 4)}
    if stage == "characters":
        chars = json.loads((b / "characters.json").read_text(encoding="utf-8"))["characters"]
        meta = json.loads((book / "book.json").read_text(encoding="utf-8"))
        hero = meta.get("protagonist", "")
        tiers = {t: sum(1 for c in chars if c["tier"] == t) for t in "SABCG"}
        found = any(c["tier"] in "SA" and (c["name"] == hero or hero in c["names"]) for c in chars) if hero else False
        return {"s_tier": tiers["S"], "a_tier": tiers["A"], "characters": len(chars), "protagonist_found": int(found)}
    if stage == "ledger":
        from .knowledge.ledger import ledger_report
        rep = ledger_report(book)
        return {"facts": rep.get("facts", 0), "events": rep["events"], "scenes": rep["scenes"],
                "witness_not_present_ratio": rep["witness_not_present_ratio"],
                "unresolved_knower_ratio": round(rep.get("unresolved_knower", 0) / max(1, rep.get("facts", 0)), 4)}
    if stage == "cards":
        from .knowledge.cards import plan_cards
        plan = plan_cards(book)
        cards = plan["cards"]
        expected = sum(v for k, v in cards.items() if k != "no_material")
        return {"cards_expected": expected, "coverage": round(cards.get("keep", 0) / max(1, expected), 4),
                "rewrite_needed": plan["rewrite"], "rewrite_estimate_usd": plan["estimate"]["usd"],
                "rewrite_examples": plan["rewrite_list"][:5]}
    if stage == "game":
        from .game.build import game_report
        return game_report(book)
    if stage == "design":
        from .game.sim import simulate
        return simulate(book)
    if stage == "accept":
        from .game.probe import probe_report
        rep = probe_report(book)
        failed = [r for r in rep["results"] if not r["passed"]]
        return {"probes": len(rep["results"]), "probe_failures": len(failed),
                "future_name_flags": sum("future_name" in r["flags"] for r in rep["results"]),
                "failed": [f"{r['id']} {r['who']} ch{r['chapter']}: {r['detail']}" for r in failed[:10]],
                "ran_at": rep["ran_at"]}
    raise ValueError(stage)


def gate(book: Path, stage: str) -> dict:
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}")
    meta = json.loads((book / "book.json").read_text(encoding="utf-8"))
    limits = {**{k: v[1] for k, v in THRESHOLDS[stage].items()},
              **{k: v for k, v in meta.get("gates", {}).get(stage, {}).items() if k != "golden_items"}}
    try:
        metrics = _metrics(book, stage)
    except FileNotFoundError as e:
        return {"stage": stage, "passed": False, "failures": [f"output missing: {e.filename}"]}
    failures = []
    for k, limit in limits.items():
        how = THRESHOLDS[stage].get(k, ("max", None))[0]
        v = metrics.get(k)
        if v is None or (how == "max" and v > limit) or (how == "min" and v < limit):
            failures.append(f"{k} = {v}, needs {'<=' if how == 'max' else '>='} {limit}")
    golden = None
    if stage in GOLDEN_TYPES:
        from .golden import evaluate
        res = [evaluate(book, t) for t in GOLDEN_TYPES[stage]]
        golden = {k: sum(r.get(k, 0) for r in res) for k in ("items", "pass", "fail", "vacuous", "missing", "known_gap")}
        need = meta.get("gates", {}).get(stage, {}).get("golden_items", MIN_GOLDEN)
        if golden["items"] < need:
            failures.append(f"golden items for this stage ({'/'.join(GOLDEN_TYPES[stage])}) = {golden['items']}, needs >= {need}: "
                            "add judgments you checked in the text with `n2g golden --add`")
        failures += [f"golden {x['id']} {x['type']}: {x['status']} ({x['detail'][:80]})"
                     for r in res for x in r["not_passing"] if x["status"] in ("fail", "missing")]
    rec = {"passed": not failures, "checked": time.strftime("%Y-%m-%d %H:%M:%S"), "metrics": metrics,
           "limits": limits, "golden": golden, "failures": failures, "inputs": fingerprint(book, stage)}
    state = load_state(book)
    state[stage] = rec
    _state_path(book).write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"stage": stage, **rec}


def status(book: Path) -> dict:
    state = load_state(book)
    out, upstream_ok = {}, True
    for stage in STAGES:
        rec = state.get(stage)
        if not rec:
            out[stage] = "no gate run" + ("" if upstream_ok else " (an earlier stage has not passed)")
            upstream_ok = False
            continue
        now = fingerprint(book, stage)
        changed = [k for k, v in now.items() if rec["inputs"].get(k) != v]
        verdict = ("passed" if rec["passed"] else f"FAILED: {rec['failures'][:3]}") + \
            (" (pilot chapters only)" if rec.get("metrics", {}).get("pilot") else "")
        out[stage] = f"{verdict} at {rec['checked']}" + (f"; stale, changed since: {changed}" if changed else "")
        upstream_ok = upstream_ok and rec["passed"] and not changed
    spend = book / "build" / "spend.jsonl"
    runs = [json.loads(x) for x in spend.read_text(encoding="utf-8").splitlines()] if spend.exists() else []
    from .golden import load_golden
    return {"stages": out, "spend_logged_usd": round(sum(r["cost"] for r in runs), 3), "runs_logged": len(runs),
            "golden_items": len(load_golden(book))}
