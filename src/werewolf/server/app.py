"""本地服务（ARCHITECTURE 第 7、8 节）：Starlette 应用，静态页面 + 健康检查 + WebSocket。

- `GET /`：前端首页；其余静态文件来自 `web/`。
- `GET /api/health`：`{ok, version, llm, game}`。
- `WS /ws`：只接受本机页面的握手（Origin 为 `http://127.0.0.1:{port}` 或 `http://localhost:{port}`；
  没有 Origin 只在 dev 下放行）。同一时刻只保留一个连接：新连接顶掉旧的。同一时刻只有一局：
  `new_game` 取代当前这局（没打完的就取消），`resume` 把这条连接接回当前这局。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from tianlong.config import Settings
from werewolf.kernel.types import Role
from werewolf.llm.config import Speed, WerewolfSettings
from werewolf.llm.gateway import Gateway
from werewolf.protocol import PROTOCOL_VERSION, ClientMsg, ErrorMsg, Hello, NewGame, Resume, ServerMsg
from werewolf.server.session import PlayerFactory, Send, Session

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parents[1] / 'web'
CLIENT_ADAPTER: TypeAdapter[Any] = TypeAdapter(ClientMsg)
REPLACED = 4000  # 被新连接顶掉
FORBIDDEN = 1008


class Hub:
    """唯一的连接与唯一的一局。"""

    def __init__(self, settings: Settings, ww: WerewolfSettings, *, offline: bool, dev: bool, speed: Speed,
                 runs_dir: Path, player_factory: PlayerFactory | None, time_scale: float) -> None:
        self.settings = settings
        self.ww = ww
        self.offline = offline
        self.dev = dev
        self.speed: Speed = speed
        self.runs_dir = runs_dir
        self.player_factory = player_factory
        self.time_scale = time_scale
        self.conn: WebSocket | None = None
        self.session: Session | None = None
        self.task: asyncio.Task[Any] | None = None
        self.gateway: Gateway | None = None
        self._idle_gw: Gateway | None = None

    def status_gateway(self) -> Gateway:
        if self.gateway is not None:
            return self.gateway
        if self._idle_gw is None:
            self._idle_gw = Gateway(self.settings, self.ww, offline=self.offline)
        return self._idle_gw

    def hello(self) -> Hello:
        speed = self.session.speed if self.session is not None else self.speed
        return Hello(llm=self.status_gateway().status(), speed=speed, resumable=self.session is not None)

    async def take(self, ws: WebSocket) -> None:
        old = self.conn
        self.conn = ws
        if self.session is not None:
            self.session.detach()
        if old is not None and old is not ws:
            with contextlib.suppress(Exception):
                await old.close(code=REPLACED)

    def release(self, ws: WebSocket) -> None:
        if self.conn is ws:
            self.conn = None
            if self.session is not None:
                self.session.detach()

    async def stop(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task
        self.task = None
        self.session = None

    def _game_id(self, seed: int) -> str:
        base = time.strftime('%Y%m%d-%H%M%S') + f'-{seed % 0x10000:04x}'
        gid, n = base, 1
        while (self.runs_dir / gid).exists():
            n += 1
            gid = f'{base}-{n}'
        return gid

    async def new_game(self, msg: NewGame, send: Send) -> None:
        await self.stop()
        seed = msg.seed if (self.dev and msg.seed is not None) else secrets.randbelow(2**53)
        game_id = self._game_id(seed)
        run_dir = self.runs_dir / game_id
        prev = self.gateway
        gw = Gateway(self.settings, self.ww, offline=self.offline, run_dir=run_dir)
        if prev is not None:  # 预算按一次启动算：把之前花的带过来
            gw.llm.ledger.spent = prev.llm.ledger.spent
            with contextlib.suppress(Exception):
                await prev.aclose()
        self.gateway = gw
        role = None if msg.role == 'random' else Role(msg.role)
        self.session = Session(gw, self.ww, human_seat=msg.seat, human_role=role, seed=seed,
                               speed=msg.speed or self.speed, send=send, run_dir=run_dir,
                               player_factory=self.player_factory, dev=self.dev, human_name=msg.name,
                               game_id=game_id, time_scale=self.time_scale)
        self.task = asyncio.create_task(self._run(self.session))

    async def _run(self, session: Session) -> None:
        try:
            await session.run()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — 已在 session 里记过日志并告诉了浏览器
            pass

    async def handle(self, msg: Any, send: Send) -> None:
        if isinstance(msg, NewGame):
            await self.new_game(msg, send)
            return
        if self.session is None:
            await send(ErrorMsg(code='no_game', message='没有进行中的对局'))
            return
        if isinstance(msg, Resume):
            await self.session.attach(send)
            return
        await self.session.on_client(msg)

    async def shutdown(self) -> None:
        await self.stop()
        for gw in (self.gateway, self._idle_gw):
            if gw is not None:
                with contextlib.suppress(Exception):
                    await gw.aclose()


def allowed_origins(port: int) -> set[str]:
    return {f'http://127.0.0.1:{port}', f'http://localhost:{port}'}


def create_app(settings: Settings, ww: WerewolfSettings, *, offline: bool = False, dev: bool = False,
               speed: Speed | None = None, runs_dir: Path | None = None,
               player_factory: PlayerFactory | None = None, time_scale: float = 1.0) -> Starlette:
    runs = Path(runs_dir) if runs_dir is not None else settings.path('runs')
    hub = Hub(settings, ww, offline=offline, dev=dev, speed=speed or ww.speed, runs_dir=runs,
              player_factory=player_factory, time_scale=time_scale)
    origins = allowed_origins(ww.port)

    async def index(request: Request) -> FileResponse:
        return FileResponse(WEB_DIR / 'index.html')

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({'ok': True, 'version': PROTOCOL_VERSION,
                             'llm': hub.status_gateway().status().model_dump(), 'game': hub.session is not None})

    async def ws_endpoint(ws: WebSocket) -> None:
        origin = ws.headers.get('origin')
        if (origin is None and not dev) or (origin is not None and origin not in origins):
            await ws.close(code=FORBIDDEN)
            return
        await ws.accept()
        await hub.take(ws)

        async def send(msg: ServerMsg) -> None:
            if hub.conn is not ws:
                raise ConnectionError('replaced')
            await ws.send_text(msg.model_dump_json())

        try:
            await send(hub.hello())
            while True:
                raw = await ws.receive_text()
                try:
                    msg = CLIENT_ADAPTER.validate_python(json.loads(raw))
                except (ValueError, ValidationError) as exc:
                    detail = str(exc).splitlines()[0][:200]
                    await send(ErrorMsg(code='bad_message', message='看不懂这条消息', detail=detail))
                    continue
                await hub.handle(msg, send)
        except (WebSocketDisconnect, ConnectionError, RuntimeError):
            pass
        finally:
            hub.release(ws)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await hub.shutdown()

    app = Starlette(
        routes=[
            Route('/', index),
            Route('/api/health', health),
            WebSocketRoute('/ws', ws_endpoint),
            Mount('/', StaticFiles(directory=WEB_DIR, html=True), name='web'),
        ],
        lifespan=lifespan,
    )
    app.state.hub = hub
    return app
