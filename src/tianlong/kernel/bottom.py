"""人物底线（方案 5.4 的 ``blocks``）：一处匹配，三处共用。

``legal_actions`` 用它把撞上底线的动作从清单里去掉；``speech`` 用它去掉不肯说的话；
请求裁决用它判 ``never``。一条 block 的每个写出来的键都要对得上，才算命中：

- ``verb``：动词。
- ``act``：``attack`` 看 ``params.intent``（缺省 ``injure``）；``say`` 看言语行为；其余动词看 ``params.act``。
- ``fact_tag``：所说（所涉）的事实带这个标签。
- ``target``：对象（``self`` / ``player`` / ``$requester`` 都可以写）；``say`` 时对象在听的人里即可。
- 其余的键（``art``、``item``、``exit``、``feature``、``intent``…）与动作的同名参数相等；写成列表时任取其一。
- ``when``：谓词，``$target`` 指对象，``$requester`` 指提出请求的人。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from tianlong.kernel import predicates
from tianlong.kernel.defs import BlockDef, Defs
from tianlong.kernel.state import Character, Ctx, WorldState

DEFAULT_INTENT = "injure"
DEFAULT_SAY_ACT = "tell"


def card_blocks(defs: Defs, c: Character) -> list[BlockDef]:
    card = defs.card_for(c.persona) or defs.cards.get(c.id)
    if card is None:
        return []
    return [b for bl in card.bottom_lines for b in bl.blocks]


def _listeners(params: Mapping[str, Any], to: Iterable[str] | None) -> list[str]:
    if to:
        return [t for t in to if t]
    t = params.get("to") or params.get("target")
    if isinstance(t, str):
        return [t]
    if isinstance(t, list | tuple):
        return [str(x) for x in t if x]
    return []


def _act_of(verb: str, params: Mapping[str, Any]) -> Any:
    if verb == "attack":
        return params.get("intent") or DEFAULT_INTENT
    if verb == "say":
        return params.get("act") or DEFAULT_SAY_ACT
    return params.get("act", params.get("intent"))


def block_matches(
    state: WorldState,
    defs: Defs,
    owner: Character,
    b: BlockDef,
    verb: str,
    params: Mapping[str, Any] | None = None,
    *,
    to: Iterable[str] | None = None,
    requester: str | None = None,
) -> bool:
    """这条 block 是否命中「owner 做 verb(params)」。``to``：这句话说给谁听（只对 say 有意义）。"""
    if b.verb != verb:
        return False
    params = params or {}
    if verb == "say":
        heard = _listeners(params, to)
        target = heard[0] if heard else None
    else:
        target = params.get("target")
        heard = [target] if target else []
    roles: dict[str, str] = {}
    if isinstance(target, str) and target:
        roles["target"] = target
    if requester:
        roles["requester"] = requester
    ctx = Ctx(self_id=owner.id, roles=roles)
    if b.act is not None and _act_of(verb, params) != b.act:
        return False
    if b.fact_tag is not None:
        f = state.facts.get(str(params.get("fact") or ""))
        if f is None or b.fact_tag not in f.tags:
            return False
    if b.target is not None:
        want = predicates.resolve_id(b.target, state, ctx)
        if verb == "say":
            if want not in heard:
                return False
        elif want != target:
            return False
    for key, raw in (b.model_extra or {}).items():
        got = params.get(key)
        if key == "intent" and got is None and verb == "attack":
            got = DEFAULT_INTENT
        if isinstance(raw, list | tuple):
            if got not in [predicates.resolve_id(v, state, ctx) for v in raw]:
                return False
        elif got != predicates.resolve_id(raw, state, ctx):
            return False
    if b.when is not None and not predicates.holds(state, defs, b.when, ctx):
        return False
    return True


def blocked(
    state: WorldState,
    defs: Defs,
    owner: Character,
    verb: str,
    params: Mapping[str, Any] | None = None,
    *,
    to: Iterable[str] | None = None,
    requester: str | None = None,
) -> bool:
    """owner 的底线是否禁止这件事。"""
    heard = list(to) if to else None
    return any(
        block_matches(state, defs, owner, b, verb, params, to=heard, requester=requester)
        for b in card_blocks(defs, owner)
    )
