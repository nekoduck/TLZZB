"""验收测试与自对弈测试共用的替身：记下每一次提示的 Jev / Claude、离线配置、确定的真人替身客户端。"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tianlong.config import Settings
from tianlong.llm import LLM, MockClaude, MockJev, make_llm
from tianlong.llm.jev import Answers, validate_response
from werewolf.llm.config import WerewolfSettings, load
from werewolf.llm.gateway import Gateway
from werewolf.protocol import Ack, ActionMsg, EventMsg, RequestMsg, ServerMsg, SessionState

REPO = Path(__file__).resolve().parents[3]
KEY_ENVS = ('JEV_API_KEY', 'JEV_KEY', 'ANTHROPIC_API_KEY', 'CLAUDE_KEY', 'CLAUDE_API_KEY')


def offline_cfg(monkeypatch: Any, tmp_path: Path, *, fake_keys: bool = False) -> tuple[Settings, WerewolfSettings]:
    """仓库的 werewolf.toml 拷到临时目录（不读 .env）；密钥清空（或换成假的）。"""
    for k in KEY_ENVS:
        monkeypatch.delenv(k, raising=False)
    if fake_keys:
        monkeypatch.setenv('JEV_KEY', 'test-jev-key')
        monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-anthropic-key')
    shutil.copy(REPO / 'werewolf.toml', tmp_path / 'werewolf.toml')
    return load(tmp_path / 'werewolf.toml')


@dataclass
class Call:
    provider: str  # 'jev' | 'claude'
    kind: str
    text: str  # Jev：state + 问题；Claude：system 块 + user
    user: str = ''
    system: list[str] = field(default_factory=list)


class RecordingJev(MockJev):
    """Jev 替身：记下 (kind, state, questions)；`fn` 给答案（缺省用 tianlong 的替身答案）。"""

    def __init__(self, *args: Any, log: list[Call], fn: Callable[..., dict[str, Any]] | None = None,
                 **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.log = log
        self.fn = fn

    async def ask(self, kind: str, state: Any, questions: Mapping[str, Mapping[str, Any]], *,
                  hints: Mapping[str, Any] | None = None, escalated: bool = False) -> Answers:
        self.log.append(Call('jev', kind, f'{state}\n{questions}'))
        if self.fn is None:
            return await super().ask(kind, state, questions, hints=hints, escalated=escalated)
        raw = self.fn(kind, state, questions)
        spec = self.spec(kind)
        data = {'model': spec.model, 'answers': raw, 'usage': {'input_tokens': 0, 'output_tokens': 0}}
        return validate_response(data, questions)


class RecordingClaude(MockClaude):
    """Claude 替身：记下 system 与 user；`fn(kind, system, user)` 给出要吐的文字（缺省空串）。"""

    def __init__(self, *args: Any, log: list[Call], fn: Callable[[str, list[str], str], str] | None = None,
                 **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.log = log
        self.fn = fn

    async def stream_text(self, kind: str, system_blocks: Any, user_text: str, **kw: Any) -> AsyncIterator[str]:
        system = [b if isinstance(b, str) else str(b) for b in (system_blocks or [])] \
            if not isinstance(system_blocks, str) else [system_blocks]
        self.log.append(Call('claude', kind, '\n'.join(system) + '\n' + user_text, user=user_text, system=system))
        text = self.fn(kind, system, user_text) if self.fn is not None else str(kw.get('mock_text') or '')
        kw.pop('mock_text', None)
        async for piece in super().stream_text(kind, system_blocks, user_text, mock_text=text, **kw):
            yield piece


def recording_gateway(cfg: tuple[Settings, WerewolfSettings], *, jev_fn: Callable[..., dict[str, Any]] | None = None,
                      claude_fn: Callable[[str, list[str], str], str] | None = None,
                      ww: WerewolfSettings | None = None) -> tuple[Gateway, list[Call]]:
    settings, ww0 = cfg
    log: list[Call] = []
    llm: LLM = make_llm(settings, mock=True, run_dir=None)
    llm.jev = RecordingJev(settings, recorder=llm.recorder, ledger=llm.ledger, log=log, fn=jev_fn)
    llm.claude = RecordingClaude(settings, recorder=llm.recorder, ledger=llm.ledger, log=log, fn=claude_fn)
    return Gateway(settings, ww or ww0, offline=False, llm=llm), log


class AutoClient:
    """确定的真人替身：发言按 `say(client, request)` 给的文字（缺省「过」），不预览；选择类请求按固定规则作答
    （能弃票就弃票，否则第一个选项）；收到要 ack 的事件就 ack。"""

    def __init__(self, say: Callable[[AutoClient, RequestMsg], str] | None = None,
                 pick: Callable[[RequestMsg], str] | None = None) -> None:
        self.say = say or (lambda c, r: '过')
        self.day = 0  # 最近一条事件的天数
        self.pick = pick
        self.msgs: list[ServerMsg] = []
        self.session: Any = None
        self.tasks: set[asyncio.Task[Any]] = set()
        self.answered: set[str] = set()
        self.requests: list[RequestMsg] = []

    def _spawn(self, coro: Any) -> None:
        t = asyncio.ensure_future(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    async def _to_server(self, msg: Any) -> None:
        await asyncio.sleep(0)
        await self.session.on_client(msg)

    async def send(self, msg: ServerMsg) -> None:
        self.msgs.append(msg)
        if isinstance(msg, EventMsg):
            self.day = msg.ev.day
        if isinstance(msg, EventMsg) and msg.wait_ack:
            self._spawn(self._to_server(Ack(seq=msg.seq)))
        req = msg if isinstance(msg, RequestMsg) else (msg.open_request if isinstance(msg, SessionState) else None)
        if req is not None and req.id not in self.answered:
            self.answered.add(req.id)
            self.requests.append(req)
            self._spawn(self._answer(req))

    async def _answer(self, req: RequestMsg) -> None:
        if req.kind == 'speak':
            await self._to_server(ActionMsg(request_id=req.id, text=self.say(self, req)))
            return
        if self.pick is not None:
            opt = self.pick(req)
        else:
            ids = [o.id for o in req.options]
            opt = 'vote:abstain' if 'vote:abstain' in ids else ids[0]
        stance = 'deep' if req.kind == 'wolf_kill' and req.context.get('first_night') else None
        await self._to_server(ActionMsg(request_id=req.id, option_id=opt, stance=stance))

    async def drain(self) -> None:
        for _ in range(50):
            pending = [t for t in self.tasks if not t.done()]
            if not pending:
                return
            await asyncio.wait(pending, timeout=5)
