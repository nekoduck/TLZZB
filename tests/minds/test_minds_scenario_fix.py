"""答复的要旨：所求之事只是「等一等」这个壳、要点在附注里的（由我来、放行），用提请求时写下的说法。"""

from __future__ import annotations

from tianlong.kernel.state import Agenda, Step
from tianlong.minds import talk


def test_noted_wait_asks_are_described_by_their_desc() -> None:
    vol = {"verb": "wait", "params": {"ticks": 1}, "desc": "让⟦player⟧替⟦boss⟧跑一趟。",
           "volunteer": {"situation": "errand", "role": "runner"}}
    assert talk.describe_do(talk.flat(vol), "boss") == "让⟦player⟧替⟦boss⟧跑一趟"
    permit = {"verb": "wait", "params": {}, "desc": "放⟦player⟧过去", "permit": {"verb": "go", "exit": "yard"}}
    assert talk.describe_do(talk.flat(permit), "boss") == "放⟦player⟧过去"
    # 真是劝人住手的（没有附注）仍说「罢手」
    assert talk.describe_do(talk.flat({"verb": "wait", "params": {}, "desc": "停下手上的事", "stop": True})) == "罢手"
    assert talk.describe_do(talk.flat({"verb": "wait", "params": {}})) == "罢手"


def test_star_text_prefers_the_gist_written_in_content(world) -> None:
    defs, state = world
    c = next(iter(state.chars.values()))
    c.agenda = Agenda(goal="g", steps=[Step(id="s1", text="去山下劝人罢手", do={"verb": "say", "act": "request", "gist": "劝对方罢手。"})])
    assert talk.star_text(state, defs, c) == "劝对方罢手"
    c.agenda.steps[0].do.pop("gist")
    assert talk.star_text(state, defs, c) == "去山下劝人罢手"
