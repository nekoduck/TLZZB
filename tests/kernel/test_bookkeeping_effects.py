"""D-078：上层模块用的记账类效果。"""

from __future__ import annotations

from tianlong.kernel import resolve


def test_reflect_and_impression(w):
    before = w.c("guest").rel["player"].trust if "player" in w.c("guest").rel else 0
    w.fx([{"reflect": ["guest", "那伙计倒还老实。", ["player"], 7]}, {"impression": ["guest", "player", "老实"]}])
    m = w.c("guest").memory[-1]
    assert m.kind == "reflection" and m.entities == ["player"] and m.salience == 7
    assert w.c("guest").rel["player"].impression == "老实"
    assert w.c("guest").rel["player"].trust == before


def test_agenda_replace_and_progress(w):
    ag = {"goal": "回家", "steps": [{"id": "x1", "text": "回去", "do": {"verb": "wait"}, "origin": "replan"}]}
    w.fx([{"agenda": ["guest", ag]}, {"progress": ["guest", 3]}, {"progress": ["guest", 2]}])
    a = w.c("guest").agenda
    assert a.goal == "回家" and a.steps[0].origin == "replan" and a.progress == 5
    w.fx([{"progress": ["guest", 0]}])
    assert w.c("guest").agenda.progress == 0
    w.fx([{"agenda": ["guest", None]}])
    assert w.c("guest").agenda is None


def test_chronicle_diverge_teller(w):
    w.fx(
        [
            {"chronicle": {"text": "比剑散了", "known_to_player": True}},
            {"diverge": {"mark": "m_x", "cause_event": None}},
            {"diverge": {"mark": "m_x"}},  # "diverge" 同一参照点只记一次
            {"teller": ["tension", 30]},
        ]
    )
    assert w.s.chronicle[-1].text == "比剑散了" and w.s.chronicle[-1].tick == w.s.tick
    assert [d.mark for d in w.s.divergences].count("m_x") == 1
    assert w.s.storyteller["tension"] == 30


def test_thread_close_rolls_back(w):
    w.fx([{"thread": {"id": "T9", "kind": "lead", "text": "一条线索"}}])
    snap = resolve.Snapshot(w.s)
    w.fx([{"close_thread": "T9"}])
    assert w.s.threads["T9"].closed
    snap.restore(w.s)
    assert not w.s.threads["T9"].closed


# ---- 开局布置与记账（wave 3：K4、K18、K20） ----


def test_request_effect_scores_like_speech(w):
    w.fx([{"give": ["guest", "player", "token"]}])
    w.fx(
        [
            {
                "request": {
                    "frm": "player",
                    "to": "fighter",
                    "action": {"verb": "follow", "params": {"target": "player"}},
                    "evidence": ["token"],
                }
            }
        ]
    )
    (r,) = w.s.requests.values()
    assert (r.frm, r.to, r.kind, r.status) == ("player", "fighter", "request", "open")
    assert r.action == {"verb": "follow", "params": {"target": "player"}}
    assert r.action_class == "effort" and "strong:item:token" in r.evidence
    assert r.score == 30 + 20 and r.verdict == "conditional" and r.created == w.s.tick
    # 同一件事再提一次：没有新凭据，attempts 加一，分数减 10（与 speech.make_request 相同）
    w.fx([{"request": {"frm": "player", "to": "fighter", "action": {"verb": "follow", "params": {"target": "player"}}}}])
    assert len(w.s.requests) == 1 and r.attempts == 2 and r.score == 40


