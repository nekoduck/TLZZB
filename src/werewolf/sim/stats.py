"""自对弈的统计与报告（ARCHITECTURE 第 10 节的各项指标、第 11 节的 `sim --report`）。

一局先收成 `GameRecord`（与对局从哪条路来无关：`sim.arena` 的规则循环，或会话层 + NpcAgent），
`summarize(records)` 算一批的指标，`report_md(...)` 渲染成 markdown。
比例都在 0 到 1 之间；分母为 0 的项记 0（并在 `n_*` 里给出分母）。
"""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from werewolf.kernel.claims import ClaimLedger, speech_acts
from werewolf.kernel.types import ActKind, Event, EventType, Phase, Role, Seat
from werewolf.mind.types import Decision, SpeechPlan


@dataclass
class GameRecord:
    seed: int
    mode: str
    events: list[Event]  # 裁判视角的全部事件
    roles: dict[Seat, Role]
    winner: str | None
    days: int
    human_seat: Seat | None = None
    decisions: list[Decision] = field(default_factory=list)
    plans: list[SpeechPlan] = field(default_factory=list)  # 与发言事件按出现顺序一一对应（尽量）
    tiers: list[str] = field(default_factory=list)  # 每段 AI 发言走了哪一档
    rejected: list[list[dict[str, Any]]] = field(default_factory=list)  # 与 tiers 一一对应
    texts: list[str] = field(default_factory=list)  # 与 tiers 一一对应
    commitments: list[tuple[Seat, int, Seat, str]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)  # 本局的模型调用（kind、provider、latency_ms、cost）
    cost: float = 0.0
    wall_s: float = 0.0
    violations: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- 收集


def from_arena(res: Any, seed: int, mode: str = 'rules') -> GameRecord:
    """`sim.arena.ArenaResult` → GameRecord。"""
    g = res.game
    r = g.result
    return GameRecord(
        seed=seed, mode=mode, events=g.events('judge'), roles=dict(res.roles),
        winner=r.winner.value if r and r.winner else None, days=r.day if r else 0, human_seat=g.config.human_seat,
        decisions=list(res.decisions), plans=list(res.plans), tiers=['template'] * len(res.texts),
        rejected=[[] for _ in res.texts], texts=list(res.texts), commitments=list(res.commitments),
        violations=list(res.violations))


def _speech_order(events: Sequence[Event], plans_by_seat: Mapping[Seat, list[Any]]) -> list[Any]:
    """按发言事件的顺序，把每个座位的记录（按请求号排好）对上去。"""
    queues = {s: list(v) for s, v in plans_by_seat.items()}
    out: list[Any] = []
    for e in events:
        if e.type == EventType.SPEECH and e.actor is not None and queues.get(e.actor):
            out.append(queues[e.actor].pop(0))
    return out


def _rid(x: str) -> int:
    try:
        return int(x.lstrip('r').split(':')[0])
    except ValueError:
        return 0


def from_session(session: Any, seed: int, mode: str, *, calls: Sequence[dict[str, Any]] = (), cost: float = 0.0,
                 wall_s: float = 0.0) -> GameRecord:
    """会话层（players 是 NpcAgent）→ GameRecord。"""
    g = session.game
    r = g.result
    events = g.events('judge')
    decisions: list[Decision] = []
    commits: list[tuple[Seat, int, Seat, str]] = []
    violations: list[str] = []
    per_seat: dict[Seat, list[tuple[SpeechPlan, Any]]] = {}
    for seat, p in sorted(session.players.items()):
        decisions += list(getattr(p, 'decisions', []))
        commits += list(getattr(p, 'commitments', []))
        violations += list(getattr(p, 'violations', []))
        pairs = list(zip(getattr(p, 'plans', []), getattr(p, 'results', []), strict=False))
        per_seat[seat] = sorted(pairs, key=lambda x: _rid(x[0].request_id))
    ordered = _speech_order(events, per_seat)
    return GameRecord(
        seed=seed, mode=mode, events=events, roles={s: g.role_of(s) for s in range(1, g.config.seats + 1)},
        winner=r.winner.value if r and r.winner else None, days=r.day if r else 0, human_seat=session.human_seat,
        decisions=decisions, plans=[x[0] for x in ordered], tiers=[x[1].tier for x in ordered],
        rejected=[list(x[1].rejected) for x in ordered], texts=[x[1].text for x in ordered],
        commitments=commits, calls=list(calls), cost=cost, wall_s=wall_s, violations=violations)


# --------------------------------------------------------------------------- 指标


