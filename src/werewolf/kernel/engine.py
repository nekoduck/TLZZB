"""对局引擎：唯一的事实来源。同步、确定、不做 I/O。

推进模型：引擎在「开出一批请求」或「终局」处停下。`submit` 收齐一批后，按座位升序结算批内各人的动作，
再自动推进到下一批。停下时的全部状态（含续接点 `_stage` 与它的上下文 `_ctx`）都是 JSON 原生的，
所以 `load(dump())` 之后的推进与不存档时逐字相同。
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from werewolf.kernel import events as E
from werewolf.kernel import rules
from werewolf.kernel.rng import Rng
from werewolf.kernel.types import (
    Action,
    Event,
    EventType,
    GameConfig,
    GameResult,
    IllegalAction,
    Option,
    Phase,
    PublicView,
    Request,
    RequestKind,
    Role,
    Seat,
    SpeechKind,
    Team,
    View,
    Viewer,
    strip_text,
)
from werewolf.kernel.views import build_public_view, build_view

DUMP_VERSION = 1


class Game:
    config: GameConfig

    def __init__(self, config: GameConfig, seed: int, roles: Mapping[Seat, Role]) -> None:
        """不要直接调用：用 `Game.new` 或 `Game.load`。"""
        self.config = config
        self.seed = seed
        self._roles: dict[Seat, Role] = {s: Role(roles[s]) for s in sorted(roles)}
        self._rng = Rng(seed, 'kernel')
        self._events: list[Event] = []
        self._alive: set[Seat] = set(self._roles)
        self._day = 0
        self._phase = Phase.SETUP
        self._antidote = True
        self._poison = True
        self._checked: list[Seat] = []
        self._n_req = 0
        self._stage = ''
        self._ctx: dict[str, Any] = {}
        self._night: dict[str, Any] = {}
        self._batch: list[Request] = []
        self._answers: dict[str, Action] = {}
        self._result: GameResult | None = None

    # ------------------------------------------------------------------ 构造

    @classmethod
    def new(cls, config: GameConfig, seed: int, roles: Mapping[Seat, Role] | None = None) -> Game:
        if not 0 <= seed < 2**53:
            raise ValueError(f'seed out of range: {seed}')
        if len(config.roles) != config.seats:
            raise ValueError('config.roles must have one role per seat')
        if roles is None:
            deck = list(config.roles)
            Rng(seed, 'deal').shuffle(deck)
            dealt = {i + 1: r for i, r in enumerate(deck)}
        else:
            dealt = {int(s): Role(r) for s, r in roles.items()}
            if set(dealt) != set(range(1, config.seats + 1)):
                raise ValueError('roles must cover seats 1..n exactly')
            if Counter(dealt.values()) != Counter(config.roles):
                raise ValueError('roles must be the same multiset as config.roles')
        if config.human_seat is not None and config.human_seat not in dealt:
            raise ValueError('human_seat out of range')
        g = cls(config, seed, dealt)
        g._setup()
        return g

    # ------------------------------------------------------------------ 公共接口

    def pending(self) -> list[Request]:
        if self._result is not None:
            return []
        return [r for r in self._batch if r.id not in self._answers]

    def submit(self, action: Action) -> list[Event]:
        if self._result is not None:
            raise IllegalAction('game_over')
        req = next((r for r in self._batch if r.id == action.request_id and r.id not in self._answers), None)
        if req is None:
            raise IllegalAction('unknown_request', action.request_id)
        if req.seat != action.seat:
            raise IllegalAction('wrong_seat', f'{action.request_id} belongs to seat {req.seat}')
        if req.kind == RequestKind.SPEAK:
            if action.speech is None:
                raise IllegalAction('missing_speech')
            problem = rules.speech_problem(action.speech, self.config.seats)
            if problem:
                raise IllegalAction('bad_speech', problem)
        elif action.option_id not in {o.id for o in req.options}:
            raise IllegalAction('not_an_option', str(action.option_id))
        self._answers[req.id] = action
        if len(self._answers) < len(self._batch):
            return []
        start = len(self._events)
        batch = sorted(self._batch, key=lambda r: r.seat)
        answers = self._answers
        self._batch, self._answers = [], {}
        self._resolve([(r, answers[r.id]) for r in batch])
        return self._events[start:]

    def view(self, seat: Seat) -> View:
        return build_view(self.config, seat, [e for e in self._events if self.visible(e, seat)])

    def public_view(self) -> PublicView:
        return build_public_view(self.config, self._events)

    def visible(self, ev: Event, viewer: Viewer) -> bool:
        if viewer == 'judge' or ev.vis == 'public':
            return True
        if ev.vis == 'wolves':
            return self._roles.get(viewer) == Role.WEREWOLF  # type: ignore[arg-type]
        if ev.vis == 'seats':
            return viewer in ev.vis_seats
        return False

    def events(self, viewer: Viewer, since: int = 0) -> list[Event]:
        return [e for e in self._events[max(since, 0):] if self.visible(e, viewer)]

    def fanout(self, events: Sequence[Event]) -> dict[Seat, list[Event]]:
        return {s: [strip_text(e) for e in events if self.visible(e, s)] for s in self._roles}

    @property
    def over(self) -> bool:
        return self._result is not None

    @property
    def result(self) -> GameResult | None:
        return self._result

    # ---- 裁判专用 ----

    def role_of(self, seat: Seat) -> Role:
        return self._roles[seat]

    def is_alive(self, seat: Seat) -> bool:
        return seat in self._alive

    def dump(self) -> dict:
        return {
            'version': DUMP_VERSION,
            'config': json.loads(self.config.model_dump_json()),
            'seed': self.seed,
            'roles': [[s, r.value] for s, r in sorted(self._roles.items())],
            'rng': self._rng.state(),
            'events': [e.model_dump(mode='json') for e in self._events],
            'alive': sorted(self._alive),
            'day': self._day,
            'phase': self._phase.value,
            'antidote': self._antidote,
            'poison': self._poison,
            'checked': list(self._checked),
            'n_req': self._n_req,
            'stage': self._stage,
            'ctx': json.loads(json.dumps(self._ctx)),
            'night': json.loads(json.dumps(self._night)),
            'batch': [r.model_dump(mode='json') for r in self._batch],
            'answers': [a.model_dump(mode='json') for a in self._answers.values()],
            'result': self._result.model_dump(mode='json') if self._result else None,
        }

    @classmethod
    def load(cls, data: dict) -> Game:
        if data.get('version') != DUMP_VERSION:
            raise ValueError(f'unsupported dump version: {data.get("version")}')
        config = GameConfig.model_validate(data['config'])
        g = cls(config, int(data['seed']), {int(s): Role(r) for s, r in data['roles']})
        g._rng = Rng.from_state(data['rng'])
        g._events = [Event.model_validate(e) for e in data['events']]
        g._alive = {int(s) for s in data['alive']}
        g._day = int(data['day'])
        g._phase = Phase(data['phase'])
        g._antidote = bool(data['antidote'])
        g._poison = bool(data['poison'])
        g._checked = [int(s) for s in data['checked']]
        g._n_req = int(data['n_req'])
        g._stage = str(data['stage'])
        g._ctx = json.loads(json.dumps(data['ctx']))
        g._night = json.loads(json.dumps(data['night']))
        g._batch = [Request.model_validate(r) for r in data['batch']]
        answers = [Action.model_validate(a) for a in data['answers']]
        g._answers = {a.request_id: a for a in answers}
        g._result = GameResult.model_validate(data['result']) if data['result'] else None
        return g

    # ------------------------------------------------------------------ 内部：记账

    def _stamp(self) -> E.Stamp:
        return E.Stamp(len(self._events) + 1, self._day, self._phase)

    def _add(self, ev: Event) -> None:
        self._events.append(ev)

    def _enter(self, phase: Phase, **kw: Any) -> None:
        self._phase = phase
        self._add(E.phase(self._stamp(), **kw))

    def _living(self) -> list[Seat]:
        return sorted(self._alive)

    def _seat_of(self, role: Role) -> Seat | None:
        return next((s for s, r in self._roles.items() if r == role), None)

    def _wolves_alive(self) -> list[Seat]:
        return [s for s in self._living() if self._roles[s] == Role.WEREWOLF]

    def _request(self, kind: RequestKind, seat: Seat, options: list[Option] | None = None,
                 speech_kind: SpeechKind | None = None, context: dict[str, Any] | None = None) -> Request:
        self._n_req += 1
        return Request(id=f'r{self._n_req}', kind=kind, seat=seat, day=self._day, phase=self._phase,
                       options=options or [], speech_kind=speech_kind, context=context or {})

    def _open(self, stage: str, requests: list[Request]) -> None:
        assert requests, 'empty batch'
        self._stage = stage
        self._batch = requests
        self._answers = {}

    def _die(self, seat: Seat, when: str, cause: str) -> None:
        self._alive.discard(seat)
        self._add(E.death(self._stamp(), seat, when))  # type: ignore[arg-type]
        self._add(E.death_cause(self._stamp(), seat, cause))  # type: ignore[arg-type]

    # ------------------------------------------------------------------ 推进

    def _setup(self) -> None:
        names = {s: self.config.names.get(s, f'{s}号') for s in self._roles}
        self._add(E.game_start(self._stamp(), names, self.config.roles))
        for s, r in self._roles.items():
            self._add(E.role_dealt(self._stamp(), s, r))
        self._add(E.wolf_team(self._stamp(), [s for s, r in self._roles.items() if r == Role.WEREWOLF]))
        self._start_night()

    def _resolve(self, answered: list[tuple[Request, Action]]) -> None:
        handler = {
            'wolf_propose': self._on_wolf_propose,
            'wolf_kill': self._on_wolf_kill,
            'seer_check': self._on_seer_check,
            'witch_act': self._on_witch_act,
            'hunter_shoot': self._on_hunter_shoot,
            'speak': self._on_speak,
            'vote': self._on_vote,
        }[self._stage]
        handler(answered)

    # ---- 夜 ----

    def _start_night(self) -> None:
        self._day += 1
        self._night = {'kill': None, 'saved': False, 'poisoned': None}
        self._enter(Phase.NIGHT_WOLF)
        wolves = self._wolves_alive()
        human = self.config.human_seat
        leader = human if human in wolves else None
        proposers = [w for w in wolves if w != leader]
        self._ctx = {'leader': leader, 'proposals': []}
        if proposers:
            opts = rules.kill_options(self._living(), self.config.allow_empty_kill)
            self._open('wolf_propose', [
                self._request(RequestKind.WOLF_PROPOSE, w, list(opts), context={'first_night': self._day == 1})
                for w in proposers
            ])
        else:
            self._ask_leader()

    def _on_wolf_propose(self, answered: list[tuple[Request, Action]]) -> None:
        props = []
        for req, act in answered:
            opt = next(o for o in req.options if o.id == act.option_id)
            self._add(E.wolf_propose(self._stamp(), req.seat, opt.id, opt.target, act.note, act.stance))
            props.append({'seat': req.seat, 'option_id': opt.id, 'target': opt.target, 'note': act.note,
                          'stance': act.stance})
        self._ctx['proposals'] = props
        if self._ctx['leader'] is not None:
            self._ask_leader()
            return
        choice, by, tally = rules.wolf_plurality({p['seat']: p['option_id'] for p in props})
        target = next(p['target'] for p in props if p['option_id'] == choice)
        self._set_kill(target, by, tally, None)

    def _ask_leader(self) -> None:
        leader = self._ctx['leader']
        opts = rules.kill_options(self._living(), self.config.allow_empty_kill)
        self._open('wolf_kill', [self._request(
            RequestKind.WOLF_KILL, leader, opts,
            context={'first_night': self._day == 1, 'proposals': list(self._ctx['proposals'])},
        )])

    def _on_wolf_kill(self, answered: list[tuple[Request, Action]]) -> None:
        req, act = answered[0]
        opt = next(o for o in req.options if o.id == act.option_id)
        tally = rules.kill_tally([p['option_id'] for p in self._ctx['proposals']] + [opt.id])
        self._set_kill(opt.target, 'leader', tally, act.stance)

    def _set_kill(self, target: Seat | None, by: str, tally: dict[str, int], stance: str | None) -> None:
        self._night['kill'] = target
        self._add(E.wolf_kill(self._stamp(), target, by, tally, stance))  # type: ignore[arg-type]
        self._seer_phase()

    def _seer_phase(self) -> None:
        self._enter(Phase.NIGHT_SEER)
        seer = self._seat_of(Role.SEER)
        if seer is not None and seer in self._alive:
            opts = rules.check_options(seer, self._living(), self._checked)
            if len(opts) >= 2:
                self._open('seer_check', [self._request(RequestKind.SEER_CHECK, seer, opts)])
                return
        self._witch_phase()

    def _on_seer_check(self, answered: list[tuple[Request, Action]]) -> None:
        req, act = answered[0]
        target = next(o.target for o in req.options if o.id == act.option_id)
        assert target is not None
        self._checked.append(target)
        result = 'wolf' if self._roles[target] == Role.WEREWOLF else 'good'
        self._add(E.seer_result(self._stamp(), req.seat, target, result, self._day))
        self._witch_phase()

    def _witch_phase(self) -> None:
        self._enter(Phase.NIGHT_WITCH)
        witch = self._seat_of(Role.WITCH)
        if witch is not None and witch in self._alive:
            victim = self._night['kill']
            self._add(E.witch_notice(self._stamp(), witch, victim, self._antidote, self._poison))
            opts = rules.witch_options(witch, victim, self._antidote, self._poison, self._living(), self._day,
                                       self.config.witch_self_save)
            if len(opts) >= 2:
                ctx = {'victim': victim if self._antidote else None, 'victim_known': self._antidote,
                       'antidote': self._antidote, 'poison': self._poison}
                self._open('witch_act', [self._request(RequestKind.WITCH_ACT, witch, opts, context=ctx)])
                return
        self._dawn()

    def _on_witch_act(self, answered: list[tuple[Request, Action]]) -> None:
        req, act = answered[0]
        opt = next(o for o in req.options if o.id == act.option_id)
        if opt.id == 'save':
            assert opt.target is not None
            self._antidote = False
            self._night['saved'] = True
            self._add(E.witch_save(self._stamp(), req.seat, opt.target))
        elif opt.id.startswith('poison:'):
            assert opt.target is not None
            self._poison = False
            self._night['poisoned'] = opt.target
            self._add(E.witch_poison(self._stamp(), req.seat, opt.target))
        self._dawn()

    def _dawn(self) -> None:
        self._enter(Phase.DAWN)
        causes: dict[Seat, str] = {}
        kill, poisoned = self._night['kill'], self._night['poisoned']
        if kill is not None and not self._night['saved']:
            causes[kill] = 'wolf'
        if poisoned is not None:
            causes[poisoned] = 'poison'  # 又刀又毒只死一次，死因记毒
        deaths = sorted(causes)
        self._ctx = {'dawn_deaths': deaths}
        self._add(E.dawn(self._stamp(), deaths))
        for s in deaths:
            self._die(s, 'night', causes[s])
        if deaths:
            self._death_batch('night', deaths, {s: causes[s] for s in deaths})
        else:
            self._start_day()

    # ---- 一批死亡：技能 → 判胜负 → 遗言 ----

    def _death_batch(self, after: str, dead: list[Seat], causes: Mapping[Seat, str]) -> None:
        self._ctx = {'after': after, 'dead': sorted(dead), 'shot': None,
                     'dawn_deaths': self._ctx.get('dawn_deaths', [])}
        self._enter(Phase.HUNTER, after=after)
        hunter = self._seat_of(Role.HUNTER)
        if hunter is not None and hunter in dead:
            if causes[hunter] == 'poison':
                self._add(E.hunter_blocked(self._stamp(), hunter))
            else:
                opts = rules.shoot_options(hunter, self._living())
                if len(opts) >= 2:
                    self._open('hunter_shoot', [self._request(RequestKind.HUNTER_SHOOT, hunter, opts,
                                                              context={'after': after})])
                    return
        self._after_skill()

    def _on_hunter_shoot(self, answered: list[tuple[Request, Action]]) -> None:
        req, act = answered[0]
        opt = next(o for o in req.options if o.id == act.option_id)
        self._add(E.hunter_shot(self._stamp(), req.seat, opt.target))
        if opt.target is not None:
            self._add(E.role_revealed(self._stamp(), req.seat, Role.HUNTER))
            self._die(opt.target, 'shot', 'shot')
            self._ctx['shot'] = opt.target
        self._after_skill()

    def _after_skill(self) -> None:
        if self._check_win():
            return
        after = self._ctx['after']
        order: list[Seat] = []
        if rules.has_last_words(after, self._day, self.config.last_words_nights):
            order = list(self._ctx['dead'])
            if self._ctx['shot'] is not None:
                order.append(self._ctx['shot'])
        if order:
            self._enter(Phase.LAST_WORDS, order=order, after=after)
            self._ctx['speech'] = {'kind': 'last_words', 'order': order, 'index': 0, 'after': after, 'rivals': []}
            self._ask_speaker()
        else:
            self._after_last_words()

    def _after_last_words(self) -> None:
        if self._ctx['after'] == 'night':
            self._start_day()
        else:
            self._end_of_day()

    def _check_win(self) -> bool:
        win = rules.check_win(self._roles, self._alive)
        if win is None:
            return False
        team, reason = win
        self._finish(team, reason)
        return True

    def _finish(self, winner: Team | None, reason: str) -> None:
        self._phase = Phase.GAME_OVER
        self._add(E.game_over(self._stamp(), winner.value if winner else None, reason, self._roles))
        self._batch, self._answers, self._stage = [], {}, ''
        self._result = GameResult(winner=winner, reason=reason, day=self._day, roles=dict(self._roles),  # type: ignore[arg-type]
                                  survivors=self._living())

    # ---- 白天 ----

    def _start_day(self) -> None:
        living = self._living()
        start = rules.speech_start(self._ctx.get('dawn_deaths', []), living, self.config.seats)
        if start is None:
            start = self._rng.choice(living)
        order = rules.ring_from(start, living)
        self._ctx = {'after': None}
        self._enter(Phase.DAY_SPEECH, order=order)
        self._ctx['speech'] = {'kind': 'day', 'order': order, 'index': 0, 'after': None, 'rivals': []}
        self._ask_speaker()

    def _ask_speaker(self) -> None:
        sp = self._ctx['speech']
        order, i = sp['order'], sp['index']
        seat = order[i]
        rivals = [s for s in sp['rivals'] if s != seat]
        ctx = {'index': i, 'total': len(order), 'after': sp['after'], 'rivals': rivals}
        self._open('speak', [self._request(RequestKind.SPEAK, seat, speech_kind=sp['kind'], context=ctx)])

    def _on_speak(self, answered: list[tuple[Request, Action]]) -> None:
        req, act = answered[0]
        sp = self._ctx['speech']
        assert act.speech is not None
        self._add(E.speech(self._stamp(), req.seat, sp['kind'], act.speech))
        sp['index'] += 1
        if sp['index'] < len(sp['order']):
            self._ask_speaker()
            return
        kind = sp['kind']
        if kind == 'last_words':
            self._after_last_words()
        elif kind == 'day':
            self._start_vote('main', self._living(), self._living())
        else:
            tied = sp['order']
            self._start_vote('pk', rules.pk_voters(self._living(), tied), tied)

    def _start_vote(self, round: str, voters: list[Seat], candidates: list[Seat]) -> None:
        reqs: list[tuple[Seat, list[Option]]] = []
        for v in voters:
            opts = rules.vote_options(v, candidates, self.config.allow_abstain)
            if len(opts) >= 2:
                reqs.append((v, opts))
        phase = Phase.DAY_VOTE if round == 'main' else Phase.PK_VOTE
        self._enter(phase, voters=[v for v, _ in reqs], candidates=list(candidates))
        self._ctx = {'after': None, 'round': round, 'candidates': list(candidates)}
        if not reqs:
            self._vote_result(round, {})
            return
        self._open('vote', [
            self._request(RequestKind.VOTE, v, opts,
                          context={'round': round, 'candidates': [o.target for o in opts if o.target is not None]})
            for v, opts in reqs
        ])

    def _on_vote(self, answered: list[tuple[Request, Action]]) -> None:
        round = self._ctx['round']
        votes: dict[Seat, Seat | None] = {}
        for req, act in answered:
            opt = next(o for o in req.options if o.id == act.option_id)
            votes[req.seat] = opt.target
            self._add(E.vote(self._stamp(), req.seat, opt.target, round, opt.id))
        self._vote_result(round, votes)

    def _vote_result(self, round: str, votes: Mapping[Seat, Seat | None]) -> None:
        tally, abstain = rules.tally_votes(votes)
        outcome, reason, exiled, tied = rules.vote_outcome(tally, round)  # type: ignore[arg-type]
        self._add(E.vote_result(self._stamp(), round, tally, abstain, outcome, reason, tied, exiled))  # type: ignore[arg-type]
        if outcome == 'exile':
            assert exiled is not None
            self._add(E.exile(self._stamp(), exiled))
            self._die(exiled, 'vote', 'vote')
            self._death_batch('vote', [exiled], {exiled: 'vote'})
        elif outcome == 'pk':
            self._enter(Phase.PK_SPEECH, order=tied, candidates=tied)
            self._ctx = {'after': None, 'speech': {'kind': 'pk', 'order': tied, 'index': 0, 'after': None,
                                                   'rivals': tied}}
            self._ask_speaker()
        else:
            self._ctx = {'after': 'vote'}
            self._end_of_day()

    def _end_of_day(self) -> None:
        if self._day >= self.config.max_days:
            self._finish(None, 'max_days')
        else:
            self._start_night()
