"""P4 人物卡（方案 5.4、9.1）：每个有魂、有名角色的出场段落 → `content/characters/<id>.yaml`。

每人一条请求（`p4_<id>`），名单取开局快照里放了位置的人加上现有的人物卡。写入前：
- 台词样例逐字比对，必须是原文的子串，对不上的丢掉（有名角色至多 3 句）；
- 抉择带段落编号（编号无效的丢掉，缺编号的保留但记进报告）；有名角色不要抉择；
- `knows` 只留事实表里、开局那一刻为真的事；
- 带 `locked: true` 的卡整份不动；草稿漏掉的字段保留原样。
报告列出每个人的底线与抉择，供人工过目（方案 9.2 的第二处）。
规则草稿（Mock 与兜底）：现有的卡原样，台词样例从原文里按「某人道：「……」」的样子找。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tianlong import prompting
from tianlong.llm.schemas import CardQuote
from tianlong.pipeline.common import (
    Corpus,
    Ctx,
    Lazy,
    Report,
    Request,
    StepResult,
    WriteResult,
    ask,
    commit_yaml,
    copied_text_problems,
    dump_yaml,
    fact_ids,
    filter_knows,
    outcome_lines,
    parse_yaml_text,
    plan_yaml,
    read_yaml,
    rel,
    write_json,
)
from tianlong.pipeline.p1_entities import load_entities
from tianlong.pipeline.schemas import CardDraft, CardsDraft, ChoiceDraft

MAX_SAMPLES = {'soul': 6, 'named': 3}
CARD_KEEP = ((), ('voice',))
SPEAKER_WINDOW = 24
SPEECH_CHARS = '道说问叫喝笑骂答嚷喊'
_QUOTE_RE = re.compile(r'[「“]([^「“」”]{2,80})[」”]')
MAX_PARAS_IN_PROMPT = 160


def cast(content: Path) -> list[str]:
    opening = read_yaml(content / 'opening.yaml') or {}
    ids = list((opening.get('chars') or {}).keys()) if isinstance(opening, Mapping) else []
    d = content / 'characters'
    if d.is_dir():
        ids += [p.stem for p in sorted(d.glob('*.yaml'))]
    return [c for c in dict.fromkeys(ids) if c != 'player']


def speaker_quotes(corpus: Corpus, names: Mapping[str, Sequence[str]], cid: str, limit: int) -> list[CardQuote]:
    """原文里「某人道：「……」」这样的台词，说话的人取引号前最近提到的那个名字。"""
    mine = [n for n in names.get(cid, []) if n]
    if not mine:
        return []
    out: list[CardQuote] = []
    for p in corpus.paragraphs:
        if not any(n in p.text for n in mine):
            continue
        for m in _QUOTE_RE.finditer(p.text):
            window = p.text[max(0, m.start() - SPEAKER_WINDOW):m.start()]
            if not any(ch in window for ch in SPEECH_CHARS):
                continue
            best: tuple[int, str] | None = None
            for other, ns in names.items():
                for n in ns:
                    k = window.rfind(n) if n else -1
                    if k >= 0 and (best is None or k > best[0]):
                        best = (k, other)
            if best is not None and best[1] == cid:
                out.append(CardQuote(text=m.group(1), para=p.id))
                if len(out) >= limit:
                    return out
    return out


def _seed_card(content: Path, cid: str) -> dict[str, Any] | None:
    d = read_yaml(content / 'characters' / f'{cid}.yaml')
    return dict(d) if isinstance(d, Mapping) else None


def rule_card(corpus: Corpus, seed: dict[str, Any] | None, cid: str,
              names: Mapping[str, Sequence[str]]) -> CardsDraft:
    if seed is None:  # 规则不发明人物：没有现成的卡就不出卡
        return CardsDraft(cards=[])
    body = {k: v for k, v in seed.items() if k not in ('choices', 'locked')}
    voice = dict(body.get('voice') or {})
    old_samples = voice.pop('samples', None) or []
    if 'voice' in body:
        body['voice'] = voice
    tier = str(seed.get('tier', 'named'))
    samples = [CardQuote(text=str(s['text']), para=str(s.get('para') or '')) for s in old_samples
               if isinstance(s, Mapping) and s.get('text')]
    if not samples:
        samples = speaker_quotes(corpus, names, cid, MAX_SAMPLES.get(tier, 3))
    choices = [ChoiceDraft(situation=str(c.get('situation', '')), did=str(c.get('did', '')), para=c.get('para'))
               for c in seed.get('choices') or [] if isinstance(c, Mapping)]
    return CardsDraft(cards=[CardDraft(id=cid, card_yaml=dump_yaml(body), voice_samples=samples, choices=choices,
                                       notes='')])


def process_card(
    corpus: Corpus, cid: str, draft: CardDraft, facts: Mapping[str, Any], seed: Mapping[str, Any] | None
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """草稿 → 要写的卡。返回 (数据, 问题, 要人工过目的)。"""
    probs: list[str] = []
    review: list[str] = []
    data, err = parse_yaml_text(draft.card_yaml)
    if err or not isinstance(data, dict):
        return None, [f'characters/{cid}.yaml：{err or "card_yaml 不是映射"}，未写入'], []
    data.pop('voice_samples', None)
    data.pop('choices', None)
    if data.get('id') not in (None, cid):
        probs.append(f'characters/{cid}.yaml：id {data["id"]!r} 改为 {cid!r}')
        data['id'] = cid
    tier = str(data.get('tier') or (seed or {}).get('tier') or 'named')
    if seed is not None and seed.get('tier') and seed.get('tier') != tier:
        review.append(f'{cid}：等级由 {seed.get("tier")} 改为 {tier}')
    # 台词样例：逐字比对
    samples: list[dict[str, str]] = []
    for q in draft.voice_samples:
        text = q.text.strip()
        hit = corpus.verify(text, q.para)
        if hit is None:
            probs.append(f'{cid}：台词「{text[:24]}」不是原文的子串，丢掉')
            continue
        if hit != q.para:
            probs.append(f'{cid}：台词「{text[:24]}」的段落编号 {q.para!r} 改为 {hit}')
        if all(s['text'] != text for s in samples):
            samples.append({'text': text, 'para': hit})
    cap = MAX_SAMPLES.get(tier, 3)
    if len(samples) > cap:
        probs.append(f'{cid}：台词样例 {len(samples)} 句，只留前 {cap} 句')
        samples = samples[:cap]
    if samples:
        voice = dict(data.get('voice') or {})
        voice['samples'] = samples
        data['voice'] = voice
    # 抉择：带段落编号；有名角色不要
    choices: list[dict[str, Any]] = []
    for c in draft.choices:
        if tier != 'soul':
            probs.append(f'{cid}：有名角色不写抉择，丢掉「{c.did[:20]}」')
            continue
        para = c.para or None
        if para is not None and not corpus.valid(para):
            probs.append(f'{cid}：抉择「{c.did[:20]}」的段落编号 {para!r} 无效，丢掉')
            continue
        if para is None:
            review.append(f'{cid}：抉择「{c.did[:20]}」缺段落编号')
        choices.append({'situation': c.situation, 'did': c.did, 'para': para})
    if choices:
        data['choices'] = choices
    # knows：只留开局那一刻为真的事
    if 'knows' in data:
        data['knows'], dropped = filter_knows(data.get('knows'), facts)
        review += [f'{cid}：knows 里的 {x} 不在事实表里、是开局之后的事或开局时无人知道，已删去' for x in dropped]
    if draft.notes.strip():
        review.append(f'{cid} 的备注：{draft.notes.strip()}')
    return data, probs, review


def card_review(cid: str, data: Mapping[str, Any]) -> list[str]:
    lines = []
    for b in data.get('bottom_lines') or []:
        if isinstance(b, Mapping):
            lines.append(f'{cid} 底线：{b.get("text", "")}（{len(b.get("blocks") or [])} 条拦截）')
    for c in data.get('choices') or []:
        if isinstance(c, Mapping):
            lines.append(f'{cid} 抉择：{c.get("situation", "")} → {c.get("did", "")}（{c.get("para") or "无编号"}）')
    return lines


async def run(ctx: Ctx) -> StepResult:
    corpus = ctx.corpus
    content = ctx.paths.content
    entities = {str(e['id']): e for e in load_entities(ctx)}
    ids = cast(content)
    seeds = {cid: _seed_card(content, cid) for cid in ids}
    names: dict[str, list[str]] = {}
    for cid in ids:
        e = entities.get(cid)
        s = seeds.get(cid) or {}
        ns = [str(s['name'])] if s.get('name') else []
        if e is not None:
            ns += [str(e.get('name', ''))] + [str(a) for a in e.get('aliases') or []]
        names[cid] = [n for n in dict.fromkeys(ns) if n]
    facts = fact_ids(content)
    fact_lines = [f'{fid}：{(f or {}).get("text", "")}' for fid, f in facts.items() if isinstance(f, Mapping)]
    requests: list[Request] = []
    for cid in ids:
        seed = seeds.get(cid)
        e = entities.get(cid)
        paras = list((e or {}).get('paragraphs') or [])
        if not paras:
            paras = [p.id for p in corpus.paragraphs if any(n in p.text for n in names.get(cid, []))]
        seed_text = (content / 'characters' / f'{cid}.yaml')
        user = prompting.render(
            'pipeline/p4_user', id=cid, name=(names.get(cid) or [cid])[0], aliases=names.get(cid, [])[1:],
            tier=str((seed or {}).get('tier', 'named')), paragraphs=paras[:MAX_PARAS_IN_PROMPT],
            more=max(0, len(paras) - MAX_PARAS_IN_PROMPT),
            seed=seed_text.read_text(encoding='utf-8') if seed_text.is_file() else '', facts=fact_lines)
        requests.append(Request(f'p4_{cid}', user, CardsDraft,
                                Lazy(lambda cid=cid, seed=seed: rule_card(corpus, seed, cid, names))))
    outcomes = await ask(ctx, 'P4', requests)
    write_json(ctx.paths.draft('p4'), {
        cid: next((c.notes for c in o.obj.cards if c.notes.strip()), '') for cid, o in
        ((cid, outcomes[f'p4_{cid}']) for cid in ids)})
    problems: list[str] = []
    review: list[str] = []
    copied: list[str] = []
    planned: list[WriteResult] = []
    for cid in ids:
        o = outcomes[f'p4_{cid}']
        cards = list(o.obj.cards)
        draft = next((c for c in cards if c.id == cid), cards[0] if cards else None)
        if draft is None:
            if seeds.get(cid) is None:
                problems.append(f'{cid}：没有卡（开局快照里有此人，却没有人物卡）')
            continue
        data, probs, rev = process_card(corpus, cid, draft, facts, seeds.get(cid))
        problems += probs
        review += rev
        if data is None:
            continue
        copied += copied_text_problems(corpus, data, f'characters/{cid}.yaml', skip=('voice.samples',))
        wr = plan_yaml(content / 'characters' / f'{cid}.yaml', data, keep_missing_at=CARD_KEEP)
        planned.append(wr)
        if wr.status != 'locked':
            review += card_review(cid, wr.data)
    rep = Report('P4 人物卡')
    rep.h('来源')
    rep.bullets(outcome_lines(outcomes))
    rep.h('人工过目：底线、抉择与备注')
    rep.bullets(review)
    rep.h('逐项差异')
    for wr in planned:
        if wr.status == 'locked':
            rep.add(f'- {rel(wr.path, content)}：locked，未改')
        elif wr.diff():
            rep.add('', f'**{rel(wr.path, content)}**{"（新文件）" if wr.status == "new" else ""}', '')
            rep.bullets(wr.diff())
    rep.h('校验（丢掉的台词、抉择等）')
    rep.bullets(problems)
    rep.h('疑似照抄原文')
    rep.bullets(copied)
    path = rep.write(ctx.paths.report('p4'))
    res = StepResult('p4', report=path, problems=problems)
    for wr in planned:
        commit_yaml(wr, 'P4')
        if wr.changed:
            res.written.append(rel(wr.path, ctx.settings.root))
    res.fallbacks = [cid for cid, o in outcomes.items() if o.source == 'rules']
    res.summary = f'{len(ids)} 人，写入 {len(res.written)} 张卡'
    ctx.out(f'P4 人物卡：{res.summary}；丢掉或改正 {len(problems)} 处；要人工过目 {len(review)} 处')
    return res
