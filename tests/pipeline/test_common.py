from __future__ import annotations

import json
from pathlib import Path

import pytest

from tianlong import pipeline
from tianlong.llm import BatchResult, BatchStatus, make_llm
from tianlong.llm.schemas import EntitiesOut, StyleDraft, StyleSample
from tianlong.pipeline.common import (
    Corpus,
    Ctx,
    MergeNotes,
    Paragraph,
    Paths,
    PipelineError,
    Request,
    ask,
    diff_data,
    filter_knows,
    merge_locked,
    parse_yaml_text,
    plan_yaml,
    system_blocks,
)

from .helpers import scripted


def _corpus() -> Corpus:
    return Corpus([
        Paragraph('ch01_p0001', 1, '甲', 1, '清晨，山里的雾还没散尽，演武堂上已经站满了人。'),
        Paragraph('ch01_p0002', 1, '甲', 2, '白逸拱手道：「在下不会武功。」'),
        Paragraph('ch07_p0001', 7, '庚', 1, '第七回的一段。'),
    ])


def test_verify_exact_substring_only() -> None:
    c = _corpus()
    assert c.verify('在下不会武功。', 'ch01_p0002') == 'ch01_p0002'
    assert c.verify('在下不会武功。', 'ch01_p0001') == 'ch01_p0002'  # 编号给错：按原文改正
    assert c.verify('在下不会武功！', 'ch01_p0002') is None  # 标点不同
    assert c.verify('在下  不会武功。', None) is None
    assert c.verify('', 'ch01_p0002') is None and c.verify('   ', None) is None
    assert c.verify(None) is None


def test_copied_runs() -> None:
    c = _corpus()
    own = '叙述要短。清晨，山里的雾还没散尽，演武堂上已经站满了人。这句是抄的。'
    runs = c.copied_runs(own)
    assert runs == ['清晨，山里的雾还没散尽，演武堂上已经站满了人。']
    assert c.copied_runs('白描为主，句子短，景物三两笔，落在声音气味冷暖上。') == []


def test_merge_locked_rules() -> None:
    old = {
        'a': {'text': '旧 a'},
        'b': {'text': '人工改过', 'locked': True},
        'c': {'text': '草稿漏了'},
        'd': {'locked': True, 'text': '草稿也漏了'},
        'steps': [{'id': 's1', 'text': '旧'}, {'id': 's2', 'text': '锁住', 'locked': True}],
    }
    new = {
        'a': {'text': '新 a'},
        'b': {'text': '模型改的'},
        'e': {'text': '新增'},
        'steps': [{'id': 's1', 'text': '新'}, {'id': 's3', 'text': '新增一步'}],
    }
    notes = MergeNotes()
    got = merge_locked(old, new, notes=notes)
    assert got['a'] == {'text': '新 a'}
    assert got['b'] == old['b'] and got['d'] == old['d']
    assert got['c'] == old['c']  # 顶层漏掉的保留
    assert got['e'] == {'text': '新增'}
    assert got['steps'] == [{'id': 's1', 'text': '新'}, {'id': 's3', 'text': '新增一步'},
                            {'id': 's2', 'text': '锁住', 'locked': True}]
    assert set(notes.locked) == {'b', 'd', 'steps.[s2]'} and notes.kept == ['c']
    # 只在顶层保留漏掉的；里层的以草稿为准
    assert merge_locked({'x': {'p': 1, 'q': 2}}, {'x': {'p': 3}}) == {'x': {'p': 3}}
    # 整份锁住
    assert merge_locked({'locked': True, 'k': 1}, {'k': 2}) == {'locked': True, 'k': 1}


def test_plan_yaml_locked_file_and_unchanged(tmp_path: Path) -> None:
    p = tmp_path / 'x.yaml'
    p.write_text('# 注释\nlocked: true\nk: 1\n', encoding='utf-8')
    assert plan_yaml(p, {'k': 2}).status == 'locked'
    q = tmp_path / 'y.yaml'
    q.write_text('# 注释\nk: 1\n', encoding='utf-8')
    assert plan_yaml(q, {'k': 1}).status == 'unchanged'
    assert plan_yaml(tmp_path / 'z.yaml', {'k': 1}).status == 'new'


def test_diff_data_lines() -> None:
    old = {'loc': 'hall', 'steps': [{'id': 'a', 'n': 1}], 'gone': 1}
    new = {'loc': 'yard', 'steps': [{'id': 'a', 'n': 2}, {'id': 'b', 'n': 1}], 'add': [1]}
    lines = diff_data(old, new)
    assert 'loc：hall → yard' in lines
    assert 'steps[a].n：1 → 2' in lines
    assert any(x.startswith('steps[b]：新增') for x in lines)
    assert any(x.startswith('add：新增') for x in lines)
    assert any(x.startswith('gone：删去') for x in lines)
    assert diff_data(old, old) == []


def test_parse_yaml_text() -> None:
    assert parse_yaml_text('```yaml\na: 1\n```') == ({'a': 1}, None)
    assert parse_yaml_text('') == (None, None)
    data, err = parse_yaml_text('a: [1, 2')
    assert data is None and err and 'YAML' in err


def test_filter_knows() -> None:
    facts = {'F_a': {'text': 'a'}, 'F_late': {'text': 'b', 'tags': ['after_opening']}, 'exit:x:y': {}}
    assert filter_knows(['F_a', 'F_late', 'F_none', 'exit:x:y', 'F_a'], facts) == (
        ['F_a', 'exit:x:y'], ['F_late', 'F_none'])


