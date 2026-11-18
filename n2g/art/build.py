"""Stage ⑦: portraits and backdrops from the local image server (N2G_IMAGE_URL). Optional.

  plan    one cheap model call per item turns what the book says into an image prompt → build/art/plan.json
  render  submits planned items one at a time (the server is serial; minutes per image), skips files whose
          prompt is unchanged, so it can be stopped and resumed → build/art/<id>.png, build/art/manifest.json
  sheet   a contact sheet of rendered images, for the agent to look at before the game uses them

What gets drawn:
  portrait  every talkable character, from the appearance in their earliest card; with "per_stage" one per
            stage card. The game shows the portrait of the last stage that ended before the current one (a
            card describes the end of its stage), or the earliest one.
  backdrop  map nodes the protagonist visits at least "min_visits" times, from the node's name, the place
            strings that map to it and a few scene summaries there. No people in backdrops.
  event     one illustration per anchor (a major turning point of the protagonist), from the event summary
            and the looks of the people in it, described by appearance rather than name.
  expression  for every portrait of a character one can talk to, one variant per mood in "expressions",
            drawn with the portrait as the reference image so the face and clothes stay the same. No model
            call; rendered after the portraits.

book.json "art" (the agent writes "setting" and "style"; nothing book-specific lives in this file):
  {"setting": "时代、地域、社会环境，一两句",  "style": "画风，所有图共用",
   "per_stage": false, "min_visits": 2, "max_portraits": 40, "max_backdrops": 40, "max_events": 40,
   "events": true, "expressions": ["高兴", "生气", "难过", "惊讶"],
   "portrait_preset": "portrait", "backdrop_preset": "1k", "event_preset": "1k"}"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path

from ..game.build import load_game
from ..knowledge.ledger import load_ledger
from ..text.ingest import load_book

PORTRAIT = """你为图像生成模型写人物立绘的提示词。根据小说对这个人的描写，写一段中文外观描述：
性别、年龄、身材、脸型五官、发型、从上到下每一件衣服（上衣、下装、鞋袜都要写到）、随身物件。
书里没写的，按身份、处境和时代背景合理补全，不要与描写矛盾。只写外观：不写名字、情节、心理、对白，不要让图里出现文字。
最后加上：全身立绘，自然站姿，正面略侧，纯色浅灰背景。
输出 JSON：{"prompt": "..."}"""

EVENT = """你为图像生成模型写小说关键情节插图的提示词。根据事件摘要和在场人物的外貌，写一段中文画面描述：
场景与环境、每个人物的外貌和此刻的动作神情（用外貌指代人物，不写名字）、构图与镜头、光线气氛。符合时代背景。
画面里不要有文字。只画这一刻，不画之后的事。
输出 JSON：{"prompt": "..."}"""

# expression variants, drawn from the neutral portrait as a reference image
MOODS = {"高兴": "眉眼舒展，嘴角上扬，面带笑意", "生气": "皱眉瞪眼，嘴角下撇，脸色阴沉",
         "难过": "眉头低垂，眼神黯淡，嘴唇紧抿", "惊讶": "眼睛睁大，眉毛上挑，嘴微微张开"}
MOOD_KEYS = {"高兴": "happy", "生气": "angry", "难过": "sad", "惊讶": "surprised"}

BACKDROP = """你为图像生成模型写游戏场景背景图的提示词。根据小说里这个地点的名字和发生在这里的场景，写一段中文画面描述：
地点类型、建筑或地形、材质与陈设、光线、季节天气（取最常见的情形）、气氛。符合时代背景。
画面里不要有人物，不要有文字。不写情节。
输出 JSON：{"prompt": "..."}"""


def art_settings(book: Path) -> dict:
    a = dict(load_book(book).get("art") or {})
    a.setdefault("per_stage", False)
    a.setdefault("min_visits", 2)
    a.setdefault("max_portraits", 40)
    a.setdefault("max_backdrops", 40)
    a.setdefault("portrait_preset", "portrait")
    a.setdefault("backdrop_preset", "1k")
    a.setdefault("expressions", list(MOODS))
    a.setdefault("events", True)
    a.setdefault("max_events", 40)
    a.setdefault("event_preset", "1k")
    unknown = set(a["expressions"]) - set(MOODS)
    if unknown:
        raise ValueError(f'book.json art.expressions: unknown moods {sorted(unknown)}; known: {list(MOODS)}')
    return a


def art_dir(book: Path) -> Path:
    return book / "build" / "art"


def load_plan(book: Path) -> dict:
    p = art_dir(book) / "plan.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"items": []}


def load_manifest(book: Path) -> dict:
    p = art_dir(book) / "manifest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _digest(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def _targets(book: Path, a: dict) -> list[dict]:
    g = load_game(book)
    out = []
    for cid, c in list(g["characters"].items())[: a["max_portraits"]]:
        p = book / "build" / "cards" / f"{cid}.json"
        stages = [s for s in json.loads(p.read_text(encoding="utf-8"))["stages"] if s.get("card")] if p.exists() else []
        if not stages:
            continue
        for s in stages if a["per_stage"] else stages[:1]:
            out.append({"id": f"portrait_{cid}_s{s['stage']}", "kind": "portrait", "cid": cid, "name": c["name"],
                        "stage": s["stage"], "card": s["card"], "chapter": s["to"]})
    led = load_ledger(book)
    places = json.loads((book / "build" / "places.json").read_text(encoding="utf-8"))
    variants = {n["name"]: n.get("variants", []) for n in places["nodes"]}
    scene_node = places.get("scene_node", {})
    summaries: dict[str, list[str]] = {}
    for s in led["scenes"]:
        if (n := scene_node.get(s["id"])):
            summaries.setdefault(n, []).append(s["summary"])
    nodes = sorted((n for n in g["map"]["nodes"] if n["visits"] >= a["min_visits"]), key=lambda n: -n["visits"])
    for n in nodes[: a["max_backdrops"]]:
        out.append({"id": f"backdrop_{_digest(n['name'])[:8]}", "kind": "backdrop", "node": n["name"], "parent": n["parent"],
                    "variants": variants.get(n["name"], []), "scenes": summaries.get(n["name"], [])[:4]})
    if a["events"]:
        events = {e["id"]: e for e in led["events"]}
        looks = {t["cid"]: t["card"].get("appearance", "") for t in out if t["kind"] == "portrait"}
        for an in g["anchors"][: a["max_events"]]:
            who = [c for c in events.get(an["event"], {}).get("who", []) if c in looks][:3]
            out.append({"id": f"event_b{an['beat']:04d}", "kind": "event", "beat": an["beat"], "summary": an["summary"],
                        "people": [{"外貌": looks[c]} for c in who]})
    return out


def _expressions(items: list[dict], a: dict, talkers: set[str]) -> list[dict]:
    """Expression variants of each portrait, drawn from the portrait itself as a reference image so the face
    and clothes stay the same. No model call."""
    out = []
    for i in items:
        if i["kind"] != "portrait" or i["cid"] not in talkers:
            continue
        for mood in a["expressions"]:
            out.append({"id": f"{i['id']}_{MOOD_KEYS[mood]}", "kind": "expression", "cid": i["cid"], "name": i["name"],
                        "stage": i["stage"], "mood": mood, "ref": i["id"], "preset": i["preset"], "problems": [],
                        "prompt": f"参考图中的同一个人，同样的脸、发型、身材和衣服，同样的姿势和背景，只改变表情：{MOODS[mood]}。"
                                  + i["prompt"]})
    return out


def _messages(t: dict, a: dict) -> list[dict]:
    setting = f"时代背景：{a['setting']}"
    if t["kind"] == "portrait":
        card = t["card"]
        body = {"身份": card.get("identity", ""), "外貌": card.get("appearance", "")}
        return [{"role": "system", "content": PORTRAIT},
                {"role": "user", "content": setting + "\n" + json.dumps(body, ensure_ascii=False)}]
    if t["kind"] == "event":
        body = {"事件": t["summary"], "在场人物": t["people"]}
        return [{"role": "system", "content": EVENT},
                {"role": "user", "content": setting + "\n" + json.dumps(body, ensure_ascii=False)}]
    body = {"地点": t["node"], "所属": t["parent"] or "", "书中写法": t["variants"], "在这里发生的场景": t["scenes"]}
    return [{"role": "system", "content": BACKDROP},
            {"role": "user", "content": setting + "\n" + json.dumps(body, ensure_ascii=False)}]


def _check(t: dict):
    def check(obj):
        prompt = (obj or {}).get("prompt", "") if isinstance(obj, dict) else ""
        problems = []
        if len(prompt) < 20:
            problems.append("prompt 太短或缺失")
        if t["kind"] == "portrait" and t["name"] in prompt:
            problems.append(f"不要写名字“{t['name']}”")
        return obj, problems
    return check


async def plan_art(book: Path, llm, *, force: bool = False) -> dict:
    a = art_settings(book)
    if not a.get("setting") or not a.get("style"):
        raise ValueError('book.json "art" needs "setting" and "style" (see stage7-art.md)')
    old = {i["source"]: i for i in load_plan(book)["items"] if i.get("source")}
    targets = _targets(book, a)
    d = art_dir(book)
    man = load_manifest(book)

    async def one(t: dict) -> dict:
        key = _digest(json.dumps(_messages(t, a), ensure_ascii=False))
        if not force and (o := old.get(key)) and o["kind"] == t["kind"]:
            if o["id"] != t["id"]:  # same picture under a new id: keep the rendered file
                if (d / f"{o['id']}.png").exists() and not (d / f"{t['id']}.png").exists():
                    (d / f"{o['id']}.png").rename(d / f"{t['id']}.png")
                    if o["id"] in man:
                        man[t["id"]] = man.pop(o["id"])
                o = {**o, "id": t["id"]}
            return o
        obj, problems = await llm.chat_json("director", _messages(t, a), validate=_check(t), max_attempts=2,
                                            temperature=0.4, max_tokens=800)
        desc = (obj or {}).get("prompt", "") if isinstance(obj, dict) else ""
        item = {k: t[k] for k in ("id", "kind", "cid", "name", "stage", "node", "beat") if k in t}
        item.update({"source": key, "prompt": f"{a['style']}。{a['setting']}。{desc}", "problems": problems,
                     "preset": a[f"{t['kind']}_preset"]})
        return item

    items = list(await asyncio.gather(*(one(t) for t in targets)))
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    g = load_game(book)
    items += _expressions(items, a, {c for b in g["beats"] for c in b["talk"]})
    d = art_dir(book)
    d.mkdir(parents=True, exist_ok=True)
    (d / "plan.json").write_text(json.dumps({"settings": a, "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    return {**{k + "s": sum(i["kind"] == k for i in items) for k in ("portrait", "backdrop", "event", "expression")},
            "with_problems": [i["id"] for i in items if i["problems"]],
            "examples": [f"{i.get('name') or i.get('node')}: {i['prompt'][:160]}" for i in items[:3]]}


async def render_art(book: Path, client, *, only: list[str] | None = None, limit: int | None = None,
                     log=print) -> dict:
    plan = load_plan(book)["items"]
    man = load_manifest(book)
    d = art_dir(book)
    todo = [i for i in plan if (not only or i["id"] in only or i.get("name") in only or i.get("node") in only)
            and not (man.get(i["id"], {}).get("prompt") == _digest(i["prompt"]) and (d / f"{i['id']}.png").exists())]
    todo = todo[:limit] if limit else todo
    # items drawn from another image come after it
    todo.sort(key=lambda i: bool(i.get("ref")))
    done, failed = [], []
    for k, i in enumerate(todo):
        t0 = time.monotonic()
        try:
            seed = int(_digest(i["id"])[:8], 16) % 100000
            refs = None
            if i.get("ref"):
                src = d / f"{i['ref']}.png"
                if not src.exists():
                    raise RuntimeError(f"reference image {i['ref']} is not rendered")
                refs = [await client.upload(src, f"{book.name}_{i['ref']}.png")]
            info = await client.wait(await client.submit(i["prompt"], preset=i["preset"], seed=seed, refs=refs,
                                                         prefix=f"{book.name}_{i['id']}"))
            files = info.get("files") or []
            if not files:
                raise RuntimeError(f"no output files: {info}")
            await client.download(files[0], d / f"{i['id']}.png")
        except Exception as e:  # one bad item should not stop a long unattended run
            failed.append({"id": i["id"], "error": str(e)[:300]})
            log(f"[art] {i['id']} failed: {e}")
            continue
        man[i["id"]] = {"prompt": _digest(i["prompt"]), "seconds": round(time.monotonic() - t0, 1)}
        (d / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
        done.append(i["id"])
        log(f"[art] {k + 1}/{len(todo)} {i['id']} {i.get('name') or i.get('node')} {man[i['id']]['seconds']}s")
    return {"rendered": len(done), "failed": failed, "remaining": len(todo) - len(done) - len(failed),
            "total_planned": len(plan), "have": sum((d / f"{i['id']}.png").exists() for i in plan)}


def _font(size: int):
    from PIL import ImageFont
    for p in ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
              "C:/Windows/Fonts/msyh.ttc"):
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def contact_sheet(book: Path, kind: str = "portrait", *, cols: int = 6, thumb: int = 260) -> Path:
    from PIL import Image, ImageDraw
    d = art_dir(book)
    items = [i for i in load_plan(book)["items"] if i["kind"] == kind and (d / f"{i['id']}.png").exists()]
    if not items:
        raise ValueError(f"no rendered {kind} images yet")
    tiles = []
    for i in items:
        im = Image.open(d / f"{i['id']}.png").convert("RGB")
        im.thumbnail((thumb, thumb))
        tiles.append((i, im))
    h = max(im.height for _, im in tiles) + 28
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * thumb, rows * h), (32, 32, 32))
    draw, font = ImageDraw.Draw(sheet), _font(18)
    for k, (i, im) in enumerate(tiles):
        x, y = (k % cols) * thumb, (k // cols) * h
        sheet.paste(im, (x + (thumb - im.width) // 2, y))
        draw.text((x + 6, y + h - 26), f"{i.get('name') or i.get('node')} ({i['id'].rsplit('_', 1)[-1]})", fill=(230, 230, 230), font=font)
    out = d / f"sheet_{kind}.jpg"
    sheet.save(out, quality=85)
    return out


def portrait_for(plan_items: list[dict], cid: str, stage: int) -> str | None:
    """Image id to show for a character during `stage`: the last stage card that ended before it, else the
    earliest one."""
    mine = sorted((i for i in plan_items if i["kind"] == "portrait" and i.get("cid") == cid), key=lambda i: i["stage"])
    if not mine:
        return None
    before = [i for i in mine if i["stage"] < stage]
    return (before[-1] if before else mine[0])["id"]
