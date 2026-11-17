from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tianlong import pipeline
from tianlong.content.loader import load
from tianlong.llm import make_llm
from tianlong.llm.schemas import CardQuote, StyleSample
from tianlong.pipeline.common import Corpus, Paths, load_paragraphs
from tianlong.pipeline.p7_validate import validate_content
from tianlong.pipeline.schemas import ChoiceDraft

from .helpers import edit_yaml, scripted


def _corpus(build: Path) -> Corpus:
    return load_paragraphs(Paths(corpus=Path('x'), build=build, content=Path('x')))


@pytest.mark.parametrize('batch', [False, True], ids=['direct', 'batch'])
async def test_all_steps_with_mock(settings, kw, mock_llm, content: Path, build: Path, out: list[str],
                                   batch: bool) -> None:
    code = await pipeline.arun('all', settings, llm=mock_llm, batch=batch, **kw)
    assert code == 0, '\n'.join(out)
    corpus = _corpus(build)
    # P1：种子实体在原文里找到的，带段落编号
    ents = {e['id']: e for e in json.loads((build / 'entities.json').read_text(encoding='utf-8'))['entities']}
    assert {'fighter', 'girl', 'guest', 'hall', 'yard', 'cliff', 'old_manual'} <= set(ents)
    assert all(e['paragraphs'] and all(corpus.valid(p) for p in e['paragraphs']) for e in ents.values())
    assert ents['girl']['paragraphs'][0] == 'ch01_p0006'
    # P2：事件按段落顺序
    events = json.loads((build / 'timeline.json').read_text(encoding='utf-8'))['events']
    order = [corpus.order(e['para_from']) for e in events]
    assert events and order == sorted(order)
    assert any(e['time_hint'] and 'ch03_p0001' in e['time_hint'] for e in events)
    # P3：差异报告有八条
    report = (build / 'diff_report.md').read_text(encoding='utf-8')
    for n in range(1, 9):
        assert f'### {n}. ' in report
    assert '与现有种子不符之处' in report and '人工过目' in report
    # P4：台词样例都是原文的子串，带段落编号
    defs, _state = load(content)
    samples = defs.cards['fighter'].voice.samples
    assert samples and all(corpus.verify(s['text'], s['para']) == s['para'] for s in samples)
    assert defs.cards['girl'].voice.samples[0]['text'] == '他说得不错呀，你这一剑本来就急。'
    # P6：文风短例都是原文的子串
    assert defs.style.samples
    for items in defs.style.samples.values():
        assert 1 <= len(items) <= 5
        assert all(corpus.verify(s['text'], s['para']) == s['para'] for s in items)
    # P7：生成的内容校验通过
    assert validate_content(content, corpus) == []
    # 调用都录下来了：Batch 模式有提交记录；system 两块都打 1 小时缓存
    recs = mock_llm.recorder.records
    kinds = {r['kind'] for r in recs if 'kind' in r}
    assert {'P1', 'P2', 'P3', 'P4', 'P5', 'P6'} <= kinds
    assert any(r.get('batch') == 'submit' for r in recs) is batch
    req = next(r['request'] for r in recs if r.get('kind') == 'P1')
    params = req['batch'][0]['params'] if batch else req
    assert [b['cache_control'] for b in params['system']] == [{'type': 'ephemeral', 'ttl': '1h'}] * 2
    assert params['model'] == settings.models['pipeline'].model


async def test_rerun_is_idempotent(settings, kw, mock_llm, content: Path) -> None:
    assert await pipeline.arun('all', settings, llm=mock_llm, **kw) == 0
    before = {p: p.read_bytes() for p in content.rglob('*') if p.is_file()}
    out2: list[str] = []
    kw['out'] = out2.append
    assert await pipeline.arun('all', settings, llm=mock_llm, **kw) == 0
    after = {p: p.read_bytes() for p in content.rglob('*') if p.is_file()}
    assert before == after
    assert any('写入 0 张卡' in x for x in out2)


async def test_identity_draft_keeps_comments(settings, kw, mock_llm, content: Path) -> None:
    """草稿与现有内容一样时不重写文件，人写的注释保住。"""
    p = content / 'agendas' / 'fighter.yaml'
    p.write_text('# 人写的注释\n' + p.read_text(encoding='utf-8'), encoding='utf-8')
    assert await pipeline.arun('p0', settings, **kw) == 0
    assert await pipeline.arun('p3', settings, llm=mock_llm, **kw) == 0
    assert p.read_text(encoding='utf-8').startswith('# 人写的注释')


