"""时延与成本（方案 11.7）：用真 Jev 玩一局 30 回合（没有 Claude 的钥匙时，叙述与选项措辞用模板、深思用规则），
量首字时延、Jev 单个请求的时延、每回合的 token 与花费，对照 11.7 的闸。

游戏用 config.toml 的原样设置（Jev 超时 2 秒、预算照旧），不放宽。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from tianlong.evalrun import gameplay
from tianlong.evalrun.common import SKIP, EvalConfig, gate, make_result, median, percentile, secs, table
from tianlong.game import Game
from tianlong.llm.record import CALLS_FILE, read_calls

TURNS = 30
MAX_INPUTS_FACTOR = 3
# 选项与自由输入混着来：三个里两个选选项，一个写一句哪里都说得通的话
SCRIPT = ("一", "二", "我四下看看，留神周围的动静。", "三", "一", "向身边的人打听这里出了什么事。", "二", "一",
          "我先等一等，看看再说。", "三", "二", "仔细听听旁人在说些什么。")
MEDIAN_BUDGET_S = 3.0
P90_BUDGET_S = 6.0
COST_BUDGET_USD = 0.02


def call_stats(calls: list[dict[str, Any]]) -> dict[str, Any]:
    jev = [c for c in calls if "seq" in c and str(c.get("provider", "")).endswith("jev")]
    ok = [c for c in jev if not c.get("error")]
    lat = [float(c.get("latency_ms") or 0.0) / 1000.0 for c in ok]
    by_kind: dict[str, list[float]] = {}
    for c in ok:
        by_kind.setdefault(str(c["kind"]), []).append(float(c.get("latency_ms") or 0.0) / 1000.0)
    return {
        "requests": len(jev), "errors": len(jev) - len(ok),
        "timeouts": sum(1 for c in jev if c.get("error") and "Timeout" in str((c["error"] or {}).get("type", c["error"]))),
        "median_s": median(lat), "p95_s": percentile(lat, 95), "max_s": max(lat) if lat else None,
        "input_tokens": sum(int((c.get("usage") or {}).get("input_tokens") or 0) for c in jev),
        "output_tokens": sum(int((c.get("usage") or {}).get("output_tokens") or 0) for c in jev),
        "cost_usd": sum(float(c.get("cost") or 0.0) for c in calls if "seq" in c),
        "by_kind": {k: {"n": len(v), "median_s": median(v), "p95_s": percentile(v, 95)} for k, v in sorted(by_kind.items())},
    }


async def run(cfg: EvalConfig) -> dict[str, Any]:
    target = cfg.limit or TURNS
    turns: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    games = 0
    ledger: dict[str, Any] = {}
    sent = 0
    with tempfile.TemporaryDirectory(prefix="tianlong-eval-") as tmp:
        settings = gameplay.game_settings(cfg, Path(tmp))
        while len([t for t in turns if t["kind"] == "turn"]) < target and sent < target * MAX_INPUTS_FACTOR:
            games += 1
            g = await Game.new(settings, seed=games, mock=cfg.mock, run_id=f"latency-{games}")
            try:
                async for _ in g.start():
                    pass
                i = 0
                while g.state.ended is None and sent < target * MAX_INPUTS_FACTOR:
                    if len([t for t in turns if t["kind"] == "turn"]) >= target:
                        break
                    raw = SCRIPT[i % len(SCRIPT)]
                    i += 1
                    sent += 1
                    t = await gameplay.send(g, raw)
                    rec = g.last_turn
                    lo, hi = rec.calls if rec is not None else (0, 0)
                    turns.append({"game": games, "raw": raw, "kind": t["kind"], "first_text_s": t["first_text_s"],
                                  "total_s": rec.total_s if rec is not None else None, "calls": [lo, hi],
                                  "notes": t["notes"]})
                for k, v in g.llm.ledger.summary().items():
                    if isinstance(v, int | float):
                        ledger[k] = ledger.get(k, 0.0) + v if k == "spent_usd" else v
                path = g.run_dir / CALLS_FILE
                game_calls = read_calls(path) if path.is_file() else []
                for c in game_calls:
                    c["game"] = games
                calls += game_calls
            finally:
                await g.aclose()
    real = [t for t in turns if t["kind"] == "turn"]
    first = [t["first_text_s"] for t in real if t["first_text_s"] is not None]
    cs = call_stats(calls)
    per_turn: list[dict[str, Any]] = []
    for t in real:
        lo, hi = t["calls"]
        mine = [c for c in calls if c.get("game") == t["game"] and "seq" in c and lo < int(c["seq"]) <= hi]
        per_turn.append({"requests": len(mine),
                         "tokens": sum(int((c.get("usage") or {}).get("input_tokens") or 0) for c in mine),
                         "cost": sum(float(c.get("cost") or 0.0) for c in mine)})
    n = len(real)
    cost_turn = sum(p["cost"] for p in per_turn) / n if n else None
    tok_turn = sum(p["tokens"] for p in per_turn) / n if n else None
    req_turn = sum(p["requests"] for p in per_turn) / n if n else None
    med, p90 = median(first), percentile(first, 90)
    tables = [table(
        "首字时延（从输入到第一段叙述）", ["回合数", "中位数", "P90", "最大", "闸：中位数 ≤ 3 秒", "闸：P90 ≤ 6 秒"],
        [[n, secs(med), secs(p90), secs(max(first) if first else None),
          None if med is None else med <= MEDIAN_BUDGET_S, None if p90 is None else p90 <= P90_BUDGET_S]])]
    tables.append(table(
        "Jev 单个请求", ["请求数", "中位数", "P95", "最大", "失败", "其中超时"],
        [[cs["requests"], secs(cs["median_s"]), secs(cs["p95_s"]), secs(cs["max_s"]), cs["errors"], cs["timeouts"]]]))
    tables.append(table("Jev 各类请求", ["调用", "次数", "中位数", "P95"],
                        [[k, v["n"], secs(v["median_s"]), secs(v["p95_s"])] for k, v in cs["by_kind"].items()]))
    tables.append(table(
        "每回合的用量（只算耗时的回合）", ["每回合请求数", "每回合输入 token", "每回合花费（美分）", "闸：≤ 2 美分", "全程花费（美元）"],
        [[None if req_turn is None else f"{req_turn:.1f}", None if tok_turn is None else f"{tok_turn:.0f}",
          None if cost_turn is None else f"{cost_turn * 100:.4f}", None if cost_turn is None else cost_turn <= COST_BUDGET_USD,
          f"{cs['cost_usd']:.5f}"]]))
    gates = [
        gate("首字时延（11.7）", None if med is None or p90 is None else (med <= MEDIAN_BUDGET_S and p90 <= P90_BUDGET_S),
             f"{n} 回合：中位数 {secs(med)}（要求 ≤ 3 秒），P90 {secs(p90)}（要求 ≤ 6 秒）。"),
        gate("每回合成本（11.7）", None if cost_turn is None else cost_turn <= COST_BUDGET_USD,
             f"每回合 {0.0 if cost_turn is None else cost_turn * 100:.4f} 美分（要求 ≤ 2 美分）；只含 Jev。"),
    ]
    skipped = []
    if not cfg.mock and cfg.settings.claude_api_key() is None:
        skipped.append(f"Claude 的部分（{SKIP}：没有 ANTHROPIC_API_KEY）：C3 叙述的首字时延与花费、C4、升级到 C1/C2 的调用都没量；"
                       "方案 7.13 估叙述约 0.7 美分一回合。有钥匙后重跑：python -m tianlong eval latency_cost --limit 100")
    notes = [f"共发出 {sent} 个输入，开了 {games} 局；耗时的回合 {n} 个，不耗时的回话等 {len(turns) - n} 个。"
             f"方案要求量 100 回合，这里量 {target} 回合（--limit 可改）。"]
    summary = {"turns": n, "inputs": sent, "games": games, "first_text": {"median_s": med, "p90_s": p90,
                                                                          "max_s": max(first) if first else None},
               "jev": cs, "per_turn": {"requests": req_turn, "input_tokens": tok_turn, "cost_usd": cost_turn},
               "ledger": ledger}
    return make_result(cfg, "latency_cost", n=n, summary=summary, tables=tables, gates=gates, skipped=skipped,
                       notes=notes, items=turns)
