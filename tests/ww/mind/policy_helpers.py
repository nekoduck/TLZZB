"""策略层测试共用的小工具：人设、按座位喂事件建心智、重复取样。"""

from __future__ import annotations

from collections import Counter
from typing import Any

from werewolf.kernel.rng import Rng
from werewolf.kernel.types import GameConfig, Request, Role, Seat, View
from werewolf.mind.mind import Mind
from werewolf.mind.types import MindTuning, Persona, Traits
from werewolf.testing import Scenario
from werewolf.voice.personas import load_personas

BASE = dict(boldness=0.5, logic=0.5, talkative=0.5, stubborn=0.5, vengeful=0.5, conformity=0.5, deception=0.5)


def persona(pid: str = 'p', **kw: float) -> Persona:
    t: dict[str, Any] = dict(BASE)
    t.update(kw)
    p = load_personas()[0]
    return p.model_copy(update={'id': pid, 'traits': Traits(**t)})


def config_of(sc: Scenario) -> GameConfig:
    roles = [sc.roles[s] for s in sorted(sc.roles)]
    return GameConfig(roles=roles, human_seat=sc.human)


def mind_for(sc: Scenario, seat: Seat, per: Persona | None = None, *, rng: Rng | None = None,
             role: Role | None = None, tuning: MindTuning | None = None) -> Mind:
    """按 Scenario 当前的事件建一个心智：每条可见事件喂一次，view 用最终视图（足够测试用）。"""
    m = Mind(seat, per or persona(), role or sc.roles[seat], config_of(sc), rng or Rng(7, f'mind{seat}'), tuning)
    view = sc.view(seat)
    for ev in view.events:
        m.observe(ev, view)
    return m


def option_counts(m: Mind, req: Request, view: View, n: int, jev: dict[str, float] | None = None) -> Counter[str]:
    """同一个心智、同一个请求，重复 decide n 次（每次用自己随机流的下一段）。"""
    cands = m.options(req, view)
    return Counter(m.decide(req, view, cands, jev).option_id for _ in range(n))


def forked_counts(sc: Scenario, seat: Seat, per: Persona, req: Request, n: int, label: str = 'fork',
                  jev: dict[str, float] | None = None) -> Counter[str]:
    """n 个用不同随机流建的同一心智，各决定一次。"""
    view = sc.view(seat)
    out: Counter[str] = Counter()
    for i in range(n):
        m = mind_for(sc, seat, per, rng=Rng(1000 + i, f'{label}{i}'))
        out[m.decide(req, view, m.options(req, view), jev).option_id] += 1
    return out


def tvd(a: Counter[str], b: Counter[str]) -> float:
    na, nb = sum(a.values()), sum(b.values())
    keys = set(a) | set(b)
    return 0.5 * sum(abs(a[k] / na - b[k] / nb) for k in keys)
