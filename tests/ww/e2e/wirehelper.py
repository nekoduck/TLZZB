"""浏览器整局之后的体检：读 runs/<game_id>/，跑 wire_check，并和界面最终状态对账。

    python wirehelper.py runs/<game_id> [result.json]
输出一行 JSON：{problems: [...], stats: {...}}。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from werewolf.kernel.engine import Game
from werewolf.kernel.types import Event, GameConfig, Role
from werewolf.protocol import ServerMsg
from werewolf.server.wirecheck import wire_check

_ADAPTER = TypeAdapter(ServerMsg)


def load_wire(run_dir: Path) -> tuple[list[dict[str, Any]], list[Any]]:
    rows = [json.loads(ln) for ln in (run_dir / 'wire.jsonl').read_text(encoding='utf-8').splitlines() if ln.strip()]
    msgs = [_ADAPTER.validate_python(r['msg']) for r in rows if r['dir'] == 'out']
    return rows, msgs


def rebuild_game(run_dir: Path) -> tuple[Game, int]:
    """从 events.jsonl 与 meta.json 重建一个只读的 Game（够 wire_check 用）。"""
    meta = json.loads((run_dir / 'meta.json').read_text(encoding='utf-8'))
    events = [json.loads(ln) for ln in (run_dir / 'events.jsonl').read_text(encoding='utf-8').splitlines() if ln.strip()]
    roles = {e['actor']: Role(e['data']['role']) for e in events if e['type'] == 'role_dealt'}
    human = int(meta['human_seat'])
    cfg = GameConfig(human_seat=human)
    d = Game.new(cfg, int(meta['seed']), roles).dump()
    d['events'] = events
    d['phase'] = 'game_over'
    return Game.load(d), human


def speech_timings(rows: list[dict[str, Any]], human: int) -> list[float]:
    """每段 AI 发言：从 thinking on 到该座位的 speech 事件，秒。"""
    on: dict[int, int] = {}
    out: list[float] = []
    for r in rows:
        if r['dir'] != 'out':
            continue
        m = r['msg']
        if m.get('t') == 'thinking' and m.get('what', 'speak') == 'speak':
            if m.get('on'):
                on[m['seat']] = r['ts_ms']
            else:
                on.setdefault(m['seat'], r['ts_ms'])
        elif m.get('t') == 'event' and m['ev']['type'] == 'speech' and not m.get('replay'):
            seat = m['ev']['actor']
            if seat in on and seat != human:
                out.append((r['ts_ms'] - on.pop(seat)) / 1000)
    return out


def god_view_problems(msgs: list[Any], human: int) -> list[str]:
    """can_god_view 只在真人已死且没有未答请求时才为真；god 消息不得早于它。"""
    out: list[str] = []
    dead = False
    allowed = False
    open_req: str | None = None
    for i, m in enumerate(msgs):
        if m.t == 'event' and m.ev.type == 'death' and m.ev.target == human:
            dead = True
        if m.t == 'request':
            open_req = m.id
        if m.t == 'accepted' and m.request_id == open_req:
            open_req = None
        if m.t == 'state':
            if m.can_god_view:
                if not dead:
                    out.append(f'#{i}: 真人没死就开了 can_god_view')
                if m.open_request is not None:
                    out.append(f'#{i}: can_god_view 为真时还有未答请求')
                allowed = True
        if m.t == 'god' and not allowed:
            out.append(f'#{i}: 在 can_god_view 之前就发了 god')
    return out


def check(run_dir: Path, result: dict[str, Any] | None = None) -> dict[str, Any]:
    rows, msgs = load_wire(run_dir)
    game, human = rebuild_game(run_dir)
    problems = list(wire_check(msgs, game, human))
    live = [m for m in msgs if m.t == 'event' and not m.replay]
    stats: dict[str, Any] = {'msgs': len(msgs), 'events': len(live)}
    if live:
        stats['last_seq'] = live[-1].seq
    over = [m for m in live if m.ev.type == 'game_over']
    stats['game_over'] = bool(over)
    stats['review'] = any(m.t == 'review' for m in msgs)
    stats['costs'] = [m.spent_usd for m in msgs if m.t == 'cost']
    stats['errors'] = [m.model_dump(mode='json') for m in msgs if m.t == 'error']
    stats['speech_s'] = speech_timings(rows, human)
    problems += god_view_problems(msgs, human)
    if not over:
        problems.append('wire 里没有 game_over')
    if not stats['review']:
        problems.append('wire 里没有 review')
    if result:
        fin = result.get('final') or {}
        if fin.get('lastSeq') != stats.get('last_seq'):
            problems.append(f'界面最后的 seq {fin.get("lastSeq")} != wire 最后的 seq {stats.get("last_seq")}')
        dead = {m.ev.target for m in live if m.ev.type == 'death'}
        ui_dead = {int(k) for k, v in (fin.get('alive') or {}).items() if not v}
        if dead != ui_dead:
            problems.append(f'界面出局 {sorted(ui_dead)} != wire 的 death {sorted(dead)}')
        if over:
            w = over[0].ev.data.get('winner')
            if fin.get('winner') != w:
                problems.append(f'界面胜方 {fin.get("winner")} != wire 的 {w}')
        bad = [m for m in stats['errors'] if m.get('code') != 'illegal_action']
        if bad:
            problems.append(f'wire 里有 error：{bad[:2]}')
        # 每个下发过的请求都应该被答复（accepted）或被新一局取代
        reqs = {m.id for m in msgs if m.t == 'request'}
        acc = {m.request_id for m in msgs if m.t == 'accepted'}
        miss = sorted(reqs - acc)
        if miss:
            problems.append(f'请求没有被受理：{miss[:3]}')
    _ = Event
    return {'problems': problems, 'stats': stats}


if __name__ == '__main__':
    rd = Path(sys.argv[1])
    res = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8')) if len(sys.argv) > 2 else None
    print(json.dumps(check(rd, res), ensure_ascii=False))
