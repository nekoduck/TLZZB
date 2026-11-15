"""真模型冒烟（`python -m werewolf smoke`）：Jev 与 Claude 各发一次，外加解析与核对的标注集评测。

评测函数本身不联网（传入预测即可），`run_smoke` 才联网。标注集在 `tests/ww/data/`。
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import yaml

from werewolf.kernel.types import ActKind, Phase, PlayerPublic, PublicView, Role, SpeechAct
from werewolf.llm.gateway import Gateway
from werewolf.voice import canon

DATA_DIR = Path(__file__).resolve().parents[3] / 'tests' / 'ww' / 'data'
CLAIM_KINDS = canon.CLAIM_KINDS


def load_yaml(name: str) -> list[dict[str, Any]]:
    with (DATA_DIR / name).open(encoding='utf-8') as f:
        return yaml.safe_load(f)


def case_pv(day: int, dead: Iterable[int] = ()) -> PublicView:
    gone = set(dead)
    players = [PlayerPublic(seat=s, name=f'{s}号', alive=s not in gone) for s in range(1, 10)]
    return PublicView(day=day, phase=Phase.DAY_SPEECH, players=players, events=[])


def _key(a: SpeechAct | dict[str, Any]) -> tuple[str, int | None, str | None]:
    if isinstance(a, SpeechAct):
        kind, target, role, result = a.kind.value, a.target, a.role, a.result
        role_s = role.value if isinstance(role, Role) else role
    else:
        kind, target, role_s, result = a['kind'], a.get('target'), a.get('role'), a.get('result')
    extra = role_s if kind == 'claim_role' else (result if kind == 'claim_check' else None)
    return (kind, target, extra)


def _fmt(k: tuple[str, int | None, str | None]) -> str:
    return f'{k[0]}:{k[1]}:{k[2]}'


def score_parse(cases: Sequence[dict[str, Any]], predicted: dict[str, Sequence[SpeechAct]]) -> dict[str, Any]:
    """声明类精确率、召回率；陷阱里的声明类误报；座位准确率（带目标的行为，种类与座位都对）。"""
    tp = fp = fn = 0
    trap_fp: list[str] = []
    seat_total = seat_ok = 0
    errors: list[str] = []
    for case in cases:
        cid = case['id']
        pred = predicted.get(cid, [])
        want = {_key(e) for e in case.get('expect') or []}
        got = {_key(a) for a in pred}
        wc = {k for k in want if k[0] in {x.value for x in CLAIM_KINDS}}
        gc = {k for k in got if k[0] in {x.value for x in CLAIM_KINDS}}
        tp += len(wc & gc)
        fp += len(gc - wc)
        fn += len(wc - gc)
        if gc - wc:
            errors.append(f'{cid} 误报 {[_fmt(k) for k in gc - wc]}: {case["text"]}')
            if case.get('trap'):
                trap_fp.append(cid)
        if wc - gc:
            errors.append(f'{cid} 漏报 {[_fmt(k) for k in wc - gc]}: {case["text"]}')
        for k in want:
            if k[1] is not None:
                seat_total += 1
                seat_ok += (k[0], k[1]) in {(g[0], g[1]) for g in got}
    return {
        'claim_precision': tp / (tp + fp) if tp + fp else 1.0,
        'claim_recall': tp / (tp + fn) if tp + fn else 1.0,
        'trap_false_claims': len(trap_fp),
        'seat_accuracy': seat_ok / seat_total if seat_total else 1.0,
        'errors': errors,
    }


def eval_parse_rules(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    from werewolf.llm.parse import parse_rules

    pred: dict[str, Sequence[SpeechAct]] = {}
    for c in cases:
        living = [s for s in range(1, 10)]
        pred[c['id']] = parse_rules(c['text'], living, c.get('speaker', 1), day=c.get('day')).acts
    return score_parse(cases, pred)


async def eval_parse_jev(gw: Gateway, cases: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], float]:
    """真 Jev（经 parse_speech，已与正则合并）；返回（指标, 平均时延秒）。"""
    import asyncio

    from werewolf.llm.parse import parse_speech

    pred: dict[str, Sequence[SpeechAct]] = {}
    lat: list[float] = []
    sem = asyncio.Semaphore(6)

    async def one(c: dict[str, Any]) -> None:
        async with sem:
            t0 = time.perf_counter()
            r = await parse_speech(gw, c['text'], case_pv(c.get('day', 2)), c.get('speaker', 1))
            lat.append(time.perf_counter() - t0)
            pred[c['id']] = r.acts

    await asyncio.gather(*(one(c) for c in cases))
    return score_parse(cases, pred), (sum(lat) / len(lat) if lat else 0.0)


# --------------------------------------------------------------------------- 核对标注集


def _point_text(p: dict[str, Any]) -> str:
    kind, t = p['kind'], p.get('target')
    if kind == 'claim_role':
        return f'自称{canon.role_word(Role(p["role"]))}'
    if kind == 'claim_check':
        return f'报查验：{t}号是{"狼人" if p.get("result") == "wolf" else "好人"}'
    return {'claim_save': f'自称救了{t}号', 'claim_poison': f'自称毒了{t}号', 'accuse': f'怀疑{t}号',
            'vouch': f'保{t}号', 'vote_intent': f'今天投{t}号', 'back_claim': f'站边{t}号',
            'doubt_claim': f'质疑{t}号的身份', 'question': f'要{t}号解释', 'defend': '替自己辩解',
            'pass': '这轮过'}.get(kind, kind)


def brief_from_case(case: dict[str, Any]) -> Any:
    """标注集条目 → SpeechBrief（用默认人设补齐其余字段）。"""
    from werewolf.mind.types import BriefPoint, PersonaVoice, PublicMood, SpeechBrief, StyleCard

    pts = []
    for p in case['points']:
        act = SpeechAct(kind=ActKind(p['kind']), target=p.get('target'),
                        role=Role(p['role']) if p.get('role') else None, result=p.get('result'), night=case['day'])
        pts.append(BriefPoint(act=act, text=p.get('text') or _point_text(p)))
    dead = case.get('dead', [])
    return SpeechBrief(
        seat=case['seat'], day=case['day'], kind=case.get('speech_kind', 'day'),
        persona=PersonaVoice(id='sample', name='老周', title='退休的语文老师', tags=['稳', '爱讲道理'],
                             bio='我教了三十年书，说话慢，讲究有凭有据。', style=StyleCard(sentence_len='medium')),
        cover_role=Role(case['cover']) if case.get('cover') else None, max_chars=case.get('max_chars', 120),
        points=pts, alive=[s for s in range(1, 10) if s not in dead], dead=dead,
        own_record=case.get('own_record', []), addressed=case.get('addressed', []),
        mood=PublicMood(hints=case.get('hints', [])),
    )


def eval_check_rules(cases: Sequence[dict[str, Any]]) -> dict[str, Any]:
    from werewolf.llm.check import check_rules

    leaks = [c for c in cases if c['leak']]
    clean = [c for c in cases if not c['leak']]
    caught = [c['id'] for c in leaks if check_rules(c['text'], brief_from_case(c))]
    blocked_clean = [c['id'] for c in clean if check_rules(c['text'], brief_from_case(c))]
    return {'leak_total': len(leaks), 'leak_caught': len(caught),
            'leak_missed': [c['id'] for c in leaks if c['id'] not in caught],
            'clean_total': len(clean), 'clean_blocked': blocked_clean}


async def eval_check_full(gw: Gateway, cases: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], float]:
    """规则加真 Jev。返回（指标, 平均时延秒）。"""
    import asyncio

    from werewolf.llm.check import check_speech

    lat: list[float] = []
    verdict: dict[str, list[str]] = {}
    sem = asyncio.Semaphore(6)

    async def one(c: dict[str, Any]) -> None:
        async with sem:
            t0 = time.perf_counter()
            verdict[c['id']] = await check_speech(gw, c['text'], brief_from_case(c))
            lat.append(time.perf_counter() - t0)

    await asyncio.gather(*(one(c) for c in cases))
    leaks = [c for c in cases if c['leak']]
    clean = [c for c in cases if not c['leak']]
    missed = [c['id'] for c in leaks if not verdict[c['id']]]
    blocked = [c['id'] for c in clean if verdict[c['id']]]
    return ({'leak_total': len(leaks), 'leak_caught': len(leaks) - len(missed), 'leak_missed': missed,
             'clean_total': len(clean), 'clean_blocked': blocked,
             'reasons': {k: v for k, v in verdict.items() if k in blocked}},
            sum(lat) / len(lat) if lat else 0.0)


# --------------------------------------------------------------------------- 冒烟


def _pct(x: float) -> str:
    return f'{x * 100:.1f}%'


async def run_smoke(gw: Gateway, *, only: Sequence[str] | None = None, verbose: bool = False) -> dict[str, Any]:
    """真模型冒烟：Jev 一次决定、Claude 一段台词、解析评测、核对评测。打印简报并返回指标。"""
    from werewolf.kernel.rng import Rng
    from werewolf.llm.decide import choose
    from werewolf.llm.speak import write_speech

    want = set(only or ('decide', 'speech', 'parse', 'check'))
    report: dict[str, Any] = {}
    lines: list[str] = []
    st = gw.status()
    lines.append(f'路由：jev={st.jev} claude={st.claude} 台词档={st.speech} 离线={st.offline}')

    if 'decide' in want:
        t0 = time.perf_counter()
        probs = await choose(
            gw, 'J2_act',
            '【我是谁】\n你是4号，平民。\n【场上】\n第2天，投票。3号昨天踩过你，还投了你；5号一直保你。',
            '这个人今天会投谁？',
            {'vote:3': '投 3 号：他踩过我、投过我', 'vote:5': '投 5 号：他一直保我', 'vote:abstain': '弃票'})
        report['decide'] = {'probs': probs, 'latency_s': round(time.perf_counter() - t0, 2)}
        lines.append(f'Jev 决定：{probs}（{report["decide"]["latency_s"]}s）')

    if 'speech' in want:
        case = next(c for c in load_yaml('check_cases.yaml') if c['id'] == 'k18')
        brief = brief_from_case(case)
        t0 = time.perf_counter()
        try:
            res = await write_speech(gw, brief, Rng(1, 'smoke'))
            report['speech'] = {'tier': res.tier, 'text': res.text, 'rejected': res.rejected,
                                'latency_s': round(time.perf_counter() - t0, 2)}
            lines.append(f'台词：档={res.tier} {report["speech"]["latency_s"]}s「{res.text}」')
            for r in res.rejected:
                lines.append(f'  被拦：{r}')
        except Exception as e:  # noqa: BLE001
            report['speech'] = {'error': f'{type(e).__name__}: {e}'}
            lines.append(f'台词失败：{report["speech"]["error"]}')

    if 'parse' in want:
        cases = load_yaml('parse_cases.yaml')
        rules = eval_parse_rules(cases)
        report['parse_rules'] = {k: v for k, v in rules.items() if k != 'errors'}
        lines.append(f'解析（正则）：声明精确率 {_pct(rules["claim_precision"])} 召回 {_pct(rules["claim_recall"])} '
                     f'陷阱误报 {rules["trap_false_claims"]} 座位准确率 {_pct(rules["seat_accuracy"])}')
        if gw.route('parse') == 'jev':
            jev, lat = await eval_parse_jev(gw, cases)
            report['parse_jev'] = {k: v for k, v in jev.items() if k != 'errors'} | {'latency_s': round(lat, 2)}
            lines.append(f'解析（Jev+正则）：声明精确率 {_pct(jev["claim_precision"])} 召回 {_pct(jev["claim_recall"])} '
                         f'陷阱误报 {jev["trap_false_claims"]} 座位准确率 {_pct(jev["seat_accuracy"])} '
                         f'平均 {lat:.2f}s')
            if verbose:
                lines.extend('  ' + e for e in jev['errors'])
        else:
            lines.append('解析（Jev）：Jev 不可用，跳过')

    if 'check' in want:
        cases = load_yaml('check_cases.yaml')
        rr = eval_check_rules(cases)
        report['check_rules'] = rr
        lines.append(f'核对（只有规则）：露底放行 {rr["leak_total"] - rr["leak_caught"]}/{rr["leak_total"]} '
                     f'{rr["leak_missed"]}；干净误拦 {len(rr["clean_blocked"])}/{rr["clean_total"]}')
        if gw.route('check') == 'jev':
            full, lat = await eval_check_full(gw, cases)
            report['check_full'] = full | {'latency_s': round(lat, 2)}
            lines.append(f'核对（规则+Jev，block={gw.ww.check.block}）：露底放行 '
                         f'{full["leak_total"] - full["leak_caught"]}/{full["leak_total"]} {full["leak_missed"]}；'
                         f'干净误拦 {len(full["clean_blocked"])}/{full["clean_total"]} {full["clean_blocked"]}；'
                         f'平均 {lat:.2f}s')
            if verbose:
                lines.extend(f'  误拦 {k}: {v}' for k, v in full['reasons'].items())
        else:
            lines.append('核对（Jev）：Jev 不可用，跳过')

    lines.append(f'本次花费：${gw.spent():.4f}')
    report['spent_usd'] = gw.spent()
    print('\n'.join(lines))
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m werewolf.llm.smoke [decide speech parse check] [-v]`（正式入口是 `python -m werewolf smoke`）。"""
    import asyncio
    import sys

    from werewolf.llm.config import load

    args = list(sys.argv[1:] if argv is None else argv)
    verbose = '-v' in args
    only = [a for a in args if not a.startswith('-')] or None
    settings, ww = load('werewolf.toml')

    async def go() -> None:
        gw = Gateway(settings, ww)
        try:
            await run_smoke(gw, only=only, verbose=verbose)
        finally:
            await gw.aclose()

    asyncio.run(go())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
