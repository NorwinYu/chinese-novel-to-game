"""Game layer rules with a neutral hand-made game: 张三 (the player) wants to save money in 青石城;
李四 runs the shop, 王五 knows where cheap goods are."""
import copy

import pytest

from n2g.game import engine as E
from n2g.game.design import ALL, check_choice, check_design, check_frame, check_intel, gate_actions
from n2g.game.looks import check_character, check_event, check_place, load_looks
from n2g.game.npc import said_enough
from n2g.game.sim import sim_report

CAST = {"c1": "李四：店主", "c2": "王五：伙计"}


def _beat(i, ch, present, talk, anchor=None, frm=0, to=3, node="青石城"):
    return {"i": i, "id": f"{ch}.s{i}", "chapter": ch, "heading": "", "stage": 0, "from": frm, "to": to, "place": node,
            "node": node, "time": "", "present": present, "hero": True, "kind": "play", "talk": talk, "lines": [],
            "events": [], "anchor": anchor}


@pytest.fixture()
def game():
    beats = [_beat(0, 0, ["c0", "c1"], ["c1"], anchor="0.e0"), _beat(1, 0, ["c0"], []),
             _beat(2, 1, ["c0", "c2"], ["c2"]), _beat(3, 1, ["c0", "c1"], ["c1"], anchor="1.e0", node="店铺")]
    return {"protagonist": {"id": "c0", "name": "张三"}, "settings": {"chapters": None}, "stages": [{"name": "起", "from": 0, "to": 1}],
            "beats": beats, "anchors": [{"beat": 0, "event": "0.e0", "chapter": 0, "summary": "张三在店里赊账"},
                                        {"beat": 3, "event": "1.e0", "chapter": 1, "summary": "李四催债"}],
            "map": {"nodes": [{"name": "青石城", "parent": None, "first_beat": 0}, {"name": "店铺", "parent": "青石城", "first_beat": 3}],
                    "moves": []},
            "trail": {"c1": [[0, "青石城"], [3, "店铺"]], "c2": [[2, "青石城"]]},
            "characters": {"c0": {"name": "张三"}, "c1": {"name": "李四"}, "c2": {"name": "王五"}},
            "first_seen": {"张三": 0, "李四": 0, "王五": 1}}


@pytest.fixture()
def design():
    return {
        "stats": [{"id": "money", "name": "积蓄", "unit": "元", "min": 0, "max": 100, "start": 30},
                  {"id": "energy", "name": "体力", "unit": "", "min": 0, "max": 100, "start": 50,
                   "fail": {"title": "累倒了", "text": "你倒在路边。"}}],
        "goal": {"text": "攒够五十元", "stat": "money", "target": 50}, "slots": 2,
        "actions": [{"id": "work", "name": "干活", "desc": "", "effects": {"money": 10, "energy": -20}, "needs": []},
                    {"id": "rest", "name": "歇着", "desc": "", "effects": {"energy": 20}, "needs": []},
                    {"id": "help", "name": "帮王五", "desc": "", "effects": {"money": 5}, "needs": []}],
        "upkeep": {"money": -2}, "relations": {"c1": 10},
        "endings": [{"id": "rich", "kind": "win", "title": "攒够了", "text": "", "when": [{"stat": "money", "op": ">=", "value": 50}]},
                    {"id": "poor", "kind": "lose", "title": "穷", "text": "", "when": [{"stat": "money", "op": "<", "value": 10}]},
                    {"id": "plain", "kind": "neutral", "title": "照旧", "text": "", "when": []}],
        "intel": [{"id": "i1", "cid": "c2", "fact": "1.f0", "topic": "便宜货", "answer": "王五说东街的旧货摊白菜价收货。",
                   "from_beat": 2, "unlock": 3}],
        "choices": {
            "0": {"beat": 0, "at_p": 2, "prompt": "赊不赊？", "options": [
                {"id": "canon", "text": "赊账", "canon": True, "effects": {"money": -10}, "rel": {"c1": 5}, "flags": []},
                {"id": "a", "text": "不赊", "canon": False, "effects": {"energy": -10}, "rel": {"c1": -5}, "flags": ["no_debt"],
                 "needs": [], "needs_intel": "", "result": "你转身走了。", "bridge": "第二天你还是回到店里。", "ending": ""}]},
            "3": {"beat": 3, "at_p": 0, "prompt": "还不还？", "options": [
                {"id": "canon", "text": "全还上", "canon": True, "effects": {"money": ALL}, "rel": {}, "flags": []},
                {"id": "b", "text": "拿旧货抵债", "canon": False, "effects": {"money": 20}, "rel": {}, "flags": [], "needs": [],
                 "needs_intel": "i1", "result": "李四收了货。", "bridge": "债清了。", "ending": ""},
                {"id": "c", "text": "跑", "canon": False, "effects": {}, "rel": {}, "flags": [], "needs": [], "needs_intel": "",
                 "result": "你跑了。", "bridge": "", "ending": "poor"}]}}}


