"""实体引用 ``⟦id⟧`` 的写法与渲染（ARCHITECTURE 第 5 节）。

景物写成 ``⟦地点id#景物id⟧``。情境与机会牌的文字里的 ``{角色位}`` 由 ``fill_roles`` 换成 ``⟦id⟧``。
"""

from __future__ import annotations

import re

from tianlong.kernel.defs import Defs
from tianlong.kernel.state import WorldState

REF_RE = re.compile(r"⟦([^⟦⟧]+)⟧")
ROLE_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

YOU = "你"


def ref(entity_id: str) -> str:
    return f"⟦{entity_id}⟧"


def feature_ref(loc: str, feature: str) -> str:
    return f"⟦{loc}#{feature}⟧"


def refs_in(text: str) -> list[str]:
    """文字里引用到的全部实体 id（按出现顺序，去重）。"""
    seen: list[str] = []
    for m in REF_RE.finditer(text or ""):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def fill_roles(text: str, roles: dict[str, str], self_id: str | None = None) -> str:
    """把 ``{角色位}`` 换成 ``⟦所绑的 id⟧``；``{self}`` 换成当前角色。认不出的原样保留。"""

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name == "self" and self_id:
            return ref(self_id)
        if name in roles and roles[name]:
            return ref(roles[name])
        return m.group(0)

    return ROLE_RE.sub(sub, text or "")


def lookup(state: WorldState, defs: Defs, entity_id: str) -> tuple[str, str] | None:
    """实体的 (真名, 不知其名时的叫法)。找不到返回 None。"""
    c = state.chars.get(entity_id)
    if c is not None:
        return c.name, c.unknown_alias or "一个人"
    loc = state.locations.get(entity_id)
    if loc is not None:
        return loc.name, loc.unknown_alias or "一处地方"
    it = state.items.get(entity_id)
    if it is not None:
        return it.name, it.unknown_alias or "一样东西"
    art = defs.arts.get(entity_id)
    if art is not None:
        return art.name, art.unknown_alias or "一门功夫"
    if "#" in entity_id:
        loc_id, _, fid = entity_id.partition("#")
        loc = state.locations.get(loc_id)
        if loc is not None and fid in loc.features:
            name = loc.features[fid].name
            return name, name
    term = defs.lexicon.terms.get(entity_id) or defs.lexicon.names.get(entity_id)
    if term is not None:
        return term.name, term.alias or term.name
    return None


def is_feature(entity_id: str) -> bool:
    return "#" in entity_id


def display_name(state: WorldState, defs: Defs, entity_id: str, viewer: str | None = None) -> str:
    """按看的人决定写真名、叫法还是「你」。"""
    found = lookup(state, defs, entity_id)
    if viewer is not None and entity_id == viewer:
        if viewer == state.player:
            return YOU
    if found is None:
        return entity_id
    name, alias = found
    if viewer is None or viewer != state.player:
        return name
    if is_feature(entity_id) or entity_id in state.known_terms:
        return name
    if entity_id in state.term_alias:
        return state.term_alias[entity_id]
    return alias


def render(text: str, state: WorldState, defs: Defs, viewer: str | None = None) -> str:
    """渲染文字里的全部 ``⟦id⟧``。``viewer`` 为空时一律用真名（全知视角）。"""
    return REF_RE.sub(lambda m: display_name(state, defs, m.group(1), viewer), text or "")
