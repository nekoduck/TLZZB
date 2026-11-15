"""模板叙述与模板选项（方案 4.3 退让表：C3 → 事实句拼成的模板叙述；C4 → 每个动词自带的模板句）。

全部是确定性的：同一个视图、同一个动作，写出来的字一样（重放逐字一致）。
文字只用玩家视图里已经渲染好的事实句与称呼（方案 4.4），不另添事实。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from tianlong.kernel import refs
from tianlong.kernel.defs import Defs
from tianlong.kernel.perception import PlayerView, ViewEvent
from tianlong.kernel.state import WorldState
from tianlong.kernel.timeflow import cn_num
from tianlong.narrator.checks import (
    close_sentence,
    cn_digits,
    split_sentences,
    text_len,
)

OPTION_MAX = 30

# 语气 → 写在说话人后面的状语（方案 7.3 J2b 的七种语气）
STANCE_ADV: dict[str, str] = {
    'warm': '热络地',
    'cold': '冷冷地',
    'angry': '沉着脸',
    'pleading': '恳切地',
    'mocking': '似笑非笑地',
    'evasive': '支支吾吾地',
}
STANCE_LABEL: dict[str, str] = {
    'plain': '平常',
    'warm': '热络',
    'cold': '冷淡',
    'angry': '恼怒',
    'pleading': '恳求',
    'mocking': '讥讽',
    'evasive': '支吾',
}

_CONNECTIVES = ('', '这时，', '', '跟着，', '', '紧接着，')
_QUIET = (
    '四下里一时没有什么动静。',
    '眼前一时没有什么动静。',
    '周遭静了片刻，并没有什么变化。',
)
_MOVE_VERBS = frozenset({'go', 'flee', 'arrive', 'enter'})


def _h(*parts: Any) -> int:
    """确定性的小哈希（不用 hash()：它每个进程不同）。"""
    raw = '|'.join(str(p) for p in parts).encode('utf-8')
    return int.from_bytes(hashlib.blake2b(raw, digest_size=4).digest(), 'big')


def place_text(name: str) -> str:
    """地名里的「·」读成「的」：某宫·某厅 → 某宫的某厅。"""
    return (name or '').replace('·', '的')


# ---------------------------------------------------------------- 事实


@dataclass
class Fact:
    """一条要写到的事：`text` 是中性的事实句（给 J5 核对用），`line` 是模板句。"""

    text: str
    line: str
    actor: str | None = None
    kind: str = 'event'  # event / heard / player
    event_id: int | None = None
    by_player: bool = False


def _speech_line(ev: ViewEvent, spoken_text: str | None) -> str:
    text = ev.text.strip()
    sp = ev.speech or {}
    if ev.by_player:
        said = sp.get('spoken_text') or spoken_text
        if said:
            head = text.split('：', 1)[0] if '：' in text else text
            quote = cn_digits(str(said).strip()).strip('「」“”"')
            if quote:
                return f'{head}：「{quote}」'
        return close_sentence(text)
    adv = STANCE_ADV.get(str(sp.get('stance') or ''), '')
    actor = ev.actor or ''
    if adv and actor and text.startswith(actor):
        rest = text[len(actor):]
        if rest.startswith('对') or rest.startswith('向'):
            text = actor + adv + rest
    return close_sentence(text)


def fact_list(view: PlayerView, *, player_fact: str = '', spoken_text: str | None = None) -> list[Fact]:
    """本回合要写到的事，按先后：玩家视图里的事件、只听见的声响。

    `player_fact` 是玩家这一回合的动作事实句；视图里已有玩家自己的事件时不再重复。
    """
    out: list[Fact] = []
    has_player_event = any(ev.by_player for ev in view.events)
    if player_fact.strip() and not has_player_event:
        line = close_sentence(player_fact)
        if spoken_text:
            head = player_fact.strip().rstrip('。')
            line = f'{head}：「{cn_digits(spoken_text.strip())}」'
            line = close_sentence(line) if not line.endswith('」') else line
        out.append(Fact(text=player_fact.strip().rstrip('。'), line=line, actor='你', kind='player', by_player=True))
    for ev in view.events:
        text = ev.text.strip().rstrip('。')
        if not text:
            continue
        if ev.speech:
            line = _speech_line(ev, spoken_text)
            stance = STANCE_LABEL.get(str(ev.speech.get('stance') or ''), '')
            if stance and stance != '平常' and not ev.by_player:
                text = f'{text}（语气{stance}）'
        else:
            line = close_sentence(ev.text)
        out.append(Fact(text=text, line=line, actor=ev.actor, kind='event', event_id=ev.id, by_player=ev.by_player))
    for h in view.heard:
        h = h.strip()
        if h:
            out.append(Fact(text=h.rstrip('。'), line=close_sentence(h), kind='heard'))
    return out


def _strip_end(line: str) -> str:
    return line.rstrip('。！？!?…')


def _join_lines(facts: Sequence[Fact]) -> list[str]:
    """把事实句连成短句：同一个人接连两件事并成一句；换人时偶尔加个连接词（确定性）。"""
    lines: list[str] = []
    prev: Fact | None = None
    merged_prev = False
    for i, f in enumerate(facts):
        line = f.line
        actor = f.actor or ''
        if (
            prev is not None
            and not merged_prev
            and actor
            and prev.actor == actor
            and f.kind == prev.kind == 'event'
            and lines
            and lines[-1].startswith(actor)
            and lines[-1].endswith('。')
            and line.startswith(actor)
            and not line.endswith('」')
            and '：' not in line
            and '：' not in lines[-1]
            and text_len(lines[-1]) + text_len(line) <= 60
        ):
            rest = line[len(actor):]
            lines[-1] = _strip_end(lines[-1]) + '，又' + rest
            merged_prev = True
            prev = f
            continue
        merged_prev = False
        if (
            i > 0
            and f.kind == 'event'
            and prev is not None
            and prev.actor != f.actor
            and not line.startswith('你')
        ):
            conn = _CONNECTIVES[_h(f.event_id, i) % len(_CONNECTIVES)]
            if conn and not line.startswith(conn):
                line = conn + line
        lines.append(line)
        prev = f
    return lines


def _scene_lines(view: PlayerView) -> list[str]:
    return [place_text(s.strip()) for s in split_sentences(view.location_desc or '') if s.strip()]


def _scene_pad(view: PlayerView, salt: int) -> list[str]:
    """凑字数用的三两笔景物：从地点描述的第一句里按 salt 轮着取两个分句，免得回回照抄整段。"""
    lines = _scene_lines(view)
    if not lines:
        return []
    first = lines[0]
    clauses = [c for c in re.split(r'[，；]', first.rstrip('。！？…')) if c.strip()]
    if len(clauses) <= 2:
        return [first]
    k = salt % len(clauses)
    idx = sorted({k, (k + 1) % len(clauses)})
    return ['，'.join(clauses[i] for i in idx) + '。']


def _present_line(view: PlayerView, salt: int) -> str:
    people = [p for p in view.present if p and p != '你'][:3]
    if not people:
        return ''
    lead = ('近旁有', '身边还有', '附近站着')[salt % 3]
    return f'{lead}{"、".join(people)}。'


def _body_lines(view: PlayerView) -> list[str]:
    out: list[str] = []
    plain: list[str] = []
    for b in view.body:
        b = b.strip().rstrip('。')
        if not b:
            continue
        if b.startswith('你'):
            out.append(b + '。')
        else:
            plain.append(b)
    if plain:
        out.insert(0, '你只觉' + '，'.join(plain[:3]) + '。')
    return out


def template_sentences(
    view: PlayerView,
    *,
    facts: Sequence[Fact] | None = None,
    player_fact: str = '',
    spoken_text: str | None = None,
    min_len: int = 80,
    max_len: int = 220,
    hard_max: int = 400,
    scene: bool | None = None,
) -> list[str]:
    """模板叙述，逐句。事实一条不漏（至多写到 `hard_max`），景物与身上的感觉在 `max_len` 之内补足。

    `scene`：要不要写地点描述；None 时自动——玩家这回合换了地方、或没有任何事时写，字数不够时也写。
    """
    fs = list(facts) if facts is not None else fact_list(view, player_fact=player_fact, spoken_text=spoken_text)
    first_id = next((f.event_id for f in fs if f.event_id is not None), 0)
    salt = _h(view.clock, view.location, len(fs), first_id)
    mandatory = [place_text(line) for line in _join_lines(fs)]
    moved = any(ev.by_player and ev.verb in _MOVE_VERBS for ev in view.events)
    want_scene = scene if scene is not None else (moved or not fs)
    hard = max(max_len, hard_max)
    out: list[str] = []
    n = 0
    for line in mandatory:
        k = text_len(line)
        if n + k > hard:
            break
        out.append(line)
        n += k
    optional: list[str] = []
    scene_lines = _scene_lines(view)
    if want_scene:
        optional += scene_lines
    if not fs:
        optional.append(_QUIET[salt % len(_QUIET)])
    optional += _body_lines(view)
    for line in optional:
        k = text_len(line)
        if n + k > max_len:
            continue
        out.append(line)
        n += k
    if n < min_len:
        extra: list[str] = []
        pl = _present_line(view, salt)
        if pl:
            extra.append(pl)
        if not want_scene:
            extra += _scene_pad(view, salt)
        for line in extra:
            if n >= min_len:
                break
            k = text_len(line)
            if n + k > max_len or line in out:
                continue
            out.append(line)
            n += k
    return out


def template_narration(view: PlayerView, **kw: Any) -> str:
    """模板叙述，整段（参数同 `template_sentences`）。"""
    return ''.join(template_sentences(view, **kw))


# ---------------------------------------------------------------- 戏内回话（不耗时）

REPLIES: dict[str, tuple[str, ...]] = {
    'unclear': (
        '你张了张嘴，一时竟不知该从何说起。',
        '话到嘴边，你又咽了回去，心里还没理出个头绪。',
        '你心里乱糟糟的，想了一想，仍拿不定该怎么说、怎么做。',
    ),
    'impossible': (
        '你掂量了一下，这件事眼下做不成。',
        '你盘算了一番，眼下却无从下手。',
        '你试着想了想，这时候实在办不到。',
    ),
    'meta': (
        '你定了定神：眼前能做的，无非看一看、听一听、开口问一问，或是动身去别处。',
        '你暗暗告诫自己，先看清眼前的情形，再拿主意不迟。',
    ),
    'absurd': (
        '你恍惚了一下，仿佛想起一样这世上没有的东西，再定睛时，眼前一切如旧。',
        '一阵古怪的恍惚掠过心头，那念头连你自己也说不上来，转眼便散了。',
        '你愣了一愣，脑子里闪过一个说不清的怪念头，随即又回过神来。',
    ),
}


def diegetic_reply(
    kind: str,
    *,
    detail: str = '',
    salt: int = 0,
    state: WorldState | None = None,
    defs: Defs | None = None,
    ok: Callable[[str], bool] | None = None,
) -> str:
    """看不懂、做不到、问怎么玩、提到世上没有的东西时的一句戏内回话（方案 2.4、4.2）。不耗时。

    `detail`：做不到的缘由（可以带 ⟦id⟧，给了 state 与 defs 就按玩家视角渲染）；`ok` 是检查函数，
    缘由过不了检查就不写。
    """
    pool = REPLIES.get(kind) or REPLIES['unclear']
    text = pool[salt % len(pool)]
    d = (detail or '').strip()
    if d and state is not None and defs is not None:
        d = refs.render(d, state, defs, state.player)
    if d and kind in ('impossible', 'unclear') and '⟦' not in d and (ok is None or ok(d)):
        text = _strip_end(text) + '：' + close_sentence(d)
    return text


_WARN_REASON = {
    'cliff': '脚下就是深崖，一失手便要粉身碎骨',
    'fight': '对方的身手远在你之上，真动起手来，只怕性命难保',
    'item': '这东西来路凶险，入口只怕要了你的命',
    'other': '这一下凶险万分，弄不好要把命搭进去',
}


def warning_text(state: WorldState, defs: Defs, action: Any) -> str:
    """致命动作的第一次：写明危险，局面不变（方案 2.4、6.10）。玩家坚持再做，才真正结算。"""
    desc = refs.render(getattr(action, 'desc', '') or '', state, defs, state.player).strip().rstrip('。')
    verb = getattr(action, 'verb', '')
    if verb in ('go', 'flee') and any(w in desc for w in ('缒', '攀', '跃', '崖')):
        why = _WARN_REASON['cliff']
    elif verb in ('go', 'flee'):
        why = '这条路凶险难行，一个不慎就回不来了'
    elif verb in ('attack', 'restrain', 'push', 'block', 'steal'):
        why = _WARN_REASON['fight']
    elif verb == 'use':
        why = _WARN_REASON['item']
    else:
        why = _WARN_REASON['other']
    head = f'你正要{desc}，' if desc else '你刚要动作，'
    return f'{head}心头猛地一凛：{why}。你停了下来，眼前的局面一如方才。若是拿定了主意，再做一次便是。'


# ---------------------------------------------------------------- 赶路


def duration_text(ticks: int) -> str:
    """一段时间的说法：一刻、小半个时辰、约莫两个时辰……（8 刻一个时辰）。"""
    t = max(0, int(ticks))
    if t <= 1:
        return '一刻工夫'
    if t < 4:
        return '片刻工夫'
    if t < 8:
        return '小半个时辰'
    hours, rest = divmod(t, 8)
    num = '两' if hours == 2 else cn_num(hours)
    if rest == 0:
        return f'约莫{num}个时辰'
    if rest >= 4:
        return f'{num}个多时辰'
    return f'{num}个时辰上下'


def travel_summary(ticks: int, place: str) -> str:
    """赶路的一句交代：你一路走了约莫两个时辰，来到某处。"""
    dest = place_text(place).strip() or '另一处地方'
    return f'你一路走了{duration_text(ticks)}，来到{dest}。'


# ---------------------------------------------------------------- 选项的模板句


def _name(state: WorldState, defs: Defs, eid: Any) -> str:
    if not eid:
        return ''
    return place_text(refs.display_name(state, defs, str(eid), state.player))


def _variants(state: WorldState, defs: Defs, action: Any) -> list[str]:  # noqa: C901
    verb = getattr(action, 'verb', '')
    p: dict[str, Any] = dict(getattr(action, 'params', {}) or {})
    desc = refs.render(getattr(action, 'desc', '') or '', state, defs, state.player).strip().rstrip('。')
    desc = place_text(desc)
    me = state.chars.get(state.player)
    loc = me.loc if me is not None else ''
    t = _name(state, defs, p.get('target'))
    item = _name(state, defs, p.get('item'))
    feat = _name(state, defs, f'{loc}#{p["feature"]}') if p.get('feature') else ''
    dest = _name(state, defs, p.get('exit'))
    if verb == 'go':
        if desc.startswith(('缒', '攀', '纵身')):
            return [f'试着{desc}', desc]
        if desc.startswith('离开这一带'):
            return [desc, f'动身{desc}']
        return [f'去{dest}看看', f'动身往{dest}去', f'往{dest}走一趟']
    if verb == 'flee':
        return [f'趁机往{dest}逃', f'拔腿往{dest}跑', desc]
    if verb == 'follow':
        if desc.startswith('不再'):
            return [desc, f'不再跟着{t}了']
        return [f'跟上{t}', f'悄悄跟着{t}', f'跟着{t}走']
    if verb == 'hide':
        if feat:
            return [f'躲进{feat}', f'悄悄躲到{feat}里']
        return ['就近找个地方躲起来', '赶紧找个地方藏身']
    if verb == 'look':
        return ['四下看看动静', '四下打量一番', '看一看周围的情形']
    if verb == 'listen':
        return ['侧耳听听动静', '屏息细听四周的声响']
    if verb == 'examine':
        if t:
            return [f'仔细打量{t}', f'多看{t}几眼']
        x = item or feat
        return [f'凑近细看{x}', f'仔细看看{x}']
    if verb == 'search':
        if feat:
            return [f'在{feat}里翻找翻找', f'到{feat}那边找一找']
        return ['在这里四处翻找', '四下里搜一搜']
    if verb == 'take':
        return [f'拿起{item}', f'把{item}捡起来']
    if verb == 'drop':
        return [f'放下{item}', f'把{item}放下']
    if verb == 'give':
        return [f'把{item}递给{t}', f'把{item}交给{t}']
    if verb == 'show':
        return [f'把{item}亮给{t}看', f'拿出{item}给{t}看']
    if verb == 'use':
        if t:
            return [f'给{t}用{item}', desc]
        return [desc]
    if verb == 'steal':
        return [f'试着偷偷拿{t}身上的{item}', f'趁人不备摸{t}的{item}']
    if verb == 'help':
        return [f'去照料{t}', f'过去看看{t}的伤']
    if verb == 'restrain':
        return [f'试着把{t}捆起来', f'想法子制住{t}']
    if verb == 'release':
        return [f'去给{t}松绑', f'放开{t}']
    if verb == 'attack':
        if p.get('intent') == 'kill':
            return [f'向{t}下杀手', f'对{t}痛下杀手']
        return [f'向{t}出手', f'试着向{t}动手']
    if verb == 'block':
        return [f'挡到{t}面前', f'上前拦住{t}']
    if verb == 'push':
        if feat:
            return [f'试着去推{feat}', desc]
        return [f'推{t}一把', f'试着推开{t}']
    if verb == 'study':
        return [desc, f'找个地方研读{item}']
    if verb == 'practice':
        return [desc, '找个僻静处练一练本事']
    if verb in ('teach',):
        return [desc]
    if verb == 'wait':
        if int(p.get('ticks', 1) or 1) >= 8:
            return [desc, '耐着性子等上一阵']
        return ['静观其变', '先不动声色，看看再说', desc]
    if verb == 'rest':
        return [desc, '找个地方歇一歇']
    if verb == 'struggle':
        return ['拼命挣脱', '试着挣开束缚']
    if verb == 'setpiece':
        d = desc
        return [d if d.startswith('试着') else f'试着{d}', d]
    if verb == 'say':
        return [desc]
    return [desc or '静观其变']


# ---------------------------------------------------------------- 开口的选项

# 言语行为 → 给 C4 看的说法（方案 6.6 的表）
SPEECH_LABEL: dict[str, str] = {
    'greet': '招呼',
    'tell': '告知',
    'ask': '打听',
    'request': '相求',
    'plead': '求情',
    'order': '吩咐',
    'warn': '示警',
    'comfort': '安慰',
    'agree': '答应',
    'refuse': '回绝',
    'bargain': '还价',
    'promise': '许诺',
    'threaten': '威胁',
    'insult': '辱骂',
    'taunt': '激将',
    'lie': '搪塞',
}
_PERSUADE = '相劝'
_ASK_ACTS = ('request', 'plead', 'order')


def _sp(speech: Any, key: str, default: Any = None) -> Any:
    """读一句话的字段：内核的 `Speech`，或存档里读回来的字典。"""
    if isinstance(speech, dict):
        return speech.get(key, default)
    return getattr(speech, key, default)


def _pv(state: WorldState, defs: Defs, text: Any) -> str:
    return place_text(refs.render(str(text or ''), state, defs, state.player)).strip().rstrip('。')


def is_persuasion(speech: Any) -> bool:
    """所求之事是眼前情境里对方自己的一招：这句话是「劝」，不是「求」。"""
    ask = _sp(speech, 'ask_action') or {}
    return _sp(speech, 'act') in _ASK_ACTS and ask.get('verb') == 'setpiece'


def speech_label(speech: Any) -> str:
    if is_persuasion(speech):
        return _PERSUADE
    act = str(_sp(speech, 'act') or '')
    return SPEECH_LABEL.get(act, '说话')


def _addressee(state: WorldState, defs: Defs, speech: Any) -> str:
    to = [t for t in (_sp(speech, 'to') or []) if t]
    return '、'.join(_name(state, defs, t) for t in to[:2]) or '在场的人'


def _topic(state: WorldState, defs: Defs, speech: Any) -> str:
    """告知、示警的那件事说的是谁、什么（第一个不是对方、也不是玩家自己的实体）。"""
    skip = {state.player, *(_sp(speech, 'to') or [])}
    for fid in _sp(speech, 'fact_refs') or []:
        f = state.facts.get(fid)
        if f is None:
            continue
        for eid in refs.refs_in(f.text):
            if eid not in skip and '#' not in eid:
                return _name(state, defs, eid)
    return ''


def speech_fact(state: WorldState, defs: Defs, speech: Any, desc: str = '') -> str:
    """开口的选项给 C4 看的事实句：哪种言语行为、对谁说（玩家眼里的叫法）、要旨。"""
    t = _addressee(state, defs, speech)
    low = '低声' if _sp(speech, 'whisper') else ''
    gist = _pv(state, defs, _sp(speech, 'gist')) or _pv(state, defs, desc)
    head = f'开口{speech_label(speech)}：对{t}{low}说'
    return f'{head}，{gist}' if gist else head


def _speech_variants(state: WorldState, defs: Defs, speech: Any, desc: str = '') -> list[str]:  # noqa: C901
    """每种言语行为的模板句：只写打算（劝、问、求、提醒……），不写对方答不答应。"""
    act = str(_sp(speech, 'act') or '')
    to = [x for x in (_sp(speech, 'to') or []) if x]
    t = _addressee(state, defs, speech)
    shown = _pv(state, defs, desc)
    ask = _sp(speech, 'ask_action') or {}
    what = _pv(state, defs, ask.get('desc'))
    if act in _ASK_ACTS:
        if is_persuasion(speech) and what:
            return [f'低声劝{t}{what}', f'悄悄劝{t}{what}', shown]
        if act == 'plead':
            return [f'求{t}{what}', f'恳求{t}{what}', shown] if what else [shown, f'向{t}求情']
        if act == 'order':
            return [f'吩咐{t}{what}', shown] if what else [shown, f'吩咐{t}几句']
        return [f'求{t}{what}', f'开口请{t}{what}', shown] if what else [shown, f'向{t}开口相求']
    if act == 'ask':
        about = _sp(speech, 'about')
        if about and about in to:
            return [f'问一问{t}的来历', f'向{t}请教来历', shown]
        if about:
            a = _name(state, defs, about)
            return [f'问一问{t}，{a}是怎么回事', f'向{t}打听{a}的事', shown]
        return [shown, f'向{t}打听近来的事']
    if act == 'warn':
        topic = _topic(state, defs, speech)
        if topic:
            return [f'提醒{t}留神{topic}的事', f'把{topic}的事提醒{t}', f'提醒{t}多加小心']
        return [shown, f'低声提醒{t}多加小心', f'提醒{t}留神']
    if act == 'tell':
        if _sp(speech, 'request'):
            return [f'回答{t}的问话', f'照实回答{t}']
        topic = _topic(state, defs, speech)
        if topic:
            return [f'把{topic}的事告诉{t}', f'同{t}说一说{topic}的事']
        return [f'把知道的事告诉{t}', shown]
    if act == 'greet':
        return [f'向{t}打个招呼', f'上前同{t}见个礼']
    if act == 'comfort':
        return [f'安慰{t}几句', f'好言宽慰{t}']
    if act == 'agree':
        return [f'答应{t}所求的事', f'应下{t}的话']
    if act == 'refuse':
        return [f'回绝{t}所求的事', f'婉言回绝{t}']
    return [shown, f'同{t}说几句话']


def _fit(text: str, limit: int) -> str:
    text = text.strip().rstrip('。')
    if text_len(text) <= limit:
        return text
    for sep in ('，', '；', '、'):
        head = text.split(sep, 1)[0]
        if head and text_len(head) <= limit:
            return head
    return text[:limit]


def option_template(state: WorldState, defs: Defs, action: Any, *, variant: int = 0,
                    avoid: Sequence[str] = (), speech: Any = None, desc: str = '',
                    signature: str = '') -> str:
    """一个动作的模板选项：写打算，不写结果，不超过 30 字（方案 2.3、7.8）。

    `variant` 换一种说法；`avoid` 里的文字（例如上回合选过的那条）不重复。
    开口的选项传 `speech`（内核的 Speech）与它的意图描述 `desc`、指纹 `signature`：按言语行为取模板句
    （低声劝……、问一问……、求……、提醒……），`action`（那一刻的 wait）不参与措辞。
    """
    if speech is not None:
        cands = [c for c in _speech_variants(state, defs, speech, desc) if c and c.strip()]
    else:
        cands = [c for c in _variants(state, defs, action) if c and c.strip()]
    if not cands:
        cands = ['静观其变']
    sig = signature or getattr(action, 'signature', '') or getattr(action, 'desc', '')
    start = (_h(sig) + variant) % len(cands)
    avoid_set = {a.strip() for a in avoid if a}
    ordered = cands[start:] + cands[:start]
    fitted = [_fit(c, OPTION_MAX) for c in ordered]
    for c in fitted:
        if c and c not in avoid_set:
            return c
    base = fitted[0] if fitted else '静观其变'
    alt = _fit('再' + base, OPTION_MAX)
    return alt if alt not in avoid_set else _fit('还是' + base, OPTION_MAX)


__all__ = [
    'OPTION_MAX',
    'REPLIES',
    'SPEECH_LABEL',
    'STANCE_ADV',
    'STANCE_LABEL',
    'Fact',
    'diegetic_reply',
    'duration_text',
    'fact_list',
    'is_persuasion',
    'option_template',
    'place_text',
    'speech_fact',
    'speech_label',
    'template_narration',
    'template_sentences',
    'travel_summary',
    'warning_text',
]
