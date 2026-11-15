"""各模块测试共用的样例构造器：不经过内核引擎，手工拼出事件与视图。

内核自己的测试不要用它（要测的就是引擎）；心智、口吻、模型适配、导演在引擎写好之前用它造数据，
引擎写好之后的集成测试改用真的 `Game`。这里造出的事件遵守 ARCHITECTURE 3.3 的表（字段与可见性）。
"""

from __future__ import annotations

from typing import Any, Literal

from werewolf.kernel.types import (
    ActKind,
    Event,
    EventType,
    Option,
    Phase,
    PlayerPublic,
    PublicView,
    Request,
    RequestKind,
    Role,
    Seat,
    SeerCheck,
    Speech,
    SpeechAct,
    SpeechKind,
    Tone,
    View,
    WitchInfo,
    WolfInfo,
    strip_text,
    team_of,
)

DEFAULT_ROLES: dict[Seat, Role] = {
    1: Role.VILLAGER,
    2: Role.WEREWOLF,
    3: Role.SEER,
    4: Role.VILLAGER,
    5: Role.WEREWOLF,
    6: Role.WITCH,
    7: Role.VILLAGER,
    8: Role.WEREWOLF,
    9: Role.HUNTER,
}


def act(kind: ActKind | str, target: Seat | None = None, **kw: Any) -> SpeechAct:
    return SpeechAct(kind=ActKind(kind), target=target, **kw)


