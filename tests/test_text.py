"""Text-level behaviour switched on by book.json profile, and first-person extraction checks."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from n2g.extract.chapter import cue_speaker, make_validator, narrator_for, number_chapter  # noqa: E402
from n2g.profile import measure, suggest  # noqa: E402
from n2g.text.clean import normalize_lines  # noqa: E402
from n2g.text.quotes import find_quotes  # noqa: E402


def test_straight_quotes_only_when_profile_says_so():
    raw = '他说："你来了。"她笑道："嗯。"'
    assert normalize_lines(raw) == [raw]
    line = normalize_lines(raw, {"quotes": "straight"})[0]
    assert line == "他说：“你来了。”她笑道：“嗯。”"
    assert len(find_quotes(line)) == 2


def test_hard_wrap_join():
    width = "一" * 30
    raw = "\n".join([width[:-2] + "他走", "了过去。", "", "第二章", width[:-1] + "。", "新的一段"])
    assert normalize_lines(raw, {"wrap": "hard"}) == [width[:-2] + "他走了过去。", "第二章", width[:-1] + "。", "新的一段"]
    assert len(normalize_lines(raw)) == 5  # unchanged apart from the blank line


def test_narrator_for():
    assert narrator_for({"person": "third"}, 3) is None
    assert narrator_for({"person": "first", "narrator": "张三"}, 3) == "张三"
    assert narrator_for({"person": "first"}, 3) == "叙述者"
    mixed = {"person": "mixed", "narrator": {"0-4": "甲", "5-": "乙"}}
    assert narrator_for(mixed, 4) == "甲" and narrator_for(mixed, 5) == "乙" and narrator_for(mixed, 99) == "乙"


def test_first_person_validator_maps_wo_to_narrator():
    paras = ["我推开门，李四坐在里面。", "我说：“你怎么在这儿？”", "李四道：“等你。”"]
    _, quotes = number_chapter(paras)
    obj = {"non_story": [], "entities": [{"name": "李四", "type": "person", "aliases": [], "p": [0]}],
           "scenes": [{"id": "s1", "from": 0, "to": 2, "present": ["我", "李四"]}],
           "utterances": [["q0", "我", ["李四"], "said"], ["q1", "李四", ["我"], "said"]],
           "events": [], "facts": []}
    obj, problems = make_validator(paras, quotes, narrator="张三")(obj)
    assert not problems, problems
    assert obj["scenes"][0]["present"] == ["张三", "李四"]
    assert obj["utterances"][0][1] == "张三" and obj["utterances"][1][2] == ["张三"]


def test_narrator_keeps_profile_name_when_chapter_uses_a_nickname():
    paras = ["李四看着我：“老弟，你来了。”", "我说：“来了。”"]
    _, quotes = number_chapter(paras)
    base = {"non_story": [], "scenes": [{"id": "s1", "from": 0, "to": 1, "present": ["老弟", "李四"]}],
            "utterances": [["q0", "李四", ["老弟"], "said"], ["q1", "老弟", ["李四"], "said"]], "events": [], "facts": []}
    # what the model writes, and what an earlier validator left behind (renamed to the chapter's nickname)
    for ent in ({"name": "张三", "type": "person", "aliases": ["老弟"], "p": [0]},
                {"name": "老弟", "type": "person", "aliases": [], "outside_name": "张三", "p": [0]}):
        obj = {**json.loads(json.dumps(base)), "entities": [ent, {"name": "李四", "type": "person", "aliases": [], "p": [0]}]}
        obj, problems = make_validator(paras, quotes, narrator="张三")(obj)
        assert obj["entities"][0]["name"] == "张三" and "老弟" in obj["entities"][0]["aliases"]
        assert not any("张三" in p for p in problems), problems
    assert obj["utterances"][1][1] == "张三" and obj["scenes"][0]["present"] == ["张三", "李四"]


def test_wo_cue_only_with_narrator():
    para = "我说：“走吧。”"
    start = para.index("“")
    assert cue_speaker(para, start, {"李四": "李四"}) is None
    assert cue_speaker(para, start, {"我": "张三"}) == "张三"
    assert cue_speaker("我们说：“走吧。”", 4, {"我": "张三"}) is None


def test_profile_flags():
    first = "我走进屋子，我看见桌上有封信。我想了很久，我还是拆开了。" * 50
    assert suggest(measure(first))["person"]["value"] == "first"
    third = "他走进屋子，她看见桌上有封信。他想了很久。" * 50
    assert suggest(measure(third))["person"]["value"] == "third"
    straight = '他说："走。"\n' * 100
    s = suggest(measure(straight))
    assert s["quotes"]["value"] == "straight"
