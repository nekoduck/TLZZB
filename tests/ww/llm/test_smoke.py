from __future__ import annotations

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