async def test_p3_diff_report_lists_every_difference(settings, kw, mock_llm, content: Path, build: Path) -> None:
    def p3(d):
        if d.opening_yaml:
            def ch(o):
                o['chars']['fighter']['loc'] = 'yard'
                o['player']['knows'] = ['F_bout', 'F_made_up']
            d.opening_yaml = edit_yaml(d.opening_yaml, ch)
            d.diff_report = '雷猛开局应在前院（ch01_p0001）。'
            d.checks = d.checks[:7]  # 模型漏了第八条
        for a in d.agendas:
            if a.id == 'fighter':
                a.yaml = edit_yaml(a.yaml, lambda g: g.update(goal='赢下比武，再去前院'))
        return d

    llm = scripted(mock_llm, {'P3': p3})
    assert await pipeline.arun('p0', settings, **kw) == 0
    assert await pipeline.arun('p3', settings, llm=llm, **kw) == 0
    report = (build / 'diff_report.md').read_text(encoding='utf-8')
    for n in range(1, 9):
        assert f'### {n}. ' in report
    assert '模型未给出结论' in report  # 第八条补上
    assert 'chars.fighter.loc：hall → yard' in report
    assert 'goal：赢下比武；有人取笑就讨回面子 → 赢下比武，再去前院' in report
    assert '雷猛开局应在前院' in report
    assert 'F_made_up' in report  # knows 里删掉的
    opening = yaml.safe_load((content / 'opening.yaml').read_text(encoding='utf-8'))
    assert opening['chars']['fighter']['loc'] == 'yard'
    assert opening['player']['knows'] == ['F_bout']


async def test_substring_verification_drops_invented_quotes(settings, kw, mock_llm, content: Path,
                                                            build: Path) -> None:
    def p4(d):
        for c in d.cards:
            if c.id == 'fighter':
                c.voice_samples = [
                    CardQuote(text='你笑什么？有本事下场来比划比划！', para='ch01_p0004'),
                    CardQuote(text='老子天下第一，谁敢不服！', para='ch01_p0004'),  # 原文里没有
                    CardQuote(text='谁在那里？', para='ch01_p0001'),  # 编号给错：改正
                ]
                c.choices = [ChoiceDraft(situation='被人当众取笑', did='立刻翻脸', para='ch01_p0004'),
                             ChoiceDraft(situation='编的', did='编的', para='ch99_p0001')]
        return d

    def p6(d):
        d.samples = [StyleSample(scene='打斗', text='雷猛追到崖边，拔剑便刺。', para='ch03_p0002'),
                     StyleSample(scene='打斗', text='刀光剑影，杀得天昏地暗。', para='ch03_p0002')]
        return d

    llm = scripted(mock_llm, {'P4': p4, 'P6': p6})
    assert await pipeline.arun('p0', settings, **kw) == 0
    assert await pipeline.arun('p4', settings, llm=llm, **kw) == 0
    assert await pipeline.arun('p6', settings, llm=llm, **kw) == 0
    card = yaml.safe_load((content / 'characters' / 'fighter.yaml').read_text(encoding='utf-8'))
    assert card['voice']['samples'] == [{'text': '你笑什么？有本事下场来比划比划！', 'para': 'ch01_p0004'},
                                        {'text': '谁在那里？', 'para': 'ch02_p0004'}]
    assert card['choices'] == [{'situation': '被人当众取笑', 'did': '立刻翻脸', 'para': 'ch01_p0004'}]
    rep4 = (build / 'p4_report.md').read_text(encoding='utf-8')
    assert '老子天下第一' in rep4 and '不是原文的子串' in rep4 and 'ch99_p0001' in rep4
    samples = yaml.safe_load((content / 'style' / 'samples.yaml').read_text(encoding='utf-8'))
    assert samples == {'打斗': [{'text': '雷猛追到崖边，拔剑便刺。', 'para': 'ch03_p0002'}]}
    assert '刀光剑影' in (build / 'p6_report.md').read_text(encoding='utf-8')


async def test_locked_entries_survive_rerun(settings, kw, mock_llm, content: Path) -> None:
    # 人工改过、加了 locked 的条目
    facts = yaml.safe_load((content / 'facts.yaml').read_text(encoding='utf-8'))
    facts['F_bout'] = {'text': '人工改过的事实句。', 'known_by': ['*'], 'locked': True}
    (content / 'facts.yaml').write_text(yaml.safe_dump(facts, allow_unicode=True, sort_keys=False), encoding='utf-8')
    guest = content / 'characters' / 'guest.yaml'
    guest.write_text(guest.read_text(encoding='utf-8') + 'locked: true\n', encoding='utf-8')
    guest_before = guest.read_text(encoding='utf-8')
    ag = yaml.safe_load((content / 'agendas' / 'fighter.yaml').read_text(encoding='utf-8'))
    ag['steps'][1]['text'] = '人工改过的一步'
    ag['steps'][1]['locked'] = True
    (content / 'agendas' / 'fighter.yaml').write_text(yaml.safe_dump(ag, allow_unicode=True), encoding='utf-8')

    def p5(d):
        if d.facts_yaml:
            def ch(f):
                f['F_bout']['text'] = '模型改的。'
                f['F_bout'].pop('locked', None)
                f['F_well_secret']['text'] = '⟦yard⟧的井壁上有几个坑。'
                del f['F_girl_sister']  # 草稿漏了一条：保留原样
            d.facts_yaml = edit_yaml(d.facts_yaml, ch)
        return d

    def p4(d):
        for c in d.cards:
            c.card_yaml = edit_yaml(c.card_yaml, lambda x: x.update(self_intro='模型重写的介绍。'))
        return d

    def p3(d):
        for a in d.agendas:
            if a.id == 'fighter':
                def ch(g):
                    for st in g['steps']:
                        st['text'] = '模型改的一步'
                        st.pop('locked', None)
                a.yaml = edit_yaml(a.yaml, ch)
        return d

    llm = scripted(mock_llm, {'P3': p3, 'P4': p4, 'P5': p5})
    assert await pipeline.arun('p0', settings, **kw) == 0
    for _ in range(2):  # 重跑两次，结果一样
        for step in ('p3', 'p4', 'p5'):
            assert await pipeline.arun(step, settings, llm=llm, **kw) == 0
    facts = yaml.safe_load((content / 'facts.yaml').read_text(encoding='utf-8'))
    assert facts['F_bout'] == {'text': '人工改过的事实句。', 'known_by': ['*'], 'locked': True}
    assert facts['F_well_secret']['text'] == '⟦yard⟧的井壁上有几个坑。'
    assert 'F_girl_sister' in facts
    assert guest.read_text(encoding='utf-8') == guest_before
    fighter = yaml.safe_load((content / 'characters' / 'fighter.yaml').read_text(encoding='utf-8'))
    assert fighter['self_intro'] == '模型重写的介绍。'
    steps = yaml.safe_load((content / 'agendas' / 'fighter.yaml').read_text(encoding='utf-8'))['steps']
    assert [s['text'] for s in steps] == ['模型改的一步', '人工改过的一步']
    defs, state = load(content)
    assert defs.cards['guest'].self_intro == '路过的读书人，爱笑。'


