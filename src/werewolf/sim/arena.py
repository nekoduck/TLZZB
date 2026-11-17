"""规则版的同步对局循环：给测试与调参用（不调模型、不走会话层）。

遵守 ARCHITECTURE 第 8 节的编排：取一批请求 → 每个座位的心智作答（`options()` 与 `decide()` 之间不喂事件）
→ 全部答案到齐后按座位升序 `submit` → 新事件经 `game.fanout` 喂给每个心智的 `observe`。
每个座位每夜恰好 `apply_reflection(reflect_rules)` 一次，在当夜第一批请求之前。
发言走窄化的那条路：`plan.public() + view.public() + mind.public_mood() → build_brief → compose`，
`Speech.acts = plan.public_acts()`，并用 `check_rules` 核对文字与计划对得上。
所有座位都是心智；「真人」座位只是托管的心智，`is_human` 只影响首夜照顾。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from werewolf.kernel import invariants as kinv
from werewolf.kernel.engine import Game
from werewolf.kernel.rng import Rng
from werewolf.kernel.types import (
    NIGHT_PHASES,
    Action,
    Event,
    EventType,
    GameConfig,
    Phase,
    Request,
    RequestKind,
    Role,
    Seat,
    Speech,
)
from werewolf.llm.check import check_rules
from werewolf.mind import invariants as minv
from werewolf.mind.mind import Mind
from werewolf.mind.types import (
    Decision,
    MindSnapshot,
    MindTuning,
    Pace,
    Persona,
    Reaction,
    Situation,
    SpeechPlan,
)
from werewolf.voice.brief import build_brief
from werewolf.voice.compose import compose
from werewolf.voice.personas import draft, voice_of


@dataclass
class ArenaResult:
    game: Game
    minds: dict[Seat, Mind]
    decisions: list[Decision] = field(default_factory=list)
    plans: list[SpeechPlan] = field(default_factory=list)
    variants: list[list[SpeechPlan]] = field(default_factory=list)  # 与 plans 一一对应：当时的全部方案
    texts: list[str] = field(default_factory=list)  # 与 plans 一一对应
    snapshots_by_day: dict[int, dict[Seat, MindSnapshot]] = field(default_factory=dict)
    reactions: list[Reaction] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)  # 内核不变量、心智不变量、台词核对
    personas: dict[Seat, Persona] = field(default_factory=dict)
    # 主投票时心智记着的「今天说过要投谁」（没被取消的）：(座位, 天, 说过要投的人, 实际选项)
    commitments: list[tuple[Seat, int, Seat, str]] = field(default_factory=list)
    steps: int = 0

    @property
    def roles(self) -> dict[Seat, Role]:
        return {s: self.game.role_of(s) for s in sorted(self.minds)}


StepHook = Callable[[Game, Mapping[Seat, Mind], list[Event]], None]


def play(seed: int, *, roles: Mapping[Seat, Role] | None = None, personas: Sequence[Persona] | None = None,
         tuning: MindTuning | None = None, human_seat: Seat | None = None, on_step: StepHook | None = None,
         config: GameConfig | None = None, check_kernel: bool = True, check_mind: bool = True,
         max_steps: int = 4000) -> ArenaResult:
    """打一局。`personas` 按座位 1..n 给（缺省从人设池里按种子抽）；`on_step(game, minds, new_events)`
    在每次提交并喂完事件之后调用。"""
    cfg = config or GameConfig(human_seat=human_seat)
    if human_seat is not None and cfg.human_seat != human_seat:
        cfg = cfg.model_copy(update={'human_seat': human_seat})
    game = Game.new(cfg, seed, roles)
    rng = Rng(seed, 'arena')
    plist = list(personas) if personas is not None else draft(rng.fork('personas'), cfg.seats)
    seats = list(range(1, cfg.seats + 1))
    minds = {s: Mind(s, plist[s - 1], game.role_of(s), cfg, rng.fork(f'mind:{s}'), tuning) for s in seats}
    res = ArenaResult(game=game, minds=minds, personas={s: plist[s - 1] for s in seats})
    compose_rng = rng.fork('compose')
    _feed(game, minds, game.events('judge'), res)
    reflected: set[tuple[Seat, int]] = set()
    snap_days: set[int] = set()
    while res.steps < max_steps:
        reqs = game.pending()
        if not reqs:
            break
        phase = reqs[0].phase
        day = reqs[0].day
        if phase in NIGHT_PHASES:
            for s in seats:
                if (s, day) not in reflected and game.is_alive(s):
                    reflected.add((s, day))
                    m = minds[s]
                    m.apply_reflection(m.reflect_rules(game.view(s)))
        if phase == Phase.DAY_SPEECH and day not in snap_days:
            snap_days.add(day)
            res.snapshots_by_day[day] = {s: minds[s].snapshot() for s in seats}
        actions: list[Action] = []
        for req in reqs:
            actions.append(_answer(game, minds[req.seat], req, res, compose_rng, check_mind))
        new: list[Event] = []
        for a in sorted(actions, key=lambda x: x.seat):
            out = game.submit(a)
            res.steps += 1
            if out:
                new = out
        _feed(game, minds, new, res)
        if check_kernel:
            res.violations += [f'kernel:{v}' for v in kinv.check(game)]
        if on_step is not None:
            on_step(game, minds, new)
    return res


def _feed(game: Game, minds: Mapping[Seat, Mind], events: Sequence[Event], res: ArenaResult) -> None:
    if not events:
        return
    per = game.fanout(events)
    for s, evs in per.items():
        if not evs:
            continue
        view = game.view(s)
        m = minds[s]
        for ev in evs:
            res.reactions += m.observe(ev, view)


def _answer(game: Game, mind: Mind, req: Request, res: ArenaResult, compose_rng: Rng, check_mind: bool) -> Action:
    view = game.view(req.seat)
    if req.kind == RequestKind.SPEAK:
        variants = mind.speech_variants(req, view)
        plan, d = mind.choose_plan(variants)
        if check_mind:
            res.violations += [f'mind:{req.seat}:{v}' for v in minv.check(mind.snapshot(req), plan, view)]
        text, problems = speak(plan, view, mind, req, compose_rng)
        res.violations += [f'check:{req.seat}:{p}' for p in problems]
        res.decisions.append(d)
        res.plans.append(plan)
        res.variants.append(variants)
        res.texts.append(text)
        sp = Speech(text=text, acts=plan.public_acts(), tone=plan.tone, emote=plan.emote, source='ai')
        return Action(request_id=req.id, seat=req.seat, speech=sp)
    intent = mind.vote_intent
    cands = mind.options(req, view)
    d = mind.decide(req, view, cands)
    if req.kind == RequestKind.VOTE and req.context.get('round', 'main') == 'main' and intent is not None \
            and any(o.target == intent for o in req.options):
        res.commitments.append((req.seat, req.day, intent, d.option_id))
    if check_mind:
        res.violations += [f'mind:{req.seat}:{v}' for v in minv.check(mind.snapshot(req), None, view)]
    res.decisions.append(d)
    stance = d.stance if req.kind in (RequestKind.WOLF_PROPOSE, RequestKind.WOLF_KILL) and req.day == 1 else None
    note = d.note[:80] if d.note and req.kind == RequestKind.WOLF_PROPOSE else None
    return Action(request_id=req.id, seat=req.seat, option_id=d.option_id, note=note, stance=stance)  # type: ignore[arg-type]


def speak(plan: SpeechPlan, view: Any, mind: Mind, req: Request, rng: Rng) -> tuple[str, list[str]]:
    """窄化：只把公开族的东西交给写台词的一方。返回 (台词, 核对出的问题)。"""
    pv = view.public()
    pace = Pace()
    ctx = req.context
    died = None
    if req.speech_kind == 'last_words':
        died = 'night' if ctx.get('after') == 'night' else 'vote'
        p = next((x for x in pv.players if x.seat == req.seat), None)
        if p is not None and p.died_when is not None:
            died = p.died_when
    sit = Situation(index=int(ctx.get('index') or 0), total=int(ctx.get('total') or 1),
                    rivals=[int(s) for s in ctx.get('rivals') or []], died=died)
    brief = build_brief(plan.public(), pv, voice_of(mind.persona), mind.public_mood(), sit, [],
                        pace.max_chars[plan.length])
    text = compose(brief, rng.fork(req.id))
    return text, check_rules(text, brief)


# --------------------------------------------------------------------------- 统计小工具


def seer_claim_day(res: ArenaResult) -> int | None:
    """真预言家第一次公开自称预言家是第几天；没跳返回 None。"""
    seer = next((s for s, r in res.roles.items() if r == Role.SEER), None)
    for ev in res.game.events('judge'):
        if ev.type == EventType.SPEECH and ev.actor == seer:
            for a in (ev.data.get('speech') or {}).get('acts') or []:
                if a.get('kind') == 'claim_role' and a.get('role') == 'seer':
                    return ev.day
    return None


def summarize(results: Sequence[ArenaResult]) -> dict[str, float]:
    """一批对局的统计（第 10 节的各项指标）。比例都在 0 到 1 之间；分母为 0 的项记 0。

    - wolf_win：狼人胜率；seer_alive_d3：预言家活到第 3 天开始（第 3 夜没死）的局（含此前已终局而他活着的）；
    - non_pass：发言里不全是「过」的比例；seer_claim_d2：真预言家在第 2 天结束前起跳的局；
    - fake_seer：出现狼自称预言家的局；two_wolf_seer：两只狼都自称预言家的局数；
    - bloc_d1：首日主投票最大票仓占投票人数的均值；no_exile：有投票的白天里无人出局的比例；
    - non_optimal：非最优选择率（全部决定，含选发言方案）；
    - commit：好人主投票时，心智仍记着（没被局面变化取消）的「今天要投谁」被兑现的比例；
    - respond：被指认、点名之后的下一次发言里回应对方（addressed 含他）的比例；
    - contradictions：全部座位的声明矛盾条数；violations：不变量与台词核对的违例条数。
    """
    from werewolf.kernel.claims import ClaimLedger, speech_acts

    n = max(1, len(results))
    c: dict[str, float] = dict.fromkeys(
        ('wolf_win', 'seer_alive_d3', 'seer_claim_d2', 'fake_seer', 'two_wolf_seer', 'contradictions', 'violations'),
        0.0)
    speeches = nonpass = dec = nonopt = bloc = bloc_n = vdays = noexile = cm = cm_n = rs = rs_n = 0.0
    for r in results:
        g = r.game
        roles = r.roles
        res = g.result
        c['wolf_win'] += bool(res and res.winner is not None and res.winner.value == 'wolf')
        pub = [e for e in g.events('judge') if e.vis == 'public']
        lg = ClaimLedger.from_events(pub)
        seer = next(s for s, x in roles.items() if x == Role.SEER)
        d = lg.death_of(seer)
        c['seer_alive_d3'] += d is None or d[0] > 3 or (d[0] == 3 and d[1] != 'night')
        cd = seer_claim_day(r)
        c['seer_claim_d2'] += cd is not None and cd <= 2
        jumpers = [s for s, x in roles.items() if x == Role.WEREWOLF and lg.role_claims(s, Role.SEER)]
        c['fake_seer'] += bool(jumpers)
        c['two_wolf_seer'] += len(jumpers) >= 2
        c['contradictions'] += sum(len(lg.contradictions(s)) for s in roles)
        c['violations'] += len(r.violations)
        for p in r.plans:
            speeches += 1
            nonpass += any(a.kind.value != 'pass' for a in p.acts)
        for x in r.decisions:
            dec += 1
            nonopt += x.option_id != x.best_option_id
        days_with_vote: set[int] = set()
        exiled_days: set[int] = set()
        for e in pub:
            if e.type == EventType.VOTE_RESULT:
                days_with_vote.add(e.day)
                if e.day == 1 and e.data.get('round') == 'main':
                    tally = e.data.get('tally') or []
                    tot = sum(len(t['voters']) for t in tally) + len(e.data.get('abstain') or [])
                    bloc += max((len(t['voters']) for t in tally), default=0) / max(1, tot)
                    bloc_n += 1
            elif e.type == EventType.EXILE:
                exiled_days.add(e.day)
        vdays += len(days_with_vote)
        noexile += len(days_with_vote - exiled_days)
        for seat, _day, intent, opt in r.commitments:
            if roles[seat] != Role.WEREWOLF:
                cm_n += 1
                cm += opt == f'vote:{intent}'
        # 回应：发言前（同一天、上次自己发言之后）被点到的人，是否在 addressed 里
        plan_by_speech = iter(r.plans)
        pending: dict[Seat, set[Seat]] = {}
        for e in g.events('judge'):
            if e.type == EventType.PHASE and e.data.get('phase') == Phase.NIGHT_WOLF.value:
                pending = {}
            if e.type != EventType.SPEECH or e.actor is None:
                continue
            p = next(plan_by_speech)
            hit = pending.pop(e.actor, set())
            if hit and e.data.get('kind') != 'last_words':
                rs_n += 1
                rs += bool(hit & set(p.addressed))
            for a in speech_acts(e):
                if a.target is not None and a.target != e.actor and a.kind.value in (
                        'accuse', 'vote_intent', 'doubt_claim', 'question'):
                    pending.setdefault(a.target, set()).add(e.actor)
    out = {k: v / n for k, v in c.items() if k not in ('two_wolf_seer', 'contradictions', 'violations')}
    out.update(two_wolf_seer=c['two_wolf_seer'], contradictions=c['contradictions'], violations=c['violations'],
               non_pass=nonpass / max(1, speeches), non_optimal=nonopt / max(1, dec), bloc_d1=bloc / max(1, bloc_n),
               no_exile=noexile / max(1, vdays), commit=cm / max(1, cm_n), respond=rs / max(1, rs_n),
               games=float(len(results)))
    return out