def _pct(xs: Sequence[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return float(s[k])


def seer_claim_day(rec: GameRecord) -> int | None:
    seer = next((s for s, x in rec.roles.items() if x == Role.SEER), None)
    for e in rec.events:
        if e.type == EventType.SPEECH and e.actor == seer and e.vis == 'public':
            if any(a.kind == ActKind.CLAIM_ROLE and a.role == Role.SEER for a in speech_acts(e)):
                return e.day
    return None


def summarize(records: Sequence[GameRecord]) -> dict[str, float]:
    """一批对局的指标。键见 `METRICS`。"""
    n = max(1, len(records))
    c: Counter[str] = Counter()
    days: list[float] = []
    lat: dict[str, list[float]] = {'jev': [], 'claude': []}
    tiers: Counter[str] = Counter()
    nonopt_state: Counter[str] = Counter()
    dec_state: Counter[str] = Counter()
    for rec in records:
        roles = rec.roles
        pub = [e for e in rec.events if e.vis == 'public']
        lg = ClaimLedger.from_events(pub)
        c['wolf_win'] += rec.winner == 'wolf'
        c['draw'] += rec.winner is None
        days.append(rec.days)
        seer = next(s for s, x in roles.items() if x == Role.SEER)
        d = lg.death_of(seer)
        c['seer_alive_d3'] += d is None or d[0] > 3 or (d[0] == 3 and d[1] != 'night')
        cd = seer_claim_day(rec)
        c['seer_claim_d2'] += cd is not None and cd <= 2
        jumpers = [s for s, x in roles.items() if x == Role.WEREWOLF and lg.role_claims(s, Role.SEER)]
        c['fake_seer'] += bool(jumpers)
        c['two_wolf_seer'] += len(jumpers) >= 2
        c['contradictions'] += sum(len(lg.contradictions(s)) for s in roles)
        c['violations'] += len(rec.violations)
        # 发言
        d1_variants: set[str] = set()
        for p in rec.plans:
            c['speeches'] += 1
            c['non_pass'] += any(a.kind != ActKind.PASS for a in p.acts)
            if p.day == 1 and p.kind == 'day':
                d1_variants.add(p.variant)
        c['d1_variety_ge3'] += len(d1_variants) >= 3
        c['d1_variety_sum'] += len(d1_variants)
        for t, rej in zip(rec.tiers, rec.rejected, strict=False):
            tiers[t] += 1
            for r in rej:
                if r.get('tier') == 'claude':
                    c['claude_attempts'] += 1
                    c['check_blocks'] += 'text' in r
            if t == 'claude':
                c['claude_attempts'] += 1
        # 决定
        for x in rec.decisions:
            c['decisions'] += 1
            bad = x.option_id != x.best_option_id
            c['non_optimal'] += bad
            dec_state[x.state] += 1
            nonopt_state[x.state] += bad
        # 投票、票仓、出局、跟票
        vote_days: set[int] = set()
        exile_days: set[int] = set()
        calls_by_day: dict[int, dict[Seat, Seat]] = {}
        main_votes: dict[int, dict[Seat, Seat | None]] = {}
        for e in pub:
            if e.type == EventType.SPEECH and e.actor is not None and e.data.get('kind') in ('day', 'pk'):
                for a in speech_acts(e):
                    if a.kind == ActKind.VOTE_INTENT and a.target is not None:
                        calls_by_day.setdefault(e.day, {})[e.actor] = a.target
            elif e.type == EventType.VOTE and e.data.get('round') == 'main' and e.actor is not None:
                main_votes.setdefault(e.day, {})[e.actor] = e.target
            elif e.type == EventType.VOTE_RESULT:
                vote_days.add(e.day)
                if e.day == 1 and e.data.get('round') == 'main':
                    tally = e.data.get('tally') or []
                    tot = sum(len(t['voters']) for t in tally) + len(e.data.get('abstain') or [])
                    c['bloc_sum'] += max((len(t['voters']) for t in tally), default=0) / max(1, tot)
                    c['bloc_n'] += 1
            elif e.type == EventType.EXILE:
                exile_days.add(e.day)
        c['vote_days'] += len(vote_days)
        c['no_exile'] += len(vote_days - exile_days)
        for day, calls in calls_by_day.items():
            votes = main_votes.get(day, {})
            for caller, target in calls.items():
                for voter, got in votes.items():
                    if voter in (caller, target):
                        continue
                    c['follow_n'] += 1
                    c['follow'] += got == target
        # 承诺（好人）
        for seat, _day, intent, opt in rec.commitments:
            if roles.get(seat) != Role.WEREWOLF:
                c['commit_n'] += 1
                c['commit'] += opt == f'vote:{intent}'
        # 托管真人座位与 AI 座位的被刀、被放逐
        for e in rec.events:
            if e.type == EventType.DEATH_CAUSE and e.target is not None:
                cause = e.data.get('cause')
                who = 'human' if e.target == rec.human_seat else 'ai'
                if cause == 'wolf':
                    c[f'{who}_killed'] += 1
                elif cause == 'vote':
                    c[f'{who}_exiled'] += 1
        c['ai_seats'] += len(roles) - (1 if rec.human_seat is not None else 0)
        c['human_games'] += rec.human_seat is not None
        # 回应：被指认、点名之后的下一次发言里 addressed 含对方
        it = iter(rec.plans)
        pending: dict[Seat, set[Seat]] = {}
        for e in rec.events:
            if e.type == EventType.PHASE and e.data.get('phase') == Phase.NIGHT_WOLF.value:
                pending = {}
            if e.type != EventType.SPEECH or e.actor is None:
                continue
            p = next(it, None)
            if p is None:
                break
            hit = pending.pop(e.actor, set())
            if hit and e.data.get('kind') != 'last_words':
                c['respond_n'] += 1
                c['respond'] += bool(hit & set(p.addressed))
            for a in speech_acts(e):
                if a.target is not None and a.target != e.actor and a.kind in (
                        ActKind.ACCUSE, ActKind.VOTE_INTENT, ActKind.DOUBT_CLAIM, ActKind.QUESTION):
                    pending.setdefault(a.target, set()).add(e.actor)
        # 模型调用
        for call in rec.calls:
            prov = str(call.get('provider', ''))
            key = 'jev' if 'jev' in prov else 'claude'
            c[f'calls_{key}'] += 1
            if call.get('error') is None:
                lat[key].append(float(call.get('latency_ms') or 0.0))
            c['call_errors'] += call.get('error') is not None
        c['cost_milli'] += rec.cost * 1000
        c['wall_ms'] += rec.wall_s * 1000

    def ratio(a: str, b: str | float) -> float:
        den = c[b] if isinstance(b, str) else b
        return c[a] / den if den else 0.0

    n_speech = max(1, sum(tiers.values()))
    out: dict[str, float] = {
        'games': float(len(records)),
        'wolf_win': ratio('wolf_win', n), 'draw': ratio('draw', n),
        'days_mean': statistics.fmean(days) if days else 0.0,
        'seer_alive_d3': ratio('seer_alive_d3', n), 'seer_claim_d2': ratio('seer_claim_d2', n),
        'fake_seer': ratio('fake_seer', n), 'two_wolf_seer': float(c['two_wolf_seer']),
        'non_pass': ratio('non_pass', 'speeches'), 'bloc_d1': ratio('bloc_sum', 'bloc_n'),
        'no_exile': ratio('no_exile', 'vote_days'), 'non_optimal': ratio('non_optimal', 'decisions'),
        'non_optimal_steady': nonopt_state['steady'] / dec_state['steady'] if dec_state['steady'] else 0.0,
        'non_optimal_tilted': nonopt_state['tilted'] / dec_state['tilted'] if dec_state['tilted'] else 0.0,
        'commit': ratio('commit', 'commit_n'), 'follow': ratio('follow', 'follow_n'),
        'respond': ratio('respond', 'respond_n'),
        'd1_variety_ge3': ratio('d1_variety_ge3', n), 'd1_variety_mean': ratio('d1_variety_sum', n),
        'human_killed': ratio('human_killed', 'human_games'), 'human_exiled': ratio('human_exiled', 'human_games'),
        'ai_killed': ratio('ai_killed', 'ai_seats'), 'ai_exiled': ratio('ai_exiled', 'ai_seats'),
        'contradictions': float(c['contradictions']), 'violations': float(c['violations']),
        'check_block': ratio('check_blocks', 'claude_attempts'),
        'calls_jev': float(c['calls_jev']), 'calls_claude': float(c['calls_claude']),
        'call_errors': float(c['call_errors']),
        'cost_usd': c['cost_milli'] / 1000, 'wall_s_per_game': c['wall_ms'] / 1000 / n,
        'lat_jev_p50': _pct(lat['jev'], 0.5), 'lat_jev_p90': _pct(lat['jev'], 0.9),
        'lat_claude_p50': _pct(lat['claude'], 0.5), 'lat_claude_p90': _pct(lat['claude'], 0.9),
        'n_decisions': float(c['decisions']), 'n_speeches': float(c['speeches']),
        'n_follow': float(c['follow_n']), 'n_commit': float(c['commit_n']),
    }
    for t in ('claude', 'jev_select', 'template'):
        out[f'tier_{t}'] = tiers[t] / n_speech
    out['human_kill_ratio'] = out['human_killed'] / out['ai_killed'] if out['ai_killed'] else 0.0
    out['human_exile_ratio'] = out['human_exiled'] / out['ai_exiled'] if out['ai_exiled'] else 0.0
    return out


# --------------------------------------------------------------------------- 报告

# (键, 中文, 第 10 节的目标或说明, 格式)
METRICS: list[tuple[str, str, str, str]] = [
    ('games', '局数', '', 'int'),
    ('wolf_win', '狼人胜率', '30%–70%', 'pct'),
    ('draw', '平局率', '', 'pct'),
    ('days_mean', '平均天数', '', 'num'),
    ('seer_alive_d3', '预言家活到第 3 天', '≥ 25%', 'pct'),
    ('seer_claim_d2', '预言家第 2 天前起跳', '≥ 70%', 'pct'),
    ('fake_seer', '出现悍跳的局', '30%–80%', 'pct'),
    ('two_wolf_seer', '两狼都跳预言家（局数）', '0', 'int'),
    ('non_pass', '非「过」的发言', '≥ 85%', 'pct'),
    ('bloc_d1', '首日最大票仓占比', '≤ 60%', 'pct'),
    ('no_exile', '无人出局的白天', '≤ 25%', 'pct'),
    ('non_optimal', '非最优选择率', '10%–40%', 'pct'),
    ('non_optimal_steady', '　其中 steady', '', 'pct'),
    ('non_optimal_tilted', '　其中 tilted', '> steady', 'pct'),
    ('commit', '好人兑现说过的票', '≥ 80% 且 < 100%', 'pct'),
    ('follow', '跟票率（号召 × 投票人）', '15%–70%（不跟票 30%–85%）', 'pct'),
    ('respond', '被点名后回应', '70%–95%', 'pct'),
    ('d1_variety_ge3', '首日方案种类 ≥ 3 的局', '100%', 'pct'),
    ('human_killed', '托管真人座位被刀率', '', 'pct'),
    ('ai_killed', 'AI 座位被刀率', '', 'pct'),
    ('human_kill_ratio', '　被刀率之比', '0.5–2', 'num'),
    ('human_exiled', '托管真人座位被放逐率', '', 'pct'),
    ('ai_exiled', 'AI 座位被放逐率', '', 'pct'),
    ('human_exile_ratio', '　被放逐率之比', '0.5–2', 'num'),
    ('contradictions', '声明矛盾（条）', '0', 'int'),
    ('violations', '不变量与核对违例（条）', '0', 'int'),
    ('tier_claude', '台词 Claude 档', '真模型 ≥ 80%', 'pct'),
    ('tier_jev_select', '台词 Jev 选句档', '', 'pct'),
    ('tier_template', '台词模板档', '', 'pct'),
    ('check_block', '核对拦截率（Claude 档）', '≤ 15%', 'pct'),
    ('calls_jev', 'Jev 调用', '', 'int'),
    ('calls_claude', 'Claude 调用', '', 'int'),
    ('call_errors', '调用出错', '', 'int'),
    ('cost_usd', '花费（美元）', '', 'usd'),
    ('lat_jev_p50', 'Jev 时延 p50（ms）', '', 'int'),
    ('lat_jev_p90', 'Jev 时延 p90（ms）', '', 'int'),
    ('lat_claude_p50', 'Claude 时延 p50（ms）', '', 'int'),
    ('lat_claude_p90', 'Claude 时延 p90（ms）', '', 'int'),
    ('wall_s_per_game', '每局耗时（秒）', '', 'num'),
]


def fmt(v: float, kind: str) -> str:
    if kind == 'pct':
        return f'{v * 100:.1f}%'
    if kind == 'int':
        return f'{v:.0f}'
    if kind == 'usd':
        return f'${v:.4f}'
    return f'{v:.2f}'


def report_md(title: str, summaries: Mapping[str, Mapping[str, float]], *,
              samples: Sequence[str] = (), notes: Sequence[str] = ()) -> str:
    """一张或几张（按模式）指标表；两列时另给差值。"""
    modes = list(summaries)
    head = '| 指标 | 目标 | ' + ' | '.join(modes) + (' | 差值 |' if len(modes) == 2 else ' |')
    sep = '|' + ' --- |' * (2 + len(modes) + (1 if len(modes) == 2 else 0))
    lines = [f'# {title}', '', head, sep]
    for key, zh, target, kind in METRICS:
        vals = [summaries[m].get(key, 0.0) for m in modes]
        row = f'| {zh} | {target} | ' + ' | '.join(fmt(v, kind) for v in vals)
        if len(modes) == 2:
            dv = vals[1] - vals[0]
            row += f' | {"+" if dv >= 0 else ""}{fmt(dv, kind)}'
        lines.append(row + ' |')
    if notes:
        lines += ['', *[f'- {x}' for x in notes]]
    if samples:
        lines += ['', '## 台词样本', '', *[f'- {s}' for s in samples]]
    return '\n'.join(lines) + '\n'
