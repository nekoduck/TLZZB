"""节奏：局势紧张度、发言长短的偏好、各种停顿的时长。

输入只有 `PublicView`（ARCHITECTURE 第 1 节纪律 2）；停顿时长只取决于 (kind, speed, rng)，
不看局面，所以夜间环节与技能环节的时长数不出谁活着、谁在行动（第 7 节规则 9）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from werewolf.kernel.rng import Rng
from werewolf.kernel.types import GODS, ActKind, EventType, PublicView, Role, Seat
from werewolf.mind.types import Length, Pace
from werewolf.protocol import Speed

# --------------------------------------------------------------------------- 紧张度

# tension = W_LIVING·人少 + W_DAY·天数 + W_COUNTER·对跳 + W_VOTE·票紧，压在 [0, 1]。权重之和为 1。
W_LIVING = 0.25
W_DAY = 0.25
W_COUNTER = 0.30
W_VOTE = 0.20
FULL_TABLE = 9  # 满员
FEW_LEFT = 3  # 剩这么多人时「人少」一项满格
DAY_FULL = 5  # 到第几天「天数」一项满格


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _speech_acts(pv: PublicView) -> list[tuple[Seat, dict[str, Any]]]:
    """公开发言事件里的言语行为，按事件顺序：(发言者, act 的字典)。"""
    out: list[tuple[Seat, dict[str, Any]]] = []
    for ev in pv.events:
        if ev.type != EventType.SPEECH or ev.vis != 'public' or ev.actor is None:
            continue
        speech = ev.data.get('speech') or {}
        for a in speech.get('acts') or []:
            if isinstance(a, Mapping):
                out.append((ev.actor, dict(a)))
    return out


def latest_claims(pv: PublicView) -> dict[Seat, Role]:
    """每个座位最近一次公开自称的身份。"""
    claims: dict[Seat, Role] = {}
    for seat, a in _speech_acts(pv):
        if a.get('kind') == ActKind.CLAIM_ROLE and a.get('role'):
            try:
                claims[seat] = Role(a['role'])
            except ValueError:
                continue
    return claims


def counter_claim(pv: PublicView) -> bool:
    """有没有未决的对跳：至少两个活着的座位当前自称同一个神职。"""
    living = set(pv.living())
    seen: dict[Role, int] = {}
    for seat, role in latest_claims(pv).items():
        if seat in living and role in GODS:
            seen[role] = seen.get(role, 0) + 1
    return any(n >= 2 for n in seen.values())


def vote_closeness(pv: PublicView) -> float:
    """最近一次 vote_result 有多胶着：平票为 1；否则 1 −（头名票数 − 次名票数）/ 投票人数；没投过或全弃票为 0。"""
    last = next((ev for ev in reversed(pv.events) if ev.type == EventType.VOTE_RESULT), None)
    if last is None:
        return 0.0
    data = last.data
    if data.get('outcome') == 'pk' or data.get('reason') in ('tie', 'pk_tie'):
        return 1.0
    counts = [len(row.get('voters') or []) for row in data.get('tally') or []]
    if not counts:
        return 0.0
    counts.sort(reverse=True)
    total = sum(counts) + len(data.get('abstain') or [])
    second = counts[1] if len(counts) > 1 else 0
    return _clamp(1.0 - (counts[0] - second) / max(total, 1))


def tension(pv: PublicView) -> float:
    """局势紧张度，0 到 1，只用公开信息。

    四项各在 [0, 1]，加权求和（权重见模块常量），每项对自己的因素单调不减：
    - 人少：(9 − 存活人数) / (9 − 3)；
    - 天数：(day − 1) / (5 − 1)，第 1 天为 0，第 5 天起满格；
    - 对跳：有未决的对跳为 1，否则 0（见 `counter_claim`）；
    - 票紧：最近一次放逐投票的胶着程度（见 `vote_closeness`）。
    """
    n = len(pv.living())
    living = _clamp((FULL_TABLE - n) / (FULL_TABLE - FEW_LEFT))
    day = _clamp((pv.day - 1) / (DAY_FULL - 1))
    counter = 1.0 if counter_claim(pv) else 0.0
    vote = vote_closeness(pv)
    return round(_clamp(W_LIVING * living + W_DAY * day + W_COUNTER * counter + W_VOTE * vote), 4)


# --------------------------------------------------------------------------- 发言节奏

BASE_CHARS: dict[str, int] = {'short': 60, 'medium': 120, 'long': 180}
LONG_TENSION = 0.55  # 紧张度到这里，偏长
LATE_GAME_LIVING = 4  # 存活不多于这么多人，偏中
CLAIM_KINDS = frozenset({ActKind.CLAIM_ROLE, ActKind.CLAIM_CHECK, ActKind.CLAIM_SAVE, ActKind.CLAIM_POISON})


def _info_count(pv: PublicView) -> int:
    """场上公开信息的量：声明类言语行为的条数加已死人数。"""
    claims = sum(1 for _, a in _speech_acts(pv) if a.get('kind') in CLAIM_KINDS
                 and not (a.get('kind') == ActKind.CLAIM_ROLE and a.get('role') == Role.VILLAGER))
    return claims + len(pv.dead())


def _scale(n_living: int) -> float:
    """人多时稍微压短，免得一个白天拖太久：9 人 0.85，8 人 0.9，7 人 0.95，6 人及以下 1。"""
    return 1.0 - 0.05 * max(0, n_living - 6)


def pace(pv: PublicView) -> Pace:
    """发言长短的偏好与各档字数上限。

    偏好按顺序判断：残局（存活 ≤ 4）→ 'medium'；紧张度 ≥ 0.55 → 'long'；
    第 1 天且还没有神职声明与查验（信息 ≤ 1：至多首夜一个死者）→ 'short'；其余不干预。
    """
    n = len(pv.living())
    t = tension(pv)
    bias: Length | None
    if n <= LATE_GAME_LIVING:
        bias = 'medium'
    elif t >= LONG_TENSION:
        bias = 'long'
    elif pv.day <= 1 and _info_count(pv) <= 1:
        bias = 'short'
    else:
        bias = None
    k = _scale(n)
    chars = {name: int(round(base * k / 5) * 5) for name, base in BASE_CHARS.items()}
    return Pace(length_bias=bias, max_chars=chars)


# --------------------------------------------------------------------------- 停顿

SPEED_FACTOR: dict[str, float] = {'slow': 1.5, 'normal': 1.0, 'fast': 0.5, 'instant': 0.0}

# normal 档下的时长区间（毫秒），在区间里均匀取。区间只按 kind 定，与局面无关。
HOLD_RANGES: dict[str, tuple[int, int]] = {
    'phase': (900, 1300),
    'narration': (1600, 2200),
    'dawn': (2200, 2800),
    'death': (1400, 1900),
    'vote_reveal': (2800, 3600),
    'exile': (2000, 2600),
    'night_step': (4500, 7500),  # 夜间每个环节；身份死没死都用同一分布
    'skill': (3500, 6000),  # 技能环节；死者是不是猎人都用同一分布
    'deal': (2600, 3200),
    'reaction': (700, 1100),
    'game_over': (3500, 4500),
}
DEFAULT_RANGE: tuple[int, int] = (800, 1200)


def hold_ms(kind: str, speed: Speed, rng: Rng) -> int:
    """一段停顿的毫秒数。只取决于 (kind, speed, rng)。

    每次调用恰好抽一次随机数（instant 档也抽），所以随机流的推进与速度档无关；instant 档恒为 0。
    """
    lo, hi = HOLD_RANGES.get(kind, DEFAULT_RANGE)
    base = rng.uniform(lo, hi)
    factor = SPEED_FACTOR.get(speed, 1.0)
    if factor <= 0:
        return 0
    return int(round(base * factor))


def hold_kinds() -> Sequence[str]:
    """已知的停顿种类。"""
    return tuple(HOLD_RANGES)
