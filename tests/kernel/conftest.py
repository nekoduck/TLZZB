"""内核测试的小世界夹具（tests/fixtures/mini）：3 个地点、4 个人。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tianlong.content.loader import load
from tianlong.kernel import actions, resolve
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Event, Speech, WorldState

MINI = Path(__file__).resolve().parent.parent / "fixtures" / "mini"

_CACHE: dict[str, tuple[Defs, WorldState]] = {}


def fresh(seed: int = 0) -> tuple[Defs, WorldState]:
    if "mini" not in _CACHE:
        _CACHE["mini"] = load(MINI)
    defs, state = _CACHE["mini"]
    st = state.model_copy(deep=True)
    st.seed = seed
    return defs, st


class World:
    def __init__(self, seed: int = 0) -> None:
        self.defs, self.state = fresh(seed)

    @property
    def s(self) -> WorldState:
        return self.state

    def c(self, cid: str) -> Any:
        return self.state.chars[cid]

    def build(self, actor: str, verb: str, **params: Any) -> actions.ActionInst | None:
        return actions.build(self.state, self.defs, actor, verb, params)

    def act(
        self, actor: str, verb: str, *, source: str | None = None, speech: Speech | None = None, **params: Any
    ) -> list[Event]:
        a = self.build(actor, verb, **params)
        assert a is not None, f"{actor} cannot {verb} {params}"
        return resolve.apply(
            self.state, self.defs, a, speech, source=source or ("player" if actor == "player" else "mind")
        )

    def say(self, actor: str, act: str, to: list[str] | None = None, **kw: Any) -> list[Event]:
        sp = Speech(act=act, to=to or [], **kw)
        a = self.build(actor, "wait", ticks=1)
        assert a is not None
        return resolve.apply(self.state, self.defs, a, sp, source="player" if actor == "player" else "mind")

    def fx(self, effs: list[dict], self_id: str | None = None, **kw: Any) -> list[Event]:
        from tianlong.kernel import effects
        from tianlong.kernel.state import Ctx

        return effects.apply_effects(self.state, self.defs, effs, Ctx(self_id=self_id, **kw), source="rule")

    def legal(self, actor: str, **kw: Any) -> list[actions.ActionInst]:
        return actions.legal_actions(self.state, self.defs, actor, **kw)

    def has(self, actor: str, verb: str, **params: Any) -> bool:
        sig = actions.signature(verb, params)
        return any(a.signature == sig for a in self.legal(actor))

    def status(self, cid: str, kind: str) -> Any:
        return next((s for s in self.c(cid).statuses if s.kind == kind), None)


@pytest.fixture
def w() -> World:
    return World()


@pytest.fixture
def mini_dir() -> Path:
    return MINI
