"""规则版自对弈（sim/arena）：每局能打完；内核与心智不变量、台词核对每一步都过；声明零矛盾；
没有两只狼都自称预言家；平衡与分布指标（常驻 60 局用宽的界，300 局的慢测试用第 10 节的目标）；
承诺兑现率；悍跳的狼口径前后一致（含从事件日志重建心智）；is_human 对调（关掉首夜照顾）不改变对局。"""

from __future__ import annotations

import pytest

from werewolf.kernel.claims import ClaimLedger
from werewolf.kernel.rng import Rng
from werewolf.kernel.types import RequestKind, Role
from werewolf.mind.mind import Mind
from werewolf.mind.speechplan import validate_plan
from werewolf.mind.types import MindTuning
from werewolf.sim.arena import ArenaResult, play, summarize

N_FAST = 60


@pytest.fixture(scope='module')
def fast() -> list[ArenaResult]:
    return [play(seed) for seed in range(N_FAST)]


def test_every_game_terminates_without_violations(fast: list[ArenaResult]) -> None:
    for r in fast:
        assert r.game.over and r.game.result is not None
        assert r.violations == [], r.violations[:5]
    assert sum(len(r.reactions) for r in fast) > 0
    assert all(r.snapshots_by_day.get(1) for r in fast)


def test_claims_are_consistent(fast: list[ArenaResult]) -> None:
    s = summarize(fast)
    assert s['contradictions'] == 0 and s['two_wolf_seer'] == 0


def test_distribution_loose_bounds(fast: list[ArenaResult]) -> None:
    s = summarize(fast)
    assert 0.2 <= s['wolf_win'] <= 0.85, s
    assert s['non_pass'] >= 0.85, s
    assert s['seer_claim_d2'] >= 0.55, s
    assert 0.1 <= s['fake_seer'] <= 0.9, s
    assert s['bloc_d1'] <= 0.8, s
    assert s['no_exile'] <= 0.3, s
    assert 0.1 <= s['non_optimal'] <= 0.4, s
    assert 0.6 <= s['respond'] <= 0.98, s


def test_good_ais_mostly_keep_their_word(fast: list[ArenaResult]) -> None:
    s = summarize(fast)
    assert 0.8 <= s['commit'] < 1.0, s


def test_fake_seer_stays_consistent_and_rebuilds_from_the_log(fast: list[ArenaResult]) -> None:
    checked = 0
    for r in fast:
        g = r.game
        pub = [e for e in g.events('judge') if e.vis == 'public']
        lg = ClaimLedger.from_events(pub)
        for s, m in r.minds.items():
            if r.roles[s] != Role.WEREWOLF or not lg.role_claims(s, Role.SEER):
                continue
            checked += 1
            assert lg.contradictions(s) == []
            # 从事件日志重建：同样的人设、身份、随机流，喂同样的可见事件，口径一致
            view = g.view(s)
            fresh = Mind(s, r.personas[s], r.roles[s], g.config, Rng(0, 'rebuilt'))
            for ev in view.events:
                fresh.observe(ev, view)
            assert fresh.cover == m.cover and fresh.cover.role == Role.SEER
            assert [c.model_dump() for c in fresh.cover.checks] == [
                {'night': c.night, 'target': c.target, 'result': c.result} for c in lg.checks_by(s)]
            # 重建后的心智给出的方案仍然过口径校验
            req = next((d for d in r.decisions if d.seat == s and d.kind == RequestKind.SPEAK), None)
            if req is not None:
                from werewolf.testing import Scenario
                sreq = Scenario().request_speak(s).model_copy(update={'day': view.day})
                for p in fresh.speech_variants(sreq, view):
                    assert validate_plan(p, view, fresh.ledger) == [], p.acts
    assert checked > 0


def test_is_human_swap_changes_nothing_without_mercy() -> None:
    tun = MindTuning(mercy_first_night=0.0)

    def log(human: int | None) -> tuple[list[tuple], dict[int, Role]]:
        r = play(3, tuning=tun, human_seat=human, check_kernel=False, check_mind=False)
        return [(e.type, e.actor, e.target, e.data.get('option_id'), e.data.get('kind'))
                for e in r.game.events('judge')], r.roles

    base, roles = log(None)
    # 真人是狼时内核改走头狼流程（那是规则不同，不是心智读了 is_human），所以只换到好人座位上比
    goods = [s for s, x in roles.items() if x != Role.WEREWOLF]
    assert log(goods[0])[0] == base and log(goods[-1])[0] == base


def test_mercy_only_affects_night_one_human_kill_probability() -> None:
    r = play(4, human_seat=5, check_kernel=False, check_mind=False)
    r0 = play(4, human_seat=5, tuning=MindTuning(mercy_first_night=0.0), check_kernel=False, check_mind=False)
    for d, d0 in zip(r.decisions, r0.decisions, strict=False):
        if d.kind in (RequestKind.WOLF_PROPOSE, RequestKind.WOLF_KILL) and d.day == 1:
            assert [c.utility for c in d.candidates] == [c.utility for c in d0.candidates]
            if d0.probs.get('kill:5', 0) > 0:
                assert d.probs['kill:5'] < d0.probs['kill:5']
            break


@pytest.mark.slow
def test_balance_and_distribution_300_games() -> None:
    results = [play(seed, check_kernel=False, check_mind=(seed < 100)) for seed in range(300)]
    s = summarize(results)
    assert all(r.game.over for r in results)
    assert s['violations'] == 0 and s['contradictions'] == 0 and s['two_wolf_seer'] == 0, s
    assert 0.3 <= s['wolf_win'] <= 0.7, s
    assert s['non_pass'] >= 0.85, s
    assert s['seer_claim_d2'] >= 0.7, s
    assert 0.3 <= s['fake_seer'] <= 0.8, s
    assert s['no_exile'] <= 0.25, s
    assert 0.1 <= s['non_optimal'] <= 0.4, s
    assert 0.8 <= s['commit'] < 1.0, s
    assert 0.7 <= s['respond'] <= 0.95, s