def _play(design, game, moves):
    st = E.new_state(design, game)
    events = []
    for m in moves:
        st, ev = E.act(design, game, st, m)
        events += ev
    return st, events


def test_reading_choice_and_soft_regression(design, game):
    st, _ = _play(design, game, [{"type": "next"}])
    assert st["phase"] == "choice"
    canon, _ = E.act(design, game, st, {"type": "choose", "option": "canon"})
    assert canon["phase"] == "read" and canon["p0"] == 2 and canon["stats"]["money"] == 20 and canon["rel"]["c1"] == 15
    alt, _ = E.act(design, game, st, {"type": "choose", "option": "a"})
    assert alt["phase"] == "narrate" and alt["narration"] == ["你转身走了。", "第二天你还是回到店里。"]
    assert "no_debt" in alt["flags"] and E.deeds_known_by(alt, "c1", {}) == ["不赊：你转身走了。"]
    nxt, _ = E.act(design, game, alt, {"type": "next"})
    assert nxt["beat"] == 1 and nxt["phase"] == "read"


def test_free_time_actions_visits_and_upkeep(design, game):
    st, ev = _play(design, game, [{"type": "next"}, {"type": "choose", "option": "canon"}, {"type": "next"}, {"type": "next"}])
    assert st["phase"] == "free" and st["slots"] == 2 and st["stats"]["money"] == 18  # upkeep -2
    assert {"type": "visit", "cid": "c1"} in E.legal(design, game, st)
    st, _ = E.act(design, game, st, {"type": "do", "action": "work"})
    assert st["slots"] == 1 and st["stats"] == {"money": 28, "energy": 30}
    st, _ = E.act(design, game, st, {"type": "visit", "cid": "c1"})
    assert st["visiting"]["node"] == "青石城" and E.can_talk(game, st, "c1") is None and E.can_talk(game, st, "c2")
    with pytest.raises(ValueError):
        E.act(design, game, st, {"type": "do", "action": "work"})  # no slot left
    st, _ = E.act(design, game, st, {"type": "leave"})
    assert st["beat"] == 2 and st["phase"] == "read"


def test_fail_stat_ends_and_all_and_intel_gate(design, game):
    st, _ = _play(design, game, [{"type": "next"}, {"type": "choose", "option": "canon"}, {"type": "next"}, {"type": "next"},
                                 {"type": "do", "action": "work"}, {"type": "do", "action": "rest"}, {"type": "leave"}])
    assert st["beat"] == 2
    # intel: only what the character really said counts, and only inside its window
    st2, ev = E.apply_talk(design, game, st, "c2", 9, ["i1"])
    assert st2["intel"] == ["i1"] and st2["rel"]["c2"] == E.REL_STEP and any(e["type"] == "intel" for e in ev)
    st, _ = E.act(design, game, st, {"type": "next"})
    st, _ = E.act(design, game, st, {"type": "next"})
    assert st["phase"] == "choice"
    assert [m["option"] for m in E.legal(design, game, st)] == ["canon", "c"]  # b needs the intel
    end, ev = E.act(design, game, st, {"type": "choose", "option": "canon"})
    assert end["stats"]["money"] == 0 and not any(e.get("clipped") for e in ev if e["type"] == "stat")
    end, ev = E.act(design, game, end, {"type": "next"})
    assert end["phase"] == "end" and end["ending"]["id"] == "poor"
    tired = copy.deepcopy(st)
    tired["phase"], tired["slots"], tired["stats"]["energy"] = "free", 1, 10
    tired, _ = E.act(design, game, tired, {"type": "do", "action": "work"})
    assert tired["phase"] == "end" and tired["ending"]["id"] == "fail_energy"


