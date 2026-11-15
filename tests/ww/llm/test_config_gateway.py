from __future__ import annotations

from pathlib import Path

import pytest

from tianlong.config import Settings
from tianlong.llm import make_llm
from werewolf.llm.config import WerewolfSettings, from_settings, load
from werewolf.llm.gateway import Gateway

from .conftest import build_gateway


def test_load_reads_werewolf_sections(cfg: tuple[Settings, WerewolfSettings]) -> None:
    s, ww = cfg
    assert ww.host == '127.0.0.1' and ww.port == 8765 and ww.speed == 'normal' and ww.open_browser is True
    assert ww.route.parse == 'jev' and ww.route.speech == 'claude' and ww.route.monologue == 'claude'
    assert ww.check.block == 0.5 and ww.check.injection == 0.8
    assert ww.mind.jev_weight == 0.4 and ww.mind.top_k == 4 and ww.mind.seer_kill_bias == 0.8
    assert ww.limits.decide_timeout_s == 4.0 and ww.limits.max_speech_chars == 180
    assert ww.limits.human_speech_chars == 500 and ww.limits.quote_chars == 80
    # 公共几节由 tianlong 读
    assert s.jev.model == 'jev-1.14.0' and 'JEV_KEY' in s.jev.api_key_env_fallbacks
    assert s.models['W_speech'].model == 'claude-haiku-5-5'
    assert s.claude.api_key_env_fallbacks == ['CLAUDE_KEY', 'CLAUDE_API_KEY']
    assert s.limits.session_budget_usd == 2.0


def test_defaults_without_werewolf_section(tmp_path: Path) -> None:
    (tmp_path / 'werewolf.toml').write_text('[jev]\nmodel = "x"\n', encoding='utf-8')
    s, ww = load(tmp_path / 'werewolf.toml')
    assert s.jev.model == 'x'
    assert ww == WerewolfSettings()
    assert from_settings(Settings()) == WerewolfSettings()


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    (tmp_path / 'werewolf.toml').write_text('[werewolf]\nport = 9000\nfuture_knob = 1\n', encoding='utf-8')
    _, ww = load(tmp_path / 'werewolf.toml')
    assert ww.port == 9000


def _gw(cfg: tuple[Settings, WerewolfSettings], **kw: object) -> Gateway:
    s, ww = cfg
    return Gateway(s, ww, llm=make_llm(s, mock=False, run_dir=None), **kw)  # type: ignore[arg-type]


def test_route_with_all_keys(cfg: tuple[Settings, WerewolfSettings]) -> None:
    g = _gw(cfg)
    assert {c: g.route(c) for c in ('parse', 'decide', 'speech', 'check', 'reflect', 'monologue')} == {
        'parse': 'jev', 'decide': 'jev', 'speech': 'claude', 'check': 'jev', 'reflect': 'jev',
        'monologue': 'claude'}
    st = g.status()
    assert st.jev and st.claude and st.speech == 'claude' and not st.offline


def test_route_without_claude_key(cfg: tuple[Settings, WerewolfSettings], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('ANTHROPIC_API_KEY')
    g = _gw(cfg)
    assert g.route('speech') == 'jev_select' and g.route('monologue') == 'off'
    assert g.route('parse') == 'jev' and g.route('decide') == 'jev'
    st = g.status()
    assert st.jev and not st.claude and st.speech == 'jev_select'


def test_route_without_jev_key(cfg: tuple[Settings, WerewolfSettings], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('JEV_KEY')
    g = _gw(cfg)
    assert g.route('parse') == 'claude'  # jev -> claude
    assert g.route('decide') == 'rules' and g.route('check') == 'rules' and g.route('reflect') == 'rules'
    assert g.route('speech') == 'claude'
    assert not g.status().jev


def test_route_without_any_key(cfg: tuple[Settings, WerewolfSettings], no_keys: None) -> None:
    g = _gw(cfg)
    assert [g.route(c) for c in ('parse', 'decide', 'speech', 'check', 'reflect', 'monologue')] == [
        'rules', 'rules', 'template', 'rules', 'rules', 'off']
    st = g.status()
    assert not st.jev and not st.claude and st.speech == 'template'


def test_claude_key_fallback_name_counts(cfg: tuple[Settings, WerewolfSettings],
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('ANTHROPIC_API_KEY')
    monkeypatch.setenv('CLAUDE_KEY', 'alt')
    assert _gw(cfg).route('speech') == 'claude'


def test_offline_forces_rules_and_templates(cfg: tuple[Settings, WerewolfSettings]) -> None:
    g = _gw(cfg, offline=True)
    assert [g.route(c) for c in ('parse', 'decide', 'speech', 'check', 'reflect', 'monologue')] == [
        'rules', 'rules', 'template', 'rules', 'rules', 'off']
    st = g.status()
    assert st.offline and not st.jev and not st.claude


def test_exhausted_budget_falls_back(cfg: tuple[Settings, WerewolfSettings]) -> None:
    g = _gw(cfg)
    assert g.spent() == 0.0 and g.route('speech') == 'claude'
    g.llm.ledger.add('W_speech', g.llm.ledger.budget_usd + 0.01)
    assert g.spent() > 2.0
    assert g.route('speech') == 'template' and g.route('decide') == 'rules' and g.route('parse') == 'rules'
    st = g.status()
    assert not st.jev and not st.claude


def test_speech_route_start_tier(cfg: tuple[Settings, WerewolfSettings]) -> None:
    s, ww = cfg
    ww = ww.model_copy(update={'route': ww.route.model_copy(update={'speech': 'jev_select'})})
    g = Gateway(s, ww, llm=make_llm(s, mock=False, run_dir=None))
    assert g.route('speech') == 'jev_select'
    ww2 = ww.model_copy(update={'route': ww.route.model_copy(update={'speech': 'template'})})
    assert Gateway(s, ww2, llm=make_llm(s, mock=False, run_dir=None)).route('speech') == 'template'


def test_missing_model_section_makes_claude_unusable(cfg: tuple[Settings, WerewolfSettings]) -> None:
    s, ww = cfg
    s2 = s.model_copy(update={'models': {k: v for k, v in s.models.items() if k != 'W_speech'}})
    g = Gateway(s2, ww, llm=make_llm(s2, mock=False, run_dir=None))
    assert g.route('speech') == 'jev_select'


def test_run_dir_records_calls(cfg: tuple[Settings, WerewolfSettings], tmp_path: Path) -> None:
    s, ww = cfg
    g = Gateway(s, ww, run_dir=tmp_path / 'run1', llm=None)
    assert g.llm.recorder.path == tmp_path / 'run1' / 'calls.jsonl'


async def test_ask_jev_unavailable_raises(cfg: tuple[Settings, WerewolfSettings]) -> None:
    from tianlong.llm.errors import LLMError

    g = _gw(cfg, offline=True)
    with pytest.raises(LLMError):
        await g.ask_jev('J2_act', 'x', {})
    await g.aclose()


def test_build_gateway_mock(cfg: tuple[Settings, WerewolfSettings]) -> None:
    g = build_gateway(cfg)
    assert g.route('speech') == 'claude'

