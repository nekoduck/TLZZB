from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from tianlong import pipeline
from tianlong.pipeline.p7_validate import validate_content

from .conftest import REPO


def test_p7_real_content_has_no_problems() -> None:
    assert validate_content(REPO / 'content') == []


def _real_copy(tmp_path: Path) -> Path:
    root = tmp_path / 'content'
    shutil.copytree(REPO / 'content', root)
    return root


def _nobody_knows(root: Path) -> list[str]:
    facts = yaml.safe_load((root / 'facts.yaml').read_text(encoding='utf-8'))
    return sorted(fid for fid, f in facts.items() if 'known_by' in f and not f['known_by'])


def test_p7_flags_a_card_that_knows_what_nobody_knows_at_opening(tmp_path: Path) -> None:
    """事实表明写「开局时无人知道」的事（known_by 为空），进了谁的 knows 都要报出来。"""
    root = _real_copy(tmp_path)
    nobody = _nobody_knows(root)
    assert nobody  # 种子里有这样的事实：开局之后才有的事、无人知道的秘密
    cid = sorted(p.stem for p in (root / 'characters').glob('*.yaml'))[0]
    path = root / 'characters' / f'{cid}.yaml'
    card = yaml.safe_load(path.read_text(encoding='utf-8'))
    card['knows'] = nobody + list(card.get('knows') or [])
    path.write_text(yaml.safe_dump(card, allow_unicode=True, sort_keys=False), encoding='utf-8')
    problems = validate_content(root)
    for fid in nobody:
        assert any(cid in x and fid in x and '无人知道' in x for x in problems), (fid, problems)
    assert len(problems) == len(nobody)  # 别的人、别的事实不受牵连


def test_p7_flags_the_player_and_extras_too(tmp_path: Path) -> None:
    root = _real_copy(tmp_path)
    fid = _nobody_knows(root)[0]
    op = yaml.safe_load((root / 'opening.yaml').read_text(encoding='utf-8'))
    op['player']['knows'] = [fid] + list(op['player'].get('knows') or [])
    extra = op['extras'][0]
    extra['knows'] = [fid] + list(extra.get('knows') or [])
    (root / 'opening.yaml').write_text(yaml.safe_dump(op, allow_unicode=True, sort_keys=False), encoding='utf-8')
    problems = validate_content(root)
    assert sorted(x.split(' ')[0] for x in problems if fid in x and '无人知道' in x) == sorted(['player', extra['id']])
    assert len(problems) == 2


async def test_p7_step_on_real_content(settings, tmp_path: Path, out: list[str]) -> None:
    code = await pipeline.arun('p7', settings, content=REPO / 'content', build=tmp_path / 'build',
                               corpus=tmp_path / 'none.txt', out=out.append)
    assert code == 0, '\n'.join(out)
    assert '通过' in out[0]
    assert (tmp_path / 'build' / 'p7_report.md').is_file()


async def test_p7_reports_problems_with_nonzero_exit(settings, content: Path, tmp_path: Path,
                                                     out: list[str]) -> None:
    op = yaml.safe_load((content / 'opening.yaml').read_text(encoding='utf-8'))
    op['chars']['fighter']['loc'] = 'nowhere'
    (content / 'opening.yaml').write_text(yaml.safe_dump(op, allow_unicode=True), encoding='utf-8')
    code = await pipeline.arun('p7', settings, content=content, build=tmp_path / 'build', out=out.append)
    assert code == 1
    assert any('nowhere' in x for x in out)


async def test_p7_load_error_is_a_problem_not_a_crash(settings, content: Path, tmp_path: Path,
                                                      out: list[str]) -> None:
    (content / 'items.yaml').write_text('rope: [unclosed\n', encoding='utf-8')
    code = await pipeline.arun('p7', settings, content=content, build=tmp_path / 'build', out=out.append)
    assert code == 1
    assert any('加载失败' in x for x in out)


async def test_p7_checks_quotes_against_corpus(settings, kw, content: Path, out: list[str]) -> None:
    assert await pipeline.arun('p0', settings, **kw) == 0
    (content / 'style' / 'samples.yaml').write_text(
        yaml.safe_dump({'写景': [{'text': '不是原文的一句。', 'para': 'ch01_p0001'}]}, allow_unicode=True),
        encoding='utf-8')
    card = yaml.safe_load((content / 'characters' / 'fighter.yaml').read_text(encoding='utf-8'))
    card['voice']['samples'] = [{'text': '谁在那里？', 'para': 'ch02_p0004'}]
    (content / 'characters' / 'fighter.yaml').write_text(yaml.safe_dump(card, allow_unicode=True), encoding='utf-8')
    assert await pipeline.arun('p7', settings, **kw) == 1
    text = '\n'.join(out)
    assert 'style/samples.yaml 写景[0]：不是原文的子串' in text
    assert 'fighter.yaml voice.samples' not in text


async def test_p7_baseline_hook(settings, content: Path, tmp_path: Path, out: list[str]) -> None:
    seen: list[Path] = []

    async def baseline(path: Path) -> list[str]:
        seen.append(path)
        return ['参照点 m_slap 没有按时成立']

    code = await pipeline.arun('p7', settings, content=content, build=tmp_path / 'build', baseline=baseline,
                               out=out.append)
    assert code == 1 and seen == [content]
    assert any('基线跑：参照点 m_slap' in x for x in out)
