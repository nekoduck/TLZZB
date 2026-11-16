"""回合主循环（方案 4.2；ARCHITECTURE 6、8）。与界面无关：测试直接驱动 ``Game``，终端经 ``ui.repl``。

一回合（``Game.turn``）的次序照方案 4.2：

1. 斜杠命令不耗时；选项（一、二、三）取上回合摆出的动作；别的文字交 ``parser`` 归类并绑定。
   看不懂、做不到、问怎么玩、提到世上没有的东西：回一句戏内的话，不耗时，原选项再给一遍。
   第一次尝试致命的动作：内核只出一条警示，局面不变。
2. ``kernel.resolve.apply`` 结算玩家的动作。
3. 在场的人各自反应，至多 ``limits.max_micro_rounds`` 个微轮（``minds``）。
4. 时间按动作的耗时推进（``_pass_time``）：逐个时辰边界走，每跨一个时辰做一次 ``world.tick``；
   歇息、等待、研读、练功这样的长动作，有动静传到玩家耳目就在那一刻打断。
5. ``world.end_of_turn``；牵挂、年谱、节奏与机会牌（``storyteller``）；玩家视图 → 叙述（流式）与选项（并行）。
6. 存档、日志、检查点；该反思的夜里做夜间反思；收场时写后事。

本模块只接线：世界的每一处改动都经内核的效果或 ``resolve.apply``；模型调用都在各模块里经 ``llm`` 发出。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from collections import Counter
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tianlong import minds, narrator, parser, store, storyteller, world
from tianlong.config import Settings
from tianlong.content.loader import load as load_content
from tianlong.kernel import actions, danger, perception, refs, resolve, timeflow
from tianlong.kernel.actions import ActionInst
from tianlong.kernel.defs import Defs
from tianlong.kernel.perception import PlayerView
from tianlong.kernel.state import (
    TICKS_PER_HOUR,
    Ctx,
    Event,
    Speech,
    WorldState,
    has_status,
    log_violation,
)
from tianlong.llm import LLM, make_llm
from tianlong.llm.record import CALLS_FILE, read_calls
from tianlong.store.checkpoints import DANGER_LABEL
from tianlong.ui import commands as ui_commands
from tianlong.ui.events import Break, Ended, Header, Notice, Options, Text, UIEvent
from tianlong.ui.text import plain_text
from tianlong.world import onscene as world_onscene
from tianlong.world import situations as world_situations

META_FILE = "meta.json"
INPUTS_FILE = "inputs.txt"

OPTION_KEYS = {"一": 0, "二": 1, "三": 2, "1": 0, "2": 1, "3": 2}
YES_WORDS = frozenset({"是", "是的", "对", "好", "确定", "走", "嗯", "y", "yes"})
NO_WORDS = frozenset({"否", "不", "不是", "不了", "不走", "算了", "留下", "n", "no"})
LONG_VERBS = ("wait", "rest", "study", "practice", "teach")  # 时间成段过去的动作（6.9、8.7）
TRAVEL_VERBS = ("go", "flee")
QUIET_VERBS = frozenset({"wait", "look", "listen", "examine", "rest", "study", "practice"})  # 算不上「动静」
# 静下来做的长动作：做满一个时辰以上时，旁人之间的闲话与小动作不打断它（见 _breaks_quiet）
QUIET_LONG_VERBS = ("wait", "rest", "study", "practice")
MOVE_VERBS = frozenset({"go", "flee", "enter", "leave", "arrive", "follow_move"})  # 有人来了、走了
GRAVE_KINDS = frozenset({"death", "harmed", "captured", "poisoned"})  # 眼前有人死伤、被擒、中毒
POINTED_ACTS = frozenset({"warn", "threaten", "order"})  # 对众人说也算冲着在场每个人来的话
TRAVEL_SUMMARY_MIN = 4  # 走了小半个时辰以上，用一句话带过路上的工夫
REST_REFLECT = 16  # 歇息满 16 刻，做夜间反思（7.10）
MAX_DANGER_CHECKPOINTS = 3  # 「大险之前」的检查点至多留几个（反复读档重来时，不让它们把每日之始的挤掉）
HELPLESS_TICKS = TICKS_PER_HOUR  # 动弹不得时，一次输入过去一个时辰
PREV_TAIL = 80
STAT_FIELDS = ("neili", "literacy", "luck", "aptitude", "base_power")
# 「走出这一带」之类的话：地图外的去处没有名字，归类对不上任何地点
LEAVE_RE = re.compile(r"(离开|走出|出了)(这一带|这片|此地|这里|这地方)|远走(高飞|他乡)?|出山去")

TEXT_ENDED = "这一局已经收场。输入 /年谱 看完整的年谱，/读档 从存档或检查点重来，/退出 离开。"
TEXT_NO_OPTION = "没有这个选项。"
TEXT_CANNOT = "此刻你做不到这件事。"
TEXT_LEAVE_ASK = (
    "从这里再往外走，就出了这一带山水，这一局也就此收笔。是否收笔？输入「是」就走出去；输入别的就留下。"
)
TEXT_LEAVE_CANCEL = "你收住脚步，没有走出去。"
TEXT_HELPLESS = "你眼下动弹不得，只能由着时间过去。随便输入点什么，再等一个时辰。"
TEXT_HELPLESS_LINE = "你昏昏沉沉，不知过了多久。"
TEXT_STALE = "上一回合出了岔子，眼前的选项重新排过了。"
TEXT_BUDGET_ASK = (
    "本局的模型花费已到预算上限（${spent:.2f} / ${budget:.2f}）。输入 /继续 追加预算、接着用模型；"
    "输入别的就改用规则与模板把这一局玩下去。"
)
TEXT_BUDGET_RULES = "这一局此后改用规则与模板，不再调用模型。"
TEXT_BUDGET_MORE = "预算已追加到 ${budget:.2f}，接着用模型。"
TEXT_NO_KEYS = "没有找到模型的密钥：这一局只用规则与模板。想看替身模型的效果，加 --mock。"
TEXT_WAKE = "⟦{me}⟧被一阵动静惊醒了"
DEBUG_USAGE = (
    "用法：/debug（内核状态与本回合的模型调用）；/debug kill <人>；/debug give <物> [给谁]；"
    "/debug art <本事> <层>；/debug stat <neili|literacy|luck|aptitude|base_power> <值>；"
    "/debug role <身份> add|remove；/debug goto <地点>；/debug tick <刻>"
)


def _now_id(seed: int) -> str:
    return time.strftime("%Y%m%d-%H%M%S") + f"-s{seed}"


def _unique_dir(root: Path, name: str) -> tuple[str, Path]:
    """runs/ 下一个还没用过的目录名。"""
    cand, n = name, 1
    while (root / cand).exists():
        n += 1
        cand = f"{name}-{n}"
    return cand, root / cand


@dataclass
class TurnRecord:
    """一回合的记账（给 /debug、测试与评测看）。"""

    raw: str
    kind: str = "turn"  # start / turn / reply / warn / command / confirm / helpless
    turn: int = 0
    tick_from: int = 0
    tick_to: int = 0
    events: list[Event] = field(default_factory=list)
    first_text_s: float | None = None  # 从输入到第一段叙述的秒数（方案 4.3 的时延预算）
    total_s: float = 0.0
    calls: tuple[int, int] = (0, 0)  # 本回合模型调用的 seq 范围（左开右闭）
    interrupted: bool = False
    notes: list[str] = field(default_factory=list)


class Game:
    """一局游戏。``start()`` 与 ``turn(raw)`` 产出 ``ui.events`` 里的界面事件（ARCHITECTURE 8）。"""

    def __init__(
        self,
        settings: Settings,
        state: WorldState,
        defs: Defs,
        llm: LLM,
        *,
        run_id: str,
        run_dir: Path,
        save_name: str,
        mock: bool = False,
        debug: bool = False,
        loaded: bool = False,
    ) -> None:
        self.settings = settings
        self.state = state
        self.defs = defs
        self.llm = llm
        self.mock = mock
        self.debug = debug
        self.loaded = loaded
        self.run_id = run_id
        self.run_dir = run_dir
        self.save_name = save_name
        self.saves_root = settings.path("saves")
        self.log = store.RunLog(run_dir)
        self.checkpoints = store.CheckpointManager(self.save_dir)
        self.options: list[storyteller.Option] = []
        self.option_texts: list[str] = []
        self.last_view: PlayerView | None = None
        self.last_report: narrator.NarrationReport | None = None
        self.last_turn: TurnRecord | None = None
        self.latencies: list[float] = []
        self.history: list[TurnRecord] = []
        self._base_budget = float(settings.limits.session_budget_usd)
        self._budget_notes: list[str] = []
        self._budget_ask = False
        self._rules_mode = False
        self._pending_leave: tuple[ActionInst, Speech | None] | None = None
        self._prev_tail = ""
        self._mark = state.next_event_id
        self._turn_events: dict[int, Event] = {}
        self._started = False
        self._stale = False
        self._closed = False
        self._rec: TurnRecord | None = None
        self._t0 = 0.0
        self._danger0 = 0  # 这一回合开始时的 danger（大险之前的检查点据此判断）
        self._credit: dict[str, int] = {}  # 微轮里已经给在场者的「等」记过、还没从过去的时间里扣掉的刻数（I6）

    # ------------------------------------------------------------------ 建局与读档

    @classmethod
    async def new(
        cls,
        settings: Settings,
        *,
        seed: int,
        mock: bool,
        player_name: str | None = None,
        run_id: str | None = None,
        replay_dir: str | Path | None = None,
        debug: bool = False,
        save_name: str | None = None,
    ) -> Game:
        """开一局新的。``replay_dir`` 给出时按那一局录下的模型调用返回（同一种子、同样的输入即逐字重现）。"""
        defs, state = load_content(settings.path("content"), player_name=player_name, seed=seed)
        return cls._build(
            settings, state, defs, mock=mock, run_id=run_id, replay_dir=replay_dir, debug=debug,
            save_name=save_name, loaded=False,
        )

    @classmethod
    async def load(
        cls,
        settings: Settings,
        save_name: str,
        *,
        mock: bool,
        debug: bool = False,
        run_id: str | None = None,
        replay_dir: str | Path | None = None,
    ) -> Game:
        """读回 ``saves/<save_name>/``，接着玩（此后的自动存档仍写在这个名字下）。存档不对时抛 ``store.SaveError``。"""
        defs, _fresh = load_content(settings.path("content"))
        path = store.save_dir(settings.path("saves"), save_name)
        state = store.load_state(path, content_hash=defs.content_hash)
        return cls._build(
            settings, state, defs, mock=mock, run_id=run_id, replay_dir=replay_dir, debug=debug,
            save_name=save_name, loaded=True,
        )

    @classmethod
    def _build(
        cls,
        settings: Settings,
        state: WorldState,
        defs: Defs,
        *,
        mock: bool,
        run_id: str | None,
        replay_dir: str | Path | None,
        debug: bool,
        save_name: str | None,
        loaded: bool,
    ) -> Game:
        runs = settings.path("runs")
        if run_id:
            rid, run_dir = run_id, runs / run_id
        else:
            rid, run_dir = _unique_dir(runs, _now_id(state.seed))
        notes: list[str] = []
        llm = make_llm(
            settings,
            mock=mock,
            run_dir=run_dir,
            replay_dir=Path(replay_dir) if replay_dir is not None else None,
            on_budget_warn=notes.append,
        )
        game = cls(
            settings, state, defs, llm, run_id=rid, run_dir=run_dir, save_name=save_name or rid, mock=mock,
            debug=debug, loaded=loaded,
        )
        game._budget_notes = notes
        game._write_meta(replay_dir)
        return game

    @property
    def save_dir(self) -> Path:
        return store.save_dir(self.saves_root, self.save_name)

    def _write_meta(self, replay_dir: str | Path | None) -> None:
        me = self.state.chars.get(self.state.player)
        meta = {
            "run_id": self.run_id,
            "seed": self.state.seed,
            "player_name": me.name if me is not None else "",
            "mock": self.mock,
            "content_hash": self.state.content_hash,
            "save_name": self.save_name,
            "loaded": self.loaded,
            "start_tick": self.state.tick,
            "start_turn": self.state.turn,
            "replay_of": str(replay_dir) if replay_dir is not None else None,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / META_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.llm.aclose()

    # ------------------------------------------------------------------ 小工具

    @property
    def player(self) -> Any:
        return self.state.chars[self.state.player]

    def _fx(self, effs: list[dict], *, source: str = "rule") -> list[Event]:
        """经内核的效果改状态（带回滚）。"""
        return resolve.run_effects(
            self.state, self.defs, effs, Ctx(self_id=self.state.player), source=source
        )

    def _collect(self, evs: Iterable[Event] | None = None) -> list[Event]:
        """把刚产生的事件收进本回合的事件表：调用方交回来的，加上事件尾巴里新出现的（按 id 去重）。"""
        new: dict[int, Event] = {}
        for ev in evs or ():
            if ev.id >= self._mark:
                new[ev.id] = ev
        for ev in reversed(self.state.events_tail):
            if ev.id < self._mark:
                break
            new.setdefault(ev.id, ev)
        self._mark = self.state.next_event_id
        out = [new[i] for i in sorted(new)]
        for ev in out:
            self._turn_events[ev.id] = ev
        return out

    def _events(self) -> list[Event]:
        return [self._turn_events[i] for i in sorted(self._turn_events)]

    def _danger(self) -> int:
        return danger.danger(self.state, self.defs)

    def _decide(self, state: WorldState, defs: Defs, cid: str) -> Any:
        """给 world 的回调：离场的人被问到话时怎样答复。"""
        return minds.decide_one(self.llm, state, defs, cid, offscreen=True)

    def _is_edge(self, act: ActionInst) -> bool:
        if act.verb not in TRAVEL_VERBS:
            return False
        e = actions.find_exit(self.state, self.defs, self.player, str(act.params.get("exit")))
        return e is not None and e.edge

    def _can_act(self) -> bool:
        return actions.can_act(self.player)

    def _leave_action(self, raw: str) -> ActionInst | None:
        """玩家说要走出这一带，而归类没能对上：眼前正好有一条通往图外的出口时，就按走那条路算（随后会问是否收笔）。"""
        if not LEAVE_RE.search(raw):
            return None
        edges = [e for e in actions.usable_exits(self.state, self.defs, self.player) if e.edge]
        if len(edges) != 1:
            return None
        return actions.build(self.state, self.defs, self.state.player, "go", {"exit": edges[0].to})

    # ------------------------------------------------------------------ 开场

    async def start(self) -> AsyncIterator[UIEvent]:
        """开场叙述与第一组选项。读档接着玩时，是此刻的所在与上回合留下的选项。"""
        async for ev in self._logged("", "start", self._start()):
            yield ev

    async def _start(self) -> AsyncIterator[UIEvent]:
        st, df = self.state, self.defs
        self._started = True
        if not self.mock and not self.llm.replay and not (self.llm.jev_available or self.llm.claude_available):
            yield Notice(TEXT_NO_KEYS, "warn")
        if st.ended is not None:
            yield Ended(st.ended)
            yield Notice(TEXT_ENDED)
            return
        fresh = st.turn == 0 and not st.pending_options and not self.loaded
        if fresh:
            # 开局那一刻条件已经成立的情境，在玩家眼前的就开场（8.1 第 2 步）；时间不动。
            self._collect(world_situations.check(st, df))
            self._collect(storyteller.threads.update(st, df, self._events()))
            self._collect(storyteller.chronicle.record(st, df, self._events()))
        else:
            self._restore_options()
        async for ev in self._present(reuse=not fresh and bool(self.options)):
            yield ev
        self._save()

    def _restore_options(self) -> None:
        """读档后：把存档里的选项还原出来；有哪个已经不合法，就整组重排。"""
        opts: list[storyteller.Option] = []
        texts: list[str] = []
        for d in self.state.pending_options:
            try:
                o = storyteller.Option.model_validate({k: v for k, v in d.items() if k not in ("text", "chosen")})
            except Exception:  # noqa: BLE001 — 旧档里的选项读不出来：重排
                opts = []
                break
            if actions.is_legal(self.state, self.defs, o.action) is None or not d.get("text"):
                opts = []
                break
            opts.append(o)
            texts.append(str(d["text"]))
        self.options = opts
        self.option_texts = texts if opts else []

    # ------------------------------------------------------------------ 一回合

    async def turn(self, raw: str) -> AsyncIterator[UIEvent]:
        """一回合（方案 4.2）。斜杠命令也走这里。"""
        text = (raw or "").strip()
        if not text:
            return
        self._record_input(text)
        async for ev in self._logged(text, "turn", self._dispatch(text)):
            yield ev

    async def _logged(self, raw: str, kind: str, agen: AsyncIterator[UIEvent]) -> AsyncIterator[UIEvent]:
        """包一层记账：界面事件原样交出去，同时记进 transcript、events.jsonl 与时延。"""
        st = self.state
        rec = TurnRecord(raw=raw, kind=kind, turn=st.turn, tick_from=st.tick, calls=(self.llm.recorder.count, 0))
        self._rec = rec
        self._t0 = time.perf_counter()
        self._turn_events = {}
        self._mark = st.next_event_id
        self._danger0 = self._danger()
        shown: list[UIEvent] = []
        ok = False
        try:
            async for ev in agen:
                if isinstance(ev, Text) and rec.first_text_s is None:
                    rec.first_text_s = time.perf_counter() - self._t0
                shown.append(ev)
                yield ev
            for ev in self._budget_events():
                shown.append(ev)
                yield ev
            ok = True
        finally:
            st = self.state  # /读档 之后是新的状态
            self._collect()
            rec.events = self._events()
            rec.turn = st.turn
            rec.tick_to = st.tick
            rec.total_s = time.perf_counter() - self._t0
            rec.calls = (rec.calls[0], self.llm.recorder.count)
            if not ok:
                self._stale = True
                rec.notes.append("aborted")
            self.last_turn = rec
            self.history.append(rec)
            if len(self.history) > 200:
                del self.history[:-200]
            if rec.kind in ("turn", "helpless") and rec.first_text_s is not None:
                self.latencies.append(rec.first_text_s)
            with contextlib.suppress(OSError):
                self._write_log(raw, rec, shown)

    def _write_log(self, raw: str, rec: TurnRecord, shown: list[UIEvent]) -> None:
        text = plain_text(shown)
        if rec.kind == "command":
            # 命令的输出进 transcript，不进 /回顾 的缓冲
            self.log.log_events(rec.events, turn=self.state.turn, state=self.state, defs=self.defs)
            self.log.log_input(raw)
            self.log.log_transcript(text if text.endswith("\n") or not text else text + "\n")
            return
        self.log.log_turn(self.state, rec.events, text, player_input=raw or None, defs=self.defs)

    def _record_input(self, raw: str) -> None:
        with contextlib.suppress(OSError):
            with (self.run_dir / INPUTS_FILE).open("a", encoding="utf-8") as f:
                f.write(raw.replace("\n", " ") + "\n")

    async def _dispatch(self, raw: str) -> AsyncIterator[UIEvent]:
        st = self.state
        assert self._rec is not None
        cmd = ui_commands.parse_command(raw)
        if cmd is not None:
            self._rec.kind = "command"
            async for ev in self._command(cmd[0], cmd[1]):
                yield ev
            return
        if not self._started:
            async for ev in self._start():
                yield ev
            return
        if st.ended is not None:
            self._rec.kind = "reply"
            yield Notice(TEXT_ENDED)
            return
        if self._budget_ask:
            # 到顶后的询问：不是 /继续，就改用规则与模板，这一句照常当作输入处理
            self._budget_ask = False
            self._use_rules_only()
            yield Notice(TEXT_BUDGET_RULES)
        if self._pending_leave is not None:
            act, speech = self._pending_leave
            self._pending_leave = None
            if raw.lower() in YES_WORDS:
                async for ev in self._play(act, speech, confirmed=True):
                    yield ev
                return
            yield Notice(TEXT_LEAVE_CANCEL)
            if raw.lower() in NO_WORDS:
                self._rec.kind = "confirm"
                yield Options(list(self.option_texts))
                return
        if self._stale:
            self._stale = False
            if self._option_index(raw) is not None:
                self._rec.kind = "reply"
                yield Notice(TEXT_STALE, "warn")
                async for ev in self._refresh_options():
                    yield ev
                return
        if not self._can_act():
            async for ev in self._helpless():
                yield ev
            return
        idx = self._option_index(raw)
        if idx is not None:
            if idx >= len(self.options):
                self._rec.kind = "reply"
                yield Notice(TEXT_NO_OPTION)
                return
            opt = self.options[idx]
            async for ev in self._play(opt.action, getattr(opt, "speech", None), option=opt, index=idx):
                yield ev
            return
        result = await parser.classify(self.llm, st, self.defs, raw)
        bound = parser.bind(st, self.defs, result)
        self._rec.notes.append(f"parse via {bound.via}: {bound.kind}")
        if bound.kind != "action" or bound.action is None:
            leave = None if getattr(bound, "absurd", False) else self._leave_action(raw)
            if leave is not None:
                async for ev in self._play(leave, None):
                    yield ev
                return
            async for ev in self._reply(bound):
                yield ev
            return
        async for ev in self._play(bound.action, bound.speech):
            yield ev

    @staticmethod
    def _option_index(raw: str) -> int | None:
        return OPTION_KEYS.get(raw.strip().rstrip("、.。)） ").strip())

    # ---- 不耗时的回话

    async def _reply(self, bound: Any) -> AsyncIterator[UIEvent]:
        """看不懂、做不到、问怎么玩、世上没有的东西：一句戏内的话，不耗时，原选项再给一遍（2.4、4.2）。"""
        assert self._rec is not None
        self._rec.kind = "reply"
        kind = "absurd" if getattr(bound, "absurd", False) else str(bound.kind)
        if kind == "meta":
            yield Notice(bound.reply or narrator.diegetic_reply("meta", salt=self.state.turn))
            yield Options(list(self.option_texts))
            return
        text = bound.reply or narrator.diegetic_reply(kind, salt=self.state.turn + len(self._rec.raw))
        async for ev in self._still(text):
            yield ev

    async def _still(self, text: str) -> AsyncIterator[UIEvent]:
        """局面不变的一屏：抬头、一句话、原来的选项。"""
        view = perception.player_view(self.state, self.defs, [])
        yield Header(view.clock, view.location)
        yield Text(text)
        yield Break()
        yield Options(list(self.option_texts))

    # ---- 动弹不得

    async def _helpless(self) -> AsyncIterator[UIEvent]:
        """玩家昏着、睡着：什么也做不了，时间照样过去（一次输入一个时辰，能动了就停）。"""
        assert self._rec is not None
        self._rec.kind = "helpless"
        st = self.state
        self._set_turn(st.turn + 1)
        pid = st.player
        pseudo = ActionInst(
            actor=pid, verb="wait", params={"ticks": HELPLESS_TICKS}, cost=HELPLESS_TICKS, desc="",
            signature=actions.signature("wait", {"ticks": HELPLESS_TICKS}),
        )
        async for ev in self._advance(pseudo, HELPLESS_TICKS, lead=TEXT_HELPLESS_LINE, stop_when_able=True):
            yield ev

    # ---- 结算一个动作

    async def _play(
        self,
        act: ActionInst,
        speech: Speech | None,
        *,
        option: storyteller.Option | None = None,
        index: int | None = None,
        confirmed: bool = False,
    ) -> AsyncIterator[UIEvent]:
        st, df = self.state, self.defs
        assert self._rec is not None
        if not confirmed and self._is_edge(act):
            # 走出地图之前先问一句（2.6）
            self._rec.kind = "confirm"
            self._pending_leave = (act, speech)
            yield Notice(TEXT_LEAVE_ASK, "warn")
            return
        fresh = actions.is_legal(st, df, act)
        cost = fresh.cost if fresh is not None else act.cost
        waits = self._wait_marks()
        # 2 结算玩家的动作
        evs = self._collect(resolve.apply(st, df, act, speech, source="player"))
        if not evs or all(e.outcome == "blocked" and e.verb != "warn_danger" for e in evs):
            self._rec.kind = "reply"
            async for ev in self._still(TEXT_CANNOT):
                yield ev
            return
        if any(e.verb == "warn_danger" for e in evs):
            # 2.4、6.10：先警示，局面不变；玩家坚持再做，才真正结算
            self._rec.kind = "warn"
            self._danger_checkpoint(st)
            text = narrator.warning_text(st, df, fresh or act)
            async for ev in self._still(text):
                yield ev
            self._save()
            return
        self._set_turn(st.turn + 1)
        if option is not None:
            storyteller.note_choice(st, df, option)
            self._mark_chosen(index)
        else:
            storyteller.note_action(st, df, fresh or act, speech)  # 只说了一句话的：记的是那句话
            self._mark_chosen(None)
        # 3 在场的人各自反应
        await self._micro_rounds(evs, int(self.settings.limits.max_micro_rounds))
        self._add_credit(waits)
        async for ev in self._advance(act, cost, speech=speech):
            yield ev

    async def _advance(
        self,
        act: ActionInst,
        ticks: int,
        *,
        speech: Speech | None = None,
        lead: str = "",
        stop_when_able: bool = False,
        interruptible: bool = True,
    ) -> AsyncIterator[UIEvent]:
        """玩家的动作结算之后的后半回合：时间、世界、讲述者、叙述与选项、收尾。"""
        st, df = self.state, self.defs
        assert self._rec is not None
        tick0 = st.tick
        day0 = timeflow.day_of(st.tick)
        danger0 = self._danger0
        warned0 = set(st.warned)
        # 4 时间推进；世界在别处继续走
        elapsed, interrupted = await self._pass_time(
            act, ticks, stop_when_able=stop_when_able, interruptible=interruptible
        )
        self._rec.interrupted = interrupted
        world.end_of_turn(st, df)
        if st.ended is None and st.replan_queue:
            self._collect(await world.process_queue(self.llm, st, df))
        # 5 讲述者：牵挂、年谱、节奏与机会牌
        self._collect(storyteller.threads.update(st, df, self._events()))
        self._collect(storyteller.chronicle.record(st, df, self._events()))
        self._collect(storyteller.record_turn(st, df, self._events()))
        if st.ended is None:
            card = self._collect(await storyteller.pace(self.llm, st, df))
            if card:
                self._rec.notes.append("card")
                self._collect(storyteller.threads.update(st, df, card))
                self._collect(storyteller.chronicle.record(st, df, card))
        # 大险之前的检查点：存的是这一回合开始之前的样子（上一回合末的自动存档）
        new_buffer = any(w.startswith("kill:") for w in set(st.warned) - warned0)
        if (self._danger() >= 3 > danger0) or new_buffer or st.ended == "dead":
            self._checkpoint_before_danger()
        # 玩家视角 → 叙述与选项
        async for ev in self._present(act=act, speech=speech, elapsed=elapsed, lead=lead):
            yield ev
        # 6 收尾：存档、检查点；该反思的夜里反思
        self._save()
        rested = act.verb == "rest" and st.tick - tick0 >= REST_REFLECT
        if st.ended is None and (rested or timeflow.day_of(st.tick) != day0):
            self._collect(await minds.reflect.nightly(self.llm, st, df))
            self._rec.notes.append("reflect")
            self._save(checkpoint=False)
        if st.ended is not None:
            async for ev in self._ending():
                yield ev

    # ---- 微轮

    async def _micro_rounds(self, seed: list[Event], rounds: int) -> list[Event]:
        """在场且与本轮事件有关的人各自抉择（并行），再按先后逐个结算（4.3）。至多 ``rounds`` 轮。"""
        st, df = self.state, self.defs
        limit = int(self.settings.limits.max_reactors)
        out: list[Event] = []
        last = list(seed)
        for _ in range(max(0, rounds)):
            if st.ended is not None:
                break
            actors = minds.pick_reactors(st, df, last, limit=limit)
            if not actors:
                break
            decisions = await minds.decide_all(self.llm, st, df, actors, events=last)
            new: list[Event] = []
            for d in minds.order(st, decisions, last):
                if st.ended is not None:
                    break
                new += self._collect(minds.apply_decision(st, df, d))
            if not new:
                break
            out += new
            last = new
        return out

    def _wait_marks(self) -> dict[str, tuple[str, int]]:
        """在场者当前这一步「等」已经等了多久（微轮之前拍一张）。"""
        out: dict[str, tuple[str, int]] = {}
        loc = self.player.loc
        for c in self.state.chars.values():
            if c.id == self.state.player or c.loc != loc or c.agenda is None:
                continue
            step = world_onscene.waiting_step(self.state, self.defs, c)
            if step is not None:  # 只有「就在这里等」的一步才由 onscene_elapsed 补时间，也只有它要挂账
                out[c.id] = (step.id, c.agenda.progress)
        return out

    def _add_credit(self, before: dict[str, tuple[str, int]]) -> None:
        """微轮里已经给谁的这一步记过几刻：记在账上，补时间时扣掉，免得一刻记两回。

        紧张的场面里动作不耗时（2.5），微轮照样一轮记一刻：这些也挂在账上，等时间真的过去时再抵。
        """
        for cid, (step_id, prog) in before.items():
            c = self.state.chars.get(cid)
            if c is None or c.agenda is None:
                continue
            step = danger.current_step(c)
            if step is not None and step.id == step_id and c.agenda.progress > prog:
                self._credit[cid] = self._credit.get(cid, 0) + c.agenda.progress - prog

    # ---- 时间

    async def _pass_time(
        self,
        act: ActionInst,
        ticks: int,
        *,
        stop_when_able: bool = False,
        interruptible: bool = True,
    ) -> tuple[int, bool]:
        """推进 ``ticks`` 刻：逐个时辰边界走，每跨一个时辰做一次 ``world.tick``（6.9、8.1、8.7）。

        长动作（等、歇、读、练）期间，在场的人每个时辰照常抉择一轮；有动静传到玩家耳目、险情加重、
        眼前有情境开场，就在那一刻打断。赶路只在后两种情形停下。等、歇、读、练满一个时辰以上时，
        旁人之间的闲话与小动作不算动静（``_breaks_quiet``）。返回（实际过去的刻数，是否被打断）。
        """
        st, df = self.state, self.defs
        elapsed = 0
        interrupted = False
        long = act.verb in LONG_VERBS
        quiet = act.verb in QUIET_LONG_VERBS and ticks >= TICKS_PER_HOUR
        danger0 = self._danger()
        sits0 = self._situations_here()
        while elapsed < ticks and st.ended is None and not interrupted:
            span = min(ticks - elapsed, TICKS_PER_HOUR - st.tick % TICKS_PER_HOUR)
            done = 0
            evs: list[Event] = []
            for _ in range(span):
                new = self._collect(timeflow.advance_time(st, df, 1))
                done += 1
                evs += new
                if st.ended is not None or (stop_when_able and self._can_act()):
                    break
                # 到点的事（scheduled、毒发的征兆）一出现就看要不要打断，不等到这个时辰走完
                if (new and interruptible and elapsed + done < ticks
                        and self._interrupts(act, new, danger0, sits0, quiet=quiet)):
                    interrupted = True
                    break
            elapsed += done
            credited, self._credit = self._credit, {}
            evs += self._collect(world.onscene_elapsed(st, df, done, credited=credited))
            for cid, n in credited.items():  # 这一段没抵完的，留到下一段
                if n > done:
                    self._credit[cid] = n - done
            if st.ended is not None or interrupted:
                break
            if st.tick % TICKS_PER_HOUR == 0:
                hour = self._collect(await world.tick(self.llm, st, df, decide=self._decide))
                if long and elapsed < ticks and st.ended is None:
                    waits = self._wait_marks()
                    hour += await self._micro_rounds(evs + hour, 1)  # 玩家忙着，在场的人照常过他们的日子
                    self._add_credit(waits)
                if interruptible and elapsed < ticks and self._interrupts(act, hour, danger0, sits0, quiet=quiet):
                    interrupted = True
            if stop_when_able and self._can_act():
                break
        if interrupted and st.ended is None and has_status(self.player, "asleep"):
            pid = st.player
            self._collect(
                self._fx(
                    [
                        {"remove_status": [pid, "asleep"]},
                        {"emit": {"actor": pid, "verb": "wake", "text": TEXT_WAKE.format(me=pid)}},
                    ]
                )
            )
        return elapsed, interrupted

    def _situations_here(self) -> set[str]:
        loc = self.player.loc
        return {s.id for s in self.state.situations.values() if s.loc == loc}

    def _interrupts(
        self, act: ActionInst, evs: list[Event], danger0: int, sits0: set[str], *, quiet: bool = False
    ) -> bool:
        """这几件新出的事要不要打断正在做的长动作。``quiet``：静下来做满一个时辰以上的事（等、歇、读、练）。"""
        st = self.state
        pid = st.player
        me = self.player
        if not perception.on_map(me):
            return False
        if self._danger() > danger0:
            return True
        if self._situations_here() - sits0:
            return True
        if act.verb in TRAVEL_VERBS:
            return False
        asleep = not perception.conscious(me)
        for ev in evs:
            if ev.actor == pid or ev.outcome == "blocked":
                continue
            if asleep:
                # 睡着的人看不见：只有近旁的巨响，或冲着他来的事，才惊得醒
                to_me = ev.params.get("target") == pid or (ev.speech is not None and pid in ev.speech.to)
                if ev.loc == me.loc and (ev.loud or to_me):
                    return True
                continue
            if ev.witnesses.get(pid) is None:
                continue
            if ev.verb in QUIET_VERBS and ev.speech is None:
                continue
            if quiet and not self._breaks_quiet(ev):
                continue
            return True
        return False

    def _breaks_quiet(self, ev: Event) -> bool:
        """玩家静下来等、歇、读、练（一个时辰以上）时，别人做的这件事算不算该停下来看的动静。

        算：巨响；有人来了、走了；冲着玩家来的事（动作的对象是他、话是对他说的）；眼前有人死伤、被擒、中毒；
        不是哪个人做的事（到点的事、身上的征兆）。不算：旁人之间的闲话、对满屋子人说的寻常话、旁人之间的小动作——
        这些照样进这一段的叙述，只是不为它停下。险情加重、情境开场在 ``_interrupts`` 里先看过了。
        """
        pid = self.state.player
        if ev.actor is None or ev.loud or ev.verb in MOVE_VERBS:
            return True
        if any(k.get("k") in GRAVE_KINDS for k in ev.kinds):
            return True
        sp = ev.speech
        if sp is None:
            return pid in (ev.params.get("target"), ev.params.get("target2")) or any(
                pid in (k.get("who"), k.get("by")) for k in ev.kinds
            )
        if pid not in sp.to:
            return False
        if len(sp.to) == 1 or sp.act in POINTED_ACTS:
            return True
        req = self.state.requests.get(sp.request) if sp.request else None
        return req is not None and pid in (req.frm, req.to)

    def _checkpoint_before_danger(self) -> None:
        """性命之忧之前的检查点（2.6、8.7）：取上一回合末的自动存档，也就是这一回合开始之前的世界。"""
        try:
            prev = store.load_state(self.save_dir, content_hash=None)
        except (store.SaveError, OSError) as e:
            log_violation(self.state, f"checkpoint before danger failed: {e}")
            return
        self._danger_checkpoint(prev)

    def _danger_checkpoint(self, state: WorldState) -> None:
        """存一个「大险之前」的检查点。这一类至多留 ``MAX_DANGER_CHECKPOINTS`` 个：

        检查点总共只留最近 10 个（8.7）。死了读档、再死、再读档，每次都会多出一个几乎一样的；
        不设上限的话，几次重来就把前几日「一日之始」的检查点全挤掉，想退回几天前另寻出路也不能了。
        """
        try:
            cps = self.checkpoints.list()
            if cps and cps[0].label == DANGER_LABEL and cps[0].tick == state.tick:
                return  # 同一刻已经存过
            old = [c for c in cps if c.label == DANGER_LABEL][MAX_DANGER_CHECKPOINTS - 1 :]
            for c in old:
                c.path.unlink(missing_ok=True)
            self.checkpoints.before_danger(state)
        except (store.SaveError, OSError) as e:
            log_violation(self.state, f"checkpoint before danger failed: {e}")

    def _drop_checkpoints_after(self, tick: int) -> None:
        """从检查点重来之后：比它晚的检查点属于放弃了的那条路，删掉；此后每日之始照常另存新的。"""
        with contextlib.suppress(OSError):
            for c in self.checkpoints.list():
                if c.tick > tick:
                    c.path.unlink(missing_ok=True)
        self.checkpoints = store.CheckpointManager(self.save_dir)

    # ---- 记账（都经效果）

    def _set_turn(self, n: int) -> None:
        self._fx([{"turn": int(n)}])

    def _mark_chosen(self, index: int | None) -> None:
        """上回合摆出的选项里，玩家选了哪一条（叙述模块据此不重复它的文字）。"""
        pend = [dict(d) for d in self.state.pending_options]
        for i, d in enumerate(pend):
            d["chosen"] = i == index
        self._fx([{"offer": pend}])

    def _offer(self, opts: list[storyteller.Option], texts: list[str]) -> None:
        pend: list[dict] = []
        for o, t in zip(opts, texts, strict=True):
            d = json.loads(o.model_dump_json())
            d["text"] = t
            pend.append(d)
        self._fx([{"offer": pend}])
        self.options = list(opts)
        self.option_texts = list(texts)

    # ------------------------------------------------------------------ 叙述与选项

    async def _make_options(self) -> tuple[list[storyteller.Option], list[str]]:
        st, df = self.state, self.defs
        opts = list(await storyteller.pick_options(self.llm, st, df))
        if not opts:
            return [], []
        texts = await narrator.phrase_options(self.llm, st, df, opts)
        return opts, list(texts)

    def _view(self, act: ActionInst | None, elapsed: int) -> PlayerView:
        """玩家视图（4.4），再做两件排版上的事：旁人原地等着不算一件事；赶路用一句话带过（8.7）。"""
        st, df = self.state, self.defs
        pid = st.player
        events = [
            ev for ev in self._events() if not (ev.verb == "wait" and ev.actor != pid and ev.speech is None)
        ]
        view = perception.player_view(st, df, events)
        if st.ended == "left" and not perception.on_map(self.player):
            # 人已经走出这一带：身后的地方与留在那里的人不再写（2.6）
            return view.model_copy(update={"location_desc": "", "present": [], "items_here": [], "heard": []})
        if act is None or act.verb not in TRAVEL_VERBS:
            return view
        mine = [e for e in view.events if e.by_player]
        arrive = next((e for e in mine if e.verb == "arrive"), None)
        depart = next((e for e in mine if e.verb == act.verb and e.outcome == "ok"), None)
        if arrive is None or depart is None:
            return view
        kept = []
        for e in view.events:
            if e is depart:
                continue  # 「往某处去了」与「来到某处」说的是一回事
            if e is arrive and elapsed >= TRAVEL_SUMMARY_MIN and act.verb == "go":
                e = e.model_copy(update={"text": narrator.travel_summary(elapsed, view.location).rstrip("。")})
            kept.append(e)
        return view.model_copy(update={"events": kept})

    async def _present(
        self,
        *,
        act: ActionInst | None = None,
        speech: Speech | None = None,
        elapsed: int = 0,
        lead: str = "",
        reuse: bool = False,
    ) -> AsyncIterator[UIEvent]:
        """抬头、叙述（流式）、选项。叙述与选项的措辞并行（方案 4.2 第 5 步、10.6）。"""
        st, df = self.state, self.defs
        view = self._view(act, elapsed)
        self.last_view = view
        yield Header(view.clock, view.location)
        ended = st.ended is not None
        task: asyncio.Task[tuple[list[storyteller.Option], list[str]]] | None = None
        if not ended and not reuse and self._can_act():
            task = asyncio.create_task(self._make_options())
        report = narrator.NarrationReport()
        spoken = speech.spoken_text if speech is not None else None
        climax = ended or view.danger >= 3 or any(ev.salience >= 9 for ev in self._events())
        try:
            if lead:
                yield Text(lead)
            async for piece in narrator.narrate(
                self.llm, st, df, view, spoken_text=spoken, prev_tail=self._prev_tail, climax=climax,
                report=report,
            ):
                if piece:
                    yield Text(piece)
        except BaseException:
            if task is not None:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
            raise
        self.last_report = report
        self._prev_tail = (report.text or "")[-PREV_TAIL:]
        yield Break()
        if ended:
            self._offer([], [])
            return
        if task is not None:
            try:
                opts, texts = await task
            except Exception as e:  # noqa: BLE001 — 选项出错不让回合断掉：记一笔，给空选项，玩家仍可自由输入
                log_violation(st, f"options failed: {type(e).__name__}: {e}")
                opts, texts = [], []
            self._offer(opts, texts)
        elif not reuse:
            self._offer([], [])
        if not self._can_act():
            yield Notice(TEXT_HELPLESS)
            return
        yield Options(list(self.option_texts))

    async def _refresh_options(self) -> AsyncIterator[UIEvent]:
        """不过时间，只把选项重排一遍（作弊命令、出岔子之后）。"""
        if self.state.ended is not None or not self._can_act():
            self._offer([], [])
            return
        opts, texts = await self._make_options()
        self._offer(opts, texts)
        yield Options(list(self.option_texts))

    # ------------------------------------------------------------------ 收尾与收场

    def _save(self, *, checkpoint: bool = True) -> None:
        """自动存档（每回合覆盖）；每个游戏日开始时另存一个检查点（10.5、8.7）。"""
        try:
            store.save_state(self.state, self.save_dir)
            if checkpoint and self.state.ended is None:
                self.checkpoints.maybe_day_start(self.state)
        except (OSError, store.SaveError) as e:
            log_violation(self.state, f"autosave failed: {e}")

    async def _ending(self) -> AsyncIterator[UIEvent]:
        """收场（2.6、8.6）：后事由 C7 写（出错退到模板），分岔清单由规则列出。"""
        st, df = self.state, self.defs
        assert st.ended is not None
        yield Ended(st.ended)
        async for piece in storyteller.epilogue(self.llm, st, df):
            if piece:
                yield Text(piece)
        yield Break()
        yield Notice(TEXT_ENDED)
        self._save(checkpoint=False)

    # ---- 预算（10.3）

    def _budget_events(self) -> list[UIEvent]:
        out: list[UIEvent] = []
        while self._budget_notes:
            out.append(Notice(self._budget_notes.pop(0), "warn"))
        led = self.llm.ledger
        if not self._rules_mode and not self._budget_ask and led.budget_usd > 0 and led.spent >= led.budget_usd:
            self._budget_ask = True
            out.append(Notice(TEXT_BUDGET_ASK.format(spent=led.spent, budget=led.budget_usd), "warn"))
        return out

    def _use_rules_only(self) -> None:
        """这一局余下的部分不再调用模型：全部路由改成 rules_only（叙述与选项用模板）。"""
        s = self.llm.settings.model_copy(deep=True)
        s.root = self.llm.settings.root
        cats = set(type(s.route).model_fields) | set(s.route.model_extra or {}) | {"narrate", "phrase"}
        for cat in sorted(cats):
            setattr(s.route, cat, "rules_only")
        self.llm.settings = s
        self.settings = s
        self._rules_mode = True

    # ------------------------------------------------------------------ 斜杠命令（2.7）

    async def _command(self, name: str, arg: str) -> AsyncIterator[UIEvent]:
        st, df = self.state, self.defs
        if name == "继续":
            led = self.llm.ledger
            if self._budget_ask:
                self._budget_ask = False
                led.extend(self._base_budget)
                yield Notice(TEXT_BUDGET_MORE.format(budget=led.budget_usd))
            else:
                yield Notice("眼下没有什么要继续的。")
            return
        spec = ui_commands.COMMANDS.get(name)
        if spec is None:
            yield Notice(ui_commands.unknown_command_text(name))
            return
        if spec.kind == "view":
            yield Notice(
                ui_commands.render_view(name, st, df, arg=arg, turns=self.log.turns, debug=self.debug)
            )
        elif name == "牵挂":
            yield Notice(storyteller.render_threads(st, df))
        elif name == "年谱":
            yield Notice(storyteller.render_chronicle(st, df, final=st.ended is not None))
        elif name == "心愿":
            async for ev in self._cmd_wish(arg):
                yield ev
        elif name == "存档":
            yield self._cmd_save(arg)
        elif name == "读档":
            async for ev in self._cmd_load(arg):
                yield ev
        elif name == "收笔":
            if st.ended is not None:
                yield Notice(TEXT_ENDED)
                return
            self._pending_leave = None
            self._collect(self._fx([{"end": "closed"}], source="player"))
            self._collect(storyteller.chronicle.record(st, df, self._events()))
            async for ev in self._ending():
                yield ev
        elif name == "退出":
            yield Notice("要离开，直接关掉终端，或在界面里输入 /退出。")
        elif name == "debug":
            if not self.debug:
                yield Notice("/debug 只在带 --debug 启动时可用。")
                return
            async for ev in self._cmd_debug(arg):
                yield ev
        else:
            yield Notice(ui_commands.unknown_command_text(name))

    async def _cmd_wish(self, arg: str) -> AsyncIterator[UIEvent]:
        st, df = self.state, self.defs
        wishes = [t for t in st.threads.values() if t.kind == "wish" and not t.closed]
        text = arg.strip()
        if st.ended is not None:
            yield Notice(TEXT_ENDED)
            return
        if not text:
            lines = ["用法：/心愿 你想要的事；/心愿 撤销 [第几条]。"]
            lines += [f"  {i + 1}. {refs.render(t.text, st, df, st.player)}" for i, t in enumerate(wishes)]
            yield Notice("\n".join(lines))
            return
        if text.startswith("撤销"):
            rest = text[2:].strip()
            if not wishes:
                yield Notice("你还没有写下过心愿。")
                return
            try:
                idx = int(rest) - 1 if rest else len(wishes) - 1
            except ValueError:
                idx = -1
            if not 0 <= idx < len(wishes):
                yield Notice("没有这一条心愿。输入 /心愿 看看有哪几条。")
                return
            t = wishes[idx]
            self._collect(storyteller.remove_wish(st, df, t.id))
            self._save(checkpoint=False)
            yield Notice(f"心愿已撤销：{refs.render(t.text, st, df, st.player)}")
            return
        thread = await parser.map_wish(self.llm, st, df, text)
        self._collect(storyteller.add_wish(st, df, thread))
        self._save(checkpoint=False)
        yield Notice(f"记下了你的心愿：{thread.get('text', text)}")

    def _cmd_save(self, arg: str) -> UIEvent:
        name = arg.strip()
        if not name:
            return Notice("用法：/存档 名字。" + self._saves_text())
        try:
            store.save_state(self.state, self.save_dir)
            if store.save_dir(self.saves_root, name).name != self.save_name:
                store.copy_save(self.save_name, name, root=self.saves_root)
        except (store.SaveError, OSError) as e:
            return Notice(str(e), "warn")
        return Notice(f"已存档：{name}（{timeflow.clock_text(self.state.tick)}）。")

    def _saves_text(self) -> str:
        rows = [i for i in store.list_saves(self.saves_root) if i.ok]
        lines = []
        if rows:
            lines.append("\n已有的存档：")
            lines += [f"  {i.name}（{i.clock}，第 {i.turn} 回合{'，已收场' if i.ended else ''}）" for i in rows[:12]]
        cps = self.checkpoints.list()
        if cps:
            lines.append("这一局的检查点（/读档 检查点 [序号]）：")
            lines += [f"  {c.seq}. {c.clock}（{'大险之前' if c.label == 'danger' else '一日之始'}）" for c in cps]
        return "\n".join(lines)

    async def _cmd_load(self, arg: str) -> AsyncIterator[UIEvent]:
        name = arg.strip()
        if not name:
            yield Notice("用法：/读档 名字；/读档 检查点 [序号]。" + self._saves_text())
            return
        try:
            if name.startswith("检查点"):
                key = name[3:].strip() or None
                state = self.checkpoints.restore(key, content_hash=self.defs.content_hash)
                self._drop_checkpoints_after(state.tick)
            else:
                src = store.save_dir(self.saves_root, name)
                state = store.load_state(src, content_hash=self.defs.content_hash)
                if src.name != self.save_name:
                    store.copy_save(name, self.save_name, root=self.saves_root)  # 原档不动，接着玩的是副本
                    self.checkpoints = store.CheckpointManager(self.save_dir)
        except (store.SaveError, OSError) as e:
            yield Notice(str(e), "warn")
            return
        self.state = state
        self.loaded = True
        self.log.reset_seen()
        self._mark = state.next_event_id
        self._turn_events = {}
        self._pending_leave = None
        self._credit = {}
        self._prev_tail = ""
        self._stale = False
        self._started = True
        yield Notice(f"已读档：{name}（{timeflow.clock_text(state.tick)}）。")
        if state.ended is not None:
            yield Ended(state.ended)
            yield Notice(TEXT_ENDED)
        else:
            self._restore_options()
            async for ev in self._present(reuse=bool(self.options)):
                yield ev
        self._save(checkpoint=False)

    # ---- /debug（10.6：只在 --debug 下可用，作弊都记进日志）

    async def _cmd_debug(self, arg: str) -> AsyncIterator[UIEvent]:
        st, df = self.state, self.defs
        parts = arg.split()
        if not parts:
            yield Notice(self._debug_summary())
            return
        sub, rest = parts[0].lower(), parts[1:]
        pid = st.player
        n = 0
        effs: list[dict] = []
        try:
            if sub == "kill":
                effs = [{"kill": rest[0]}]
            elif sub == "give":
                effs = [{"spawn": [rest[0], rest[1] if len(rest) > 1 else pid]}]
            elif sub == "art":
                effs = [{"art": [pid, rest[0], int(rest[1])]}]
            elif sub == "stat":
                if rest[0] not in STAT_FIELDS:
                    raise ValueError(rest[0])
                effs = [{"stat": [pid, rest[0], int(rest[1]) - int(getattr(self.player, rest[0]))]}]
            elif sub == "role":
                if rest[1] not in ("add", "remove"):
                    raise ValueError(rest[1])
                effs = [{"role": [pid, rest[0], rest[1]]}]
            elif sub == "goto":
                effs = [{"move": [pid, rest[0]]}]
            elif sub == "tick":
                n = int(rest[0])
                if n <= 0:
                    raise ValueError(n)
            else:
                raise ValueError(sub)
        except (IndexError, ValueError):
            yield Notice(DEBUG_USAGE)
            return
        if st.ended is not None:
            yield Notice(TEXT_ENDED)
            return
        log_violation(st, f"debug cheat: /debug {arg.strip()}")
        if sub == "tick":
            pseudo = ActionInst(
                actor=pid, verb="wait", params={"ticks": n}, cost=n, desc="",
                signature=actions.signature("wait", {"ticks": n}),
            )
            yield Notice(f"【调试】时间拨过 {n} 刻。")
            self._danger0 = self._danger()
            async for ev in self._advance(pseudo, n, interruptible=False):
                yield ev
            return
        before = len(st.violations)
        evs = self._collect(self._fx(effs))
        failed = [v for v in st.violations[before:] if "rollback" in v or "skipped" in v]
        if failed:
            yield Notice("【调试】没有生效：" + "；".join(failed), "warn")
            return
        self._collect(storyteller.threads.update(st, df, evs))
        self._collect(storyteller.chronicle.record(st, df, evs))
        lines = [f"【调试】已生效（记入日志）：/debug {arg.strip()}"]
        lines += [f"  {refs.render(ev.fact_text, st, df)}" for ev in evs]
        yield Notice("\n".join(lines))
        if st.ended is not None:
            self._save(checkpoint=False)
            async for ev in self._ending():
                yield ev
            return
        async for ev in self._refresh_options():
            yield ev
        self._save(checkpoint=False)

    def _debug_summary(self) -> str:
        st = self.state
        me = self.player
        lines = [
            "【调试】",
            f"时刻 {timeflow.clock_text(st.tick)}（刻 {st.tick}）；回合 {st.turn}；种子 {st.seed}；随机数用了 {st.rng_counter} 次",
            f"所在 {me.loc}；场面 {st.scene_mode}（步数 {st.step}）；danger {self._danger()}；收场 {st.ended}",
            f"局名 {self.save_name}；日志 {self.run_dir}",
        ]
        for s in st.situations.values():
            roles = " ".join(f"{k}={v}" for k, v in s.roles.items())
            lines.append(f"情境 {s.id}（{s.loc}，第 {s.turns} 回合）{roles}")
        for c in perception.present(st, me.loc, exclude=me.id):
            step = danger.current_step(c)
            what = f"{step.id}：{step.text}（{c.agenda.progress}/{step.takes}）" if step and c.agenda else "—"
            flags = ",".join(s.kind for s in c.statuses)
            lines.append(f"在场 {c.id}[{c.tier}]{'{' + flags + '}' if flags else ''} {what}")
        if st.replan_queue:
            lines.append("待重新打算 " + "、".join(st.replan_queue))
        open_threads = [t.id for t in st.threads.values() if not t.closed]
        lines.append(f"牵挂 {len(open_threads)} 条：" + "、".join(open_threads[:8]))
        if self.options:
            lines.append("选项 " + "；".join(f"{o.tag} {o.action.signature}" for o in self.options))
        for v in st.violations[-5:]:
            lines.append(f"日志 {v}")
        lines += self._calls_summary()
        return "\n".join(lines)

    def _calls_summary(self) -> list[str]:
        """上一回合的模型调用（calls.jsonl 里的 seq 范围）。"""
        rec = next((r for r in reversed(self.history) if r.kind != "command"), None)
        led = self.llm.ledger
        tail = f"本局花费 ${led.spent:.4f} / ${led.budget_usd:.2f}"
        if rec is None:
            return [tail]
        lo, hi = rec.calls
        rows: list[dict[str, Any]] = []
        path = self.run_dir / CALLS_FILE
        with contextlib.suppress(OSError, ValueError):
            rows = [r for r in read_calls(path) if lo < int(r.get("seq", 0)) <= hi]
        kinds = Counter(str(r.get("kind")) for r in rows)
        ms = sum(float(r.get("latency_ms") or 0.0) for r in rows)
        cost = sum(float(r.get("cost") or 0.0) for r in rows)
        esc = sum(1 for r in rows if r.get("escalated"))
        err = sum(1 for r in rows if r.get("error"))
        first = f"{rec.first_text_s:.2f}s" if rec.first_text_s is not None else "—"
        head = (
            f"上一回合（第 {rec.turn} 回合）模型调用 {hi - lo} 次：" + " ".join(f"{k}×{n}" for k, n in sorted(kinds.items()))
        )
        return [
            head,
            f"  共 {ms:.0f}ms，${cost:.4f}，升级 {esc} 次，出错 {err} 次；首字 {first}；{'；'.join(rec.notes)}",
            tail,
        ]


__all__ = ["Game", "TurnRecord", "INPUTS_FILE", "META_FILE"]
