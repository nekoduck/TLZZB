from __future__ import annotations

from werewolf.llm.gateway import Gateway
from werewolf.llm.smoke import brief_from_case, load_yaml, run_smoke

from .conftest import build_gateway


async def test_run_smoke_with_doubles(cfg, capsys) -> None:
    gw = build_gateway(cfg, claude_texts=['我是预言家，6号是我的金水。今天出9号。'])
    report = await run_smoke(gw)
    out = capsys.readouterr().out
    assert report['decide']['probs'] and report['speech']['tier'] in ('claude', 'jev_select', 'template')
    assert report['parse_rules']['trap_false_claims'] == 0
    assert 'check_full' in report and '解析（Jev+正则）' in out and '本次花费' in out


def test_brief_from_case_roundtrip() -> None:
    c = next(c for c in load_yaml('check_cases.yaml') if c['id'] == 'k18')
    b = brief_from_case(c)
    assert b.cover_role is not None and [p.act.kind.value for p in b.points] == [
        'claim_role', 'claim_check', 'vote_intent']


async def test_run_smoke_names_missing_keys_and_skips(cfg, no_keys, capsys) -> None:
    """没有密钥：先说清楚缺哪把（环境变量名）、哪些项目跳过，再只跑规则部分；不联网。"""
    settings, ww = cfg
    gw = Gateway(settings, ww)
    try:
        report = await run_smoke(gw)
    finally:
        await gw.aclose()
    out = capsys.readouterr().out
    first = out.splitlines()[:2]
    assert settings.jev.api_key_env in first[0] and '跳过 Jev 决定' in first[0]
    assert settings.claude.api_key_env in first[1] and '模板档' in first[1]
    assert report['decide'] == {'skipped': 'Jev 不可用'} and report['speech']['tier'] == 'template'
    assert 'parse_jev' not in report and 'check_full' not in report
    assert 'test-jev-key' not in out and 'test-anthropic-key' not in out


async def test_run_smoke_offline_note(cfg, capsys) -> None:
    gw = build_gateway(cfg, offline=True)
    report = await run_smoke(gw, only=['decide'])
    assert report['notes'][0].startswith('离线模式') and report['decide'] == {'skipped': 'Jev 不可用'}
