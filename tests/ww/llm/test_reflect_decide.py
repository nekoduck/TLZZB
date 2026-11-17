from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from tianlong.llm.errors import ClaudeError
from werewolf.llm.decide import choose
from werewolf.llm.reflect import LEVELS, level_to_prob, monologue, reflect

from .conftest import build_gateway, choice, score
from .helpers import persona


def test_level_to_prob_clamps_and_maps() -> None:
    assert level_to_prob(0) == 0.02 and level_to_prob(4) == 0.98
    assert level_to_prob(2) == pytest.approx(0.5)
    assert level_to_prob(1) == pytest.approx(0.25) and level_to_prob(-3) == 0.02 and level_to_prob(9) == 0.98
    assert level_to_prob(1, n_levels=3) == pytest.approx(0.5)


async def test_reflect_maps_scores_to_deltas(cfg) -> None:
    scores = {'wolf_2': 4.0, 'wolf_3': 0.0, 'wolf_5': 2.0, 'wolf_7': 3.0}

    def fn(kind, state, questions):
        return {q: score(scores[q]) for q in questions}

    gw = build_gateway(cfg, jev_fn=fn)
    cur = {2: 0.5, 3: 0.3, 5: 0.5}  # 7 号没有当前值：按 1/3 算
    res = await reflect(gw, '【场上】……', [2, 3, 5, 7], cur, 'private')
    assert res is not None and res.source == 'jev' and res.basis == 'private'
    assert res.deltas[2] == pytest.approx(0.98 - 0.5)
    assert res.deltas[3] == pytest.approx(0.02 - 0.3)
    assert res.deltas[5] == pytest.approx(0.0)
    assert res.deltas[7] == pytest.approx(0.75 - 1 / 3)
    assert res.focus == 2
    kind, state, questions = gw.llm.jev.calls[0]
    assert kind == 'J3_reflect' and set(questions) == {'wolf_2', 'wolf_3', 'wolf_5', 'wolf_7'}
    assert questions['wolf_2']['criteria'] == LEVELS


async def test_reflect_public_basis_and_no_focus(cfg) -> None:
    gw = build_gateway(cfg, jev_fn=lambda k, s, q: {n: score(1.0) for n in q})
    res = await reflect(gw, 's', [3, 4], {3: 0.3, 4: 0.3}, 'public')
    assert res is not None and res.basis == 'public' and res.focus is None


async def test_reflect_failures_return_none(cfg) -> None:
    def boom(*a):
        raise ValueError('x')

    assert await reflect(build_gateway(cfg, jev_fn=boom), 's', [3], {3: 0.3}, 'private') is None
    assert await reflect(build_gateway(cfg, offline=True), 's', [3], {3: 0.3}, 'private') is None
    assert await reflect(build_gateway(cfg), 's', [], {}, 'private') is None


async def test_monologue(cfg) -> None:
    gw = build_gateway(cfg, claude_texts=['3号那一票投得太巧了，我得盯紧他。'])
    assert await monologue(gw, '【场上】……', persona()) == '3号那一票投得太巧了，我得盯紧他。'
    system, user = gw.llm.claude.prompts[0]
    assert user == '【场上】……' and any('内心独白' in b for b in system)
    assert [r['kind'] for r in gw.llm.recorder.records] == ['W_monologue']


async def test_monologue_failures_return_none(cfg) -> None:
    assert await monologue(build_gateway(cfg, claude_texts=[ClaudeError('x')]), 's', persona()) is None
    assert await monologue(build_gateway(cfg, offline=True), 's', persona()) is None
    gw = build_gateway(cfg, claude_texts=[''])
    assert await monologue(gw, 's', persona()) is None
    gw2 = build_gateway(cfg, claude_texts=['x'])
    gw2.ww = gw2.ww.model_copy(update={'route': gw2.ww.route.model_copy(update={'monologue': 'off'})})
    assert await monologue(gw2, 's', persona()) is None


# --------------------------------------------------------------------------- decide

OPTS = {'vote:3': '投 3 号：他踩过我', 'vote:5': '投 5 号', 'vote:abstain': '弃票'}


async def test_choose_returns_normalised_probs(cfg) -> None:
    def fn(kind, state, questions):
        return {'pick': choice('vote:3', {'vote:3': 0.6, 'vote:5': 0.3, 'vote:abstain': 0.3})}

    gw = build_gateway(cfg, jev_fn=fn)
    p = await choose(gw, 'J2_act', '【场上】', '你会投谁？', OPTS)
    assert p == {'vote:3': pytest.approx(0.5), 'vote:5': pytest.approx(0.25), 'vote:abstain': pytest.approx(0.25)}
    kind, state, questions = gw.llm.jev.calls[0]
    assert kind == 'J2_act' and questions['pick']['criteria'] == OPTS and questions['pick']['instructions'] == '你会投谁？'


async def test_choose_bad_kind_and_trivial_options(cfg) -> None:
    gw = build_gateway(cfg, jev_fn=lambda k, s, q: {'pick': choice('vote:3', {'vote:3': 1.0, 'vote:5': 0.0,
                                                                              'vote:abstain': 0.0})})
    await choose(gw, 'nonsense', 's', 'q', OPTS)
    assert gw.llm.jev.calls[0][0] == 'J2_act'
    assert await choose(gw, 'J2_act', 's', 'q', {'pass': '过'}) == {'pass': 1.0}
    assert await choose(gw, 'J2_act', 's', 'q', {}) is None
    assert len(gw.llm.jev.calls) == 1


async def test_choose_failures_are_none(cfg) -> None:
    def boom(*a):
        raise ValueError('x')

    assert await choose(build_gateway(cfg, jev_fn=boom), 'J2_act', 's', 'q', OPTS) is None
    assert await choose(build_gateway(cfg, offline=True), 'J2_act', 's', 'q', OPTS) is None


async def test_choose_times_out(cfg) -> None:
    class Slow(Mapping):  # 仅为触发超时
        pass

    gw = build_gateway(cfg)
    gw.ww = gw.ww.model_copy(update={'limits': gw.ww.limits.model_copy(update={'decide_timeout_s': 0.05})})

    async def slow_ask(*a: Any, **k: Any):
        await asyncio.sleep(1)

    gw.llm.jev.ask = slow_ask  # type: ignore[method-assign]
    assert await choose(gw, 'J2_act', 's', 'q', OPTS) is None
