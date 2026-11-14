"""方案 4.3 的公用判断：快先慢后、取样可重放、路由取值（7.12）。"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, Literal

from tianlong.config import JevThresholds, Settings

Mode = Literal['jev_first', 'claude_first', 'claude_only', 'rules_only']

# 各类判断的路由键（config.toml 的 [route]）→ 快、慢两条路的调用代号
CATEGORIES: dict[str, tuple[str, str | None]] = {
    'classify': ('J1', 'C1'),
    'decide_soul': ('J2', 'C2_soul'),
    'decide_named': ('J2', 'C2_named'),
    'replan': ('J3', 'C5'),
    'options': ('J4', None),
    'check': ('J5', None),
    'cards': ('J6', None),
}

_MODES: frozenset[str] = frozenset({'jev_first', 'claude_first', 'claude_only', 'rules_only'})


def mode(settings: Settings, category: str) -> Mode:
    """某类判断此刻走哪条路。未知的类别按 'jev_first'。"""
    value = getattr(settings.route, category, None)
    if value is None:
        extra = settings.route.model_extra or {}
        value = extra.get(category, 'jev_first')
    value = str(value)
    if value not in _MODES:
        raise ValueError(f'route.{category} = {value!r}; expected one of {sorted(_MODES)}')
    return value  # type: ignore[return-value]


def _th(thresholds: JevThresholds | Mapping[str, float], name: str) -> float:
    if isinstance(thresholds, Mapping):
        return float(thresholds[name])
    return float(getattr(thresholds, name))


def _probs(answer: Mapping[str, Any]) -> list[float]:
    raw = answer.get('probabilities') or {}
    return sorted((float(v) for v in raw.values()), reverse=True)


def choice_margin(answer: Mapping[str, Any]) -> float:
    ps = _probs(answer)
    if not ps:
        return 0.0
    return ps[0] - (ps[1] if len(ps) > 1 else 0.0)


def choice_unsure(answer: Mapping[str, Any], thresholds: JevThresholds | Mapping[str, float]) -> bool:
    """一个 choice 答案是否没把握：confidence 低于门槛，或头两名相差不到门槛。"""
    conf = float(answer.get('confidence', 0.0))
    return conf < _th(thresholds, 'choice_confidence') or choice_margin(answer) < _th(thresholds, 'choice_margin')


def should_escalate(
    answer: Mapping[str, Any],
    thresholds: JevThresholds | Mapping[str, float],
    important: bool = False,
    *,
    keys: Iterable[str] | None = None,
    needs_thought_key: str = 'needs_thought',
) -> bool:
    """方案 4.3：同一个问题要不要改交 Claude。

    `answer` 可以是单个答案（带 'type' 的映射），也可以是 `jev.ask` 返回的整组答案。
    整组时检查 `keys` 指定的 choice 题（默认全部 choice 题），外加名为 `needs_thought` 的 noul 题。
    任一情形即升级：
      - 内核把这一刻标为要紧（`important`）；
      - choice 的 confidence 低于 `choice_confidence`；
      - choice 头两名的概率差小于 `choice_margin`；
      - `needs_thought` 的概率高于 `needs_thought` 门槛。
    """
    if important:
        return True
    if 'type' in answer and not isinstance(answer.get('type'), Mapping):
        group: Mapping[str, Mapping[str, Any]] = {'_': answer}
    else:
        group = answer  # type: ignore[assignment]
    wanted = set(keys) if keys is not None else None
    for qid, a in group.items():
        if not isinstance(a, Mapping):
            continue
        t = a.get('type')
        if t == 'choice' and (wanted is None or qid in wanted or qid == '_'):
            if choice_unsure(a, thresholds):
                return True
        elif t == 'noul' and qid == needs_thought_key:
            if float(a.get('noul', 0.0)) > _th(thresholds, 'needs_thought'):
                return True
    return False


def sample_choice(
    probabilities: Mapping[str, float],
    roll_fn: Callable[[], int | float],
    floor: float,
) -> str:
    """在概率不低于 `floor` 的选项里按概率取样（方案 4.3「取样可重放」）。

    `roll_fn` 由调用方从 `kernel.rng` 取，所以整局可重放：返回 1 到 100 的整数（`rng.roll`），
    或 [0, 1) 之间的浮点数（`rng.uniform`，精度更细）。
    过线的只有一项时直接取它，不掷骰；一项都不过线时取概率最高的一项（同分取先出现的）。
    选项按传入的顺序排布，结果只取决于概率与掷出的数。
    """
    if not probabilities:
        raise ValueError('sample_choice: empty probabilities')
    items = [(str(k), float(v)) for k, v in probabilities.items()]
    passing = [(k, p) for k, p in items if p >= floor and p > 0.0]
    if not passing:
        best = max(items, key=lambda kv: kv[1])
        return best[0]
    if len(passing) == 1:
        return passing[0][0]
    total = sum(p for _, p in passing)
    r = roll_fn()
    if isinstance(r, float) and 0.0 <= r < 1.0:
        u01 = r
    elif not isinstance(r, bool) and float(r) == int(r) and 1 <= int(r) <= 100:
        # 把 1..100 映射到 (0, 1) 里各格的中点
        u01 = (int(r) - 0.5) / 100.0
    else:
        raise ValueError(f'roll_fn must return an int 1..100 or a float in [0, 1), got {r!r}')
    u = u01 * total
    acc = 0.0
    for k, p in passing:
        acc += p
        if u < acc:
            return k
    return passing[-1][0]
