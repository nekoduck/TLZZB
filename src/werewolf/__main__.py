"""命令行入口（ARCHITECTURE 第 11 节）。

    python -m werewolf                 # 等同 serve
    python -m werewolf serve [--port 8765] [--host 127.0.0.1] [--offline] [--no-browser] [--speed normal] [--dev]
    python -m werewolf sim ...         # 交给 werewolf.sim.selfplay.main(argv)
    python -m werewolf smoke           # 真模型冒烟
    python -m werewolf demo <game_id> <名字>   # 把 runs/<game_id>/wire.jsonl 转成 web/demo/<名字>.json
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

WEB_DEMO_DIR = Path(__file__).resolve().parent / 'web' / 'demo'
SPEEDS = ('slow', 'normal', 'fast', 'instant')


def _serve(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(prog='python -m werewolf serve', description='启动本地狼人杀服务')
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default=None)
    ap.add_argument('--offline', action='store_true', help='不调模型：全走规则与模板')
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--speed', choices=SPEEDS, default=None)
    ap.add_argument('--dev', action='store_true', help='开发模式：new_game.seed 生效，允许没有 Origin 的握手')
    ap.add_argument('--config', default='werewolf.toml')
    ap.add_argument('--allow-origin', action='append', default=[], metavar='ORIGIN',
                    help='另外放行的页面来源（如 https://game.example），可重复；经反向代理访问时用，精确匹配')
    args = ap.parse_args(list(argv))

    import uvicorn

    from werewolf.llm.config import load
    from werewolf.server.app import create_app

    settings, ww = load(args.config)
    if args.port is not None:
        ww = ww.model_copy(update={'port': args.port})
    host = args.host or ww.host
    app = create_app(settings, ww, offline=args.offline, dev=args.dev, speed=args.speed or ww.speed,
                     extra_origins=tuple(args.allow_origin))
    shown = '127.0.0.1' if host in ('0.0.0.0', '::', '') else host
    url = f'http://{shown}:{ww.port}/'
    print(f'狼人杀已启动：{url}  （Ctrl+C 退出）', flush=True)
    if not args.no_browser and ww.open_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=ww.port, log_level='warning')
    return 0


def _sim(argv: Sequence[str]) -> int:
    try:
        mod = importlib.import_module('werewolf.sim.selfplay')
    except ModuleNotFoundError as exc:
        if exc.name not in ('werewolf.sim.selfplay', 'werewolf.sim'):
            raise
        print('自对弈模块（werewolf.sim.selfplay）还没装上，sim 暂不可用。', file=sys.stderr)
        return 2
    main = getattr(mod, 'main', None)
    if main is None:
        print('werewolf.sim.selfplay 里没有 main(argv)。', file=sys.stderr)
        return 2
    return int(main(list(argv)) or 0)


def _smoke(argv: Sequence[str]) -> int:
    from werewolf.llm.config import load
    from werewolf.llm.gateway import Gateway
    from werewolf.llm.smoke import run_smoke

    verbose = '-v' in argv
    only = [a for a in argv if not a.startswith('-')] or None

    async def go() -> None:
        settings, ww = load()
        gw = Gateway(settings, ww)
        try:
            await run_smoke(gw, only=only, verbose=verbose)
        finally:
            await gw.aclose()

    asyncio.run(go())
    return 0


def _demo(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(prog='python -m werewolf demo', description='把一局的 wire.jsonl 转成演示文件')
    ap.add_argument('game_id')
    ap.add_argument('name')
    ap.add_argument('--runs', default=None, help='runs 目录（默认取配置里的 paths.runs）')
    ap.add_argument('--out', default=None, help='输出目录（默认 web/demo）')
    args = ap.parse_args(list(argv))
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', args.name):
        print('演示名只能用字母、数字、下划线和连字符。', file=sys.stderr)
        return 2
    from werewolf.server.store import read_jsonl, wire_to_demo

    if args.runs is not None:
        runs = Path(args.runs)
    else:
        from werewolf.llm.config import load

        settings, _ = load()
        runs = settings.path('runs')
    src = runs / args.game_id / 'wire.jsonl'
    if not src.exists():
        print(f'找不到 {src}', file=sys.stderr)
        return 2
    demo = wire_to_demo(read_jsonl(src))
    out_dir = Path(args.out) if args.out else WEB_DEMO_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f'{args.name}.json'
    out.write_text(json.dumps(demo, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f'已写入 {out}（{len(demo["steps"])} 步）；打开 /?demo={args.name} 播放')
    return 0


COMMANDS = {'serve': _serve, 'sim': _sim, 'smoke': _smoke, 'demo': _demo}


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    if not args or args[0].startswith('-'):
        return _serve(args)
    cmd, rest = args[0], args[1:]
    fn = COMMANDS.get(cmd)
    if fn is None:
        print(f'不认识的子命令：{cmd}\n{__doc__}', file=sys.stderr)
        return 2
    return fn(rest)


if __name__ == '__main__':
    raise SystemExit(main())
