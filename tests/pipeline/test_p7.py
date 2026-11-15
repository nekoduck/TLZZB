from __future__ import annotations

from pathlib import Path

import yaml

from tianlong import pipeline
from tianlong.pipeline.p7_validate import validate_content

from .conftest import REPO


def test_p7_real_content_has_no_problems() -> None:
    assert validate_content(REPO / 'content') == []


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