def test_request_effect_roles_ids_status_and_bottom_line(w):
    from tianlong.kernel.state import Ctx

    w.fx(
        [
            {
                "request": {
                    "id": "R_setup",
                    "frm": "$asker",
                    "to": "guest",
                    "kind": "request",
                    "action": {"verb": "give", "params": {"item": "token", "target": "$asker"}},
                    "action_class": "trivial",
                    "status": "agreed",
                }
            }
        ],
        roles={"asker": "player"},
    )
    r = w.s.requests["R_setup"]
    assert r.id == "R_setup" and r.status == "agreed" and r.action["params"]["target"] == "player"
    st = w.c("guest").agenda.steps[w.c("guest").agenda.cursor]  # 答应的事插到打算的最前面
    assert st.id == "req_R_setup" and st.do == {"verb": "give", "item": "token", "target": "player"}
    assert any(p.request == "R_setup" and p.frm == "guest" for p in w.s.promises.values())
    # 撞上底线：never（白逸不对人动手）
    w.fx([{"request": {"frm": "player", "to": "guest", "action": {"verb": "attack", "params": {"target": "fighter"}}}}])
    never = [x for x in w.s.requests.values() if x.action["verb"] == "attack"]
    assert never[0].verdict == "never"
    # 还价与问话
    w.fx(
        [
            {
                "request": {
                    "frm": "player",
                    "to": "fighter",
                    "action": {"verb": "show", "params": {"item": "sword", "target": "player"}},
                    "status": "countered",
                    "condition": {"verb": "give", "params": {"item": "rope", "target": "fighter"}},
                    "attempts": 2,
                }
            },
            {"request": {"frm": "guest", "to": "player", "kind": "question", "about": "girl"}},
        ]
    )
    c = next(x for x in w.s.requests.values() if x.status == "countered")
    assert c.condition == {"verb": "give", "params": {"item": "rope", "target": "fighter"}} and c.attempts == 2
    q = next(x for x in w.s.requests.values() if x.kind == "question")
    assert q.verdict == "may" and q.action["params"]["about"] == "girl"
    import pytest

    from tianlong.kernel.effects import EffectError, apply_effects

    for bad in (
        {"frm": "player", "to": "player", "action": {"verb": "wait"}},
        {"frm": "player", "to": "guest", "action": {}},
        {"frm": "player", "to": "guest", "kind": "beg", "action": {"verb": "wait"}},
        {"frm": "player", "to": "guest", "action": {"verb": "wait"}, "status": "countered"},
        {"frm": "player", "to": "guest", "action": {"verb": "wait"}, "id": "R_setup"},
        {"frm": "player", "to": "guest", "action": {"verb": "wait"}, "color": "red"},
    ):
        with pytest.raises(EffectError):
            apply_effects(w.s, w.defs, [{"request": bad}], Ctx(), source="rule")


def test_superior_dequeue_turn_offer(w):
    w.fx([{"superior": ["player", "guest"]}])
    assert w.c("player").superior == "guest"
    w.fx([{"superior": ["player", None]}])
    assert w.c("player").superior is None
    w.fx([{"replan": "guest"}, {"replan": "fighter"}])
    assert w.s.replan_queue == ["guest", "fighter"]
    w.fx([{"dequeue": "guest"}, {"dequeue": "girl"}])  # 不在队列里的人：无事发生
    assert w.s.replan_queue == ["fighter"]
    w.fx([{"turn": 7}])
    assert w.s.turn == 7
    opts = [{"signature": "wait:ticks=1", "tag": "自保", "text": "等一等"}, {"signature": "look:", "tag": "探索"}]
    w.fx([{"offer": opts}])
    assert w.s.pending_options == opts and w.s.pending_options is not opts
    w.fx([{"offer": []}])
    assert w.s.pending_options == []
    import pytest

    from tianlong.kernel.effects import EffectError

    with pytest.raises(EffectError):
        w.fx([{"superior": ["player", "player"]}])
    with pytest.raises(EffectError):
        w.fx([{"turn": -1}])
    with pytest.raises(EffectError):
        w.fx([{"offer": ["wait"]}])


