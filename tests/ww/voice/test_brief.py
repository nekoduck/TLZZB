"""简报：只见公开类型；逐字确定；forbidden 只由口径与要点决定。"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from werewolf.kernel.types import ActKind, Role, Tone
from werewolf.mind.types import PublicAct, PublicMood, PublicPlan, Quote, Situation
from werewolf.voice import brief as brief_mod
from werewolf.voice.brief import build_brief

VOICE_DIR = Path(brief_mod.__file__).parent
BANNED_NAMES = {'View', 'MindSnapshot', 'SpeechPlan', 'PlannedAct', 'Decision'}


def test_ast_no_private_types():
    for name in ('brief.py', 'compose.py', 'digest.py'):
        tree = ast.parse((VOICE_DIR / name).read_text(encoding='utf-8'))
        seen: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                seen.add(node.id)
            elif isinstance(node, ast.Attribute):
                seen.add(node.attr)
            elif isinstance(node, ast.alias):
                seen.add(node.name.split('.')[-1])
                seen.add((node.asname or '').split('.')[-1])
            elif isinstance(node, ast.arg) and node.annotation is not None:
                seen.update(n.id for n in ast.walk(node.annotation) if isinstance(n, ast.Name))
        assert not seen & BANNED_NAMES, (name, seen & BANNED_NAMES)


def test_signature_takes_only_public_types():
    sig = inspect.signature(build_brief)
    text = ' '.join(str(p.annotation) for p in sig.parameters.values())
    for banned in BANNED_NAMES:
        assert not any(tok == banned for tok in text.replace('[', ' ').replace(']', ' ').replace(',', ' ').split())
    assert list(sig.parameters) == ['plan', 'pv', 'voice', 'mood', 'situation', 'heard', 'max_chars']


def _plan(**kw) -> PublicPlan:
    acts = kw.pop('acts', [
        PublicAct(kind=ActKind.CLAIM_ROLE, role=Role.SEER, weight=3),
        PublicAct(kind=ActKind.CLAIM_CHECK, target=7, result='wolf', night=2, reason='seer_check'),
        PublicAct(kind=ActKind.ACCUSE, target=2, reason='accused_me', evidence=[27], fuzzy=True),
        PublicAct(kind=ActKind.VOTE_INTENT, target=2, reason='gut'),
    ])
    return PublicPlan(seat=kw.pop('seat', 3), day=2, kind='day', acts=acts, tone=Tone.FIRM, length='medium',
                      cover_role=kw.pop('cover_role', Role.SEER), addressed=kw.pop('addressed', [7]), **kw)


def _build(pv, voices, plan=None, **kw):
    return build_brief(plan or _plan(), pv, voices[0], PublicMood(state='tense', hints=['2号踩我']),
                       Situation(index=2, total=6), kw.get('heard', []), 120)


def test_brief_deterministic(pv, voices):
    a = _build(pv, voices)
    b = _build(pv, voices)
    assert a.model_dump_json() == b.model_dump_json()


def test_brief_content(pv, voices):
    b = _build(pv, voices)
    assert b.seat == 3 and b.day == 2 and b.max_chars == 120 and b.addressed == [7]
    assert b.alive == pv.living() and b.dead == pv.dead()
    assert [p.act.kind for p in b.points] == [ActKind.CLAIM_ROLE, ActKind.CLAIM_CHECK, ActKind.ACCUSE, ActKind.VOTE_INTENT]
    assert b.points[0].text == '自称身份：预言家'
    assert b.points[1].text == '报查验：第2夜验了7号，是狼人'
    assert b.points[1].reason == '我手里有验人的结果'
    assert b.points[2].hedge and b.points[3].hedge  # fuzzy 与 GUT 都留余地
    assert not b.points[1].hedge
    assert b.recent and b.recent[-1] == '第2天，9号站3号的边，信其身份'
    assert b.own_record[0].startswith('第1天，我自称预言家')
    assert all(type(p.act).__name__ == 'SpeechAct' for p in b.points)  # 不带理由与证据字段


def test_reason_evidence_rendered_without_stray_seats(pv, voices):
    ev = next(e for e in pv.events if e.type.value == 'vote' and e.actor == 2)
    plan = _plan(acts=[PublicAct(kind=ActKind.ACCUSE, target=2, reason='voted_me', evidence=[ev.id])])
    b = _build(pv, voices, plan)
    assert b.points[0].reason == '2号之前投票冲着我来，比如2号第1天把票给了7号'
    # 证据牵涉到简报之外的座位时，不渲染证据句
    plan2 = _plan(acts=[PublicAct(kind=ActKind.ACCUSE, target=5, reason='accused_me', evidence=[ev.id])])
    b2 = _build(pv, voices, plan2)
    assert b2.points[0].reason == '5号之前踩过我'


def test_private_or_unplaceable_reason_dropped(pv, voices):
    plan = _plan(acts=[PublicAct(kind=ActKind.CLAIM_ROLE, role=Role.SEER, reason='team'),
                       PublicAct(kind=ActKind.DEFEND, reason='quiet')])
    b = _build(pv, voices, plan)
    assert [p.reason for p in b.points] == [None, None]


def test_forbidden_depends_only_on_cover_and_points(pv, voices):
    wolf_view = _build(pv, voices, _plan(seat=2))
    seer_view = _build(pv, voices, _plan(seat=2))
    assert wolf_view.forbidden == seer_view.forbidden
    b = _build(pv, voices)
    joined = '\n'.join(b.forbidden)
    assert 'AI' in joined
    assert '不要说自己是女巫、猎人、平民、狼人' in joined
    assert '不要报任何查验结果' not in joined  # 要点里有查验
    none = _build(pv, voices, _plan(cover_role=None, acts=[PublicAct(kind=ActKind.PASS)]))
    nj = '\n'.join(none.forbidden)
    assert '不要说出自己的身份' in nj and '不要报任何查验结果' in nj and '不要表态今天投谁' in nj


def test_role_independence_same_public_input(pv, voices):
    """简报函数只收公开类型：同一份输入，谁来调用结果都一样（这里连调两次、换顺序也一样）。"""
    a = _build(pv, voices, _plan(seat=2))
    _build(pv, voices, _plan(seat=5))
    b = _build(pv, voices, _plan(seat=2))
    assert a == b


def test_heard_passed_through_and_not_in_recent(pv, voices):
    q = Quote(seat=1, text='金丝雀的原话')
    b = build_brief(_plan(), pv, voices[0], PublicMood(), Situation(), [q], 100)
    assert b.heard == [q]
    assert all('金丝雀' not in s for s in [*b.recent, *b.own_record])
