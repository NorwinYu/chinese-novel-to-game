from n2g.knowledge.places import _clean, _joined_names


def test_clean_strips_how_and_when_but_keeps_names():
    assert _clean("从青石城到白水镇的路上") == "青石城"
    assert _clean("在张家后院") == "张家后院"
    assert _clean("深夜的走廊里") == "走廊"
    assert _clean("初冬傍晚的街上") == "街上"
    assert _clean("距白水镇不远的小山坡") == "白水镇"
    assert _clean("春风楼") == "春风楼"
    assert _clean("夏家的院子") == "夏家院子"
    assert _clean("大厅之下") == "大厅"


def test_joined_names_are_not_split():
    keep = _joined_names(["长和街", "长和街门口", "饭馆"])
    assert keep == frozenset({"长和街"})
    assert _clean("东门大道长和街门口", keep) == "东门大道长和街"
    assert _clean("东门大道长和街门口") == "东门大道长"
    assert _clean("仓库及码头", keep) == "仓库"
