"""知识边界集（方案 11.5、evals/README.md 第 7 节）。

不用 C 模型也能做的结构核对：
- 静态：搭好场景后，这个人此刻能说出口的事实（内核 ``speech_options`` 给的清单，J2b 与 C2 都只在它里面挑）
  全在 ``may_use`` 里，且不含任何 ``forbidden``；给模型看的状态文字（minds 的 J2 状态）里没有 ``forbidden`` 的事实句。
- 动态：用 Mock 模型把每个问题当作玩家的输入走完整回合（没开口就再等一回合），这个人说出的 ``fact_refs``
  全在 ``may_use`` 与这几回合里新相信的事实之内，且与 ``forbidden`` 不相交；回合之后能说的清单同样核对。
台词一级的判读（``forbidden_desc``、C3 写出的那句话）要 C 模型：经 Claude 客户端实现，没有钥匙时记 SKIPPED。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from tianlong.evalrun import gameplay
from tianlong.evalrun.common import SKIP, EvalConfig, frac, gate, make_result, table
from tianlong.kernel import predicates
from tianlong.kernel import speech as kspeech
from tianlong.llm import LLMError
from tianlong.minds import render

JUDGE_KIND = "C2_named"  # 判读用 Haiku 那一档的配置
JUDGE_SYSTEM = (
    "你在核对一部武侠文字游戏里一个人物的台词有没有越出他所知。给你：这个人物是谁、玩家问他的话、"
    "游戏这一回合写出的叙述、一份「他此刻不该知道的事」的清单。只看叙述里这个人物说出口的话与要旨："
    "其中若透露了清单上任何一件事的内容，leaked 填 true，并在 why 里用一句话指出是哪一件；"
    "他拒答、说不知道、岔开话题、说的是清单以外的事，leaked 都填 false。"
)


class Leak(BaseModel):
    model_config = ConfigDict(extra="ignore")
    leaked: bool
    why: str = ""


def believed(state: Any, cid: str) -> set[str]:
    return {f for f, b in state.chars[cid].beliefs.items() if b.confidence >= predicates.BELIEVE}


def static_check(state: Any, defs: Any, cid: str, may_use: set[str], forbidden: set[str]) -> dict[str, Any]:
    """此刻能说的事实清单与状态文字。返回越界的事实 id。"""
    opts = kspeech.speech_options(state, defs, cid)
    sayable = set(opts.facts)
    text = render.state_text(state, defs, cid, ())
    in_text = []
    for f in sorted(forbidden):
        fact = state.facts.get(f)
        if fact is None:
            continue
        shown = render.render_for(fact.text, state, defs, cid).strip()
        if shown and shown in text:
            in_text.append(f)
    return {
        "sayable": sorted(sayable),
        "outside_may_use": sorted(sayable - may_use),
        "forbidden_sayable": sorted(sayable & forbidden),
        "forbidden_in_state_text": in_text,
    }


def said_facts(events: Sequence[Any], cid: str) -> list[list[str]]:
    return [list(e.speech.fact_refs) for e in events if e.actor == cid and e.verb == "say" and e.speech is not None]


def dynamic_check(played: gameplay.Played, defs: Any, cid: str, may_use: set[str], forbidden: set[str]
                  ) -> dict[str, Any]:
    """走完回合之后：他说出的事实、他此后能说的事实，都不越出 may_use 与这几回合里新相信的事。"""
    learned = believed(played.after, cid) - believed(played.before, cid)
    allowed = may_use | learned
    said = said_facts(played.events, cid)
    flat = {f for refs in said for f in refs}
    alive = cid in played.after.chars
    sayable = set(kspeech.speech_options(played.after, defs, cid).facts) if alive else set()
    return {
        "lines": len(said), "facts_said": sorted(flat), "learned": sorted(learned),
        "said_outside": sorted(flat - allowed), "said_forbidden": sorted(flat & forbidden),
        "sayable_outside": sorted(sayable - allowed), "sayable_forbidden": sorted((sayable & forbidden) - learned),
    }


async def judge(llm: Any, name: str, q: Mapping[str, Any], text: str, facts: Mapping[str, str]) -> dict[str, Any]:
    """台词一级的判读（C 模型）：叙述里他那句话有没有带出 forbidden 的事实或 forbidden_desc 写的后文。"""
    listed = [facts[f] for f in q.get("forbidden") or [] if f in facts] + list(q.get("forbidden_desc") or [])
    user = "\n".join([f"【人物】{name}", f"【玩家问】{q['q']}", f"【这一回合的叙述】{text or '（没有叙述）'}",
                      "【他不该知道的事】", *[f"{i}. {t}" for i, t in enumerate(listed, 1)]])
    try:
        obj, _meta = await llm.claude.call_json(JUDGE_KIND, [JUDGE_SYSTEM], user, Leak,
                                                mock_default=lambda: Leak(leaked=False, why=""))
    except LLMError as e:
        return {"error": f"{type(e).__name__}: {e}"}
    return {"leaked": bool(obj.leaked), "why": obj.why}


async def run(cfg: EvalConfig) -> dict[str, Any]:
    data = cfg.dataset("knowledge")
    budget = cfg.limit
    can_judge = cfg.mock or cfg.settings.claude_api_key() is not None
    llm = cfg.open_llm() if can_judge else None
    chars: list[dict[str, Any]] = []
    try:
        for item in data["items"]:
            if budget is not None and budget <= 0:
                break
            cid = str(item["char"])
            setup = cfg.scene_setup(item["scene"])
            defs, st, _ = cfg.build(setup)
            may_use = set(item["may_use"])
            questions = list(item["questions"])
            if budget is not None:
                questions = questions[:budget]
                budget -= len(questions)
            all_forbidden = {f for q in item["questions"] for f in q.get("forbidden") or []}
            static = static_check(st, defs, cid, may_use, all_forbidden)
            fact_text = {f: render.render_for(st.facts[f].text, st, defs, None) for f in all_forbidden if f in st.facts}
            qrecs: list[dict[str, Any]] = []
            for q in questions:
                forb = set(q.get("forbidden") or [])
                rec: dict[str, Any] = {"id": q["id"], "desc_only": bool(q.get("forbidden_desc")) and not forb,
                                       "has_desc": bool(q.get("forbidden_desc"))}
                try:
                    p = await gameplay.play(
                        cfg, setup, [str(q["q"])], real_j1=False, run_id=f"knowledge-{q['id']}",
                        extra_wait=lambda _b, _g, evs, cid=cid: not said_facts(evs, cid))
                except Exception as e:  # noqa: BLE001
                    rec["error"] = f"{type(e).__name__}: {e}"
                    qrecs.append(rec)
                    continue
                rec.update(dynamic_check(p, defs, cid, may_use, forb))
                rec["ok"] = not (rec["said_outside"] or rec["said_forbidden"] or rec["sayable_outside"]
                                 or rec["sayable_forbidden"])
                rec["parse"] = [n for t in p.turns for n in t["notes"] if n.startswith("parse")]
                if llm is not None:
                    rec["judge"] = await judge(llm, st.chars[cid].name, q, "".join(t["text"] for t in p.turns),
                                               fact_text)
                qrecs.append(rec)
            chars.append({"char": cid, "scene": item["scene"], "static": static, "questions": qrecs})
    finally:
        if llm is not None:
            await llm.aclose()
    rows = []
    for c in chars:
        s, qs = c["static"], c["questions"]
        rows.append([
            c["char"], len(s["sayable"]), len(s["outside_may_use"]), len(s["forbidden_sayable"]),
            len(s["forbidden_in_state_text"]), len(qs), sum(1 for q in qs if q.get("lines")),
            sum(len(q.get("facts_said") or []) for q in qs),
            sum(1 for q in qs if q.get("ok") is False), sum(1 for q in qs if q.get("error")),
        ])
    tables = [table(
        "结构核对（静态 + Mock 回合）",
        ["人物", "此刻能说的事实", "其中不在 may_use", "其中属 forbidden", "状态文字里的 forbidden", "问题数",
         "开了口的问题", "说出的事实条数", "越界的问题", "跑崩的问题"], rows)]
    all_q = [q for c in chars for q in c["questions"]]
    bad_static = sum(len(c["static"]["outside_may_use"]) + len(c["static"]["forbidden_sayable"])
                     + len(c["static"]["forbidden_in_state_text"]) for c in chars)
    bad_dyn = [q for q in all_q if q.get("ok") is False]
    errors = [q for q in all_q if q.get("error")]
    if bad_dyn or errors:
        tables.append(table("越界或跑崩的问题", ["问题", "说了 may_use 之外的", "说了 forbidden", "此后能说的越界", "错误"],
                            [[q["id"], "、".join(q.get("said_outside") or []), "、".join(q.get("said_forbidden") or []),
                              "、".join((q.get("sayable_outside") or []) + (q.get("sayable_forbidden") or [])),
                              q.get("error", "")] for q in bad_dyn + errors]))
    skipped: list[str] = []
    notes = [f"{sum(1 for q in all_q if q['has_desc'])} 个问题带 forbidden_desc（事实库里没有的后文），"
             f"其中 {sum(1 for q in all_q if q['desc_only'])} 个只有 forbidden_desc：结构核对管不到，要看台词。"]
    judged = [q for q in all_q if isinstance(q.get("judge"), dict) and "leaked" in q["judge"]]
    if llm is None:
        skipped.append(f"台词一级的判读（{SKIP}：没有 ANTHROPIC_API_KEY）：C 模型对照 forbidden 与 forbidden_desc 读叙述里他那句话。"
                       "有钥匙后重跑：python -m tianlong eval knowledge")
    else:
        leaked = [q for q in judged if q["judge"]["leaked"]]
        tables.append(table("台词一级的判读（C 模型）", ["判读的问题", "判为越界"], [[len(judged), len(leaked)]],
                            note="Mock 下判读只是走通管道（替身一律答不越界）；回合里的叙述也是模板句。" if cfg.mock else
                            "叙述取自 Mock 回合的模板句；要判真模型写的台词，须用真模型开局（见 11.7 的试玩）。"))
    n = len(all_q)
    passed = None if not n else (bad_static == 0 and not bad_dyn and not errors)
    gates = [gate("知识边界（结构核对）", passed,
                  f"静态越界 {bad_static} 处；Mock 回合里越界的问题 {len(bad_dyn)}/{n}，跑崩 {len(errors)}。要求全为零。")]
    summary = {"chars": len(chars), "questions": n, "static_violations": bad_static,
               "dynamic_violations": len(bad_dyn), "errors": len(errors),
               "spoke": sum(1 for q in all_q if q.get("lines")),
               "facts_said": sum(len(q.get("facts_said") or []) for q in all_q),
               "judged": len(judged), "judged_leaks": sum(1 for q in judged if q["judge"]["leaked"])}
    notes.append(f"开了口的问题 {frac(summary['spoke'], n)}；Mock 的人物多半不答或拒答，说出的事实共 {summary['facts_said']} 条。")
    notes.append("回合一律用 Mock 模型走（不论带不带 --mock）：验的是整条链上有没有别的口子，不是模型的口风。")
    return make_result(cfg, "knowledge", n=n, summary=summary, tables=tables, gates=gates, skipped=skipped,
                       notes=notes, items=chars)
