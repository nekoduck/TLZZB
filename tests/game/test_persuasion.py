"""方案 2.1 / 5.7 的劝说路径，经真正的回合循环（tianlong.game.Game，真实 content/，Mock）：

笑声之后选「低声劝……赔个不是」→ 客人当场答应并赔礼，耳光不落；过了参照点的期限，
「当众掴了一记耳光」记成一条分岔，起因追到玩家那句劝。
"""

from __future__ import annotations

from tianlong.config import Settings
from tianlong.kernel import invariants

from .conftest import new_session, options_of, text_of

GUEST, VICTOR = "duan_yu", "gong_guangjie"


async def _until_offered(s, word: str, limit: int = 6) -> int:
    """一直选第一项，直到选项里出现带 ``word`` 的一条；返回它的序号。"""
    opts = options_of(s.steps[-1][1]) or []
    for _ in range(limit):
        hit = next((i for i, o in enumerate(opts) if word in o), None)
        if hit is not None:
            return hit
        opts = options_of(await s.send("一")) or opts
    raise AssertionError(f"没有等到带「{word}」的选项：{opts}")


async def test_persuasion_through_the_real_loop(real_settings: Settings) -> None:
    s = await new_session(real_settings, seed=1)
    g, st = s.game, s.state
    k = await _until_offered(s, "赔个不是")
    offered = (options_of(s.steps[-1][1]) or [])[k]
    assert offered.startswith("低声劝") and "你" not in offered, offered
    assert st.flags.get("guest_laughed") and not st.flags.get("slapped")
    first = st.next_event_id
    evs = await s.send("一二三"[k])
    assert g.last_turn is not None and g.last_turn.kind == "turn"
    # 客人先答话、当场赔礼；出手的人这一掌没有落下
    new = [e for e in st.events_tail if e.id >= first]
    verbs = [(e.actor, e.verb) for e in new]
    assert (GUEST, "apologize") in verbs and (VICTOR, "slap") not in verbs, verbs
    said = next(e for e in new if e.actor == st.player and e.verb == "say")
    assert said.speech is not None and said.speech.act == "request" and said.speech.to == [GUEST]
    assert st.flags.get("apologized") and not st.flags.get("slapped")
    assert not [x for x in st.chars[GUEST].statuses if x.kind == "injured"]
    assert not any(sit.kind == "bout_laugh" for sit in st.situations.values())
    # 答应的那一步没有卡在他的打算里；没有回滚
    ag = st.chars[GUEST].agenda
    assert ag is None or not [x for x in ag.steps if x.id.startswith("req_") and x.state == "pending"]
    assert not [v for v in st.violations if "rollback" in v], st.violations
    shown = text_of(evs)
    assert "赔" in shown and "掴" not in shown, shown
    assert "情境" not in shown and "答应了：答应" not in shown, shown
    # 时间过去：过了期限加一日的宽限，那记耳光成了一条分岔，起因是玩家那句劝
    for _ in range(60):
        if any(d.mark == "m_slap" for d in st.divergences) or st.ended is not None:
            break
        await s.send("我找个角落歇息两个时辰")
    assert st.ended is None
    (dv,) = [d for d in st.divergences if d.mark == "m_slap"]
    assert dv.cause_event == said.id, (dv, said.id)
    assert not st.flags.get("slapped")
    assert invariants.check(st, g.defs) == []
    await s.close()


async def test_speech_only_free_input_is_noted_as_that_speech(real_settings: Settings, monkeypatch) -> None:
    """P4：自由输入只说了一句话时，game 把那句话一并交给 storyteller.note_action（下回合不再把同一句当选项给）。"""
    from tianlong import storyteller

    seen: list[tuple[str, object]] = []
    orig = storyteller.note_action

    def spy(state, defs, action, speech=None):
        seen.append((action.verb, speech))
        return orig(state, defs, action, speech)

    monkeypatch.setattr(storyteller, "note_action", spy)
    s = await new_session(real_settings, seed=1)
    st = s.state
    await s.send("我对身边的人说：这场比剑当真好看")
    assert s.game.last_turn is not None and s.game.last_turn.kind == "turn"
    assert seen and seen[-1][0] == "wait" and seen[-1][1] is not None, seen
    sp = seen[-1][1]
    assert sp.spoken_text and "比剑" in sp.spoken_text
    # 记下的是那句话（言语行为与对象），不是「等了一刻」
    last = (st.storyteller.get("recent_chosen") or [])[-1]
    assert last[1] != "wait" and str(st.storyteller["last_chosen"]["sig"]).startswith("say"), last
    await s.send("我四下看看")
    assert seen[-1] == ("look", None)
    await s.close()
