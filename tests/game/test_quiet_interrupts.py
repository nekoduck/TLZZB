"""静下来等、歇、读、练满一个时辰以上时，什么才算该停下来看的动静（方案 6.9、8.7；ISSUES_wave3 的 T4）。

旁人之间的闲话与小动作不打断；冲着玩家来的事、巨响、有人来去、有人死伤、险情加重、情境开场照样打断。
"""

from __future__ import annotations

from tianlong.config import Settings
from tianlong.game import QUIET_LONG_VERBS
from tianlong.kernel import actions, resolve
from tianlong.kernel.state import Speech

from .conftest import Session, assert_full_turn, new_session, text_of

QUIET_YARD = ("一", "二", "我去前院看看")  # 把开场那场事演完，再走到没人的前院


def _wait(s: Session) -> actions.ActionInst:
    act = actions.build(s.state, s.game.defs, s.state.player, "wait", {"ticks": 16})
    assert act is not None
    return act


def _say(s: Session, who: str, act: str, to: list[str]) -> list:
    st, df = s.state, s.game.defs
    carrier = actions.build_speech_wait(st, df, who)
    assert carrier is not None
    evs = resolve.apply(st, df, carrier, Speech(act=act, to=to), source="mind")
    assert evs and evs[-1].speech is not None, (who, act, to)
    return evs


def _stops(s: Session, evs: list, *, quiet: bool = True) -> bool:
    g = s.game
    return g._interrupts(_wait(s), evs, g._danger(), g._situations_here(), quiet=quiet)


async def test_chatter_between_others_does_not_break_a_quiet_hour(settings: Settings) -> None:
    s = await new_session(settings)
    await s.play(*QUIET_YARD)
    st = s.state
    pid = st.player
    s.fx([{"move": ["guest", "yard"]}, {"move": ["girl", "yard"]}, {"remove_status": ["girl", "hidden"]}])
    assert QUIET_LONG_VERBS == ("wait", "rest", "study", "practice")
    # 旁人之间的一句话：玩家听见了，可不是对他说的
    chat = _say(s, "guest", "greet", ["girl"])
    assert chat[-1].witnesses.get(pid) == "saw" and pid not in chat[-1].speech.to
    assert not _stops(s, chat)
    assert _stops(s, chat, quiet=False)  # 不到一个时辰的等待照旧：有动静就停
    # 对满院子的人说的寻常话：也不算冲着他来
    room = _say(s, "guest", "greet", [])
    assert set(room[-1].speech.to) == {pid, "girl"} and not _stops(s, room)
    # 对他一个人说的：停
    mine = _say(s, "girl", "greet", [pid])
    assert mine[-1].speech.to == [pid] and _stops(s, mine)
    # 旁人之间的小动作（递东西、自己忙活）不算；冲着他来的动作算
    quiet_act = s.fx([{"emit": {"actor": "guest", "verb": "fidget", "text": "⟦guest⟧理了理衣袖"}}])
    assert quiet_act[-1].witnesses.get(pid) == "saw" and not _stops(s, quiet_act)
    assert _stops(s, quiet_act, quiet=False)
    poke = s.fx([{"emit": {"actor": "guest", "verb": "poke", "text": "⟦guest⟧拍了拍⟦player⟧",
                           "params": {"target": pid}}}])
    assert poke[-1].params.get("target") == pid and _stops(s, poke)
    # 巨响、有人走开、眼前有人受伤：停
    assert _stops(s, s.fx([{"emit": {"actor": "guest", "verb": "shout", "text": "⟦guest⟧大喊了一声", "loud": True}}]))
    hurt = s.fx([{"injure": ["girl", 1, "guest"]}])
    assert any(k.get("k") == "harmed" for e in hurt for k in e.kinds) and _stops(s, hurt)
    came = s.fx([{"move": ["fighter", "yard"]}])
    assert came[-1].verb in ("enter", "arrive") and came[-1].witnesses.get(pid) == "saw" and _stops(s, came)
    left = resolve.apply(st, s.game.defs, next(a for a in actions.legal_actions(st, s.game.defs, "fighter")
                                               if a.verb == "go"), source="mind")
    assert left[0].verb == "go" and left[0].witnesses.get(pid) == "saw" and _stops(s, left[:1])
    # 不是哪个人做的事（到点的事、身上的征兆）：停
    omen = s.fx([{"emit": {"verb": "omen", "text": "远处隐隐传来钟声", "loc": "yard"}}])
    assert omen[-1].actor is None and _stops(s, omen)
    await s.close()


async def test_an_hour_long_wait_sits_through_small_talk(settings: Settings) -> None:
    """整回合走一遍：等两个时辰，中途旁人自顾自的动静不打断，照样写进叙述；来了人才停。"""
    s = await new_session(settings)
    await s.play(*QUIET_YARD)
    st = s.state
    s.fx([{"move": ["girl", "yard"]}, {"remove_status": ["girl", "hidden"]}])
    t0 = st.tick
    mutter = {"emit": {"actor": "girl", "verb": "mutter", "text": "⟦girl⟧自言自语地嘀咕了几句"}}
    s.fx([{"schedule": [5, [mutter]]}])
    evs = await s.send("我在这里等上两个时辰")
    assert_full_turn(evs)
    assert st.tick == t0 + 16 and not s.game.last_turn.interrupted
    assert "嘀咕" in text_of(evs)  # 没有为它停下，可玩家看见了
    # 只等三刻（不满一个时辰）：照旧，一有动静就停
    t1 = st.tick
    s.fx([{"schedule": [2, [mutter]]}])
    await s.send("我在这里等上三刻")
    assert s.game.last_turn.interrupted and st.tick == t1 + 2
    # 等两个时辰，中途有人走进院子：停在那一刻
    t2 = st.tick
    s.fx([{"schedule": [3, [mutter]]}, {"schedule": [10, [{"move": ["guest", "yard"]}]]}])
    await s.send("我在这里等上两个时辰")
    assert s.game.last_turn.interrupted and st.tick == t2 + 10
    await s.close()