class Scenario:
    """一局手工拼的对局。按顺序调方法追加事件，最后用 `view(seat)` 取某个座位的视图。

        sc = Scenario()                      # 默认身份见 DEFAULT_ROLES，真人在 1 号
        sc.night(1, kill=4, check=(3, 5, 'good'), witch_victim=4)
        sc.dawn([4])
        sc.speech(3, '我是预言家，昨晚验了5号是好人。', [act('claim_role', role=Role.SEER),
                  act('claim_check', 5, result='good', night=1)])
        sc.vote({1: 8, 2: 6, 3: 8, 5: 6, 6: 8, 7: 8, 8: 6, 9: None}, exiled=8)
        v = sc.view(7)
    """

    def __init__(
        self,
        roles: dict[Seat, Role] | None = None,
        human: Seat | None = 1,
        names: dict[Seat, str] | None = None,
    ) -> None:
        self.roles: dict[Seat, Role] = dict(roles or DEFAULT_ROLES)
        self.human = human
        self.names = {s: (names or {}).get(s, f'{s}号') for s in self.roles}
        self.day = 0
        self.phase = Phase.SETUP
        self.events: list[Event] = []
        self.alive: dict[Seat, bool] = {s: True for s in self.roles}
        self.died: dict[Seat, tuple[int, str]] = {}
        self.revealed: dict[Seat, Role] = {}
        self.checks: list[SeerCheck] = []
        self.antidote = True
        self.poison = True
        self.saved: list[tuple[int, Seat]] = []
        self.poisoned: list[tuple[int, Seat]] = []
        self.kills: list[tuple[int, Seat | None]] = []
        self.tonight_victim: Seat | None = None
        self._n_req = 0
        self._tied: list[Seat] = []
        self.add(EventType.GAME_START, data={
            'seats': sorted(self.roles),
            'names': [{'seat': s, 'name': self.names[s]} for s in sorted(self.roles)],
            'role_counts': {r.value: list(self.roles.values()).count(r) for r in Role},
        })
        for s in sorted(self.roles):
            self.add(EventType.ROLE_DEALT, actor=s, data={'role': self.roles[s].value}, vis='seats', vis_seats=[s])
        self.add(EventType.WOLF_TEAM, data={'wolves': self.wolves()}, vis='wolves')

    # ---- 基础 ----
    def seat_of(self, role: Role) -> Seat:
        return next(s for s, r in sorted(self.roles.items()) if r == role)

    def wolves(self) -> list[Seat]:
        return sorted(s for s, r in self.roles.items() if r == Role.WEREWOLF)

    def living(self) -> list[Seat]:
        return sorted(s for s, a in self.alive.items() if a)

    def add(
        self,
        type: EventType,
        *,
        actor: Seat | None = None,
        target: Seat | None = None,
        data: dict[str, Any] | None = None,
        vis: Literal['public', 'wolves', 'seats', 'judge'] = 'public',
        vis_seats: list[Seat] | None = None,
    ) -> Event:
        ev = Event(
            id=len(self.events) + 1, day=self.day, phase=self.phase, type=type, actor=actor, target=target,
            data=data or {}, vis=vis, vis_seats=vis_seats or [],
        )
        self.events.append(ev)
        return ev

    def set_phase(self, phase: Phase, **data: Any) -> Event:
        self.phase = phase
        return self.add(EventType.PHASE, data={'phase': phase.value, 'day': self.day, **data})

    def _die(self, seat: Seat, when: str, cause: str) -> None:
        self.alive[seat] = False
        self.died[seat] = (self.day, when)
        self.add(EventType.DEATH, target=seat, data={'when': when, 'day': self.day})
        self.add(EventType.DEATH_CAUSE, target=seat, data={'cause': cause}, vis='judge')

    # ---- 夜 ----
    def night(
        self,
        day: int,
        *,
        kill: Seat | None = None,
        check: tuple[Seat, Seat, str] | None = None,  # (预言家座位, 目标, 'wolf' | 'good')
        save: bool = False,
        poison: Seat | None = None,
    ) -> None:
        """追加一整夜的事件（不结算死亡；之后调 `dawn`）。三个夜间阶段的 phase 事件每夜都发。"""
        self.day = day
        self.set_phase(Phase.NIGHT_WOLF)
        self.add(EventType.WOLF_KILL, target=kill, data={'by': 'plurality', 'tally': {f'kill:{kill or "none"}': 1}, 'stance': None},
                 vis='wolves')
        self.kills.append((day, kill))
        self.tonight_victim = kill
        self.set_phase(Phase.NIGHT_SEER)
        if check is not None:
            seer, tgt, result = check
            self.add(EventType.SEER_RESULT, actor=seer, target=tgt, data={'result': result, 'night': day},
                     vis='seats', vis_seats=[seer])
            self.checks.append(SeerCheck(night=day, target=tgt, result=result))  # type: ignore[arg-type]
        self.set_phase(Phase.NIGHT_WITCH)
        witch = self.seat_of(Role.WITCH)
        if self.alive[witch]:
            self.add(EventType.WITCH_NOTICE, target=kill if self.antidote else None,
                     data={'antidote': self.antidote, 'poison': self.poison, 'victim_known': self.antidote},
                     vis='seats', vis_seats=[witch])
            if save and kill is not None:
                self.add(EventType.WITCH_SAVE, actor=witch, target=kill, vis='seats', vis_seats=[witch])
                self.antidote = False
                self.saved.append((day, kill))
            elif poison is not None:
                self.add(EventType.WITCH_POISON, actor=witch, target=poison, vis='seats', vis_seats=[witch])
                self.poison = False
                self.poisoned.append((day, poison))

    def dawn(self, deaths: list[Seat], causes: dict[Seat, str] | None = None) -> None:
        """天亮：公布死者。有人死就接着进技能环节（phase hunter，不论死者是谁）。"""
        self.set_phase(Phase.DAWN)
        deaths = sorted(deaths)
        self.add(EventType.DAWN, data={'deaths': deaths, 'peaceful': not deaths})
        for s in deaths:
            self._die(s, 'night', (causes or {}).get(s, 'wolf'))
        self.tonight_victim = None
        if deaths:
            self.set_phase(Phase.HUNTER, after='night')

    # ---- 白天 ----
    def start_speeches(self, order: list[Seat] | None = None) -> None:
        self.set_phase(Phase.DAY_SPEECH, order=order or self.living())

    def speech(
        self,
        seat: Seat,
        text: str,
        acts: list[SpeechAct] | None = None,
        *,
        kind: SpeechKind = 'day',
        tone: Tone = Tone.CALM,
        flags: list[str] | None = None,
    ) -> Event:
        if kind == 'day' and self.phase != Phase.DAY_SPEECH:
            self.start_speeches()
        elif kind == 'pk' and self.phase != Phase.PK_SPEECH:
            self.set_phase(Phase.PK_SPEECH, order=list(self._tied), candidates=list(self._tied))
        elif kind == 'last_words' and self.phase != Phase.LAST_WORDS:
            self.set_phase(Phase.LAST_WORDS, order=[seat], after='night' if self.died.get(seat, (0, ''))[1] == 'night' else 'vote')
        sp = Speech(text=text, acts=acts or [], tone=tone, source='human' if seat == self.human else 'ai',
                    flags=flags or [])
        return self.add(EventType.SPEECH, actor=seat, data={'kind': kind, 'speech': sp.model_dump(mode='json')})

    def vote(self, votes: dict[Seat, Seat | None], *, exiled: Seat | None = None, round: str = 'main') -> None:
        """`votes`：投票者 → 目标（None 是弃票）。结果按票数自动算：唯一最高票出局；主轮平票进 PK；
        PK 再平或全员弃票无人出局。`exiled` 只用来核对（给了而算出来不是它就报错）。"""
        phase = Phase.DAY_VOTE if round == 'main' else Phase.PK_VOTE
        cands = list(self._tied) if round == 'pk' else self.living()
        self.set_phase(phase, voters=sorted(votes), candidates=cands)
        by_target: dict[Seat, list[Seat]] = {}
        abstain: list[Seat] = []
        for voter, tgt in sorted(votes.items()):
            self.add(EventType.VOTE, actor=voter, target=tgt,
                     data={'round': round, 'option_id': f'vote:{tgt}' if tgt else 'vote:abstain'})
            if tgt is None:
                abstain.append(voter)
            else:
                by_target.setdefault(tgt, []).append(voter)
        tally = [{'target': t, 'voters': v} for t, v in sorted(by_target.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
        top = len(tally[0]['voters']) if tally else 0
        tied = sorted(row['target'] for row in tally if len(row['voters']) == top) if top else []
        if not tally:
            outcome, reason, out = 'none', 'all_abstain', None
        elif len(tied) == 1:
            outcome, reason, out = 'exile', 'top', tied[0]
        elif round == 'main':
            outcome, reason, out = 'pk', 'tie', None
        else:
            outcome, reason, out = 'none', 'pk_tie', None
        if exiled is not None and exiled != out:
            raise ValueError(f'votes give {out}, not {exiled}')
        self._tied = tied if outcome == 'pk' else []
        self.add(EventType.VOTE_RESULT, target=out,
                 data={'round': round, 'tally': tally, 'abstain': abstain, 'outcome': outcome, 'reason': reason,
                       'tied': tied if len(tied) > 1 else []})
        if out is not None:
            self.add(EventType.EXILE, target=out)
            self._die(out, 'vote', 'vote')
            self.set_phase(Phase.HUNTER, after='vote')

    def shot(self, hunter: Seat, target: Seat) -> None:
        """猎人开枪（技能环节已由 dawn 或 vote 进入）。"""
        self.add(EventType.HUNTER_SHOT, actor=hunter, target=target)
        self.add(EventType.ROLE_REVEALED, target=hunter, data={'role': Role.HUNTER.value})
        self.revealed[hunter] = Role.HUNTER
        self._die(target, 'shot', 'shot')

    # ---- 视图与请求 ----
    def visible(self, ev: Event, seat: Seat | None) -> bool:
        if seat is None or ev.vis == 'public':
            return True
        if ev.vis == 'wolves':
            return self.roles[seat] == Role.WEREWOLF
        if ev.vis == 'seats':
            return seat in ev.vis_seats
        return False

    def view(self, seat: Seat) -> View:
        role = self.roles[seat]
        known: dict[Seat, Role] = {seat: role, **self.revealed}
        if role == Role.WEREWOLF:
            known.update({s: Role.WEREWOLF for s in self.wolves()})
        players = [
            PlayerPublic(
                seat=s, name=self.names[s], alive=self.alive[s], is_human=(s == self.human),
                died_day=self.died[s][0] if s in self.died else None,
                died_when=self.died[s][1] if s in self.died else None,  # type: ignore[arg-type]
                revealed_role=self.revealed.get(s),
            )
            for s in sorted(self.roles)
        ]
        return View(
            seat=seat, role=role, team=team_of(role), alive=self.alive[seat], day=self.day, phase=self.phase,
            players=players, known_roles=known,
            seer_checks=list(self.checks) if role == Role.SEER else [],
            witch=WitchInfo(
                antidote=self.antidote, poison=self.poison,
                tonight_victim=self.tonight_victim if (self.antidote and self.phase == Phase.NIGHT_WITCH) else None,
                saved=list(self.saved), poisoned=list(self.poisoned),
            ) if role == Role.WITCH else None,
            wolf=WolfInfo(teammates=[s for s in self.wolves() if s != seat], kills=list(self.kills))
            if role == Role.WEREWOLF else None,
            hunter_can_shoot=True if role == Role.HUNTER else None,
            events=[strip_text(e) for e in self.events if self.visible(e, seat)],
        )

    def public_view(self) -> PublicView:
        return self.view(next(iter(sorted(self.roles)))).public()

    def _rid(self) -> str:
        self._n_req += 1
        return f'r{self._n_req}'

    def request_vote(self, seat: Seat, candidates: list[Seat] | None = None, round: str = 'main') -> Request:
        cands = candidates or [s for s in self.living() if s != seat]
        opts = [Option(id=f'vote:{s}', target=s, label=f'投 {s} 号') for s in cands]
        opts.append(Option(id='vote:abstain', label='弃票'))
        return Request(id=self._rid(), kind=RequestKind.VOTE, seat=seat, day=self.day,
                       phase=Phase.DAY_VOTE if round == 'main' else Phase.PK_VOTE, options=opts,
                       context={'round': round, 'candidates': cands})

    def request_speak(self, seat: Seat, kind: SpeechKind = 'day', index: int = 0, total: int | None = None) -> Request:
        phase = {'day': Phase.DAY_SPEECH, 'pk': Phase.PK_SPEECH, 'last_words': Phase.LAST_WORDS}[kind]
        died = self.died.get(seat, (0, None))[1]
        return Request(id=self._rid(), kind=RequestKind.SPEAK, seat=seat, day=self.day, phase=phase,
                       speech_kind=kind,
                       context={'index': index, 'total': total or len(self.living()),
                                'after': ('night' if died == 'night' else 'vote') if kind == 'last_words' else None,
                                'rivals': [s for s in self._tied if s != seat] if kind == 'pk' else []})

    def request_kill(self, seat: Seat, kind: RequestKind = RequestKind.WOLF_PROPOSE) -> Request:
        opts = [Option(id=f'kill:{s}', target=s, label=f'刀 {s} 号') for s in self.living()]
        opts.append(Option(id='kill:none', label='空刀'))
        ctx: dict[str, Any] = {'first_night': self.day <= 1}
        if kind == RequestKind.WOLF_KILL:
            ctx['proposals'] = []
        return Request(id=self._rid(), kind=kind, seat=seat, day=self.day, phase=Phase.NIGHT_WOLF, options=opts,
                       context=ctx)

    def request_check(self, seat: Seat) -> Request:
        done = {c.target for c in self.checks}
        opts = [Option(id=f'check:{s}', target=s, label=f'查验 {s} 号') for s in self.living()
                if s != seat and s not in done]
        return Request(id=self._rid(), kind=RequestKind.SEER_CHECK, seat=seat, day=self.day, phase=Phase.NIGHT_SEER,
                       options=opts)

    def request_witch(self, seat: Seat, victim: Seat | None) -> Request:
        self.phase = Phase.NIGHT_WITCH
        self.tonight_victim = victim
        opts: list[Option] = []
        if self.antidote and victim is not None and (victim != seat or self.day == 1):
            opts.append(Option(id='save', target=victim, label=f'用解药救 {victim} 号'))
        if self.poison:
            opts += [Option(id=f'poison:{s}', target=s, label=f'毒 {s} 号') for s in self.living() if s != seat]
        opts.append(Option(id='pass', label='不用药'))
        return Request(id=self._rid(), kind=RequestKind.WITCH_ACT, seat=seat, day=self.day, phase=Phase.NIGHT_WITCH,
                       options=opts,
                       context={'victim': victim if self.antidote else None, 'victim_known': self.antidote,
                                'antidote': self.antidote, 'poison': self.poison})

    def request_shoot(self, seat: Seat) -> Request:
        opts = [Option(id=f'shoot:{s}', target=s, label=f'带走 {s} 号') for s in self.living() if s != seat]
        opts.append(Option(id='shoot:none', label='不开枪'))
        return Request(id=self._rid(), kind=RequestKind.HUNTER_SHOOT, seat=seat, day=self.day, phase=Phase.HUNTER,
                       options=opts, context={'after': 'night' if self.died.get(seat, (0, ''))[1] == 'night' else 'vote'})


def duel_scenario() -> Scenario:
    """常用样例：打到第 2 天白天的中途，轮到 1 号（真人）发言之前。

    身份见 DEFAULT_ROLES：狼 2、5、8；预言家 3；女巫 6；猎人 9；平民 1、4、7；真人坐 1 号。
    - 第 1 夜：狼刀 4 号，女巫没救；预言家验 7 号，是好人。4 号留了遗言。
    - 第 1 天：8 号（狼）和 2 号（狼）踩 7 号划水；3 号跳预言家，报 7 号金水，反踩 8 号和 2 号。
      投票 7 号与 8 号各 4 票平票，PK 后 8 号出局，留遗言反咬 3 号。
    - 第 2 夜：狼刀 6 号（女巫），预言家验 2 号，是狼人。
    - 第 2 天：发言顺序 7、9、1、2、3、5；7 号和 9 号已经说完。
    """
    sc = Scenario()
    sc.night(1, kill=4, check=(3, 7, 'good'))
    sc.dawn([4])
    sc.speech(4, '我是平民，没什么信息。大家好好玩。', [act('claim_role', role=Role.VILLAGER, weight=1)],
              kind='last_words')
    sc.start_speeches([5, 6, 7, 8, 9, 1, 2, 3])
    sc.speech(5, '我是平民，第一天没什么信息，先听听。', [act('claim_role', role=Role.VILLAGER, weight=1)])
    sc.speech(6, '4号夜里倒了，我觉得刀口有点意思，先不多说。', [])
    sc.speech(7, '我没什么可说的，过。', [act('pass')])
    sc.speech(8, '7号这发言也太划水了吧，我觉得7号有问题。', [act('accuse', 7), act('vote_intent', 7, weight=1)],
              tone=Tone.FIRM)
    sc.speech(9, '8号踩7号踩得有点急，我先记一笔。', [act('question', 8, weight=1)])
    sc.speech(1, '我是好人，暂时没有怀疑对象。', [])
    sc.speech(2, '同意8号，7号确实划水。', [act('accuse', 7, weight=1)])
    sc.speech(3, '我是预言家，昨晚验了7号，是好人。8号2号你们踩一个金水，我看你们才有问题。',
              [act('claim_role', role=Role.SEER, weight=3), act('claim_check', 7, result='good', night=1),
               act('vouch', 7), act('accuse', 8), act('accuse', 2, weight=1)], tone=Tone.FIRM)
    sc.vote({1: 8, 2: 7, 3: 8, 5: 7, 6: 8, 7: 8, 8: 7, 9: 7})  # 4 比 4，进 PK
    sc.speech(7, '我是3号的金水，投我的自己掂量。', [act('defend'), act('back_claim', 3)], kind='pk')
    sc.speech(8, '3号的预言家我不认，他保7号保得太刻意。', [act('doubt_claim', 3), act('defend')], kind='pk')
    sc.vote({1: 8, 2: 7, 3: 8, 5: 7, 6: 8, 9: 8}, exiled=8, round='pk')
    sc.speech(8, '我是好人，你们投错了。3号是悍跳的狼。', [act('accuse', 3, weight=3), act('doubt_claim', 3)],
              kind='last_words', tone=Tone.ANGRY)
    sc.night(2, kill=6, check=(3, 2, 'wolf'))
    sc.dawn([6])
    sc.start_speeches([7, 9, 1, 2, 3, 5])
    sc.speech(7, '3号给我发的金水，我信3号。昨天2号5号都投我，我记着。',
              [act('back_claim', 3), act('accuse', 2), act('accuse', 5, weight=1)])
    sc.speech(9, '6号夜里走了，可惜。我跟3号的查验走。', [act('back_claim', 3, weight=1)])
    return sc