def test_validators_catch_bad_designs(design, game):
    frame, p = check_frame({"stats": [{"id": "money", "name": "积蓄", "min": 0, "max": 100, "start": 30}],
                            "goal": {"text": "x", "stat": "gold", "target": 5}, "actions": [], "endings": [{"id": "a", "kind": "lose", "title": "t", "when": []}]}, CAST)
    assert any("2~4" in x for x in p) and any("goal" in x for x in p) and any("win" in x for x in p)
    beat = game["beats"][3]
    bad = {"at_p": 9, "prompt": "?", "options": [
        {"id": "x", "text": "一", "canon": True, "effects": {"money": 5}},
        {"id": "y", "text": "二", "canon": True, "effects": {"gold": 1}},
        {"id": "z", "text": "三", "effects": {"money": ALL}, "result": "后来遇见了赵六。", "bridge": "好。"}]}
    _, p = check_choice(bad, frame=design, characters=CAST, beat=beat, intel=design["intel"], future=["赵六"])
    text = "\n".join(p)
    for want in ("at_p", "canon", "gold", "赵六", "i1", ALL):
        assert want in text, want
    _, p = check_choice(design["choices"]["3"], frame=design, characters=CAST, beat=beat, intel=design["intel"], future=[])
    assert p == []
    items, p = check_intel({"intel": [{"cid": "c2", "fact": "1.f0", "topic": "便宜货", "answer": "旧货摊", "unlock": 0}]},
                           candidates={"1.f0": {"c2": 2}}, anchors={0, 3})
    assert not items and any("晚于" in x for x in p)
    assert check_design(design, game) == []


def test_actions_wait_for_the_people_they_name(design, game):
    gate_actions(design, game)
    assert {a["id"]: a["from_chapter"] for a in design["actions"]} == {"work": 0, "rest": 0, "help": 1}
    st = E.new_state(design, game)
    assert "help" not in [a["id"] for a in E.offered(design, game, st)]


def test_simulation_report(design, game):
    rep = sim_report(None, design, game, runs=10)
    assert rep["deadlocks"] == 0 and rep["choice_coverage"] == 1.0
    assert rep["canon_survives"] == 1 and rep["canon_clipped"] == 0
    assert rep["win_reachable"] == 1 and rep["lose_reachable"] == 1 and rep["intel_obtained_share"] == 1.0


def test_said_enough():
    answer = "王五说东街的旧货摊白菜价收货。"
    assert not said_enough("这事啊，以后再说吧。", answer, ["王五", "张三"])
    assert said_enough("东街那个旧货摊，白菜价就收，你去看看。", answer, ["王五", "张三"])


def test_looks_validators_keep_values_in_the_schema():
    c, p = check_character({"sex": "male", "age": "ancient", "hat": "tophat", "skin": "brown", "height": 3, "accessories": ["sword", "laser"]})
    assert c["age"] in ("teen",) or c["age"] != "ancient"
    assert c["hat"] == "none" and c["height"] == 1.15 and c["accessories"] == ["sword"] and len(p) >= 3
    pl, p = check_place({"kind": "spaceship", "props": ["table", "robot"], "crowd": 99})
    assert pl["kind"] == "street" and pl["props"] == ["table"] and pl["crowd"] == 6 and p
    ev, p = check_event({"people": [{"id": "c9", "pose": "dance"}, {"id": "c1", "pose": "dance", "mood": "高兴"}]}, {"c1"})
    assert [x["id"] for x in ev["people"]] == ["c1"] and ev["people"][0]["pose"] == "stand" and p

def test_hand_looks_override_fields_and_is_validated(tmp_path):
    import json
    (tmp_path / "build").mkdir()
    base, _ = check_character({"sex": "male", "age": "adult", "top": "jacket", "top_color": "#112233"})
    (tmp_path / "build" / "looks.json").write_text(json.dumps(
        {"characters": {"c1": {**base, "source": "x"}}, "places": {}, "events": {}}), encoding="utf-8")
    (tmp_path / "looks.json").write_text(json.dumps(
        {"characters": {"c1": {"top_color": "#aa0000", "hat": "nonsense"}}}), encoding="utf-8")
    lk = load_looks(tmp_path)
    c = lk["characters"]["c1"]
    assert c["top_color"] == "#aa0000" and c["top"] == "jacket" and c["hat"] == "none" and c["override"]
    assert load_looks(tmp_path, raw=True)["characters"]["c1"]["top_color"] == "#112233"
