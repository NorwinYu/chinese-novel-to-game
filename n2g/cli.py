"""Command line entry point: python -m n2g <command> ..."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .config import book_dir


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def _llm(book: Path, args):
    from .llm import LLM
    return LLM(cache_dir=book / "build" / "llm_cache", budget=getattr(args, "budget", None), label=args.cmd)


def cmd_init(args) -> None:
    book = book_dir(args.slug)
    book.mkdir(parents=True, exist_ok=True)
    path = book / "book.json"
    meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    meta.update({"slug": args.slug, "source": str(Path(args.source).resolve())})
    if args.title:
        meta["title"] = args.title
    meta.setdefault("headings", {})
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    _print(meta)


def cmd_ingest(args) -> None:
    from .text.ingest import ingest
    summary = ingest(book_dir(args.slug))
    summary.pop("anomalies")
    _print(summary)


def cmd_noise_scan(args) -> None:
    from .text.ingest import read_source
    from .text.noise_scan import scan
    book = book_dir(args.slug)
    raw, _, _ = read_source(book)

    async def main():
        async with _llm(book, args) as llm:
            result = await scan(book, llm, raw)
            result["usage"] = llm.usage_summary()
            return result

    result = asyncio.run(main())
    out = book / "build" / "noise_proposal.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    _print({"written": str(out), "rules": result["test"], "notes": result["notes"],
            "unresolved_problems": result["unresolved_problems"], "usage": result["usage"]})


def cmd_noise_test(args) -> None:
    from .text.clean import load_rules, normalize_lines
    from .text.ingest import load_book, read_source
    from .text.noise_scan import test_rules
    book = book_dir(args.slug)
    raw, _, _ = read_source(book)
    rules = load_rules(Path(args.rules) if args.rules else book / "noise_rules.json")
    _print(test_rules(normalize_lines(raw, load_book(book).get("profile")), rules))


def cmd_evidence(args) -> None:
    from .text.inspect import evidence
    book = book_dir(args.slug)
    out = evidence(book, seed=args.seed)
    path = book / "build" / "evidence.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    _print({"evidence": str(path), "samples": str(book / "build" / "evidence_samples.md"),
            "counts": {k: len(v) for k, v in out["candidates"].items()}})


def cmd_grep(args) -> None:
    from .text.inspect import grep
    _print(grep(book_dir(args.slug), args.pattern, raw_text=args.raw, limit=args.limit, seed=args.seed))


def cmd_show(args) -> None:
    from .text.inspect import show
    _print(show(book_dir(args.slug), args.chapter, start=args.start, count=args.count))


def cmd_anomalies(args) -> None:
    from .text.inspect import anomalies
    items = anomalies(book_dir(args.slug), args.kind)
    _print({"total": len(items), "items": items[: args.limit]})


def cmd_long(args) -> None:
    from .text.inspect import long_chapters
    _print(long_chapters(book_dir(args.slug), factor=args.factor))


def _parse_range(spec: str, n: int) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        if ":" in part:
            a, b = part.split(":")
            out.extend(range(int(a or 0), min(n, int(b or n))))
        else:
            out.append(int(part))
    return [i for i in out if 0 <= i < n]


def cmd_extract(args) -> None:
    import time
    from .extract.chapter import extract_book, revalidate_book
    from .text.ingest import load_chapters
    book = book_dir(args.slug)
    if args.revalidate:
        _print(revalidate_book(book))
        return
    indices = _parse_range(args.chapters, len(load_chapters(book)))

    async def main():
        async with _llm(book, args) as llm:
            t0 = time.monotonic()
            recs = await extract_book(book, llm, indices, force=args.force)
            return recs, llm.usage_summary(), time.monotonic() - t0

    recs, usage, secs = asyncio.run(main())
    _print({"chapters": len(recs), "errors": [r["idx"] for r in recs if "error" in r],
            "with_problems": {r["idx"]: r["problems"][:3] for r in recs if r.get("problems")},
            "usage": usage, "wall_seconds": round(secs, 1)})


def cmd_extract_review(args) -> None:
    from .extract.review import extract_report, review_chapter, uncued_sample
    from .text.ingest import load_chapters
    book = book_dir(args.slug)
    if args.uncued:
        only = set(_parse_range(args.chapters, len(load_chapters(book)))) if args.chapters else None
        print("\n".join(uncued_sample(book, args.uncued, seed=args.seed, only=only)))
    elif args.chapter is not None:
        _print(review_chapter(book, args.chapter, seed=args.seed))
    else:
        _print(extract_report(book))


def cmd_characters(args) -> None:
    from .consolidate.characters import build_characters
    from .text.ingest import load_chapters
    from .gates import pilot_chapters
    book = book_dir(args.slug)
    n = len(load_chapters(book))
    n = len(pilot_chapters(book, n) or range(n))  # tiers are shares of the chapters in scope

    async def main():
        if args.no_llm:
            return await build_characters(book, None, n), {}
        async with _llm(book, args) as llm:
            return await build_characters(book, llm, n), llm.usage_summary()

    out, usage = asyncio.run(main())
    path = book / "build" / "characters.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    chars = out["characters"]
    tiers = {t: sum(1 for c in chars if c["tier"] == t) for t in "SABCG"}
    decided = {k: sum(1 for d in out["merge_decisions"] if d["same"] == k) for k in ("yes", "no", "unsure", "blocked_by_rules", "blocked_by_conflict")}
    _print({"written": str(path), "protagonist_guess": out["protagonist_guess"], "tiers": tiers,
            "merge_decisions": decided, "usage": usage,
            "S_and_A": [f"{c['tier']} {c['name']} ch={c['chapters']} said={c['said']} 名字={list(c['names'])[:5]}"
                        for c in chars if c["tier"] in "SA"][:80]})


def cmd_characters_review(args) -> None:
    book = book_dir(args.slug)
    out = json.loads((book / "build" / "characters.json").read_text(encoding="utf-8"))
    if args.name:
        _print([c for c in out["characters"] if args.name in c["names"] or args.name in c["aliases"]])
    else:
        ds = out["merge_decisions"]
        _print({"yes": [f"{d['A']} = {d['B']}：{d['reason']}" for d in ds if d["same"] == "yes"],
                "unsure": [f"{d['A']} ? {d['B']}：{d['reason']}" for d in ds if d["same"] == "unsure"],
                "no_sample": [f"{d['A']} ≠ {d['B']}：{d['reason']}" for d in ds if d["same"] == "no"][:args.limit]})


def cmd_lexicon(args) -> None:
    from .consolidate.lexicon import build_lexicon
    _print(build_lexicon(book_dir(args.slug), seed=args.seed))


def cmd_run(args) -> None:
    """Run an agent-written analysis or one-off script with the package importable and `book` set."""
    import runpy
    script = Path(args.script)
    sys.argv = [str(script), *args.script_args]
    runpy.run_path(str(script), init_globals={"book": book_dir(args.slug), "slug": args.slug}, run_name="__main__")


def cmd_timeline(args) -> None:
    from .knowledge.cards import book_stages, timeline
    from .text.ingest import load_chapters
    book = book_dir(args.slug)
    print("\n".join(timeline(book, min_importance=args.min_importance, start=args.start, end=args.end)))
    print("\n当前阶段划分：", json.dumps(book_stages(book, len(load_chapters(book))), ensure_ascii=False))


def cmd_cards(args) -> None:
    from .knowledge.cards import build_cards, plan_cards, review_card
    book = book_dir(args.slug)
    names = args.names.split(",") if args.names else None
    if args.review:
        _print(review_card(book, args.review, args.stage))
        return
    if args.dry_run:
        _print(plan_cards(book, tiers=args.tiers, names=names, force=args.force))
        return

    async def main():
        async with _llm(book, args) as llm:
            rep = await build_cards(book, llm, tiers=args.tiers, names=names, force=args.force)
            rep["usage"] = llm.usage_summary()
            return rep
    _print(asyncio.run(main()))


def cmd_ledger(args) -> None:
    from .knowledge.ledger import build_ledger, ledger_report
    book = book_dir(args.slug)
    if args.name:
        _print(ledger_report(book, name=args.name, chapter=args.chapter))
        return
    build_ledger(book)
    _print(ledger_report(book))


def cmd_golden(args) -> None:
    from .golden import add_golden, load_golden
    book = book_dir(args.slug)
    if args.add:
        _print(add_golden(book, json.loads(args.add)))
    else:
        items = load_golden(book)
        _print({"items": len(items), "by_type": {t: sum(1 for g in items if g["type"] == t) for t in sorted({g["type"] for g in items})},
                "list": items[-args.limit:]})


def cmd_eval(args) -> None:
    from .golden import evaluate
    _print(evaluate(book_dir(args.slug), args.type))


def cmd_profile(args) -> None:
    from .profile import profile, set_profile
    book = book_dir(args.slug)
    if args.set:
        _print(set_profile(book, args.set))
    else:
        _print(profile(book))


def cmd_places(args) -> None:
    from .knowledge.places import build_places
    _print(build_places(book_dir(args.slug), min_scenes=args.min_scenes))


def cmd_rules_test(args) -> None:
    from .knowledge.rules import test_rules
    _print(test_rules(book_dir(args.slug), samples=args.samples, seed=args.seed))


def cmd_vocab_test(args) -> None:
    from .vocab import test_vocab
    _print(test_vocab(book_dir(args.slug), json.loads(args.spec) if args.spec else None))


def cmd_vocab_suggest(args) -> None:
    from .vocab_suggest import suggest
    out = suggest(book_dir(args.slug), top=args.top)
    _print({k: v for k, v in out.items() if not args.key or k in args.key})


def cmd_gate(args) -> None:
    from .gates import STAGES, gate
    book = book_dir(args.slug)
    results = [gate(book, s) for s in (STAGES if args.stage == "all" else [args.stage])]
    _print(results if len(results) > 1 else results[0])
    if not all(r["passed"] for r in results):
        sys.exit(1)


def cmd_status(args) -> None:
    from .gates import status
    _print(status(book_dir(args.slug)))


def cmd_plugin_test(args) -> None:
    from .plugins import test_plugins
    _print(test_plugins(book_dir(args.slug), args.hook, args.plugin, samples=args.samples, seed=args.seed))


def cmd_game(args) -> None:
    from .game.build import build_game, game_report
    book = book_dir(args.slug)
    g = build_game(book)
    _print({"written": str(book / "build" / "game.json"), **game_report(book, g)})


def cmd_talk(args) -> None:
    """One or more turns with a character at a chapter, from the command line."""
    from .game.npc import World, beat_for, build_messages, find_character, reply
    book = book_dir(args.slug)
    w = World(book)
    cid = find_character(w, args.name)
    beat = args.beat if args.beat is not None else beat_for(w, cid, args.chapter)
    if args.show_context:
        msgs, _ = build_messages(w, cid, beat, None, [], args.say[0])
        print(msgs[0]["content"])
        return

    async def run():
        history, out = [], []
        async with _llm(book, args) as llm:
            for text in args.say:
                r = await reply(w, llm, cid, beat, None, history, text)
                history += [{"role": "player", "text": text}, {"role": "npc", "text": r["say"], "act": r["act"]}]
                out.append({"player": text, **r})
            return {"character": w.name(cid), "beat": beat, "chapter": w.game["beats"][beat]["chapter"], "turns": out,
                    "usage": llm.usage_summary()}
    _print(asyncio.run(run()))

def cmd_looks(args) -> None:
    from .game.looks import build_looks, load_looks
    from .text.ingest import load_book
    book = book_dir(args.slug)
    if args.show:
        lk = load_looks(book)
        w = json.loads((book / "build" / "game.json").read_text(encoding="utf-8"))
        cid = next((c for c, v in w["characters"].items() if v["name"] == args.show), None)
        _print(lk["characters"].get(cid) or lk["places"].get(args.show) or lk["events"].get(args.show) or "not found")
        return

    async def run():
        async with _llm(book, args) as llm:
            setting = (load_book(book).get("art") or {}).get("setting", "")
            rep = await build_looks(book, llm, setting=setting, force=args.force)
            rep["usage"] = llm.usage_summary()
            return rep
    _print(asyncio.run(run()))


def cmd_design(args) -> None:
    from .game.design import build_design
    from .game.sim import simulate
    book = book_dir(args.slug)
    if not args.check:
        async def run():
            async with _llm(book, args) as llm:
                rep = await build_design(book, llm, force=args.force, rounds=args.balance_rounds, log=lambda m: print(m, file=sys.stderr, flush=True))
                rep["usage"] = llm.usage_summary()
                return rep
        _print(asyncio.run(run()))
    _print({"simulation": simulate(book, runs=args.runs)})


def cmd_art(args) -> None:
    from .art.build import contact_sheet, plan_art, render_art
    from .art.client import ImageClient
    book = book_dir(args.slug)
    if args.action == "sheet":
        _print({"sheet": str(contact_sheet(book, args.kind))})
        return
    if args.action == "plan":
        async def plan():
            async with _llm(book, args) as llm:
                rep = await plan_art(book, llm, force=args.force)
                rep["usage"] = llm.usage_summary()
                return rep
        _print(asyncio.run(plan()))
        return

    async def render():
        async with ImageClient() as client:
            return await render_art(book, client, only=args.only.split(",") if args.only else None, limit=args.limit,
                                    log=lambda m: print(m, file=sys.stderr, flush=True))
    _print(asyncio.run(render()))


def cmd_probe(args) -> None:
    from .game.probe import run_probes
    book = book_dir(args.slug)

    async def run():
        async with _llm(book, args) as llm:
            rep = await run_probes(book, llm, only=args.only.split(",") if args.only else None,
                                   log=lambda m: print(m, file=sys.stderr, flush=True))
            rep["usage"] = llm.usage_summary()
            return rep
    _print(asyncio.run(run()))


def cmd_play(args) -> None:
    import uvicorn
    from .game.server import create_app
    book = book_dir(args.slug)
    app = create_app(book, lambda: _llm(book, args))
    print(f"open http://127.0.0.1:{args.port}/", file=sys.stderr)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")

def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="n2g")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("game", help="stage 6: build/game.json — beats, anchors, map, trail — from the earlier stages")
    s.add_argument("--slug", required=True)
    s.set_defaults(fn=cmd_game)
    s = sub.add_parser("talk", help="talk to a character as the protagonist at a chapter (each --say is one turn)")
    s.add_argument("--slug", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--chapter", type=int, default=10**9, help="0-based chapter; the last beat by then where they can be talked to")
    s.add_argument("--beat", type=int)
    s.add_argument("--say", action="append", required=True)
    s.add_argument("--show-context", action="store_true", help="print what the character is given, no model call")
    s.set_defaults(fn=cmd_talk)
    s = sub.add_parser("looks", help="stage 7: appearance parameters for the 3D scenes (characters, places, anchor stills)")
    s.add_argument("--slug", required=True)
    s.add_argument("--force", action="store_true")
    s.add_argument("--show", help="print one character name, node name or anchor beat")
    s.set_defaults(fn=cmd_looks)
    s = sub.add_parser("design", help="stage 6b: stats, goal, free-time actions, intel, a choice at every anchor, endings; then simulate")
    s.add_argument("--slug", required=True)
    s.add_argument("--force", action="store_true", help="ask the model again even for unchanged inputs")
    s.add_argument("--check", action="store_true", help="no model calls: validate and simulate the current design")
    s.add_argument("--runs", type=int, default=60, help="random playthroughs in the simulation")
    s.add_argument("--balance-rounds", type=int, default=3, help="simulate → model adjusts numbers, at most this many times")
    s.set_defaults(fn=cmd_design)
    s = sub.add_parser("art", help="stage 7, optional: AI images on N2G_IMAGE_URL that replace the 3D pictures")
    s.add_argument("action", choices=["plan", "render", "sheet"])
    s.add_argument("--slug", required=True)
    s.add_argument("--force", action="store_true", help="plan: rewrite every prompt")
    s.add_argument("--only", help="render: comma-separated item ids, character names or node names")
    s.add_argument("--limit", type=int, help="render: at most this many images this run")
    s.add_argument("--kind", default="portrait", choices=["portrait", "backdrop"], help="sheet: which images")
    s.set_defaults(fn=cmd_art)
    s = sub.add_parser("probe", help="stage 8: run golden probe items (spoiler/memory) against the NPC runtime")
    s.add_argument("--slug", required=True)
    s.add_argument("--only", help="comma-separated golden ids; other results are kept")
    s.set_defaults(fn=cmd_probe)
    s = sub.add_parser("play", help="play the book in a browser: visual novel, map, talking to characters (local only)")
    s.add_argument("--slug", required=True)
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=cmd_play)
    s = sub.add_parser("run", help="run your own script for this book: globals book (Path) and slug; n2g is importable")
    s.add_argument("--slug", required=True)
    s.add_argument("script")
    s.add_argument("script_args", nargs=argparse.REMAINDER)
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("timeline", help="major events in chapter order, and the current stage split")
    s.add_argument("--slug", required=True)
    s.add_argument("--min-importance", type=int, default=3)
    s.add_argument("--start", type=int, default=0)
    s.add_argument("--end", type=int)
    s.set_defaults(fn=cmd_timeline)
    s = sub.add_parser("cards", help="write staged character cards, or --review NAME")
    s.add_argument("--slug", required=True)
    s.add_argument("--tiers", default="SA")
    s.add_argument("--names", help="comma-separated names instead of --tiers")
    s.add_argument("--force", action="store_true")
    s.add_argument("--review")
    s.add_argument("--stage", type=int)
    s.add_argument("--dry-run", action="store_true", help="no model calls: which cards would be rewritten and why, and the estimated cost")
    s.set_defaults(fn=cmd_cards)
    s = sub.add_parser("ledger", help="build the knowledge ledger, or show what --name knows at --chapter")
    s.add_argument("--slug", required=True)
    s.add_argument("--name")
    s.add_argument("--chapter", type=int)
    s.set_defaults(fn=cmd_ledger)
    s = sub.add_parser("plugin-test", help="run games/<slug>/plugins on the whole book and show what they change")
    s.add_argument("--slug", required=True)
    s.add_argument("--hook", default="repair_line")
    s.add_argument("--plugin", action="append", help="only this plugin file (repeatable)")
    s.add_argument("--samples", type=int, default=12)
    s.add_argument("--seed", type=int, default=0)
    s.set_defaults(fn=cmd_plugin_test)
    s = sub.add_parser("golden", help="list the book's hand-verified judgments, or --add one (JSON)")
    s.add_argument("--slug", required=True)
    s.add_argument("--add", help='e.g. {"type":"same","a":"甲","b":"乙","expect":false,"note":"ch3 原文……"}')
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(fn=cmd_golden)
    s = sub.add_parser("eval", help="recheck every golden judgment against the current build")
    s.add_argument("--slug", required=True)
    s.add_argument("--type")
    s.set_defaults(fn=cmd_eval)
    s = sub.add_parser("profile", help="stage 0: measure narrative person, quotes, wrapping, scale; or --set decisions")
    s.add_argument("--slug", required=True)
    s.add_argument("--set", nargs="+", metavar="KEY=VALUE", help='e.g. person=first narrator=张三 quotes=straight')
    s.set_defaults(fn=cmd_profile)
    s = sub.add_parser("places", help="normalise scene places into map nodes with parents; writes build/places.json")
    s.add_argument("--slug", required=True)
    s.add_argument("--min-scenes", type=int, default=3)
    s.set_defaults(fn=cmd_places)
    s = sub.add_parser("rules-test", help="what games/<slug>/knowledge_rules.json adds to scenes and knowers, whole book")
    s.add_argument("--slug", required=True)
    s.add_argument("--samples", type=int, default=8)
    s.add_argument("--seed", type=int, default=0)
    s.set_defaults(fn=cmd_rules_test)
    s = sub.add_parser("vocab-test", help="names whose descriptor verdict games/<slug>/vocab.json (or --spec JSON) flips")
    s.add_argument("--slug", required=True)
    s.add_argument("--spec", help='try a vocab without writing it, e.g. {"descriptor_tails":["司机"]}')
    s.set_defaults(fn=cmd_vocab_test)
    s = sub.add_parser("vocab-suggest", help="candidate words for games/<slug>/vocab.json, mined from the book's extraction")
    s.add_argument("--slug", required=True)
    s.add_argument("--key", nargs="+", help="only these vocab keys")
    s.add_argument("--top", type=int, default=30)
    s.set_defaults(fn=cmd_vocab_suggest)
    from .gates import STAGES as _STAGES
    s = sub.add_parser("gate", help="check a stage's metrics and golden items; records the verdict in build/state.json")
    s.add_argument("stage", choices=(*_STAGES, "all"))
    s.add_argument("--slug", required=True)
    s.set_defaults(fn=cmd_gate)
    s = sub.add_parser("status", help="per stage: gate verdict and whether its inputs changed since")
    s.add_argument("--slug", required=True)
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("init", help="register a source TXT under games/<slug>")
    s.add_argument("source")
    s.add_argument("--slug", required=True)
    s.add_argument("--title")
    s.set_defaults(fn=cmd_init)
    for name, fn, help_ in (("ingest", cmd_ingest, "clean + split into chapters"),
                            ("noise-scan", cmd_noise_scan, "propose source-specific noise rules")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("--slug", required=True)
        s.set_defaults(fn=fn)
    s = sub.add_parser("noise-test", help="test noise rules against the whole book")
    s.add_argument("--slug", required=True)
    s.add_argument("--rules", help="rules file (default: games/<slug>/noise_rules.json)")
    s.set_defaults(fn=cmd_noise_test)
    s = sub.add_parser("evidence", help="statistics and excerpts for the agent to review")
    s.add_argument("--slug", required=True)
    s.add_argument("--seed", type=int, default=0)
    s.set_defaults(fn=cmd_evidence)
    s = sub.add_parser("grep", help="count and show a regex in the chapters (or --raw source)")
    s.add_argument("--slug", required=True)
    s.add_argument("pattern")
    s.add_argument("--raw", action="store_true")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--seed", type=int, default=0)
    s.set_defaults(fn=cmd_grep)
    s = sub.add_parser("show", help="print paragraphs of one chapter")
    s.add_argument("--slug", required=True)
    s.add_argument("--chapter", type=int, required=True)
    s.add_argument("--start", type=int, default=0)
    s.add_argument("--count", type=int, default=40)
    s.set_defaults(fn=cmd_show)
    s = sub.add_parser("anomalies", help="numbering anomalies with neighbouring headings")
    s.add_argument("--slug", required=True)
    s.add_argument("--kind")
    s.add_argument("--limit", type=int, default=60)
    s.set_defaults(fn=cmd_anomalies)
    s = sub.add_parser("long-chapters", help="unusually long chapters and their short lines (missed headings)")
    s.add_argument("--slug", required=True)
    s.add_argument("--factor", type=float, default=1.8)
    s.set_defaults(fn=cmd_long)
    s = sub.add_parser("extract", help="per-chapter structured extraction")
    s.add_argument("--slug", required=True)
    s.add_argument("--chapters", default="0:", help="e.g. 0:20 or 0,5,9 or 100:")
    s.add_argument("--revalidate", action="store_true", help="re-check stored results with the current validator, no model calls")
    s.add_argument("--force", action="store_true", help="redo chapters that already have output")
    s.set_defaults(fn=cmd_extract)
    s = sub.add_parser("extract-review", help="whole-book extraction report, or one chapter next to its text")
    s.add_argument("--slug", required=True)
    s.add_argument("--chapter", type=int)
    s.add_argument("--uncued", type=int, default=0, help="print N random spoken lines without a speech cue, for hand checking")
    s.add_argument("--chapters", help="limit --uncued to these chapters, e.g. 1200:1400 or 3,8")
    s.add_argument("--seed", type=int, default=0)
    s.set_defaults(fn=cmd_extract_review)
    s = sub.add_parser("characters", help="merge per-chapter entities into a book-level character registry")
    s.add_argument("--slug", required=True)
    s.add_argument("--no-llm", action="store_true", help="deterministic merge only")
    s.set_defaults(fn=cmd_characters)
    s = sub.add_parser("characters-review", help="merge decisions, or one character's record")
    s.add_argument("--slug", required=True)
    s.add_argument("--name")
    s.add_argument("--limit", type=int, default=40)
    s.set_defaults(fn=cmd_characters_review)
    s = sub.add_parser("lexicon", help="book user dictionary for jieba, with a before/after segmentation check")
    s.add_argument("--slug", required=True)
    s.add_argument("--seed", type=int, default=0)
    s.set_defaults(fn=cmd_lexicon)
    for name, s in sub.choices.items():
        if name in ("noise-scan", "extract", "characters", "cards", "talk", "play", "art", "probe", "looks", "design"):
            s.add_argument("--budget", type=float, help="stop starting paid calls after this many USD (default N2G_BUDGET_USD)")
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
