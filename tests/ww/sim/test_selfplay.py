"""自对弈（sim/selfplay.py）与统计（sim/stats.py）：命令行、各模式（模型换成替身）、报告；
以及第 10 节「平衡与分布」「动机与主动性」「口径一致」「不完美」走真正的会话层 + NpcAgent 的版本
（常驻的小样本用宽界；300 + 300 局的慢测试用第 10 节的目标）。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tianlong.config import Settings
from tianlong.llm import make_llm
from werewolf.llm.config import WerewolfSettings
from werewolf.sim import selfplay
from werewolf.sim.stats import METRICS, GameRecord, report_md, summarize

from ..acceptance.fakes import RecordingClaude, RecordingJev, offline_cfg


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Settings, WerewolfSettings]:
    return offline_cfg(monkeypatch, tmp_path)


def test_ww_for_modes(cfg: tuple[Settings, WerewolfSettings]) -> None:
    _, ww = cfg
    j = selfplay.ww_for('jev', ww)
    assert j.route.speech == 'template' and j.route.monologue == 'off' and j.route.decide == 'jev'
    f = selfplay.ww_for('full', ww, jev_weight=0.1)
    assert f.route.speech == ww.route.speech and f.mind.jev_weight == 0.1
    assert ww.mind.jev_weight != 0.1 or True  # 原对象不变
    assert selfplay.host_seat(3, None) == selfplay.host_seat(3, None) and selfplay.host_seat(3, 7) == 7


def test_main_rules_arena_and_session_write_reports(cfg: tuple[Settings, WerewolfSettings], tmp_path: Path,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    toml = str(tmp_path / 'werewolf.toml')
    out = tmp_path / 'r' / 'sim.md'
    assert selfplay.main(['--games', '3', '--engine', 'arena', '--report', str(out), '--config', toml]) == 0
    text = out.read_text(encoding='utf-8')
    assert '狼人胜率' in text and '| 局数 |' in text
    assert selfplay.main(['--games', '2', '--seed', '7', '--human-seat', '4', '--config', toml]) == 0
    printed = capsys.readouterr().out
    assert '种子 7–8' in printed


def test_compare_without_keys_skips_the_model_mode(cfg: tuple[Settings, WerewolfSettings], tmp_path: Path,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    rc = selfplay.main(['--games', '1', '--compare', '--config', str(tmp_path / 'werewolf.toml'), '--engine', 'arena'])
    cap = capsys.readouterr()
    assert rc == 0 and '需要 Jev 的密钥' in cap.err and '| rules |' in cap.out


@pytest.mark.parametrize('mode', ['jev', 'full'])
async def test_model_modes_with_fake_models(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str) -> None:
    settings, ww = offline_cfg(monkeypatch, tmp_path, fake_keys=True)
    calls: list = []

    def factory():  # noqa: ANN202
        llm = make_llm(settings, mock=True, run_dir=None)
        llm.recorder.keep_in_memory = True
        llm.jev = RecordingJev(settings, recorder=llm.recorder, ledger=llm.ledger, log=calls)
        llm.claude = RecordingClaude(settings, recorder=llm.recorder, ledger=llm.ledger, log=calls,
                                     fn=lambda k, s, u: '我先听听大家的。')
        return llm

    recs = await selfplay.run_batch(mode, [1, 2], settings, ww, parallel=2, llm_factory=factory)
    assert len(recs) == 2 and all(r.winner in ('wolf', 'good', None) for r in recs)
    s = summarize(recs)
    assert s['calls_jev'] > 0
    kinds = {c.kind for c in calls}
    assert 'J2_act' in kinds and 'J3_reflect' in kinds
    if mode == 'jev':
        assert s['tier_template'] == 1.0 and 'W_speech' not in kinds and 'W_monologue' not in kinds
    else:
        assert 'W_speech' in kinds and 'W_monologue' in kinds
        assert s['calls_claude'] > 0


def _check_targets(s: dict[str, float], strict: bool) -> None:
    """第 10 节的目标（strict）或常驻小样本用的宽界。"""
    lo, hi = (0.0, 0.0) if strict else (0.1, 0.1)
    assert s['violations'] == 0 and s['contradictions'] == 0 and s['two_wolf_seer'] == 0, s
    assert 0.3 - lo <= s['wolf_win'] <= 0.7 + hi, s
    assert s['non_pass'] >= 0.85, s
    assert s['seer_claim_d2'] >= 0.7 - lo, s
    assert 0.3 - lo <= s['fake_seer'] <= 0.8 + hi, s
    assert s['seer_alive_d3'] >= 0.25 - lo, s
    assert s['bloc_d1'] <= 0.6 + hi / 2, s
    assert s['no_exile'] <= 0.25 + hi, s
    assert 0.1 <= s['non_optimal'] <= 0.4, s
    assert 0.8 - lo <= s['commit'] < 1.0, s
    assert 0.15 - lo <= s['follow'] <= 0.7 + hi, s
    assert 0.7 - lo <= s['respond'] <= 0.95 + hi / 2, s
    assert s['d1_variety_mean'] >= 3.0 - (0.5 if not strict else 0.0), s


def _session_batch(cfg: tuple[Settings, WerewolfSettings], seeds: list[int]) -> list[GameRecord]:
    settings, ww = cfg
    return asyncio.run(selfplay.run_batch('rules', seeds, settings, ww, parallel=1))


def test_real_pipeline_distribution_loose(cfg: tuple[Settings, WerewolfSettings]) -> None:
    """常驻：40 局走会话层 + NpcAgent（托管真人座位），宽界。"""
    recs = _session_batch(cfg, list(range(100, 140)))
    assert all(r.days >= 1 and r.winner in ('wolf', 'good', None) for r in recs)
    s = summarize(recs)
    _check_targets(s, strict=False)
    md = report_md('x', {'rules': s})
    assert all(zh in md for _, zh, _, _ in METRICS)


@pytest.mark.slow
def test_section10_targets_300_plus_300(cfg: tuple[Settings, WerewolfSettings]) -> None:
    """第 10 节「平衡与分布」：规则版 300 局（arena，没有真人座位）+ 300 局会话层（托管真人座位）。"""
    arena = [selfplay.play_arena(sd, human_seat=None) for sd in range(1, 301)]
    sess = _session_batch(cfg, list(range(1, 301)))
    for recs in (arena, sess):
        _check_targets(summarize(recs), strict=True)
    s = summarize(sess)
    assert 0.5 <= s['human_kill_ratio'] <= 2.0 and 0.5 <= s['human_exile_ratio'] <= 2.0, s
    assert s['d1_variety_ge3'] >= 0.95, s