def test_fact_effect_adds_and_teaches(w):
    w.fx(
        [
            {
                "fact": {
                    "id": "F_paths",
                    "text": "{self}常年在⟦cliff⟧一带走动，熟悉山路。",
                    "tags": ["route"],
                    "gossip": True,
                    "known_by": ["self", "guest"],
                }
            }
        ],
        self_id="player",
    )
    f = w.s.facts["F_paths"]
    assert f.text == "⟦player⟧常年在⟦cliff⟧一带走动，熟悉山路。" and f.tags == ["route"] and f.gossip
    assert w.c("player").beliefs["F_paths"].confidence == 1.0 and "F_paths" in w.c("guest").beliefs
    assert "cliff" in w.s.known_terms  # 玩家确信的事实里提到的专名
    w.fx([{"fact": {"id": "F_paths", "text": "改过的说法。"}}])  # 同 id：替换
    assert w.s.facts["F_paths"].text == "改过的说法。"
    import pytest

    from tianlong.kernel.effects import EffectError

    with pytest.raises(EffectError):
        w.fx([{"fact": {"text": "没有 id"}}])
    with pytest.raises(EffectError):
        w.fx([{"fact": {"id": "F_x", "text": "x", "secret": True}}])


def test_mark_char_only_bookkeeping_fields(w):
    w.fx([{"mark_char": ["guest", "reflect_mark", 12]}, {"mark_char": ["guest", "last_replan", 40]}])
    assert w.c("guest").reflect_mark == 12 and w.c("guest").last_replan == 40
    w.fx([{"mark_char": ["guest", "last_replan", None]}])
    assert w.c("guest").last_replan is None
    import pytest

    from tianlong.kernel.effects import EffectError

    for bad in (["guest", "neili", 99], ["guest", "loc", "yard"], ["guest", "reflect_mark", None]):
        with pytest.raises(EffectError):
            w.fx([{"mark_char": bad}])


def test_mark_char_setout_marks_live_on_the_current_step(w):
    """离场结算记的两项账（动身前的前提检查）：记在眼下的打算上，换了一步、插了一步就清掉。"""
    w.fx([{"mark_char": ["player", "doubt", 40]}])  # 没有打算的人：不记，也不报错
    ag = {
        "goal": "g",
        "steps": [
            {"id": "x1", "text": "一", "do": {"verb": "wait"}},
            {"id": "x2", "text": "二", "do": {"verb": "wait"}},
        ],
    }
    w.fx([{"agenda": ["guest", ag]}, {"mark_char": ["guest", "doubt", 40]}])
    a = w.c("guest").agenda
    assert a.doubt == 40 and not a.en_route
    w.fx([{"mark_char": ["guest", "en_route", 1]}])
    assert a.en_route and a.doubt is None  # 上了路：之前那次不成立不留底
    w.fx([{"step_state": ["guest", "x1", "done"]}])
    assert not a.en_route and a.doubt is None and a.cursor == 1
    w.fx([{"mark_char": ["guest", "doubt", 48]}, {"step": ["guest", {"id": "x0", "text": "零", "do": {"verb": "wait"}}]}])
    assert a.doubt is None and not a.en_route
    w.fx([{"mark_char": ["guest", "en_route", 1]}, {"mark_char": ["guest", "en_route", None]}])
    assert not a.en_route


def test_setup_effects_roll_back(w):
    snap = resolve.Snapshot(w.s)
    w.fx(
        [
            {"request": {"frm": "player", "to": "guest", "action": {"verb": "wait"}, "status": "agreed"}},
            {"superior": ["player", "guest"]},
            {"replan": "guest"},
            {"turn": 3},
            {"offer": [{"signature": "look:"}]},
            {"fact": {"id": "F_new", "text": "新的一条。", "known_by": ["guest"]}},
            {"mark_char": ["guest", "reflect_mark", 5]},
        ]
    )
    assert w.s.requests and w.s.promises and "F_new" in w.s.facts
    snap.restore(w.s)
    assert not w.s.requests and not w.s.promises and "F_new" not in w.s.facts
    assert "F_new" not in w.c("guest").beliefs and w.c("guest").reflect_mark == 0
    assert w.c("player").superior is None and w.s.turn == 0 and w.s.pending_options == []
    assert w.s.replan_queue == [] and not any(s.id.startswith("req_") for s in w.c("guest").agenda.steps)
    assert w.s.counters.get("R", 0) == 0
