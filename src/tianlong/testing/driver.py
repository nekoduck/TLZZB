"""驱动一局游戏（Mock 模型），并把每回合的事件收成一份完整的事件日志。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tianlong.config import Settings, load_settings
from tianlong.game import Game
from tianlong.kernel import actions, resolve
from tianlong.kernel.actions import ActionInst
from tianlong.kernel.state import TICKS_PER_DAY, Ctx, Event, Speech, WorldState
from tianlong.ui.events import Notice, Options, Text, UIEvent

REPO = Path(__file__).resolve().parents[3]


def make_settings(root: str | Path, *, content: str | Path | None = None, config: str | Path | None = None) -> Settings:
    """读仓库的 config.toml，把日志与存档指到 ``root`` 下（测试、试探都不往仓库里写）。"""
    root = Path(root)
    s = load_settings(Path(config) if config else REPO / "config.toml", env_file=root / "none.env")
    s.paths.content = str(content or REPO / "content")
    s.paths.runs = str(root / "runs")
    s.paths.saves = str(root / "saves")
    return s


class Driver:
    """包住 ``Game``：测试里的「玩家」。``events`` 是这一局到此刻为止的全部事件（按 id 排好）。"""

    def __init__(self, game: Game, *, keep_events: bool = True) -> None:
        self.game = game
        self.keep_events = keep_events
        self.events: list[Event] = []
        self.last_events: list[Event] = []  # 最近一次输入产生的事件
        self.last_ui: list[UIEvent] = []
        self.cheat_ids: set[int] = set()  # 布景与调试命令产生的事件（不算世界自己的改动）
        self.inputs = 0
        self._seen: set[int] = set()
        self._state_obj: Any = game.state  # 读档会换掉整个状态对象：据此知道时间线断了

    @classmethod
    async def new(
        cls,
        settings: Settings,
        *,
        seed: int = 1,
        debug: bool = True,
        player_name: str | None = "阿青",
        run_id: str | None = None,
        keep_events: bool = True,
    ) -> Driver:
        game = await Game.new(settings, seed=seed, mock=True, debug=debug, player_name=player_name, run_id=run_id)
        d = cls(game, keep_events=keep_events)
        await d.start()
        return d

    # ---- 读

    @property
    def state(self) -> WorldState:
        return self.game.state

    @property
    def defs(self) -> Any:
        return self.game.defs

    @property
    def me(self) -> Any:
        return self.state.chars[self.state.player]

    def char(self, cid: str) -> Any:
        return self.state.chars[cid]

    @property
    def tick(self) -> int:
        return self.state.tick

    @property
    def day(self) -> int:
        return self.state.tick // TICKS_PER_DAY + 1

    def text(self) -> str:
        return "".join(e.piece for e in self.last_ui if isinstance(e, Text))

    def notices(self) -> list[str]:
        return [e.text for e in self.last_ui if isinstance(e, Notice)]

    def option_texts(self) -> list[str]:
        for e in self.last_ui:
            if isinstance(e, Options):
                return list(e.items)
        return []

    def legal(self, verb: str | None = None, **params: Any) -> list[ActionInst]:
        """玩家此刻的合法动作，可按动词与参数筛。"""
        acts = actions.legal_actions(self.state, self.defs, self.state.player)
        out = []
        for a in acts:
            if verb is not None and a.verb != verb:
                continue
            if any(a.params.get(k) != v for k, v in params.items()):
                continue
            out.append(a)
        return out

    # ---- 写（都经 Game 或内核的入口）

    def _harvest(self, evs: list[Event], *, cheat: bool = False) -> list[Event]:
        new = [e for e in evs if e.id not in self._seen]
        for e in new:
            self._seen.add(e.id)
            if cheat:
                self.cheat_ids.add(e.id)
        if self.keep_events:
            self.events.extend(new)
            self.events.sort(key=lambda e: e.id)
        self.last_events = new
        return new

    def _after(self, ui: list[UIEvent], *, cheat: bool = False) -> list[UIEvent]:
        self.inputs += 1
        self.last_ui = ui
        rec = self.game.last_turn
        evs = list(rec.events) if rec is not None else []
        st = self.game.state
        if st is not self._state_obj:
            # 读档了：事件 id 从存档那一刻接着编，会与放弃了的那段时间线重号。
            # 认过的 id 作废；全量日志里，放弃了的那一段也去掉。
            self._state_obj = st
            first = min((e.id for e in evs), default=st.next_event_id)
            self._seen = {i for i in self._seen if i < first}
            self.cheat_ids = {i for i in self.cheat_ids if i < first}
            self.events = [e for e in self.events if e.id < first]
        self._harvest(evs, cheat=cheat)
        return ui

    async def start(self) -> list[UIEvent]:
        return self._after([ev async for ev in self.game.start()])

    async def send(self, raw: str) -> list[UIEvent]:
        """一次玩家输入（选项、自由输入、斜杠命令）。"""
        cheat = raw.strip().startswith("/debug") and not raw.strip().startswith("/debug tick")
        return self._after([ev async for ev in self.game.turn(raw)], cheat=cheat)

    async def play(self, *inputs: str) -> list[UIEvent]:
        out: list[UIEvent] = []
        for raw in inputs:
            out = await self.send(raw)
        return out

    async def act(self, action: ActionInst, speech: Speech | None = None, *, label: str | None = None) -> list[UIEvent]:
        """直接结算合法清单里的一个动作：与选中一个选项走同一条路（记账、微轮、时间、叙述、存档）。

        ``Game`` 对外只收文字；这里借它内部的 ``_logged`` 与 ``_play``，好让试探能取到三个选项之外的合法动作。
        """
        g = self.game
        raw = label or f"〔动作〕{action.signature}"
        g._record_input(raw)
        ui = [ev async for ev in g._logged(raw, "turn", g._play(action, speech))]
        return self._after(ui)

    async def do(self, verb: str, speech: Speech | None = None, **params: Any) -> list[UIEvent]:
        """按动词与参数造一个动作并结算；此刻不合法就报错（脚本写错了，或者世界不是脚本以为的样子）。"""
        a = actions.build(self.state, self.defs, self.state.player, verb, params)
        if a is None or actions.is_legal(self.state, self.defs, a) is None:
            raise AssertionError(f"not legal now: {verb} {params} (tick {self.state.tick}, at {self.me.loc})")
        return await self.act(a, speech)

    async def say(self, speech: Speech) -> list[UIEvent]:
        """只说话、不动（等 1 刻带那句话）。"""
        a = actions.build_speech_wait(self.state, self.defs, self.state.player)
        if a is None:
            raise AssertionError("cannot speak now")
        return await self.act(a, speech)

    def fx(self, effs: list[dict], *, self_id: str | None = None, source: str = "rule") -> list[Event]:
        """测试布景：经内核的效果改世界（带回滚）。产生的事件记作布景，不算世界自己的改动。"""
        st = self.state
        evs = resolve.run_effects(st, self.defs, effs, Ctx(self_id=self_id or st.player), source=source)
        self._harvest(list(evs), cheat=True)
        return evs

    async def debug(self, arg: str) -> list[UIEvent]:
        return await self.send(f"/debug {arg}")

    async def pass_ticks(self, n: int) -> list[UIEvent]:
        """拨过 ``n`` 刻（``/debug tick``：世界照常每个时辰推演一次，不因动静打断）。"""
        return await self.send(f"/debug tick {int(n)}")

    async def run_until(self, tick: int, *, chunk: int = 8) -> None:
        """让世界走到 ``tick``（玩家只是等着；可以不在图内）。收场了就停。"""
        while self.state.tick < tick and self.state.ended is None:
            await self.pass_ticks(min(chunk, tick - self.state.tick))

    async def close(self) -> None:
        await self.game.aclose()


__all__ = ["Driver", "make_settings", "REPO"]
