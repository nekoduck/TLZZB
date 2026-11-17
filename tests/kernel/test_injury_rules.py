"""D-076 伤上加伤、D-077 轻伤独自歇息可愈。"""

from __future__ import annotations


def test_heavier_blow_sets_level_lighter_adds_one(w):
    w.fx([{"injure": ["guest", 1]}])
    assert w.status("guest", "injured").level == 1
    w.fx([{"injure": ["guest", 2]}])  # 耳光之后坠崖：到 2 级，不是 3 级
    assert w.status("guest", "injured").level == 2
    w.fx([{"injure": ["guest", 1]}])  # 不比现有的重：再加一级
    assert w.status("guest", "injured").level == 3


def test_light_wound_heals_with_rest_alone(w):
    w.fx([{"injure": ["guest", 1]}])
    w.fx([{"rest": ["guest", 16]}])
    assert w.status("guest", "injured") is None


def test_serious_wound_needs_tending(w):
    w.fx([{"injure": ["guest", 2]}])
    w.fx([{"rest": ["guest", 32]}])
    assert w.status("guest", "injured").level == 2
