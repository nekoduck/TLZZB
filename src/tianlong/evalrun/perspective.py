"""视角（方案 11.5）：用 Mock 模型玩 200 回合，选项与自由输入混着来，对玩家看到的每一句叙述、每一个选项
跑叙述模块的字符串检查（narrator.checks）：不出现玩家未知的专名、禁用词、阿拉伯数字、拉丁字母、提示词标记。

J5 与人工各查一遍的那一半要真模型写的叙述（C3）：没有 Claude 的钥匙时，叙述全是模板句，这里只做字符串检查。
"""

from __future__ import annotations

import random
import tempfile
from pathlib import Path
from typing import Any

from tianlong import narrator, parser
from tianlong.evalrun import gameplay
from tianlong.evalrun.common import EvalConfig, gate, make_result, table

TURNS = 200
TURNS_PER_GAME = 40
SEED = 20261115
OPTION_SHARE = 0.6
OPTION_INPUTS = ("一", "二", "三")
KINDS = ("name", "banned", "digit", "latin", "markup")


def free_inputs(cfg: EvalConfig) -> list[str]:
    """数据集里现成的自由输入：归类集的全部输入，加防作弊集的各条。"""
    out: list[str] = []
    for it in cfg.dataset("classify")["items"]:
        out.append(str(it["input"]))
    for it in cfg.dataset("anticheat")["items"]:
        out += [str(x) for x in it["inputs"]]
    return out


def violation_kind(v: str) -> str:
    return v.split(":", 1)[0]


def check_screen(g: Any, raw: str, turn: dict[str, Any]) -> tuple[int, int, list[dict[str, Any]]]:
    """这一屏的叙述（逐句）与选项过一遍检查器；返回 (句数, 选项数, 违规清单)。"""
    st = g.state
    quoted = [raw]
    clean, _ = parser.strip_stage(raw) if raw else ("", [])
    if clean:
        quoted.append(clean)
    for ev in turn.get("events") or []:
        if ev.actor == st.player and ev.speech is not None and ev.speech.spoken_text:
            quoted.append(ev.speech.spoken_text)
    ck = narrator.build_checker(st, g.defs, g.last_view, quoted=quoted)
    sentences = narrator.split_sentences(turn["text"]) if turn["text"] else []
    bad: list[dict[str, Any]] = []
    for s in sentences:
        for v in ck.check(s):
            bad.append({"where": "narration", "violation": v, "text": s.strip()})
    for o in turn["options"]:
        for v in ck.check(o):
            bad.append({"where": "option", "violation": v, "text": o})
    return len(sentences), len(turn["options"]), bad


async def run(cfg: EvalConfig) -> dict[str, Any]:
    target = cfg.limit or TURNS
    rng = random.Random(SEED)
    pool = free_inputs(cfg)
    played = 0
    games = 0
    sentences = options = 0
    kinds: dict[str, int] = {}
    picks = {"option": 0, "free": 0}
    bad: list[dict[str, Any]] = []
    ended: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="tianlong-eval-") as tmp:
        settings = gameplay.game_settings(cfg, Path(tmp))
        while played < target:
            games += 1
            seed = games
            g = await gameplay.Game.new(settings, seed=seed, mock=True, run_id=f"perspective-{seed}")
            try:
                evs = [ev async for ev in g.start()]
                first = {"text": "".join(e.piece for e in evs if isinstance(e, gameplay.Text)),
                         "options": next((list(e.items) for e in evs if isinstance(e, gameplay.Options)), []),
                         "events": []}
                ns, no, b = check_screen(g, "", first)
                sentences += ns
                options += no
                bad += [{**x, "seed": seed, "turn": 0, "input": ""} for x in b]
                for _ in range(TURNS_PER_GAME):
                    if played >= target or g.state.ended is not None:
                        break
                    use_option = rng.random() < OPTION_SHARE
                    raw = rng.choice(OPTION_INPUTS) if use_option else rng.choice(pool)
                    turn = await gameplay.send(g, raw)
                    played += 1
                    picks["option" if use_option else "free"] += 1
                    kinds[turn["kind"]] = kinds.get(turn["kind"], 0) + 1
                    ns, no, b = check_screen(g, raw, turn)
                    sentences += ns
                    options += no
                    bad += [{**x, "seed": seed, "turn": played, "input": raw} for x in b]
                if g.state.ended is not None:
                    ended[str(g.state.ended)] = ended.get(str(g.state.ended), 0) + 1
            finally:
                await g.aclose()
    counts = {k: sum(1 for x in bad if violation_kind(x["violation"]) == k) for k in KINDS}
    other = len(bad) - sum(counts.values())
    tables = [table(
        "玩了多少、查了多少", ["项目", "数"],
        [["回合（输入）", played], ["其中选选项", picks["option"]], ["其中自由输入", picks["free"]], ["开的局数", games],
         ["耗时的回合（turn）", kinds.get("turn", 0)], ["不耗时的回话（reply）", kinds.get("reply", 0)],
         ["其他（警示、确认、动弹不得等）", played - kinds.get("turn", 0) - kinds.get("reply", 0)],
         ["查过的叙述句", sentences], ["查过的选项", options]])]
    tables.append(table(
        "字符串检查的违规", ["种类", "条数"],
        [["玩家未知的专名（name）", counts["name"]], ["禁用词（banned）", counts["banned"]],
         ["阿拉伯数字（digit）", counts["digit"]], ["拉丁字母（latin）", counts["latin"]],
         ["提示词标记（markup）", counts["markup"]], ["其他", other], ["合计", len(bad)]]))
    if bad:
        tables.append(table("违规的句子（至多列 30 条）", ["局", "回合", "输入", "哪里", "违规", "文字"],
                            [[x["seed"], x["turn"], x["input"][:30], x["where"], x["violation"], x["text"][:80]]
                             for x in bad[:30]]))
    gates = [gate("视角（字符串检查）", (len(bad) == 0) if played else None,
                  f"{played} 回合、{sentences} 句叙述、{options} 个选项里违规 {len(bad)} 处；要求为零。")]
    skipped = ["J5 与人工各查一遍：要真模型（C3）写的叙述，没有 ANTHROPIC_API_KEY 时叙述全是模板句。"
               "有钥匙后用真模型试玩并抽查（11.7），J5 的准头见核对集。"]
    summary = {"turns": played, "games": games, "sentences": sentences, "options": options, "violations": len(bad),
               "by_kind": counts, "turn_kinds": kinds, "ended": ended}
    notes = ["这个套件不论带不带 --mock 都用 Mock 模型开局（叙述与选项是模板句）；随机种子固定，结果可重现。"]
    return make_result(cfg, "perspective", n=played, summary=summary, tables=tables, gates=gates, skipped=skipped,
                       notes=notes, items=bad)
