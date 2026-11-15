"""三选项（方案 2.3、8.4）：真内容、Mock 模型。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tianlong.kernel import actions, danger
from tianlong.llm import JevError
from tianlong.storyteller import common, options, speaking, threads
from tianlong.storyteller.options import Option, note_action, note_choice, pick_options

from .conftest import calls

UNKNOWN_AT_START = ["段誉", "钟灵", "龚光杰", "干光豪", "葛光佩", "辛双清", "容子矩", "吴光胜", "郁光标"]


async def _opts(llm, w) -> list[Option]:
    threads.update(w.s, w.defs, [])
    return await pick_options(llm, w.s, w.defs)


def _assert_basic(w, opts: list[Option]) -> None:
    assert len(opts) == 3
    tags = [o.tag for o in opts]
    assert len(set(tags)) == 3, tags
    sigs = [o.signature for o in opts]
    assert len(set(sigs)) == 3 and len({o.id for o in opts}) == 3
    for o in opts:
        assert o.tag in options.TAGS
        assert not o.action.lethal
        assert actions.is_legal(w.s, w.defs, o.action) is not None
        if o.speech is None:
            assert o.signature == o.action.signature and o.desc == o.action.desc and o.id == o.action.id
        else:  # 开口的选项：动作是 1 刻的 wait，那句话此刻说得出口
            assert o.action.verb == "wait" and o.action.params == {"ticks": 1}
            assert o.signature.startswith("say:") and o.id.startswith("s")
            assert speaking.sayable(w.s, w.defs, o.speech)


async def test_opening_has_intervene_when_bout_is_open(llm, w):
    w.open_bout()
    opts = await _opts(llm, w)
    _assert_basic(w, opts)
    tags = {o.tag for o in opts}
    assert "介入" in tags
    inter = next(o for o in opts if o.tag == "介入")
    assert inter.action.params.get("target") in {"gong_guangjie", "duan_yu"}
    assert inter.thread_ids  # 眼前事的牵挂
    assert any(o.thread_ids for o in opts)


async def test_opening_without_situation_still_three_and_thread_tied(llm, w):
    opts = await _opts(llm, w)
    _assert_basic(w, opts)
    assert "介入" not in {o.tag for o in opts}
    # 开局的牵挂「随东家做完客」挂在马五德身上：至少一个选项与牵挂挂钩
    assert any(o.thread_ids for o in opts)


async def test_self_preservation_forced_in_danger(llm, w):
    w.fx([{"rel": ["gong_guangjie", "player", "affinity", -80]}])
    assert danger.danger(w.s, w.defs) >= 2
    opts = await _opts(llm, w)
    _assert_basic(w, opts)
    assert "自保" in {o.tag for o in opts}


async def test_danger_and_situation_both_forced(llm, w):
    w.open_bout()
    w.fx([{"rel": ["yu_guangbiao", "player", "affinity", -80]}])
    assert danger.danger(w.s, w.defs) >= 2
    opts = await _opts(llm, w)
    _assert_basic(w, opts)
    assert {"自保", "介入"} <= {o.tag for o in opts}


async def test_lethal_actions_never_offered(llm, w):
    w.open_bout()
    legal = actions.legal_actions(w.s, w.defs, w.s.player)
    assert any(a.lethal for a in legal)  # 开局就有致命的动作（打比自己强得多的人）
    _, cands = options.candidates(w.s, w.defs)
    assert cands and not any(o.action.lethal for o in cands)
    for _ in range(3):
        opts = await _opts(llm, w)
        assert not any(o.action.lethal for o in opts)
        note_choice(w.s, w.defs, opts[0])


async def test_no_unknown_proper_names_in_option_text(llm, w):
    w.open_bout()
    _, cands = options.candidates(w.s, w.defs)
    unknown = common.unknown_names(w.s, w.defs)
    for name in UNKNOWN_AT_START:
        assert name in unknown
    for o in cands:
        shown = common.pv(w.s, w.defs, o.desc)
        assert not any(n in shown for n in UNKNOWN_AT_START), shown
        assert "⟦" not in shown and "out_" not in shown
    opts = await _opts(llm, w)
    for o in opts:
        shown = common.pv(w.s, w.defs, o.desc)
        assert not any(n in shown for n in unknown), shown


def test_name_check_catches_raw_names(w):
    assert common.reveals_unknown_name(w.s, w.defs, "去找段誉说话")
    assert not common.reveals_unknown_name(w.s, w.defs, "挡在⟦duan_yu⟧面前")  # 渲染成「段公子」
    assert not common.reveals_unknown_name(w.s, w.defs, "往⟦cliff_edge⟧去")  # 「一处断崖」里的「断崖」不算
    w.fx([{"know_term": "duan_yu"}])
    assert not common.reveals_unknown_name(w.s, w.defs, "去找段誉说话")


def test_items_carried_by_others_need_to_be_seen(w):
    sigs = {o.action.signature for o in options.candidates(w.s, w.defs)[1]}
    steal = actions.signature("steal", {"item": "woodshed_key", "target": "wu_guangsheng"})
    # 内核自己就不列看不见的东西了（wave 3 的 K9）；细看过那人之后才合法
    assert steal not in {a.signature for a in actions.legal_actions(w.s, w.defs, w.s.player)}
    assert steal not in sigs
    w.fx([{"mark": ["examined", "char:wu_guangsheng"]}])
    sigs = {o.action.signature for o in options.candidates(w.s, w.defs)[1]}
    assert steal in sigs


def test_edge_exits_not_offered(w):
    w.fx([{"move": ["player", "mountain_path"]}])
    legal = {a.signature for a in actions.legal_actions(w.s, w.defs, w.s.player)}
    edge = actions.signature("go", {"exit": "out_east"})
    assert edge in legal
    assert edge not in {o.action.signature for o in options.candidates(w.s, w.defs)[1]}


async def test_last_chosen_action_excluded(llm, w):
    w.open_bout()
    opts = await _opts(llm, w)
    chosen = opts[0]
    note_choice(w.s, w.defs, chosen)
    again = await _opts(llm, w)
    assert chosen.signature not in {o.signature for o in again}
    assert len(again) == 3
    # 下一回合：不再排除，但近三回合选过同一动词与对象，扣 15
    w.s.turn += 1
    _, cands = options.candidates(w.s, w.defs)
    same = next(o for o in cands if o.signature == chosen.signature)
    assert same.rule == pytest.approx(chosen.rule - 15)


async def test_note_action_for_free_input(llm, w):
    a = actions.build(w.s, w.defs, w.s.player, "look", {})
    note_action(w.s, w.defs, a)
    assert w.s.storyteller["last_chosen"]["sig"] == a.signature
    opts = await _opts(llm, w)
    assert a.signature not in {o.signature for o in opts}


async def test_j4_failure_still_three_options(llm, w, monkeypatch):
    async def boom(*args: Any, **kwargs: Any):
        raise JevError("down")

    monkeypatch.setattr(llm.jev, "ask", boom)
    w.open_bout()
    opts = await _opts(llm, w)
    _assert_basic(w, opts)
    assert "介入" in {o.tag for o in opts}
    for o in opts:
        assert o.score == pytest.approx(o.rule + 10)


async def test_j4_scores_feed_the_ranking(llm, w):
    w.open_bout()
    opts = await _opts(llm, w)
    j4 = calls(llm, "J4")
    assert len(j4) == 1
    qs = j4[0]["request"]["questions"]
    assert 1 <= len(qs) <= options.TOP_N
    assert all(q["type"] == "score" and len(q["criteria"]) == 4 for q in qs.values())
    inter = next(o for o in opts if o.tag == "介入")
    assert inter.score == pytest.approx(inter.rule + 30)  # Mock 照规则提示给了最高一级
    state_text = j4[0]["request"]["state"]
    assert "【未了的牵挂】" in state_text and "段誉" not in state_text


async def test_rules_only_route_skips_j4(llm, w):
    llm.settings.route.options = "rules_only"
    w.open_bout()
    opts = await _opts(llm, w)
    _assert_basic(w, opts)
    assert not calls(llm, "J4")


async def test_bound_player_gets_what_there_is(llm, w):
    w.fx([{"add_status": ["player", "bound"]}])
    opts = await _opts(llm, w)
    assert 1 <= len(opts) <= 3
    assert {o.action.verb for o in opts} <= {"wait", "struggle"}
    assert "struggle" in {o.action.verb for o in opts}


async def test_offered_options_are_booked(llm, w):
    opts = await _opts(llm, w)
    offered = w.s.storyteller["offered"]
    assert [x["sig"] for x in offered] == [o.signature for o in opts]
    assert isinstance(opts[0].to_pending(), dict)


def test_no_names_or_places_in_storyteller_source(w):
    """方案 10.6：src/ 里不出现人名、地名、剧情（专名与实体 id 都不许）。"""
    src = Path(__file__).resolve().parents[2] / "src" / "tianlong" / "storyteller"
    text = "\n".join(p.read_text(encoding="utf-8") for p in sorted(src.glob("*.py")))
    table = {**w.defs.lexicon.names, **w.defs.lexicon.terms}
    for eid, term in table.items():
        if eid == w.s.player:
            continue
        assert term.name not in text, term.name
        assert f'"{eid}"' not in text and f"'{eid}'" not in text, eid
