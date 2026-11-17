"""效用打分、候选截断与取样（ARCHITECTURE 4.2）。

- 单位是自然对数几率：差 1 是「明显更好」，差 2.5 以上是「没得选」。
- `score(ctx, request)`：按身份分派到 strategy/ 的打分函数，得到每个合法选项的 `Score`；
  建立在硬知识上的选项标 `hard`，并被抬到领先其余选项至少 `decisive_gap`。
- `cut(...)`：未被否决的选项按 `p_rule ∝ exp(u / T)` 转成概率，留 `p_rule ≥ min_rule_prob` 的至多 `top_k` 项；
  排序时同分用 `rng.fork(request.id)` 的确定性微扰，不按座位号。纯函数（派生流不动原流）。
- `sample(...)`：头名领先第二名 ≥ `decisive_gap` 时直接选头名；否则（有 Jev 概率时在对数空间混合）用自己的
  随机数取样。首夜照顾只在这里作为概率乘子出现（真人那一项乘 1 − mercy_first_night 再归一）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from werewolf.kernel.rng import Rng
from werewolf.kernel.types import Request, RequestKind, Role, Seat, View
from werewolf.mind import explain
from werewolf.mind.strategy import hunter, seer, villager, witch, wolf
from werewolf.mind.strategy.common import Ctx, Score
from werewolf.mind.types import MindTuning, ScoredOption

JITTER = 1e-6  # 同分时的确定性微扰幅度（远小于任何有意义的效用差）


# --------------------------------------------------------------------------- 打分


def score(ctx: Ctx, request: Request) -> list[Score]:
    """请求的每个合法选项打分（含被否决的）。SPEAK 不在这里打分（见 speechplan）。"""
    kind = request.kind
    role = ctx.role
    if kind in (RequestKind.WOLF_PROPOSE, RequestKind.WOLF_KILL):
        out = wolf.score_kill(ctx, request)
    elif kind == RequestKind.SEER_CHECK:
        out = seer.score_check(ctx, request)
    elif kind == RequestKind.WITCH_ACT:
        out = witch.score_witch(ctx, request)
    elif kind == RequestKind.HUNTER_SHOOT:
        out = hunter.score_shoot(ctx, request)
    elif kind == RequestKind.VOTE:
        mod = {Role.WEREWOLF: wolf, Role.SEER: seer, Role.WITCH: witch, Role.HUNTER: hunter}.get(role, villager)
        out = mod.score_vote(ctx, request)
    else:
        out = [Score(o.id, o.label, o.target) for o in request.options]
    enforce_hard(out, ctx.tuning.decisive_gap)
    return out


def enforce_hard(scores: Sequence[Score], gap: float) -> None:
    """硬知识的选项必须领先其余（非硬、未否决的）选项至少 gap。"""
    live = [s for s in scores if not s.vetoed]
    hard = [s for s in live if s.hard]
    soft = [s for s in live if not s.hard]
    if not hard or not soft:
        return
    floor = max(s.u for s in soft) + gap + 0.01
    for s in hard:
        if s.u < floor:
            s.u = floor


def to_scored(scores: Sequence[Score]) -> list[ScoredOption]:
    return [ScoredOption(option_id=s.option_id, label=s.label, utility=round(s.u, 4),
                         reasons=explain.reasons(s.whys), hard=s.hard, vetoed=s.vetoed, veto_reason=s.veto_reason)
            for s in scores]


# --------------------------------------------------------------------------- 候选


def temperature(tuning: MindTuning, mood: float) -> float:
    return tuning.temperature * (1 + tuning.mood_temperature * max(0.0, 50.0 - mood) / 50.0)


def softmax(utils: Mapping[str, float], temp: float) -> dict[str, float]:
    if not utils:
        return {}
    m = max(utils.values())
    ex = {k: math.exp((u - m) / max(temp, 1e-6)) for k, u in utils.items()}
    z = sum(ex.values())
    return {k: v / z for k, v in ex.items()}


def _jitter(rng: Rng, label: str, ids: Sequence[str]) -> dict[str, float]:
    r = rng.fork(f'jitter:{label}')
    return {i: r.random() * JITTER for i in ids}


def cut(options: Sequence[ScoredOption], tuning: MindTuning, mood: float, rng: Rng,
        label: str) -> list[ScoredOption]:
    """未否决的选项转成规则概率，截出候选（按效用降序）。纯：只在派生流上取微扰。"""
    live = [o for o in options if not o.vetoed]
    if not live:
        return []
    jit = _jitter(rng, label, [o.option_id for o in live])
    order = sorted(live, key=lambda o: (-(o.utility + jit[o.option_id]), o.option_id))
    probs = softmax({o.option_id: o.utility for o in order}, temperature(tuning, mood))
    keep = [o for o in order if probs[o.option_id] >= tuning.min_rule_prob][: max(1, tuning.top_k)]
    return keep or order[:1]


# --------------------------------------------------------------------------- 取样


@dataclass
class Sampled:
    option_id: str
    probs: dict[str, float]
    best: str
    decisive: bool


def human_seat(view: View) -> Seat | None:
    """首夜照顾要知道真人坐哪（唯一读取 is_human 的地方）。"""
    for p in view.players:
        if p.is_human:
            return p.seat
    return None


def mercy_target(request: Request, view: View) -> Seat | None:
    if request.kind not in (RequestKind.WOLF_PROPOSE, RequestKind.WOLF_KILL) or request.day != 1:
        return None
    return human_seat(view)


def distribution(cands: Sequence[ScoredOption], tuning: MindTuning, mood: float,
                 jev_probs: Mapping[str, float] | None = None,
                 mercy: Mapping[str, float] | None = None) -> tuple[dict[str, float], str, bool]:
    """候选上的取样分布：(分布, 规则头名, 是否一锤定音)。"""
    if not cands:
        raise ValueError('no candidates')
    ordered = list(cands)  # 已按效用降序
    best = ordered[0].option_id
    utils = {o.option_id: o.utility for o in ordered}
    p_rule = softmax(utils, temperature(tuning, mood))
    decisive = len(ordered) == 1 or ordered[0].utility - ordered[1].utility >= tuning.decisive_gap
    if decisive:
        probs = {k: (1.0 if k == best else 0.0) for k in utils}
    elif jev_probs:
        w = tuning.jev_weight
        logs = {k: (1 - w) * math.log(max(p_rule[k], 1e-12)) + w * math.log(max(float(jev_probs.get(k, 0.0)),
                                                                                  tuning.jev_floor))
                for k in utils}
        m = max(logs.values())
        ex = {k: math.exp(v - m) for k, v in logs.items()}
        z = sum(ex.values())
        probs = {k: v / z for k, v in ex.items()}
    else:
        probs = dict(p_rule)
    if mercy:
        probs = {k: v * mercy.get(k, 1.0) for k, v in probs.items()}
        z = sum(probs.values())
        if z <= 0:
            probs = {k: (1.0 if k == best else 0.0) for k in utils}
        else:
            probs = {k: v / z for k, v in probs.items()}
    return probs, best, decisive


def draw(probs: Mapping[str, float], rng: Rng) -> str:
    items = [(k, v) for k, v in probs.items() if v > 0]
    if len(items) == 1:
        rng.random()  # 也抽一次，让随机流的消耗与分布形状无关
        return items[0][0]
    return rng.weighted(dict(items))


def sample(cands: Sequence[ScoredOption], tuning: MindTuning, mood: float, rng: Rng,
           jev_probs: Mapping[str, float] | None = None, mercy: Mapping[str, float] | None = None) -> Sampled:
    probs, best, decisive = distribution(cands, tuning, mood, jev_probs, mercy)
    choice = draw(probs, rng)
    return Sampled(choice, {k: round(v, 6) for k, v in probs.items()}, best, decisive)
