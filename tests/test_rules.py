"""Declarative knowledge rules."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from n2g.knowledge.rules import KnowledgeRules  # noqa: E402

IDS = {"甲": "c1", "乙": "c2", "丙": "c3"}


def rules(spec):
    return KnowledgeRules(spec, IDS.get)


def test_companion_present_only_with_host_and_in_range():
    r = rules({"companions": [{"who": "乙", "with": "甲", "chapters": [[0, 10], [20, 30]]}]})
    assert r.present(5, {"c1"}) == {"c1", "c2"}
    assert r.present(15, {"c1"}) == {"c1"}          # asleep
    assert r.present(5, {"c3"}) == {"c3"}           # host absent
    assert r.present(30, {"c1", "c3"}) == {"c1", "c2", "c3"}


def test_companion_shares_what_host_hears_not_thinks():
    r = rules({"companions": [{"who": "乙", "with": "甲", "chapters": [[0, 10]]}]})
    told = {"c1": "told", "c3": "self"}
    assert r.share(3, told) == 1 and told["c2"] == "shared"
    thought = {"c1": "self"}
    assert r.share(3, thought) == 0 and "c2" not in thought
    late = {"c1": "witnessed"}
    assert r.share(11, late) == 0


def test_no_sharing_when_disabled_and_explicit_shared_rule():
    r = rules({"companions": [{"who": "乙", "with": "甲", "chapters": [[0, 10]], "shares_knowledge": False}],
               "shared_knowledge": [{"from": "丙", "to": "甲", "chapters": [[0, 99]], "how": ["self"]}]})
    k = {"c1": "told"}
    assert r.share(1, k) == 0
    k = {"c3": "self"}
    assert r.share(50, k) == 1 and k["c1"] == "shared"


def test_unknown_names_and_keys_fail_loudly():
    with pytest.raises(ValueError):
        rules({"companions": [{"who": "丁", "with": "甲", "chapters": [[0, 1]]}]})
    with pytest.raises(ValueError):
        rules({"companion": []})
