"""生成前端演示文件 src/werewolf/web/demo/*.json。

用 protocol 与 kernel.types 的 pydantic 模型构造每条消息，所以按构造就是合法的；
事件日志用 werewolf.testing.Scenario 手工拼出，再按「真人看得见的事件」换算成 seq。
运行：python scripts/ww_build_demos.py [名字...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from werewolf.kernel.types import (
    EMOTES,
    ROLE_ZH,
    Event,
    EventType,
    Phase,
    Role,
    SpeechAct,
    Tone,
)
from werewolf.mind.types import AvatarSpec
from werewolf.protocol import (
    Accepted,
    ClientOption,
    CostMsg,
    EventMsg,
    GameStarted,
    GodView,
    Hello,
    LlmStatus,
    Narration,
    Parsed,
    PlayerCard,
    PrivateInfo,
    ReactionMsg,
    RequestMsg,
    Review,
    ReviewDay,
    ReviewSeat,
    ReviewStats,
    ReviewThought,
    RulesInfo,
    SeatRole,
    ServerMsg,
    SessionState,
    SuspicionPoint,
    Thinking,
)
from werewolf.testing import DEFAULT_ROLES, Scenario, act

OUT = Path(__file__).resolve().parent.parent / 'src' / 'werewolf' / 'web' / 'demo'
ADAPTER = TypeAdapter(ServerMsg)

# --------------------------------------------------------------------------- 人物

CAST: list[dict[str, Any]] = [
    dict(name='老陈', title='退休语文老师', tags=['慢热', '爱讲理'],
         avatar=AvatarSpec(bg='#3b4a7a', skin='#e7c19c', hair='#9a9a9a', hair_style='short', accessory='glasses', outfit='#5a6a58')),
    dict(name='小满', title='外卖骑手', tags=['爽快', '冲动'],
         avatar=AvatarSpec(bg='#7a3b4a', skin='#f2cba3', hair='#c8553d', hair_style='spiky', accessory='headphones', outfit='#2f6f9a')),
    dict(name='白夜', title='夜班护士', tags=['冷静', '多疑'],
         avatar=AvatarSpec(bg='#2c3a5a', skin='#f3d9c4', hair='#1b1b2a', hair_style='long', accessory='mask', outfit='#e8edf2')),
    dict(name='柳青', title='自由插画师', tags=['感性', '爱调侃'],
         avatar=AvatarSpec(bg='#4a6a5a', skin='#f0c8a8', hair='#3a2a55', hair_style='bob', accessory='flower', outfit='#8a5a9a')),
    dict(name='周教练', title='健身教练', tags=['强势', '固执'],
         avatar=AvatarSpec(bg='#6a4a2a', skin='#c99a72', hair='#2a2a2a', hair_style='cap', accessory='scarf', outfit='#c8323c')),
    dict(name='米粒', title='大学生', tags=['胆小', '从众'],
         avatar=AvatarSpec(bg='#5a4a8a', skin='#f6d6b8', hair='#6a3a2a', hair_style='ponytail', accessory='earring', outfit='#4a9a8a')),
    dict(name='老K', title='爵士乐手', tags=['随性', '演技好'],
         avatar=AvatarSpec(bg='#2a2a3a', skin='#8a5a3a', hair='#1a1a1a', hair_style='curly', accessory='hat', outfit='#3a3a55')),
    dict(name='阿福', title='早餐店老板', tags=['憨厚', '话多'],
         avatar=AvatarSpec(bg='#6a5a2a', skin='#e0b088', hair='#4a3a2a', hair_style='bald', accessory='beard', outfit='#d4a24a')),
]

RULES = RulesInfo(
    board='9 人标准局：3 狼 3 民 预言家 女巫 猎人',
    role_counts={'werewolf': 3, 'villager': 3, 'seer': 1, 'witch': 1, 'hunter': 1},
    witch_self_save='first_night', last_words_nights=1, allow_abstain=True, allow_empty_kill=True, max_days=15,
)

ACTION_OF = {'kill': 'kill', 'check': 'check', 'save': 'save', 'poison': 'poison', 'shoot': 'shoot', 'vote': 'vote'}
NARR = {
    'night_fall': '天黑请闭眼。', 'wolves_wake': '狼人请睁眼，商量今夜的目标。', 'wolves_sleep': '狼人请闭眼。',
    'seer_wake': '预言家请睁眼，今晚你要验谁？', 'seer_sleep': '预言家请闭眼。', 'witch_wake': '女巫请睁眼，今夜的情况你已知晓。',
    'witch_sleep': '女巫请闭眼。', 'dawn_peaceful': '天亮了。昨夜是平安夜。', 'skill': '有技能的玩家，请发动技能。',
    'last_words': '请留下遗言。', 'speech_start': '请按顺序发言。', 'vote_start': '发言结束，请投票。', 'vote_result': '票型如下。',
    'pk_start': '平票。请平票的玩家依次 PK 发言。', 'pk_vote': '请对 PK 的玩家再投一次。', 'exile': '放逐结果已定。',
    'no_exile': '无人出局。', 'hunter_shot': '猎人开了枪。', 'game_over': '游戏结束。', 'deal': '身份已发好，请查看你的身份牌。',
}


class W:
    """一局演示的编排：Scenario 记事件，msgs 记浏览器收到的消息（含 hold 与作答）。"""

    def __init__(self, human: int, roles: dict[int, Role] | None = None) -> None:
        self.human = human
        self.sc = Scenario(roles=roles or DEFAULT_ROLES, human=human)
        self.msgs: list[dict[str, Any]] = []
        self.seq = 0
        self.flushed = 0
        self.tok = 0
        self.god = False
        self.raw2seq: dict[int, int] = {}
        self.thoughts: dict[int, list[ReviewThought]] = {s: [] for s in self.sc.roles}
        self.days: dict[int, list[ReviewDay]] = {s: [] for s in self.sc.roles}
        others = [s for s in sorted(self.sc.roles) if s != human]
        self.persona = {s: CAST[i] for i, s in enumerate(others)}
        self.names = {s: (CAST[others.index(s)]['name'] if s != human else '阿泽') for s in self.sc.roles}
        self.sc.names = dict(self.names)
        self.last_seq = 0
        self._vr: tuple[list[int], str, str, list[int] | None] = ([], 'main', '', None)

    # ---- 基础
    def add(self, m: Any, answer: dict[str, Any] | None = None) -> None:
        step: dict[str, Any] = {'msg': m.model_dump(mode='json')}
        if answer:
            step['answer'] = answer
        self.msgs.append(step)

    def narr(self, key: str, text: str | None = None, hold: int = 1100) -> None:
        self.add(Narration(key=key, text=text or NARR[key], hold_ms=hold))

    def visible(self, ev: Event) -> bool:
        return self.god or self.sc.visible(ev, self.human)

    def token(self) -> str:
        self.tok += 1
        return f'q{self.tok:02d}x{self.tok * 7 % 10}'

    # ---- 事件冲刷
    HOLD = {'phase': 1300, 'dawn': 3600, 'death': 500, 'vote': 220, 'vote_result': 5200, 'exile': 1800, 'game_over': 4200,
            'role_dealt': 3600, 'game_start': 700, 'hunter_shot': 2600, 'role_revealed': 1200, 'seer_result': 1500,
            'witch_notice': 900, 'witch_save': 900, 'witch_poison': 900, 'wolf_propose': 1000, 'wolf_kill': 1000, 'wolf_team': 1200}
    PRE = {'night_wolf': ['night_fall'], 'night_seer': ['wolves_sleep', 'seer_wake'], 'night_witch': ['seer_sleep', 'witch_wake'],
           'hunter': ['skill'], 'last_words': ['last_words'], 'day_speech': ['speech_start'], 'day_vote': ['vote_start'],
           'pk_speech': ['pk_start'], 'pk_vote': ['pk_vote']}

    def flush(self, *, hold: dict[str, int] | None = None, narrate: bool = True, upto: int | None = None) -> None:
        sc = self.sc
        end = len(sc.events) if upto is None else upto
        for ev in sc.events[self.flushed:end]:
            if not self.visible(ev):
                continue
            ph = ev.data.get('phase') if ev.type == EventType.PHASE else None
            if narrate and ph in self.PRE:
                for key in self.PRE[ph]:
                    self.narr(key)
            if narrate and ev.type == EventType.VOTE_RESULT:
                self.narr('vote_result', hold=900)
            if narrate and ev.type == EventType.HUNTER_SHOT and ev.target:
                self.narr('hunter_shot', hold=900)
            if narrate and ev.type == EventType.GAME_OVER:
                self.narr('game_over', hold=900)
            if ev.type == EventType.ROLE_DEALT and ev.actor == self.human and narrate:
                self.narr('deal', hold=900)
            self.seq += 1
            self.raw2seq[ev.id] = self.seq
            h = (hold or {}).get(ev.type.value, self.HOLD.get(ev.type.value, 400))
            ai_speech = ev.type == EventType.SPEECH and ev.actor != self.human
            self.add(EventMsg(seq=self.seq, ev=ev.model_copy(update={'id': self.seq}), hold_ms=0 if ai_speech else h,
                              wait_ack=ai_speech))
            self.last_seq = self.seq
            self._private(ev)
            if narrate and ev.type == EventType.DAWN:
                self.narr('dawn_peaceful', hold=900) if ev.data.get('peaceful') else self.narr(
                    'dawn_peaceful', '天亮了。昨夜有人倒下：' + '、'.join(f'{s} 号' for s in ev.data['deaths']) + '。', hold=900)
        self.flushed = end

    def _private(self, ev: Event) -> None:
        if ev.vis == 'public' or self.god:
            return
        d, tg = ev.data, ev.target
        texts = {
            EventType.ROLE_DEALT: lambda: f'你的身份是：{ROLE_ZH[Role(d["role"])]}',
            EventType.WOLF_TEAM: lambda: '你的狼队友：' + '、'.join(f'{s} 号' for s in d['wolves'] if s != self.human),
            EventType.WOLF_PROPOSE: lambda: f'{ev.actor} 号提议刀 {tg} 号：{d.get("note", "")}',
            EventType.WOLF_KILL: lambda: f'今夜的刀口：{tg} 号' if tg else '今夜空刀',
            EventType.SEER_RESULT: lambda: f'{tg} 号是{"狼人" if d["result"] == "wolf" else "好人"}',
            EventType.WITCH_NOTICE: lambda: (f'今夜 {tg} 号被刀了' if tg else '你不知道今夜谁被刀了'),
            EventType.WITCH_SAVE: lambda: f'你用解药救了 {tg} 号',
            EventType.WITCH_POISON: lambda: f'你用毒药毒了 {tg} 号',
            EventType.HUNTER_BLOCKED: lambda: '你被毒死了，无法开枪',
            EventType.HUNTER_SHOT: lambda: '你选择不开枪',
        }
        txt = texts[ev.type]() if ev.type in texts else None
        key = {EventType.ROLE_DEALT: 'role', EventType.WOLF_TEAM: 'wolf_team', EventType.WOLF_PROPOSE: 'wolf_propose',
               EventType.WOLF_KILL: 'wolf_kill', EventType.SEER_RESULT: 'seer_result', EventType.WITCH_NOTICE: 'witch_notice',
               EventType.WITCH_SAVE: 'witch_used', EventType.WITCH_POISON: 'witch_used', EventType.HUNTER_BLOCKED: 'hunter_blocked',
               EventType.HUNTER_SHOT: 'hunter_pass'}.get(ev.type)
        if txt and key:
            self.add(PrivateInfo(key=key, text=txt, seq=self.seq))

    # ---- AI 发言
    def say(self, seat: int, text: str, acts: list[SpeechAct] | None = None, *, kind: str = 'day', tone: Tone = Tone.CALM,
            emote: str | None = None, reactions: list[tuple[int, str, str | None]] | None = None, think: int = 900) -> Event:
        self.add(Thinking(seat=seat, on=True))
        self.add(Thinking(seat=seat, on=False))
        ev = self.sc.speech(seat, text, acts or [], kind=kind, tone=tone)  # type: ignore[arg-type]
        if emote:
            ev.data['speech']['emote'] = emote
        self.flush()
        for rs, emo, quip in reactions or []:
            self.add(ReactionMsg(seat=rs, emote=emo, label=EMOTES[emo], quip=quip, intensity=0.6, seq=self.last_seq))
        return ev

    # ---- 请求
    def request(self, kind: str, options: list[tuple[str, str, int | None]] = (), *, speech_kind: str | None = None,
                context: dict[str, Any] | None = None, answer: dict[str, Any] | None = None) -> str:
        rid = self.token()
        opts = [ClientOption(id=i, label=lab, target=t, action=('none' if (i.endswith(':none') or i.endswith(':abstain') or i == 'pass')
                                                              else ACTION_OF[i.split(':')[0]])) for i, lab, t in options]
        self.add(RequestMsg(id=rid, kind=kind, options=opts, speech_kind=speech_kind, context=context or {}), answer)  # type: ignore[arg-type]
        return rid

    def accepted(self, rid: str, option_id: str | None = None) -> None:
        self.add(Accepted(request_id=rid, option_id=option_id))

    def my_speech(self, text: str, acts: list[SpeechAct], *, kind: str = 'day', index: int = 0, total: int = 8, confirm: bool = True,
                  ignored: list[str] | None = None, notice: str | None = None, rivals: list[int] | None = None,
                  after: str | None = None, flags: list[str] | None = None) -> Event:
        ctx = {'index': index, 'total': total, 'after': after, 'rivals': rivals or [], 'max_chars': 120, 'confirm': confirm}
        rid = self.request('speak', speech_kind=kind, context=ctx, answer={'text': text})
        if confirm:
            self.add(Parsed(preview=True, request_id=rid, acts=acts, flags=flags or [], ignored=ignored or [], notice=notice))
        self.accepted(rid)
        clean = text
        for frag in ignored or []:
            clean = clean.replace(frag, '')
        ev = self.sc.speech(self.human, clean, acts, kind=kind)  # type: ignore[arg-type]
        self.flush()
        self.add(Parsed(preview=False, seq=self.last_seq, acts=acts, flags=flags or [], ignored=ignored or [], notice=notice))
        return ev

    def vote_request(self, living: list[int], *, round: str = 'main', answer: str, candidates: list[int] | None = None) -> None:
        self._vr = (living, round, answer, candidates)

    def run_vote(self, votes: dict[int, int | None], *, exiled: int | None = None, round: str = 'main') -> None:
        """先发 phase（投票阶段），再向真人要票，答完后公开全部票与结果。"""
        n0 = len(self.sc.events)
        self.sc.vote(votes, exiled=exiled, round=round)
        self.flush(upto=n0 + 1)
        living, rnd, answer, candidates = self._vr
        cands = candidates or [s for s in living if s != self.human]
        opts = [(f'vote:{s}', f'投 {s} 号', s) for s in cands] + [('vote:abstain', '弃票', None)]
        rid = self.request('vote', opts, context={'round': rnd, 'candidates': cands}, answer={'option_id': answer})
        self.accepted(rid, answer)
        self.flush()

    def cost(self, usd: float) -> None:
        self.add(CostMsg(spent_usd=usd))

    # ---- 开场
    def prelude(self, speed: str = 'normal') -> None:
        self.add(Hello(llm=LlmStatus(jev=True, claude=True, speech='claude'), speed=speed, resumable=False))  # type: ignore[arg-type]
        players = [PlayerCard(seat=s, name=self.names[s], is_human=(s == self.human),
                              title=self.persona[s]['title'] if s != self.human else '',
                              tags=self.persona[s]['tags'] if s != self.human else [],
                              avatar=self.persona[s]['avatar'] if s != self.human else None) for s in sorted(self.sc.roles)]
        self.add(GameStarted(game_id='demo', you=self.human, your_role=self.sc.roles[self.human], players=players, rules=RULES))
        self.add(SessionState(speed=speed))  # type: ignore[arg-type]
        self.flush()

    # ---- 复盘
    def think(self, seat: int, day: int, phase: Phase, kind: str, text: str, ev: Event | None = None, *, choice: str | None = None,
              best: str | None = None, lies: list[int] | None = None, state: str | None = None, source: str = 'jev') -> None:
        self.thoughts[seat].append(ReviewThought(day=day, phase=phase, kind=kind, text=text, event_id=ev.id if ev else None,  # type: ignore[arg-type]
                                                 choice=choice, best=best, lies=lies or [], state=state, source=source))

    def review(self, winner: str | None, reason: str, strategies: dict[int, str], sus: dict[int, dict[int, list[tuple[int, float]]]],
               moods: dict[int, list[tuple[float, str]]], days: int, spent: float = 0.42) -> Review:
        seats = []
        for s in sorted(self.sc.roles):
            ds = []
            for i, (m, st) in enumerate(moods.get(s, []), start=1):
                ds.append(ReviewDay(day=i, mood=m, state=st, suspicion=[SuspicionPoint(seat=a, p=p) for a, p in sus.get(s, {}).get(i, [])]))  # type: ignore[arg-type]
            seats.append(ReviewSeat(seat=s, name=self.names[s], role=self.sc.roles[s], is_human=(s == self.human), alive=self.sc.alive[s],
                                    strategy=strategies.get(s, ''), thoughts=self.thoughts[s], days=ds))
        return Review(winner=winner, reason=reason, seats=seats, events=list(self.sc.events),  # type: ignore[arg-type]
                      stats=ReviewStats(seed=20240601, days=days, calls=96, jev_calls=58, claude_calls=38, spent_usd=spent,
                                        speech_sources={'claude': 31, 'jev_select': 4, 'template': 3}))

    def finish(self, game_result_winner: str | None, reason: str) -> None:
        sc = self.sc
        sc.set_phase(Phase.GAME_OVER)
        sc.add(EventType.GAME_OVER, data={'winner': game_result_winner, 'reason': reason,
                                          'roles': [{'seat': s, 'role': r.value} for s, r in sorted(sc.roles.items())]})


def N(*seats: int) -> list[int]:
    return list(seats)


# --------------------------------------------------------------------------- 场景：平民（完整的一局）


def build_villager(w: W) -> None:
    """人类 4 号平民。第 1 夜刀 7 号；白天 3 号跳预言家，平票 PK，8 号出局；第 2 夜刀猎人 9 号，他带走 2 号；
    第 2 天放逐 5 号，好人胜。"""
    sc = w.sc
    w.prelude()
    # ---- 第 1 夜（真人无行动）
    sc.night(1, kill=7, check=(3, 8, 'wolf'))
    sc.dawn([7])
    w.flush()
    w.cost(0.03)
    sc.set_phase(Phase.LAST_WORDS, order=[7], after='night')
    e7 = w.say(7, '我就是个平民，被刀也算帮大家排了个坑。别急着乱投，多看看谁的发言躲躲闪闪。', [act('claim_role', role=Role.VILLAGER, weight=1)],
               kind='last_words', tone=Tone.NERVOUS, emote='sigh', reactions=[(1, 'sigh', '可惜了'), (9, 'frown', None)])
    # ---- 第 1 天
    sc.start_speeches([8, 9, 1, 2, 3, 4, 5, 6])
    w.flush()
    e8 = w.say(8, '第一晚就倒了7号，刀口没什么规律可言。我是平民，没有信息，先听大家说。', [act('claim_role', role=Role.VILLAGER, weight=1)],
               emote='think', reactions=[(2, 'nod', None)])
    e9 = w.say(9, '7号的遗言我听着像真平民。我也没什么信息，先过，听后面的人说。', [act('pass')], tone=Tone.HESITANT,
               reactions=[(5, 'shrug', '又一个划水的')])
    w.say(1, '我也是平民。提个建议：今天谁也别划水，把自己的想法说清楚，哪怕说错也比不说强。', [act('claim_role', role=Role.VILLAGER, weight=1)],
               tone=Tone.EARNEST, reactions=[(5, 'nod', None), (6, 'nod', None)])
    e2 = w.say(2, '划水的嫌疑最大。9号刚才一句有用的话都没有，我先怀疑9号。', [act('accuse', 9, weight=2)], tone=Tone.FIRM,
               emote='stare', reactions=[(9, 'frown', '这话我不爱听'), (1, 'think', None)])
    e3 = w.say(3, '我是预言家。昨晚验了8号，是狼人。8号刚才说自己是平民，请你解释一下。',
               [act('claim_role', role=Role.SEER, weight=3), act('claim_check', 8, result='wolf', night=1, weight=3),
                act('accuse', 8, weight=3), act('question', 8, weight=1)], tone=Tone.FIRM, emote='stare',
               reactions=[(8, 'shock', None), (5, 'sweat', None), (1, 'shock', '这么快就跳了')])
    w.cost(0.11)
    # 真人发言
    w.my_speech('（拍了拍桌子）我是平民。3号报8号是狼人，我站3号，今天出8号。',
                      [act('claim_role', role=Role.VILLAGER, weight=1), act('back_claim', 3), act('vote_intent', 8)],
                      index=5, total=8, ignored=['（拍了拍桌子）'], notice='括号里的动作不算数')
    e5 = w.say(5, '我是平民。3号这个预言家来得挺突然，不过可以先验验看。8号，你怎么说？',
               [act('claim_role', role=Role.VILLAGER, weight=1), act('doubt_claim', 3, weight=1), act('question', 8, weight=1)],
               tone=Tone.CALM, reactions=[(3, 'stare', None)])
    e6 = w.say(6, '我不多说。3号的发言比较完整，我倾向相信他。8号，好好回应一下。', [act('back_claim', 3), act('question', 8, weight=1)],
               tone=Tone.COLD, emote='nod', reactions=[(8, 'sweat', '有点慌')])
    # ---- 投票：4 : 4 平票，进入 PK
    w.vote_request([1, 2, 3, 4, 5, 6, 8, 9], answer='vote:8')
    w.run_vote({1: 8, 2: 3, 3: 8, 4: 8, 5: 3, 6: 8, 8: 3, 9: 3})
    w.flush()
    sc.set_phase(Phase.PK_SPEECH, order=[3, 8], candidates=[3, 8])
    w.flush()
    w.say(3, '我是真预言家，昨晚验的8号是狼人。投我的人，请你们想清楚：9号、5号、2号为什么要保一个被查杀的人。', [act('defend'), act('accuse', 8, weight=3)],
                 kind='pk', tone=Tone.EARNEST, reactions=[(9, 'sweat', None)])
    w.say(8, '我不是狼。3号是悍跳的狼，他要借你们的手把神职投出去。给我一轮，我会证明自己。', [act('defend'), act('doubt_claim', 3, weight=2)],
                 kind='pk', tone=Tone.ANGRY, emote='angry', reactions=[(1, 'frown', None), (6, 'sneer', None)])
    w.vote_request([1, 2, 4, 5, 6, 9], round='pk', answer='vote:8', candidates=[3, 8])
    w.run_vote({1: 8, 2: 3, 4: 8, 5: 3, 6: 8, 9: 8}, exiled=8, round='pk')
    w.flush()
    sc.set_phase(Phase.LAST_WORDS, order=[8], after='vote')
    w.say(8, '我真是平民，你们投错了。记住，3号是悍跳的狼，今晚他还会继续刀人。', [act('claim_role', role=Role.VILLAGER, weight=2), act('accuse', 3, weight=3)],
                 kind='last_words', tone=Tone.ANGRY, reactions=[(3, 'sneer', None)])
    # ---- 第 2 夜：刀猎人，猎人带走 2 号
    sc.night(2, kill=9, check=(3, 2, 'wolf'))
    sc.dawn([9])
    w.flush()
    w.cost(0.19)
    sc.shot(9, 2)
    w.flush()
    # ---- 第 2 天
    sc.start_speeches([1, 3, 4, 5, 6])
    w.flush()
    w.say(1, '9号是猎人，开枪带走了2号，2号一定是狼。现在只剩5号一匹狼了，大家别犹豫。', [act('accuse', 5, weight=2), act('vote_intent', 5, weight=2)],
                tone=Tone.FIRM, reactions=[(6, 'nod', None)])
    w.say(3, '我昨晚验了2号，是狼人，和猎人的枪口对上了。今天出5号，他站过8号的边。', [act('claim_check', 2, result='wolf', night=2, weight=3), act('vote_intent', 5, weight=3)],
                tone=Tone.FIRM, emote='nod', reactions=[(5, 'sweat', None)])
    rid = w.request('speak', speech_kind='day', context={'index': 2, 'total': 5, 'after': None, 'rivals': [], 'max_chars': 120, 'confirm': True}, answer={'text': '过'})
    w.accepted(rid)
    sc.speech(4, '过。', [act('pass')])
    w.flush()
    e5b = w.say(5, '我是平民，被你们逼到这一步，我认了。但3号你也别太得意，8号出局的时候你们真的验证过吗？', [act('defend'), act('doubt_claim', 3, weight=2)],
                tone=Tone.ANGRY, emote='angry', reactions=[(1, 'sneer', None), (6, 'sneer', None)])
    w.say(6, '我是女巫。昨晚没有用药，所以刀口只有9号一个。5号，你没得选了。', [act('claim_role', role=Role.WITCH, weight=3)], tone=Tone.FIRM,
                reactions=[(5, 'shock', None)])
    w.vote_request([1, 3, 4, 5, 6], answer='vote:5')
    w.run_vote({1: 5, 3: 5, 4: 5, 5: 3, 6: 5}, exiled=5)
    w.flush()
    sc.set_phase(Phase.LAST_WORDS, order=[5], after='vote')
    w.say(5, '好吧，我是狼。这一局打得不亏，你们赢了。', [act('claim_role', role=Role.WEREWOLF, weight=3)], kind='last_words', tone=Tone.CALM,
                 emote='shrug', reactions=[(3, 'nod', None)])
    w.finish('good', 'wolves_dead')
    w.flush()
    w.cost(0.42)

    # ---- 复盘：想法挂在它引出的事件上
    def ev_of(type_: EventType, **kw: Any) -> Event:
        return next(e for e in sc.events if e.type == type_ and all(getattr(e, k) == v for k, v in kw.items()))
    P = Phase
    w.think(2, 1, P.NIGHT_WOLF, 'decision', '7号白天话少、像在藏牌，先刀掉最没有风险的。队友提议跟我一致。', ev_of(EventType.WOLF_KILL, day=1), choice='刀 7 号', state='steady', source='rules')
    w.think(3, 1, P.NIGHT_SEER, 'decision', '8号夜里没有任何动静，却急着想让我相信他。先验他。', ev_of(EventType.SEER_RESULT, day=1), choice='查验 8 号', state='confident')
    w.think(6, 1, P.NIGHT_WITCH, 'decision', '刀口是7号，一个陌生的面孔。首夜通常该救，但我想把解药留给更关键的人。', ev_of(EventType.WITCH_NOTICE, day=1), choice='不用药', best='用解药救 7 号', state='steady')
    w.think(7, 1, P.LAST_WORDS, 'speech', '我真的是平民，遗言只想提醒大家别乱投。心里有点害怕。', e7, state='tense', source='claude')
    w.think(8, 1, P.DAY_SPEECH, 'speech', '首位发言，先把平民牌亮出来，不留话柄。', e8, lies=[0], state='steady', source='claude')
    w.think(9, 1, P.DAY_SPEECH, 'speech', '我是猎人，现在不能暴露。先过，听听看谁在带节奏。', e9, state='steady', source='template')
    w.think(2, 1, P.DAY_SPEECH, 'speech', '9号一直不说话，踩他是最安全的，队友8号也能被我引开视线。', e2, lies=[0], state='confident')
    w.think(3, 1, P.DAY_SPEECH, 'speech', '昨晚验到狼，必须第一时间跳出来，否则8号会抢占话语权。', e3, state='confident', source='claude')
    w.think(5, 1, P.DAY_SPEECH, 'speech', '队友8号被查杀了。不能直接保，只能先质疑预言家的出现时机。', e5, lies=[0, 1], state='tense', source='claude')
    w.think(6, 1, P.DAY_SPEECH, 'speech', '3号的逻辑完整，我相信他是真预言家。解药还在手上，先不暴露。', e6, state='confident', source='jev_select')
    for voter, tgt, why, st in [(1, 8, '3号报了查杀，我信3号。', 'steady'), (2, 3, '预言家是真的话，要把他投出去；先投3号制造平票。', 'tense'), (5, 3, '全力保队友，投预言家。', 'tense'),
                                (6, 8, '女巫位不能乱，跟着预言家走。', 'confident'), (9, 3, '3号太急，我更怀疑他。', 'steady')]:
        vote_ev = next(e for e in sc.events if e.type == EventType.VOTE and e.actor == voter and e.day == 1 and e.data['round'] == 'main')
        w.think(voter, 1, P.DAY_VOTE, 'decision', why, vote_ev, choice=f'投 {tgt} 号', state=st, best=('投 8 号' if (voter == 9) else None))
    w.think(9, 2, P.NIGHT_WOLF, 'decision', '被刀了。我是猎人，没有被毒，可以开枪。2号第一天踩我，一直在带节奏，带走他。', ev_of(EventType.HUNTER_SHOT), choice='带走 2 号', state='tilted', source='rules')
    w.think(5, 2, P.DAY_SPEECH, 'speech', '只剩我一个了，只能赌女巫手里的药。', e5b, lies=[0], state='tilted', source='claude')
    w.think(4, 1, P.DAY_SPEECH, 'monologue', '（真人座位无内心想法）', None, source='fallback')
    w.thoughts[4] = []
    w.think(3, 1, P.NIGHT_WOLF, 'reflection', '白天结束：8号可疑度升到 92%，9号的投票让我有些在意。', None, state='confident', source='jev')
    strategies = {2: '深水转倒钩：先保队友，必要时踩神。', 5: '冲锋：力挺悍跳队友，攻击真预言家。', 8: '装平民，被查杀后强硬辩解。', 3: '首日起跳报查验。', 6: '留药，第二天跳身份定音。', 9: '隐藏身份，被刀则开枪带走头号怀疑。'}
    sus = {3: {1: [(8, 0.92), (2, 0.55), (5, 0.52), (9, 0.4)], 2: [(5, 0.88), (2, 0.99)]},
           1: {1: [(8, 0.7), (2, 0.5), (9, 0.3)], 2: [(5, 0.8), (2, 0.9)]},
           6: {1: [(8, 0.85), (3, 0.2), (2, 0.5)], 2: [(5, 0.9)]},
           9: {1: [(2, 0.7), (3, 0.5), (8, 0.45)]},
           5: {1: [(3, 0.9), (6, 0.4), (1, 0.3)], 2: [(3, 0.7), (6, 0.5)]}}
    moods = {3: [(78, 'confident'), (82, 'confident')], 1: [(55, 'steady'), (66, 'confident')], 6: [(68, 'confident'), (70, 'confident')], 9: [(50, 'steady'), (22, 'tilted')],
             5: [(40, 'tense'), (18, 'tilted')], 2: [(70, 'confident')], 8: [(45, 'tense')], 7: [(35, 'tense')]}
    w.add(w.review('good', 'wolves_dead', strategies, sus, moods, 2))


# --------------------------------------------------------------------------- 场景：狼人（头狼首夜 + 一个白天）


def build_wolf(w: W) -> None:
    sc = w.sc
    w.prelude()
    sc.day = 1
    sc.set_phase(Phase.NIGHT_WOLF)
    w.flush()
    sc.add(EventType.WOLF_PROPOSE, actor=2, target=3, data={'option_id': 'kill:3', 'note': '3号白天话最少，像神，先刀', 'stance': 'jump_seer'}, vis='wolves')
    sc.add(EventType.WOLF_PROPOSE, actor=8, target=6, data={'option_id': 'kill:6', 'note': '6号一看就是女巫，先下手', 'stance': 'charge'}, vis='wolves')
    w.flush()
    living = [s for s in sorted(sc.roles) if sc.alive[s]]
    opts = [(f'kill:{s}', f'刀 {s} 号', s) for s in living] + [('kill:none', '空刀', None)]
    props = [{'seat': 2, 'option_id': 'kill:3', 'target': 3, 'note': '3号白天话最少，像神，先刀', 'stance': 'jump_seer'},
             {'seat': 8, 'option_id': 'kill:6', 'target': 6, 'note': '6号一看就是女巫，先下手', 'stance': 'charge'}]
    rid = w.request('wolf_kill', opts, context={'proposals': props, 'first_night': True}, answer={'option_id': 'kill:3', 'stance': 'deep'})
    w.accepted(rid, 'kill:3')
    sc.add(EventType.WOLF_KILL, target=3, data={'by': 'leader', 'tally': {'kill:3': 1}, 'stance': 'deep'}, vis='wolves')
    sc.kills.append((1, 3))
    w.flush()
    sc.set_phase(Phase.NIGHT_SEER)
    sc.add(EventType.SEER_RESULT, actor=3, target=2, data={'result': 'wolf', 'night': 1}, vis='seats', vis_seats=[3])
    sc.set_phase(Phase.NIGHT_WITCH)
    sc.add(EventType.WITCH_NOTICE, target=3, data={'antidote': True, 'poison': True, 'victim_known': True}, vis='seats', vis_seats=[6])
    w.flush()
    sc.dawn([3])
    w.flush()
    w.cost(0.04)
    sc.set_phase(Phase.LAST_WORDS, order=[3], after='night')
    w.say(3, '我是预言家。昨晚验了2号，是狼人。我走了，你们看着办。',
          [act('claim_role', role=Role.SEER, weight=3), act('claim_check', 2, result='wolf', night=1, weight=3), act('accuse', 2, weight=3)],
          kind='last_words', tone=Tone.FIRM, emote='stare', reactions=[(2, 'shock', None), (9, 'shock', None)])
    sc.start_speeches([4, 5, 6, 7, 8, 9, 1, 2])
    w.flush()
    w.say(4, '预言家被刀，还留了一个查杀。2号，你该给大家一个说法。', [act('question', 2, weight=2), act('back_claim', 3, weight=1)], tone=Tone.CALM,
          reactions=[(2, 'sweat', '压力有点大')])
    w.my_speech('3号临死前乱咬人，我不信。2号我觉得是好人，他刚才很稳。我是平民，今天不投2号。',
                [act('claim_role', role=Role.VILLAGER, weight=1), act('doubt_claim', 3, weight=2), act('vouch', 2, weight=2)],
                index=1, total=8, notice='「不投2号」不算表态投票')
    w.say(6, '3号遗言报了查杀，但他已经死了，死人的话我们没法验证。不过我更想听听2号怎么说。', [act('question', 2, weight=1)], tone=Tone.CALM, reactions=[(8, 'nod', None)])
    w.say(7, '我听3号的，2号可疑。我今天投2号。', [act('back_claim', 3), act('accuse', 2, weight=2), act('vote_intent', 2)], tone=Tone.FIRM, reactions=[(2, 'angry', None)])
    w.say(8, '2号我看不出问题。我是平民，先不站边，听完再说。', [act('claim_role', role=Role.VILLAGER, weight=1)], tone=Tone.HESITANT, emote='think')
    w.say(9, '查杀都摆在这儿了，还要听什么？投2号。', [act('vote_intent', 2, weight=2)], tone=Tone.FIRM, reactions=[(5, 'frown', None)])
    w.say(1, '我也投2号，先出一个看看。', [act('vote_intent', 2, weight=2)], tone=Tone.CALM)
    w.say(2, '我不是狼！3号是死前乱咬，你们别被带了节奏。如果我被投出去，狼人就赢了一半。', [act('defend'), act('doubt_claim', 3, weight=2)], tone=Tone.ANGRY, emote='angry',
          reactions=[(4, 'sneer', None), (6, 'stare', None)])
    w.vote_request([1, 2, 4, 5, 6, 7, 8, 9], answer='vote:1')
    w.run_vote({1: 2, 2: 1, 4: 2, 5: 1, 6: 2, 7: 2, 8: 1, 9: 2}, exiled=2)
    w.flush()
    sc.set_phase(Phase.LAST_WORDS, order=[2], after='vote')
    w.say(2, '我是狼人，你们赢了一半。接下来，就看你们的了。', [act('claim_role', role=Role.WEREWOLF, weight=3)], kind='last_words', tone=Tone.COLD, emote='shrug')


# --------------------------------------------------------------------------- 场景：预言家


def build_seer(w: W) -> None:
    sc = w.sc
    w.prelude()
    sc.day = 1
    sc.set_phase(Phase.NIGHT_WOLF)
    sc.add(EventType.WOLF_KILL, target=4, data={'by': 'plurality', 'tally': {'kill:4': 3}}, vis='wolves')
    w.flush()
    sc.set_phase(Phase.NIGHT_SEER)
    w.flush()
    opts = [(f'check:{s}', f'查验 {s} 号', s) for s in sorted(sc.roles) if s != 3]
    rid = w.request('seer_check', opts, answer={'option_id': 'check:8'})
    w.accepted(rid, 'check:8')
    sc.add(EventType.SEER_RESULT, actor=3, target=8, data={'result': 'wolf', 'night': 1}, vis='seats', vis_seats=[3])
    w.flush()
    sc.set_phase(Phase.NIGHT_WITCH)
    sc.add(EventType.WITCH_NOTICE, target=4, data={'antidote': True, 'poison': True, 'victim_known': True}, vis='seats', vis_seats=[6])
    sc.add(EventType.WITCH_SAVE, actor=6, target=4, vis='seats', vis_seats=[6])
    w.flush()
    w.narr('seer_sleep', hold=500)
    sc.dawn([])
    w.flush()
    w.cost(0.03)
    sc.start_speeches([6, 7, 8, 9, 1, 2, 3, 4, 5])
    w.flush()
    w.say(6, '平安夜，说明女巫用了药，或者狼人空刀。我是好人，没有信息，大家正常发言。', [act('claim_role', role=Role.VILLAGER, weight=1)], emote='think', reactions=[(1, 'nod', None)])
    w.say(7, '平安夜是好事。我是平民，先听大家的。', [act('claim_role', role=Role.VILLAGER, weight=1)], tone=Tone.NERVOUS)
    w.say(8, '平安夜我看没什么好说的。预言家有的话请跳出来，我们好盘逻辑。', [act('question', 3, weight=1)], tone=Tone.CALM, emote='stare', reactions=[(3, 'frown', None)])
    w.my_speech('我是预言家，昨晚验了8号，是狼人。8号你解释一下，你刚才还在催预言家跳出来。',
                [act('claim_role', role=Role.SEER, weight=3), act('claim_check', 8, result='wolf', night=1, weight=3), act('question', 8, weight=1)],
                index=3, total=9)
    w.say(9, '3号报8号查杀，我认。8号你怎么说？', [act('back_claim', 3), act('question', 8, weight=2)], tone=Tone.FIRM, reactions=[(8, 'sweat', None)])
    w.say(1, '预言家出来了，8号刚才催跳的样子，我现在回头看有点刻意。', [act('accuse', 8, weight=2)], tone=Tone.CALM, reactions=[(6, 'nod', None)])
    w.say(2, '我不信3号。凭什么他说8号是狼就是狼？', [act('doubt_claim', 3, weight=2)], tone=Tone.ANGRY, emote='angry', reactions=[(1, 'sneer', None)])
    w.say(4, '昨晚好像有人救了我？反正我信3号。', [act('back_claim', 3, weight=2), act('vote_intent', 8, weight=2)], tone=Tone.JOKING, reactions=[(3, 'laugh', None)])
    w.say(5, '预言家的话我也认。今天出8号。', [act('vote_intent', 8, weight=2)], tone=Tone.CALM)
    w.vote_request([1, 2, 3, 4, 5, 6, 7, 8, 9], answer='vote:8')
    w.run_vote({1: 8, 2: 3, 3: 8, 4: 8, 5: 8, 6: 8, 7: 8, 8: 3, 9: 8}, exiled=8)
    w.flush()
    sc.set_phase(Phase.LAST_WORDS, order=[8], after='vote')
    w.say(8, '你们会后悔的。', [act('defend')], kind='last_words', tone=Tone.ANGRY, emote='sneer')


# --------------------------------------------------------------------------- 场景：女巫


def build_witch(w: W) -> None:
    sc = w.sc
    w.prelude()
    sc.day = 1
    sc.set_phase(Phase.NIGHT_WOLF)
    sc.add(EventType.WOLF_KILL, target=4, data={'by': 'plurality', 'tally': {'kill:4': 2}}, vis='wolves')
    sc.set_phase(Phase.NIGHT_SEER)
    sc.add(EventType.SEER_RESULT, actor=3, target=8, data={'result': 'wolf', 'night': 1}, vis='seats', vis_seats=[3])
    sc.set_phase(Phase.NIGHT_WITCH)
    sc.add(EventType.WITCH_NOTICE, target=4, data={'antidote': True, 'poison': True, 'victim_known': True}, vis='seats', vis_seats=[6])
    w.flush()
    opts = [('save', '用解药救 4 号', 4)] + [(f'poison:{s}', f'毒 {s} 号', s) for s in sorted(sc.roles) if s != 6] + [('pass', '不用药', None)]
    rid = w.request('witch_act', opts, context={'victim': 4, 'victim_known': True, 'antidote': True, 'poison': True}, answer={'option_id': 'save'})
    w.accepted(rid, 'save')
    sc.add(EventType.WITCH_SAVE, actor=6, target=4, vis='seats', vis_seats=[6])
    sc.antidote = False
    w.flush()
    sc.dawn([])
    w.flush()
    w.cost(0.03)
    sc.start_speeches([7, 8, 9, 1, 2, 3, 4, 5, 6])
    w.flush()
    w.say(7, '平安夜。我是平民，没有信息。', [act('claim_role', role=Role.VILLAGER, weight=1)], tone=Tone.NERVOUS)
    w.say(8, '平安夜可能是女巫救人，也可能是狼人手滑。大家别太紧张。', [act('pass')], tone=Tone.CALM, emote='shrug')
    w.say(9, '我也没什么好说的，过。', [act('pass')], tone=Tone.HESITANT)
    w.say(1, '平安夜对好人是好事。先听预言家发言吧。', [act('question', 3, weight=1)], tone=Tone.EARNEST, reactions=[(3, 'think', None)])
    w.say(2, '我是平民。今天先出一个划水的，7号如何？', [act('claim_role', role=Role.VILLAGER, weight=1), act('accuse', 7, weight=1)], tone=Tone.FIRM, reactions=[(7, 'shock', '我只是不知道说啥')])
    w.say(3, '我是预言家。昨晚验了8号，是狼人。', [act('claim_role', role=Role.SEER, weight=3), act('claim_check', 8, result='wolf', night=1, weight=3), act('accuse', 8, weight=3)],
          tone=Tone.FIRM, reactions=[(8, 'angry', None), (1, 'shock', None)])
    w.say(4, '昨晚好像有人救了我，谢谢你。我站3号。', [act('back_claim', 3, weight=2)], tone=Tone.JOKING, reactions=[(3, 'laugh', None)])
    w.say(5, '3号来得太巧了，我先不站。', [act('doubt_claim', 3, weight=1)], tone=Tone.COLD)
    w.my_speech('我先听听大家的，过。', [act('pass')], index=9, total=9, confirm=False)
    w.vote_request([1, 2, 3, 4, 5, 6, 7, 8, 9], answer='vote:8')
    w.run_vote({1: 8, 2: 3, 3: 8, 4: 8, 5: 3, 6: 8, 7: 8, 8: 3, 9: 8}, exiled=8)
    w.flush()
    sc.set_phase(Phase.LAST_WORDS, order=[8], after='vote')
    w.say(8, '3号才是狼，你们赢不了。', [act('accuse', 3, weight=3)], kind='last_words', tone=Tone.ANGRY, emote='angry')
    # ---- 第 2 夜：解药已用；毒 2 号
    sc.day = 2
    sc.set_phase(Phase.NIGHT_WOLF)
    sc.add(EventType.WOLF_KILL, target=9, data={'by': 'plurality', 'tally': {'kill:9': 2}}, vis='wolves')
    sc.set_phase(Phase.NIGHT_SEER)
    sc.set_phase(Phase.NIGHT_WITCH)
    sc.add(EventType.WITCH_NOTICE, target=None, data={'antidote': False, 'poison': True, 'victim_known': False}, vis='seats', vis_seats=[6])
    w.flush()
    living = [s for s in sorted(sc.roles) if sc.alive[s] and s != 6]
    opts = [(f'poison:{s}', f'毒 {s} 号', s) for s in living] + [('pass', '不用药', None)]
    rid = w.request('witch_act', opts, context={'victim': None, 'victim_known': False, 'antidote': False, 'poison': True}, answer={'option_id': 'poison:2'})
    w.accepted(rid, 'poison:2')
    sc.add(EventType.WITCH_POISON, actor=6, target=2, vis='seats', vis_seats=[6])
    sc.poison = False
    w.flush()
    sc.dawn([2, 9], {2: 'poison', 9: 'wolf'})
    w.flush()
    w.cost(0.15)
    sc.shot(9, 5)
    w.flush()


# --------------------------------------------------------------------------- 场景：猎人（含上帝视角）


def build_hunter(w: W) -> None:
    """猎人演示的实际脚本：9 号被放逐，开枪带走 8 号，随后打开上帝视角。"""
    sc = w.sc
    w.prelude()
    sc.night(1, kill=4, check=(3, 7, 'good'))
    sc.dawn([4])
    w.flush()
    w.cost(0.03)
    sc.set_phase(Phase.LAST_WORDS, order=[4], after='night')
    w.say(4, '我是平民，被刀了。大家加油，别被带节奏。', [act('claim_role', role=Role.VILLAGER, weight=1)], kind='last_words', tone=Tone.EARNEST, emote='sigh',
          reactions=[(5, 'sigh', None)])
    sc.start_speeches([5, 6, 7, 8, 9, 1, 2, 3])
    w.flush()
    w.say(5, '我是平民，没信息。4号被刀，狼人想先刀个平民混淆视听。', [act('claim_role', role=Role.VILLAGER, weight=1)], emote='think')
    w.say(6, '我也不多说，先听预言家。', [act('pass')], tone=Tone.CALM)
    w.say(7, '我是平民，先过。', [act('claim_role', role=Role.VILLAGER, weight=1), act('pass')], tone=Tone.HESITANT)
    w.say(8, '9号你这身份牌拿得挺稳的啊，我先怀疑你。', [act('accuse', 9, weight=2)], tone=Tone.FIRM, emote='stare', reactions=[(9, 'frown', '无端被点名')])
    w.my_speech('我不是狼。8号凭什么怀疑我？我不跳身份，今天先听预言家。', [act('defend'), act('question', 8, weight=1)], index=5, total=8, confirm=False)
    w.say(1, '9号被点了名，反应倒是挺正常的。', [act('vouch', 9, weight=1)], tone=Tone.CALM, reactions=[(9, 'nod', None)])
    w.say(2, '9号确实没价值，我投9号。', [act('accuse', 9, weight=2), act('vote_intent', 9, weight=2)], tone=Tone.FIRM)
    w.say(3, '我是预言家，验了7号是好人。8号点名9号，我反倒觉得8号可疑。',
          [act('claim_role', role=Role.SEER, weight=3), act('claim_check', 7, result='good', night=1, weight=3), act('accuse', 8, weight=2)],
          tone=Tone.FIRM, emote='stare', reactions=[(8, 'angry', None)])
    w.vote_request([1, 2, 3, 5, 6, 7, 8, 9], answer='vote:8')
    w.run_vote({1: 9, 2: 9, 3: 8, 5: 9, 6: 9, 7: 8, 8: 9, 9: 8}, exiled=9)
    w.flush()
    opts = [(f'shoot:{s}', f'带走 {s} 号', s) for s in sorted(sc.roles) if sc.alive[s] and s != 9] + [('shoot:none', '不开枪', None)]
    rid = w.request('hunter_shoot', opts, context={'after': 'vote'}, answer={'option_id': 'shoot:8'})
    w.accepted(rid, 'shoot:8')
    sc.shot(9, 8)
    w.flush()
    sc.set_phase(Phase.LAST_WORDS, order=[9, 8], after='vote')
    w.flush()
    w.my_speech('我是猎人。8号，你一直踩我，带你走。好人们，预言家说的是真的，跟着3号走。',
                [act('claim_role', role=Role.HUNTER, weight=3), act('accuse', 8, weight=3), act('back_claim', 3, weight=2)], kind='last_words', index=0, total=2,
                confirm=False, after='vote')
    w.say(8, '你开枪带走我，我认了。但好人没赢，狼人还没输。', [act('defend')], kind='last_words', tone=Tone.COLD, emote='sneer', reactions=[(3, 'stare', None)])
    # 真人已出局且不会再被问：开放上帝视角
    w.add(SessionState(speed='normal', can_god_view=True))
    sc_roles = [SeatRole(seat=s, role=r) for s, r in sorted(sc.roles.items())]
    hidden = [e for e in sc.events if not sc.visible(e, w.human)]
    w.add(GodView(roles=sc_roles, events=hidden))
    w.add(SessionState(speed='normal', god_view=True, can_god_view=True))
    w.god = True
    sc.night(2, kill=6, check=(3, 2, 'wolf'))
    w.flush()
    sc.dawn([6])
    w.flush()


# --------------------------------------------------------------------------- 重放


def build_resume() -> list[dict[str, Any]]:
    """平民局第 1 天投票之前的消息全部变成重放，最后用 state 带上未答的投票请求，之后接现场（票型公示）。"""
    w = W(4)
    build_villager(w)
    start = next(i for i, s in enumerate(w.msgs) if s['msg']['t'] == 'request' and s['msg']['kind'] == 'vote')
    out: list[dict[str, Any]] = []
    for step in w.msgs[:start]:
        m = step['msg']
        if m['t'] in ('thinking', 'reaction', 'cost', 'accepted', 'error', 'request', 'state'):
            continue
        if m['t'] == 'parsed' and m.get('preview'):
            continue
        if m['t'] == 'hello':
            m['resumable'] = True
        if m['t'] in ('game', 'event', 'narration', 'private', 'parsed'):
            m['replay'] = True
        if m['t'] == 'event':
            m['wait_ack'] = False
        out.append({'msg': m})
    req = RequestMsg.model_validate(w.msgs[start]['msg'])
    out.append({'msg': SessionState(speed='normal', open_request=req).model_dump(mode='json'), 'answer': {'option_id': 'vote:8'}})
    tail = w.msgs[start + 1:]
    stop = next(i for i, s in enumerate(tail) if s['msg']['t'] == 'event' and s['msg']['ev']['type'] == 'vote_result')
    out.extend(tail[: stop + 2])
    return out


# --------------------------------------------------------------------------- 输出


def validate(steps: list[dict[str, Any]], name: str) -> None:
    for i, st in enumerate(steps):
        m = ADAPTER.validate_python(st['msg'])
        ans = st.get('answer')
        if ans and m.t == 'request':
            if m.options and ans.get('option_id'):
                assert ans['option_id'] in {o.id for o in m.options}, f'{name}[{i}]: bad answer {ans}'
        if ans and m.t == 'state' and m.open_request and m.open_request.options and ans.get('option_id'):
            assert ans['option_id'] in {o.id for o in m.open_request.options}, f'{name}[{i}]: bad answer {ans}'


def main(argv: list[str]) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = {
        'villager': lambda: _run(W(4), build_villager),
        'wolf': lambda: _run(W(5), build_wolf),
        'seer': lambda: _run(W(3), build_seer),
        'witch': lambda: _run(W(6), build_witch),
        'hunter': lambda: _run(W(9), build_hunter),
        'resume': build_resume,
    }
    for name, job in jobs.items():
        if argv and name not in argv:
            continue
        steps = job()
        validate(steps, name)
        (OUT / f'{name}.json').write_text(json.dumps({'version': 1, 'steps': steps}, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        print(f'{name}: {len(steps)} steps')
    return 0


def _run(w: W, fn: Any) -> list[dict[str, Any]]:
    fn(w)
    return w.msgs


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
