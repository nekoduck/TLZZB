from __future__ import annotations

import json
from pathlib import Path

from tianlong import pipeline
from tianlong.pipeline.common import cn_to_int, int_to_cn
from tianlong.pipeline.p0_split import split_paragraphs, split_text

from .conftest import CORPUS


async def test_p0_split_counts(settings, kw, build: Path, out: list[str]) -> None:
    code = await pipeline.arun('p0', settings, **kw)
    assert code == 0
    rows = [json.loads(x) for x in (build / 'paragraphs.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(rows) == 14
    assert [r['id'] for r in rows[:2]] == ['ch01_p0001', 'ch01_p0002']
    assert rows[0]['chapter'] == 1 and rows[0]['seq'] == 1 and rows[0]['title'] == '演武堂比武'
    per_ch = {n: sum(1 for r in rows if r['chapter'] == n) for n in (1, 2, 3)}
    assert per_ch == {1: 6, 2: 4, 3: 4}
    assert rows[-1]['id'] == 'ch03_p0004'
    # 断行拼接，不留空格；缩进与卷首都去掉
    assert rows[10]['text'] == '次日，白逸独自走到断崖边。崖边风大，往下看云气翻涌，看不见底。一株老松斜伸出崖外，根下像是有条小路。'
    assert all(not r['text'].startswith('　') for r in rows)
    assert all('测试用的短文' not in r['text'] for r in rows)
    total = sum(len(r['text']) for r in rows)
    summary = '\n'.join(out)
    assert '3 回' in summary and '14 段' in summary and f'{total} 字' in summary
    assert '第一回 演武堂比武：6 段' in summary
    # 只有三回：提示缺第四至六回
    assert '缺第四回' in summary


def test_split_handles_toc_title_lines_and_false_headings() -> None:
    text = '\n'.join([
        '目录',
        '第一回　甲',
        '第二回　乙',
        '',
        '第一回',
        '　　甲回目',
        '　　第一段，说到第三回合便输了。',
        '　　第二段。',
        '第2回 乙',
        '一行一段甲。',
        '一行一段乙。',
        '一行一段丙。',
        '一行一段丁。',
    ])
    res = split_text(text)
    assert [c[:3] for c in res.chapters] == [(1, '甲回目', 2), (2, '乙', 4)]
    assert res.paragraphs[0].text == '第一段，说到第三回合便输了。'
    assert res.paragraphs[2].id == 'ch02_p0001' and res.paragraphs[2].text == '一行一段甲。'
    assert len(res.dropped_headings) == 2  # 目录里的两行


def test_split_paragraphs_blank_and_indent() -> None:
    lines = ['　　第一段前半', '后半。', '　　第二段。', '', '第三段。']
    assert split_paragraphs(lines) == ['第一段前半后半。', '第二段。', '第三段。']


def test_numerals() -> None:
    assert [cn_to_int(x) for x in ('一', '十', '十一', '二十', '二十三', '一百零五', '两', '12')] == \
        [1, 10, 11, 20, 23, 105, 2, 12]
    assert [int_to_cn(x) for x in (1, 6, 10, 14, 20, 37)] == ['一', '六', '十', '十四', '二十', '三十七']


async def test_missing_corpus_gives_clear_message(settings, kw, tmp_path: Path, out: list[str], capsys) -> None:
    kw['corpus'] = tmp_path / 'nope' / 'tianlong.txt'
    code = await pipeline.arun('p0', settings, **kw)
    assert code == 2
    msg = '\n'.join(out)
    assert 'corpus/tianlong.txt' in msg and 'UTF-8' in msg
    assert 'Traceback' not in msg and 'Traceback' not in capsys.readouterr().err


async def test_later_step_without_p0_gives_clear_message(settings, kw, out: list[str]) -> None:
    code = await pipeline.arun('p1', settings, mock=True, **kw)
    assert code == 2
    assert 'pipeline p0' in '\n'.join(out)


async def test_bad_step_name(settings, kw, out: list[str]) -> None:
    assert await pipeline.arun('p9', settings, **kw) == 2
    assert 'p0' in out[0]


async def test_non_utf8_corpus(settings, kw, tmp_path: Path, out: list[str]) -> None:
    bad = tmp_path / 'gbk.txt'
    bad.write_bytes(CORPUS.read_text(encoding='utf-8').encode('gbk'))
    kw['corpus'] = bad
    assert await pipeline.arun('p0', settings, **kw) == 2
    assert 'UTF-8' in '\n'.join(out)


def test_sync_entry_point(settings, kw, build: Path) -> None:
    assert pipeline.run('p0', settings, **kw) == 0
    assert (build / 'paragraphs.jsonl').is_file()
