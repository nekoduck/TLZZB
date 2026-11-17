from __future__ import annotations

from pathlib import Path

import pytest

from tianlong.config import Settings, load_settings
from tianlong.llm.specs import claude_spec, config_key, is_jev_kind, jev_spec, model_family, spec_for

REPO = Path(__file__).resolve().parents[2]


def test_loads_repo_config(settings: Settings) -> None:
    assert settings.jev.model.startswith('jev-')
    assert settings.jev.timeout_s == 2.0
    assert settings.jev.thresholds.choice_margin == 0.15
    assert settings.models['C3'].thinking == 'between_tools'
    assert settings.models['pipeline'].batch is True
    assert settings.prices['sonnet'].cache_read == 0.10
    assert settings.root == REPO
    assert settings.path('runs') == REPO / 'runs'


def test_jev_key_fallback_order(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.delenv('JEV_API_KEY', raising=False)
    monkeypatch.setenv('JEV_KEY', 'fallback-value')
    assert settings.jev_api_key() == 'fallback-value'
    assert settings.jev_key_source() == 'JEV_KEY'
    monkeypatch.setenv('JEV_API_KEY', 'primary-value')
    assert settings.jev_api_key() == 'primary-value'
    assert settings.jev_key_source() == 'JEV_API_KEY'
    monkeypatch.delenv('JEV_API_KEY')
    monkeypatch.delenv('JEV_KEY')
    assert settings.jev_api_key() is None


def test_keys_never_in_settings_dump(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setenv('JEV_KEY', 'sekrit-123')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sekrit-456')
    text = repr(settings) + settings.model_dump_json()
    assert 'sekrit' not in text


def test_dotenv_does_not_override_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / 'config.toml').write_text("[jev]\nmodel = 'jev-x'\n", encoding='utf-8')
    (tmp_path / '.env').write_text('JEV_API_KEY=from-dotenv\n', encoding='utf-8')
    monkeypatch.setenv('JEV_API_KEY', 'from-env')
    s = load_settings(tmp_path / 'config.toml')
    assert s.jev.model == 'jev-x'
    assert s.jev_api_key() == 'from-env'
    monkeypatch.delenv('JEV_API_KEY')
    s2 = load_settings(tmp_path / 'config.toml')
    assert s2.jev_api_key() == 'from-dotenv'


def test_missing_config_gives_defaults(tmp_path: Path) -> None:
    s = load_settings(tmp_path / 'nope.toml')
    assert s.jev.model == 'jev-1.13.0'
    assert s.limits.session_budget_usd == 5.0


def test_specs(settings: Settings) -> None:
    assert is_jev_kind('J1') and is_jev_kind('J2a') and is_jev_kind('J6')
    assert not is_jev_kind('C1') and not is_jev_kind('J7')
    js = jev_spec(settings, 'J2b')
    assert js.model == settings.jev.model and js.base_url == 'https://api.typesafe.ai'
    c3 = claude_spec(settings, 'C3')
    assert c3.model == 'claude-sonnet-5-5' and c3.stream and c3.thinking == 'between_tools'
    assert claude_spec(settings, 'C2').config_key == 'C2_soul'
    assert claude_spec(settings, 'C2_named').model == 'claude-haiku-5-5'
    assert config_key('P3') == 'pipeline'
    p = claude_spec(settings, 'P4')
    assert p.model == 'claude-opus-5-5' and p.batch
    assert spec_for(settings, 'J4').provider == 'jev'
    with pytest.raises(KeyError):
        claude_spec(settings, 'C9')


def test_model_family() -> None:
    assert model_family('claude-haiku-5-5') == 'haiku'
    assert model_family('claude-sonnet-5-5') == 'sonnet'
    assert model_family('claude-opus-5-5') == 'opus'
    assert model_family('jev-1.14.0') == 'jev'
    assert model_family('gpt') == 'unknown'
