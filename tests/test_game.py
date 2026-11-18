"""Stage ⑥/⑧ with a neutral two-chapter book: 张三 (the player), 李四, 青石城."""
import asyncio
import json

import pytest

from n2g.game.build import build_game, game_settings
from n2g.game.npc import World, beat_for, find_character, located, reply
from n2g.game.probe import run_probes


def _char(cid, name, tier, mentions):
    return {"id": cid, "name": name, "type": "person", "tier": tier, "names": {name: len(mentions)},
            "aliases": {}, "mentions": mentions, "descs": [f"ch0: {name}，青石城的住户。"], "said": 0}


@pytest.fixture()
def book(tmp_path):
    b = tmp_path / "t"
    (b / "build" / "extract").mkdir(parents=True)
    (b / "build" / "cards").mkdir()
    (b / "book.json").write_text(json.dumps({
        "slug": "t", "source": str(tmp_path / "t.txt"), "protagonist": "张三",
        "stages": [{"name": "起", "from": 0, "to": 0}, {"name": "承", "from": 1, "to": 1}]}, ensure_ascii=False),
        encoding="utf-8")
    chapters = [
        {"idx": 0, "heading": "城门", "paragraphs": ["张三走到青石城门口。", "“站住。”李四喊。", "张三停下了。"]},
        {"idx": 1, "heading": "茶馆", "paragraphs": ["张三进了茶馆。", "“喝茶。”张三说。", "李四后来也到了。"]},
    ]
    (b / "build" / "chapters.jsonl").write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in chapters) + "\n", encoding="utf-8")
    chars = [_char("c0000", "张三", "S", [[0, "张三"], [1, "张三"]]),
             _char("c0001", "李四", "A", [[0, "李四"], [1, "李四"]])]
    (b / "build" / "characters.json").write_text(json.dumps({"characters": chars}, ensure_ascii=False), encoding="utf-8")
    scenes = [
        {"id": "0.s0", "chapter": 0, "from": 0, "to": 2, "place": "青石城门口", "time": "早上",
         "present": ["c0000", "c0001"], "summary": "张三在城门口被李四喊住。"},
        {"id": "1.s0", "chapter": 1, "from": 0, "to": 1, "place": "青石城茶馆", "time": "",
         "present": ["c0000"], "summary": "张三在茶馆喝茶。"},
        {"id": "1.s1", "chapter": 1, "from": 2, "to": 2, "place": "青石城茶馆", "time": "",
         "present": ["c0000", "c0001"], "summary": "李四也到了茶馆。"},
    ]
    events = [{"id": "0.e0", "chapter": 0, "scene": "0.s0", "who": ["c0000", "c0001"], "witnesses": [],
               "importance": 3, "summary": "李四在城门口拦下张三。"}]
    ledger = {"scenes": scenes, "events": events, "facts": [], "index": {"c0001": {"facts": [], "events": ["0.e0"]}},
              "stats": {}}
    (b / "build" / "ledger.json").write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    places = {"nodes": [{"name": "青石城", "parent": None, "scenes": 3, "chapters": [0, 1], "chapter_count": 2,
                         "variants": ["青石城"]}],
              "scene_node": {"0.s0": "青石城", "1.s0": "青石城", "1.s1": "青石城"}, "scene_guessed": []}
    (b / "build" / "places.json").write_text(json.dumps(places, ensure_ascii=False), encoding="utf-8")
    for ch, utt in ((0, [["q0", "李四", "站住", "said", "高"]]), (1, [["q1", "张三", "喝茶", "said", "平"]])):
        (b / "build" / "extract" / f"{ch:05d}.json").write_text(json.dumps({
            "idx": ch, "result": {"entities": [{"name": n, "type": "person"} for n in ("张三", "李四")],
                                  "scenes": [], "utterances": utt, "events": [], "facts": []}},
            ensure_ascii=False), encoding="utf-8")
    (b / "build" / "cards" / "c0001.json").write_text(json.dumps({"id": "c0001", "name": "李四", "stages": []},
                                                                 ensure_ascii=False), encoding="utf-8")
    return b


def test_build_game(book):
    g = build_game(book)
    assert [b["kind"] for b in g["beats"]] == ["play", "play", "play"]
    assert g["beats"][0]["talk"] == ["c0001"]
    assert g["beats"][0]["anchor"] == "0.e0"
    assert g["beats"][0]["lines"][0]["who"] == "c0001"
    assert g["anchors"][0]["beat"] == 0
    assert g["first_seen"]["李四"] == 0
    assert g["trail"]["c0001"] == [[0, "青石城"], [2, "青石城"]]


def test_viewpoint_defaults(book):
    assert game_settings(book)["viewpoint"] == "hero"
    meta = json.loads((book / "book.json").read_text(encoding="utf-8"))
    meta["profile"] = {"protagonist": "ensemble"}
    (book / "book.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    assert game_settings(book)["viewpoint"] == "reader"


def test_located_forgets_other_stages(book):
    build_game(book)
    w = World(book)
    assert located(w, 0) == {"c0001": "青石城"}
    # stage 1 has not seen 李四 yet at beat 1; a sighting in stage 0 must not follow him forever
    assert located(w, 1) == {}
    assert located(w, 2) == {"c0001": "青石城"}


def test_reply_and_probe(book):
    build_game(book)
    (book / "golden.jsonl").write_text(json.dumps({
        "id": "g001", "type": "probe", "who": "李四", "chapter": 0, "ask": "你刚才在城门喊我做什么？",
        "must": ["拦"], "note": "城门口拦人的事李四自己做过，应当记得"}, ensure_ascii=False) + "\n", encoding="utf-8")

    class FakeLLM:
        async def chat_json(self, role, msgs, **kw):
            assert "李四" in msgs[0]["content"]
            return {"say": "是我拦下了你。", "act": "皱眉"}, []

    w = World(book)
    cid = find_character(w, "李四")
    assert beat_for(w, cid, 0) == 0
    r = asyncio.run(reply(w, FakeLLM(), cid, 0, None, [], "你刚才在城门喊我做什么？"))
    assert r["say"] == "是我拦下了你。" and not r["flags"]
    rep = asyncio.run(run_probes(book, FakeLLM(), log=lambda m: None))
    assert rep == {"ran": 1, "passed": 1, "failed": []}
