"""纯规则：胜负、发言顺序、计票、合法选项表、遗言、发言校验。不碰引擎状态，便于单测。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal

from werewolf.kernel.types import (
    EMOTES,
    GODS,
    SPEECH_FLAGS,
    Option,
    Role,
    Seat,
    Speech,
    Team,
)

WinReason = Literal['wolves_dead', 'villagers_dead', 'gods_dead']
MAX_TEXT = 600
MAX_ACTS = 12


# ---- 胜负 ----


def check_win(roles: Mapping[Seat, Role], alive: Iterable[Seat]) -> tuple[Team, WinReason] | None:
    """屠边。狼全死好人胜（优先）；否则平民全死或神职全死狼胜；否则未分胜负。"""
    living = set(alive)
    alive_roles = [roles[s] for s in living]
    if Role.WEREWOLF not in alive_roles:
        return Team.GOOD, 'wolves_dead'
    if Role.VILLAGER not in alive_roles:
        return Team.WOLF, 'villagers_dead'
    if not any(r in GODS for r in alive_roles):
        return Team.WOLF, 'gods_dead'
    return None


# ---- 发言顺序 ----


def ring_from(start: Seat, living: Iterable[Seat]) -> list[Seat]:
    """从 start 起按座位升序环形一圈（start 必须存活）。"""
    seats = sorted(living)
    i = seats.index(start)
    return seats[i:] + seats[:i]


def speech_start(deaths: Sequence[Seat], living: Iterable[Seat], n_seats: int) -> Seat | None:
    """有夜间死者：最小死者 d 之后（d+1、…、n、1、…）第一个存活座位。平安夜返回 None（由调用方掷骰）。"""
    alive = set(living)
    if not deaths or not alive:
        return None
    d = min(deaths)
    for k in range(1, n_seats + 1):
        s = (d - 1 + k) % n_seats + 1
        if s in alive:
            return s
    return None


# ---- 计票 ----


def tally_votes(votes: Mapping[Seat, Seat | None]) -> tuple[list[dict[str, Any]], list[Seat]]:
    """返回 (tally, abstain)。tally 每行 {target, voters}，按票数降序、座位升序；voters 升序。"""
    by_target: dict[Seat, list[Seat]] = {}
    abstain: list[Seat] = []
    for voter in sorted(votes):
        t = votes[voter]
        if t is None:
            abstain.append(voter)
        else:
            by_target.setdefault(t, []).append(voter)
    tally = [{'target': t, 'voters': v} for t, v in sorted(by_target.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
    return tally, abstain


def vote_outcome(
    tally: Sequence[Mapping[str, Any]], round: Literal['main', 'pk'],
) -> tuple[Literal['exile', 'pk', 'none'], Literal['top', 'tie', 'pk_tie', 'all_abstain'], Seat | None, list[Seat]]:
    """返回 (outcome, reason, 出局者, tied)。tied 只在最高票平票时非空。"""
    if not tally:
        return 'none', 'all_abstain', None, []
    top = len(tally[0]['voters'])
    tied = sorted(row['target'] for row in tally if len(row['voters']) == top)
    if len(tied) == 1:
        return 'exile', 'top', tied[0], []
    if round == 'main':
        return 'pk', 'tie', None, tied
    return 'none', 'pk_tie', None, tied


def pk_voters(living: Iterable[Seat], candidates: Sequence[Seat]) -> list[Seat]:
    """PK 投票人：台下的存活者；台下没人时全体存活者。"""
    alive = sorted(living)
    off = [s for s in alive if s not in candidates]
    return off or alive


def wolf_plurality(proposals: Mapping[Seat, str]) -> tuple[str, Literal['plurality', 'tiebreak'], dict[str, int]]:
    """狼人提议取多数；平票取座位最小的提议者的提议。返回 (option_id, by, tally)。"""
    tally: dict[str, int] = {}
    for s in sorted(proposals):
        tally[proposals[s]] = tally.get(proposals[s], 0) + 1
    top = max(tally.values())
    tops = [o for o, n in tally.items() if n == top]
    if len(tops) == 1:
        return tops[0], 'plurality', tally
    return proposals[min(proposals)], 'tiebreak', tally


def kill_tally(choices: Iterable[str]) -> dict[str, int]:
    tally: dict[str, int] = {}
    for o in choices:
        tally[o] = tally.get(o, 0) + 1
    return tally


# ---- 合法选项 ----


def kill_options(living: Iterable[Seat], allow_empty: bool) -> list[Option]:
    opts = [Option(id=f'kill:{s}', target=s, label=f'刀 {s} 号') for s in sorted(living)]
    if allow_empty:
        opts.append(Option(id='kill:none', label='空刀'))
    return opts


def check_options(seer: Seat, living: Iterable[Seat], checked: Iterable[Seat]) -> list[Option]:
    done = set(checked)
    return [Option(id=f'check:{s}', target=s, label=f'查验 {s} 号') for s in sorted(living)
            if s != seer and s not in done]


def can_self_save(rule: Literal['never', 'first_night', 'always'], night: int) -> bool:
    return rule == 'always' or (rule == 'first_night' and night == 1)


def witch_options(
    witch: Seat,
    victim: Seat | None,
    antidote: bool,
    poison: bool,
    living: Iterable[Seat],
    night: int,
    self_save: Literal['never', 'first_night', 'always'],
) -> list[Option]:
    """解药（刀口存在且允许）→ 毒药（除自己外的存活者）→ 不用药。"""
    opts: list[Option] = []
    if antidote and victim is not None and (victim != witch or can_self_save(self_save, night)):
        opts.append(Option(id='save', target=victim, label=f'用解药救 {victim} 号'))
    if poison:
        opts += [Option(id=f'poison:{s}', target=s, label=f'毒 {s} 号') for s in sorted(living) if s != witch]
    opts.append(Option(id='pass', label='不用药'))
    return opts


def shoot_options(hunter: Seat, living: Iterable[Seat]) -> list[Option]:
    opts = [Option(id=f'shoot:{s}', target=s, label=f'带走 {s} 号') for s in sorted(living) if s != hunter]
    opts.append(Option(id='shoot:none', label='不开枪'))
    return opts


def vote_options(voter: Seat, candidates: Iterable[Seat], allow_abstain: bool) -> list[Option]:
    opts = [Option(id=f'vote:{s}', target=s, label=f'投 {s} 号') for s in sorted(candidates) if s != voter]
    if allow_abstain:
        opts.append(Option(id='vote:abstain', label='弃票'))
    return opts


# ---- 遗言 ----


def has_last_words(after: Literal['night', 'vote'], night: int, last_words_nights: int) -> bool:
    """被放逐的总有遗言；夜间死者只在前 last_words_nights 夜有。"""
    return after == 'vote' or night <= last_words_nights


# ---- 发言校验 ----


def speech_problem(speech: Speech, n_seats: int = 9) -> str | None:
    """结构校验；合法返回 None，否则返回原因。"""
    if not speech.text.strip():
        return 'empty text'
    if len(speech.text) > MAX_TEXT:
        return 'text too long'
    if len(speech.acts) > MAX_ACTS:
        return 'too many acts'
    for a in speech.acts:
        if a.target is not None and not 1 <= a.target <= n_seats:
            return f'bad target {a.target}'
    if speech.emote is not None and speech.emote not in EMOTES:
        return f'bad emote {speech.emote}'
    bad = [f for f in speech.flags if f not in SPEECH_FLAGS]
    if bad:
        return f'bad flags {bad}'
    return None
