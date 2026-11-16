"""后事（方案 2.6、7.11、8.6）：收场时先由 C7 写一段「后事」，再由规则列出「与原著的分岔」。

- 输入只有玩家知道的东西：年谱里玩家知道的条目；收场那一刻玩家所知的各人下落；已了与未了的牵挂；
  玩家的长进。
- 输出流式，400 到 800 字，第二人称；只写已经发生的事和收场那一刻的处境。
- C7 出任何错（没有密钥、拒答、超预算……）都退到模板：用年谱里的事实句拼成的后事。中途出错的，补一句收束。
- 分岔清单由规则列出，不经模型。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

from tianlong import prompting
from tianlong.kernel import perception, refs
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import WorldState, is_alive
from tianlong.llm import LLM, LLMError
from tianlong.storyteller import chronicle, common, threads

MAX_CHARS = 800
SEEN_CAP = 8
HEARD_CAP = 4
WHERE_CAP = 8
ENDED_TEXT = {
    "closed": "你在这里收了笔。",
    "dead": "你的这一程，到这里就断了。",
    "left": "你走出了这一带山水，身后的事从此与你隔开了。",
}
CLOSING = "往后如何，便是另一段故事了。"


# ---------------------------------------------------------------- 素材


def _known_lines(state: WorldState, defs: Defs) -> tuple[list[str], list[str]]:
    cols = chronicle.columns(state)

    def line(i: int) -> str:
        e = state.chronicle[i]
        return f"{common.clock(e.tick)}，{common.pv(state, defs, e.text)}"

    return [line(i) for i in cols["saw"]], [line(i) for i in cols["heard"]]


def _whereabouts(state: WorldState, defs: Defs) -> list[str]:
    """收场那一刻玩家所知的各人下落：就在身边的照实写；不在的，写最后一次见到他是在哪里。"""
    p = common.player(state)
    if p is None:
        return []
    out: list[str] = []
    here = set(common.visible_people_ids(state)) if perception.on_map(p) else set()
    in_threads = {r for t in state.threads.values() for r in t.refs}
    met: list[str] = []
    for c in state.chars.values():
        if c.id == p.id or c.tier not in ("soul", "named"):
            continue
        r = c.rel.get(p.id)
        mine = p.rel.get(c.id)
        tied = (
            c.id in state.known_terms
            or c.id in state.term_alias
            or c.id in in_threads
            or (r is not None and (r.trust or r.affinity or r.fear))
            or (mine is not None and (mine.trust or mine.affinity))
        )
        if tied:
            met.append(c.id)
    for cid in met:
        if len(out) >= WHERE_CAP:
            break
        name = common.pv(state, defs, f"⟦{cid}⟧")
        if cid in here:
            state_bits = [b for b in _visible_condition(state, cid)]
            out.append(f"{name}就在你身边" + (f"，{'、'.join(state_bits)}" if state_bits else ""))
            continue
        last = None
        for ev in reversed(state.events_tail):
            if ev.witnesses.get(p.id) != "saw":
                continue
            if ev.actor == cid or cid in refs.refs_in(ev.fact_text):
                last = ev
                break
        if last is not None:
            dead = any(k.get("k") == "death" and k.get("who") == cid for k in last.kinds)
            if dead:
                out.append(f"{name}已经不在人世（{common.clock(last.tick)}，你亲眼所见）")
            else:
                place = common.pv(state, defs, f"⟦{last.loc}⟧")
                out.append(f"你最后一次见到{name}，是{common.clock(last.tick)}在{place}")
        if len(out) >= WHERE_CAP:
            break
    return out


def _visible_condition(state: WorldState, cid: str) -> list[str]:
    c = state.chars[cid]
    bits: list[str] = []
    if not is_alive(c):
        return ["已经没了气息"]
    kinds = {s.kind for s in c.statuses}
    if "bound" in kinds:
        bits.append("被捆着")
    elif "captive" in kinds:
        bits.append("被人看押着")
    if "injured" in kinds:
        bits.append("身上带着伤")
    if "unconscious" in kinds:
        bits.append("昏迷不醒")
    return bits


def _growth(state: WorldState, defs: Defs) -> list[str]:
    p = common.player(state)
    if p is None:
        return []
    start = common.teller(state, "chron_me0")
    if not isinstance(start, dict):
        return []  # 年谱从没记过开局的样子：不知道长进了多少，就不写
    out: list[str] = []
    for art, lvl in p.arts.items():
        before = int((start.get("arts") or {}).get(art, 0))
        if lvl <= before:
            continue
        ad = defs.arts.get(art)
        stage = ad.stages[lvl - 1].name if ad is not None and 0 < lvl <= len(ad.stages) else ""
        name = common.pv(state, defs, f"⟦{art}⟧")
        out.append(f"{name}练到了「{stage}」" if stage else f"{name}有了长进")
    if p.literacy > int(start.get("literacy", p.literacy)):
        out.append("识得的字比从前多了")
    gained = [r for r in p.roles if r not in (start.get("roles") or p.roles) and not r.startswith("surname:")]
    for r in gained:
        if r.startswith("disciple_of:"):
            out.append(f"拜了{common.pv(state, defs, '⟦' + r.split(':', 1)[1] + '⟧')}为师")
        else:
            label = (defs.world.labels or {}).get(f"role:{r}") or (defs.world.labels or {}).get(r)
            out.append(f"成了{label}" if label else "有了新的身份")
    return out


def _threads(state: WorldState, defs: Defs) -> tuple[list[str], list[str]]:
    opened = list(dict.fromkeys(common.pv(state, defs, t.text) for t in threads.open_threads(state)))
    # 了结了的：眼前事（情境）的开场与了结已经在年谱里各有一句，这里不再列（它的牵挂写的是
    # 「某某正在做什么」，放在「了结了的」底下读不通）；同一句只留一条；还开着的不算了结。
    kinds = [k for k in threads.KIND_ORDER if k != "event"]
    closed = [t for t in threads.recent_closed(state, defs, 0, kinds=kinds) if t not in opened]
    return opened, closed


def material(state: WorldState, defs: Defs) -> dict[str, Any]:
    """C7 与模板共用的素材：全是玩家知道的东西，已按玩家视角渲染。"""
    seen, heard = _known_lines(state, defs)
    opened, closed = _threads(state, defs)
    p = common.player(state)
    return {
        "background": defs.world.background,
        "ended": state.ended or "closed",
        "ended_text": ENDED_TEXT.get(state.ended or "closed", ENDED_TEXT["closed"]),
        "clock": common.clock(state.tick),
        "place": common.pv(state, defs, f"⟦{p.loc}⟧") if p is not None else "",
        "seen": seen,
        "heard": heard,
        "whereabouts": _whereabouts(state, defs),
        "open_threads": opened,
        "closed_threads": closed,
        "growth": _growth(state, defs),
    }


# ---------------------------------------------------------------- 模板


def template_text(state: WorldState, defs: Defs, m: dict[str, Any] | None = None) -> str:
    """C7 的退路：用年谱里的事实句拼成的后事。只写已经发生的事。"""
    m = m or material(state, defs)
    paras: list[str] = [f"{m['clock']}，{m['place']}。{m['ended_text']}"]
    seen = m["seen"][-SEEN_CAP:]
    if seen:
        paras.append("这些日子里，你亲眼见过的事：" + "；".join(seen) + "。")
    else:
        paras.append("这些日子过得平淡，没有什么大事落在你眼前。")
    heard = m["heard"][-HEARD_CAP:]
    if heard:
        paras.append("你还听人说起：" + "；".join(heard) + "。")
    if m["growth"]:
        paras.append("这一程下来，你" + "，".join(m["growth"]) + "。")
    if m["closed_threads"]:
        paras.append("了结了的：" + "；".join(m["closed_threads"][-4:]) + "。")
    if m["open_threads"]:
        paras.append("还没了结的：" + "；".join(m["open_threads"]) + "。这些事后来如何，你已无从知道。")
    if m["whereabouts"]:
        paras.append("收笔的时候，" + "；".join(m["whereabouts"]) + "。")
    paras.append(CLOSING)
    return "\n\n".join(paras)


def divergence_block(state: WorldState, defs: Defs) -> str:
    """规则列出的「与原著的分岔」完整清单（不经模型）。"""
    lines = chronicle.divergence_lines(state, defs, final=True)
    head = "【与原著的分岔】"
    if not lines:
        return head + "\n  （这一局没有记下与原著的分岔）"
    return head + "\n" + "\n".join(lines)


# ---------------------------------------------------------------- 流式


async def epilogue(llm: LLM, state: WorldState, defs: Defs) -> AsyncIterator[str]:
    """收场时调用：先流出 C7 的后事（出错退到模板），再流出分岔清单。"""
    m = material(state, defs)
    fallback = template_text(state, defs, m)
    system = prompting.load("c7_system")
    user = prompting.render("c7_user", **m)
    produced = 0
    try:
        async with aclosing(llm.claude.stream_text("C7", [system], user, mock_text=fallback)) as stream:
            async for piece in stream:
                if not piece:
                    continue
                produced += len(piece)
                yield piece
                if produced >= MAX_CHARS:
                    break
    except LLMError:
        if produced:
            yield "\n\n" + CLOSING
    if produced == 0:
        yield fallback  # 出错，或者模型什么也没写
    yield "\n\n" + divergence_block(state, defs)
