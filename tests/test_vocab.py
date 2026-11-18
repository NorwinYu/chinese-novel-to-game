"""The vocab interface: CORE is grammar only; everything else comes from the book's vocab.json."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from n2g import vocab  # noqa: E402
from n2g.consolidate import characters as ch  # noqa: E402

BOOK = {"descriptor_tails": ["经理", "厂长", "老人"], "unique_roles": ["厂长"], "organisation_suffixes": ["厂"],
        "descriptor_modifiers": list("灰老")}


@pytest.fixture(autouse=True)
def fresh():
    saved, plugin = vocab.current, ch._book_descriptor
    ch._book_descriptor = None
    yield
    vocab.current, ch._book_descriptor = saved, plugin


def test_core_has_no_setting_words():
    vocab.current = vocab.Vocab()
    assert vocab.current.descriptor_tails == () and vocab.current.generic_places == ()
    assert not ch.is_descriptor("红星厂经理")
    assert ch.is_descriptor("那老人")  # demonstrative heads are grammar


def test_member_of_group_is_descriptor_but_unique_role_is_not():
    vocab.current = vocab.Vocab(BOOK)
    assert ch.is_descriptor("红星厂经理") and ch.is_descriptor("灰衣老人")
    assert not ch.is_descriptor("红星厂厂长")  # one person per factory
    assert not ch.is_descriptor("张经理")


def test_book_overrides():
    vocab.current = vocab.Vocab({**BOOK, "names": ["红星厂经理"], "descriptors": ["阿七"]})
    assert not ch.is_descriptor("红星厂经理")
    assert ch.is_descriptor("阿七")


def test_remove_core_word():
    vocab.current = vocab.Vocab({"remove": {"crowd_words": ["们"]}})
    assert "们" not in vocab.current.crowd_words and "众人" in vocab.current.crowd_words


def test_unknown_key_rejected():
    with pytest.raises(ValueError):
        vocab.Vocab({"descriptor_tail": ["x"]})


def test_speech_verbs_extend_cues():
    from n2g.extract.chapter import cue_speaker
    para = "张三嘀咕：“走吧。”"
    start = para.index("“")
    vocab.current = vocab.Vocab()
    assert cue_speaker(para, start, {"张三": "张三"}) is None
    vocab.current = vocab.Vocab({"speech_verbs": ["嘀咕"]})
    assert cue_speaker(para, start, {"张三": "张三"}) == "张三"