async def test_llm_unavailable_falls_back_to_rules(settings, kw, content: Path, build: Path,
                                                   out: list[str]) -> None:
    """没有 Claude 密钥：每条请求都退到规则草稿，管线照常走完，不抛异常。"""
    llm = make_llm(settings, mock=False, run_dir=None)
    for batch in (True, False):
        assert await pipeline.arun('all', settings, llm=llm, batch=batch, **kw) == 0
    assert '规则草稿' in (build / 'p1_report.md').read_text(encoding='utf-8')
    assert 'LLMUnavailable' in (build / 'diff_report.md').read_text(encoding='utf-8')
    assert validate_content(content) == []


async def test_rules_only_route_makes_no_calls(settings, kw, content: Path, build: Path) -> None:
    settings.route.pipeline = 'rules_only'
    assert await pipeline.arun('all', settings, **kw) == 0
    assert not (build / 'calls').exists()
    assert json.loads((build / 'entities.json').read_text(encoding='utf-8'))['sources']['p1_ch01'] == 'rules'


async def test_own_mock_llm_records_calls(settings, kw, build: Path) -> None:
    assert await pipeline.arun('p0', settings, **kw) == 0
    assert await pipeline.arun('p1', settings, mock=True, **kw) == 0
    calls = list((build / 'calls').rglob('calls.jsonl'))
    assert calls and calls[0].read_text(encoding='utf-8').count('"kind": "P1"') >= 1


async def test_garbage_drafts_are_reported_not_written(settings, kw, mock_llm, content: Path,
                                                       build: Path) -> None:
    from tianlong.llm.schemas import NamedYaml

    def p3(d):
        if d.opening_yaml:
            d.opening_yaml = 'player: [unclosed'
            d.agendas = [NamedYaml(id='Newbie Guy', yaml='goal: 新来的人\nsteps:\n  - {id: n1, text: 四处看看, '
                                   'at: hall, do: {verb: look}}\n'),
                         NamedYaml(id='broken', yaml='just a string')]
        return d

    def p4(d):
        for c in d.cards:
            if c.id == 'girl':
                c.card_yaml = ': : :'
        return d

    def p5(d):
        if d.items_yaml:
            d.items_yaml = '- a\n- b\n'
        return d

    before = {p.name: p.read_bytes() for p in content.glob('*.yaml')}
    llm = scripted(mock_llm, {'P3': p3, 'P4': p4, 'P5': p5})
    assert await pipeline.arun('p0', settings, **kw) == 0
    for step in ('p3', 'p4', 'p5'):
        assert await pipeline.arun(step, settings, llm=llm, **kw) == 0
    assert (content / 'opening.yaml').read_bytes() == before['opening.yaml']
    assert (content / 'items.yaml').read_bytes() == before['items.yaml']
    assert not (content / 'agendas' / 'broken.yaml').exists()
    new = yaml.safe_load((content / 'agendas' / 'newbie_guy.yaml').read_text(encoding='utf-8'))
    assert new['goal'] == '新来的人'
    report = (build / 'diff_report.md').read_text(encoding='utf-8')
    assert 'opening.yaml：YAML 解析失败' in report and 'agendas/broken.yaml：不是映射' in report
    assert 'agendas/newbie_guy.yaml**（新文件）' in report
    assert 'agendas/newbie_guy.yaml：步骤 n1（四处看看）没写前提' in report
    assert 'girl.yaml' in (build / 'p4_report.md').read_text(encoding='utf-8')
    assert 'items.yaml：不是映射，未写入' in (build / 'p5_report.md').read_text(encoding='utf-8')