async def test_system_blocks_only_first_chapters_and_stable(settings, kw, build: Path) -> None:
    assert await pipeline.arun('p0', settings, **kw) == 0
    paths = Paths(corpus=kw['corpus'], build=build, content=kw['content'])
    ctx = Ctx(settings=settings, paths=paths, max_chapter=2)
    rules, text = ctx.blocks
    assert '[ch01_p0001] 清晨' in text and '[ch02_p0004]' in text
    assert 'ch03_p0001' not in text
    assert '`same_loc`' in rules and '`give`' in rules  # 速查表取自内核
    assert system_blocks(ctx.corpus, 2) == [rules, text]  # 逐字不变，缓存才命中
    assert system_blocks(_corpus(), 6)[1].count('ch07') == 0


class _FakeBatchClaude:
    """不是 Mock 的 Batch 客户端：第一次轮询还在处理。"""

    mock = False

    def __init__(self) -> None:
        self.submits = 0
        self.polls = 0
        self.items = []

    async def batch_submit(self, kind, items, *, ttl=None):
        self.submits += 1
        self.items = list(items)
        assert ttl == '1h'
        return 'batch_1'

    async def batch_poll(self, bid):
        self.polls += 1
        return BatchStatus(id=bid, processing_status='ended' if self.polls >= 3 else 'in_progress')

    async def batch_results(self, bid, kind, *, schemas=None, ttl=None):
        out = {}
        for it in self.items:
            obj = it.mock_default()
            out[it.custom_id] = BatchResult(custom_id=it.custom_id, ok=True, obj=obj)
        out[self.items[-1].custom_id] = BatchResult(custom_id=self.items[-1].custom_id, ok=False,
                                                    error='errored: overloaded')
        return out


class _Holder:
    def __init__(self, claude) -> None:
        self.claude = claude


async def test_batch_resume_does_not_resubmit(settings, kw, build: Path) -> None:
    assert await pipeline.arun('p0', settings, **kw) == 0
    fake = _FakeBatchClaude()
    paths = Paths(corpus=kw['corpus'], build=build, content=kw['content'])
    ctx = Ctx(settings=settings, paths=paths, llm=_Holder(fake), batch=True, poll_interval=0.0, max_wait=0.0,
              out=lambda s: None)
    reqs = [Request(f'r{i}', f'问题 {i}', EntitiesOut, lambda: EntitiesOut(entities=[])) for i in range(3)]
    with pytest.raises(PipelineError):  # 还没处理完：记下批号，稍后重跑
        await ask(ctx, 'P1', reqs)
    pending = build / 'batches' / 'P1.json'
    assert json.loads(pending.read_text(encoding='utf-8'))['batch_id'] == 'batch_1'
    ctx.max_wait = 3600.0
    got = await ask(ctx, 'P1', reqs)
    assert fake.submits == 1
    assert [o.source for o in got.values()] == ['model', 'model', 'rules']
    assert got['r2'].error == 'errored: overloaded'
    assert not pending.exists()


async def test_p6_guide_drops_copied_lines(settings, kw, mock_llm, content: Path, build: Path) -> None:
    def p6(d: StyleDraft) -> StyleDraft:
        d.guide_md = '# 文风\n\n- 白描为主，句子短。\n- 例如：白逸独自走到断崖边。崖边风大，往下看云气翻涌，看不见底。\n'
        d.samples = [StyleSample(scene='写景', text='崖边风大，往下看云气翻涌，看不见底。', para='ch03_p0001')]
        return d

    llm = scripted(mock_llm, {'P6': p6})
    assert await pipeline.arun('p0', settings, **kw) == 0
    assert await pipeline.arun('p6', settings, llm=llm, **kw) == 0
    guide = (content / 'style' / 'guide.md').read_text(encoding='utf-8')
    assert '白描为主' in guide and '断崖边' not in guide
    assert '删去含原文' in (build / 'p6_report.md').read_text(encoding='utf-8')


async def test_p6_locked_guide_not_overwritten(settings, kw, mock_llm, content: Path) -> None:
    g = content / 'style' / 'guide.md'
    g.write_text('<!-- locked -->\n人写的要点。\n', encoding='utf-8')

    def p6(d: StyleDraft) -> StyleDraft:
        d.guide_md = '模型的要点。'
        return d

    assert await pipeline.arun('p0', settings, **kw) == 0
    assert await pipeline.arun('p6', settings, llm=scripted(mock_llm, {'P6': p6}), **kw) == 0
    assert g.read_text(encoding='utf-8') == '<!-- locked -->\n人写的要点。\n'


async def test_budget_is_raised_for_pipeline(settings, kw, monkeypatch) -> None:
    """自建的 llm 用管线自己的预算，不受单局 5 美元护栏限制。"""
    made = []

    def spy(*a, **k):
        llm = make_llm(*a, **k)
        made.append(llm)
        return llm

    monkeypatch.setattr(pipeline, 'make_llm', spy)
    assert await pipeline.arun('p0', settings, **kw) == 0
    assert not made  # P0 不用模型，不建客户端
    assert await pipeline.arun('p1', settings, mock=True, budget_usd=12.5, **kw) == 0
    assert made[0].ledger.budget_usd == 12.5 and made[0].mock
