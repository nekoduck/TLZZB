"""重新打算（方案 8.3）：消费 ``state.replan_queue``，从规则生成的候选里选一个，落成新打算。

1. 列目标、配步骤、打分：``world/goals.py``（读 ``content/goals.yaml``），留前 12 个。
   由事件触发（不是一步作废或做完）、且当前这一步的前提仍成立时，「照旧做原来的打算」也是一个候选，
   描述前加「（你本来要做的）」。这样一个人被擒、受伤时，他原来那串打算不会白白丢掉。
2. 抉择：J3 在候选里选一个（+ needs_thought）；没把握，或有魂角色的整份打算落空，交 C5；
   都失败就取规则分最高的一个（4.3 的退让表）。路由按 ``settings.route.replan``。
3. 落地：经效果 ``agenda`` 整份替换，步骤的 origin 记 replan。没有候选时转入 waiting，写明在等什么，记日志。

同一个人每个时辰至多重新打算 ``limits.replan_per_hour`` 次（记账旗标 ``__replan_at__``）。
J3 / C5 的请求并行发出；取样与落地按队列的先后逐个做，随机数来自状态里的种子，整局可以重放。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from tianlong import prompting
from tianlong.kernel import danger, growth, mood, perception, refs, resolve, rng, timeflow
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import TICKS_PER_HOUR, Character, Ctx, Event, WorldState, has_status, injury_level
from tianlong.llm import STAR_PREFIX, LLMError
from tianlong.llm.route import sample_choice, should_escalate
from tianlong.llm.schemas import ReplanOut
from tianlong.world import goals as G
from tianlong.world.common import LOG, REPLAN_FLAG, flag_dict, set_flag_dict

MAX_MEMORIES = 6


@dataclass
class Job:
    cid: str
    trigger: str  # 'void' / 'done' / 'event'
    what: str  # 【出了什么事】
    important: bool  # 有魂角色的整份打算落空
    cands: list[G.Candidate] = field(default_factory=list)
    j3: Any = None  # J3 的答案，或 LLMError
    c5: Any = None  # C5 的结果 (goal_id, step_ids)，或 LLMError
    via: str = "rule"
    escalated: bool = False
    choice: G.Candidate | None = None
    steps: list[dict] | None = None


# ---------------------------------------------------------------- queue


def _dequeue(state: WorldState, cid: str) -> None:
    """取出队列里的人。内核没有出队的效果（见交付说明），这里是 world 唯一直接写状态的地方。"""
    while cid in state.replan_queue:
        state.replan_queue.remove(cid)


def classify_trigger(state: WorldState, c: Character) -> str:
    """这次重新打算是怎么来的：上一步刚作废（void）、整份做完（done），还是出了事（event）。"""
    ag = c.agenda
    if ag is None or ag.status == "waiting":
        return "event"
    if 0 < ag.cursor <= len(ag.steps) and ag.steps[ag.cursor - 1].state == "void":
        return "void"
    if ag.cursor >= len(ag.steps) or ag.status == "done":
        return "done"
    return "event"


def _what(state: WorldState, c: Character, trigger: str) -> str:
    ag = c.agenda
    if trigger == "void" and ag is not None:
        st = ag.steps[ag.cursor - 1]
        return f"原来打算「{st.text}」，可眼下做不成了。"
    if trigger == "done" and ag is not None:
        return f"「{ag.goal}」这件事已经了结，接下来没有别的打算。"
    if ag is None:
        return "眼下没有什么打算。"
    if ag.status == "waiting":
        return f"一直在等：{ag.waiting_for or '看看局面'}。"
    recent = [m for m in c.memory if m.kind != "reflection"][-1:]
    if recent:
        return f"刚出了事：{recent[0].text}"
    return "局面有了变化。"


def _whole_plan_failed(c: Character, trigger: str) -> bool:
    if c.tier != "soul" or trigger != "void" or c.agenda is None:
        return False
    return not any(s.state == "pending" for s in c.agenda.steps[c.agenda.cursor :])


# ---------------------------------------------------------------- texts for the models


def j3_state(state: WorldState, defs: Defs, c: Character, what: str) -> str:
    card = defs.card_for(c.persona) or defs.cards.get(c.id)
    who = c.name
    if card is not None:
        bits = [card.self_intro] if card.self_intro else []
        if card.values:
            bits.append("；".join(card.values[:4]))
        if bits:
            who = f"{c.name}。" + "。".join(b.rstrip("。") for b in bits) + "。"
    loc = state.locations.get(c.loc)
    place = loc.name if loc is not None else c.loc
    now = f"{timeflow.clock_text(state.tick)}，{place}。" + ("场面紧张。" if state.scene_mode == "tense" else "")
    me = growth.power(state, defs, c)
    present = []
    for o in perception.visible_people(state, c):
        r = c.rel.get(o.id)
        regard = r.regard if r is not None else 0
        present.append(f"{o.name}（{mood.trust_text(r.trust if r else 0)}；{mood.relative_regard_text(regard, me)}）")
    tail = [e for e in state.events_tail if c.id in e.witnesses and e.tick >= state.tick - TICKS_PER_HOUR]
    recent = "。".join(refs.render(e.fact_text, state, defs) for e in tail[-3:])
    mems = sorted(
        (m for m in c.memory if m.salience >= 5 or m.kind == "reflection"), key=lambda m: (m.salience, m.tick)
    )[-MAX_MEMORIES:]
    memories = [refs.render(m.text, state, defs) for m in sorted(mems, key=lambda m: m.tick)]
    return prompting.render(
        "jev/j3_state",
        who=who,
        now=now,
        present="｜".join(present),
        recent=recent,
        memories=memories,
        status=_status_text(c),
        plan=_plan_text(state, defs, c),
        what=refs.render(what, state, defs),
    )


def _status_text(c: Character) -> str:
    bits = [mood.mood_text(c)]
    lvl = injury_level(c)
    bits.append(("受了轻伤", "伤得不轻", "受了重伤，走不动")[lvl - 1] if lvl else "没有受伤")
    if any(s.kind.startswith("poisoned") for s in c.statuses):
        bits.append("中了毒")
    if has_status(c, "captive"):
        bits.append("被人扣着")
    if has_status(c, "bound"):
        bits.append("被捆着")
    return "；".join(bits) + "。"


def _plan_text(state: WorldState, defs: Defs, c: Character) -> str:
    ag = c.agenda
    if ag is None:
        return "没有打算。"
    st = danger.current_step(c)
    if st is None:
        return f"{refs.render(ag.goal, state, defs)}。眼下没有要做的事。"
    return f"{refs.render(ag.goal, state, defs)}。眼下要{refs.render(st.text, state, defs)}。"


def _option_text(state: WorldState, defs: Defs, cd: G.Candidate) -> str:
    text = refs.render(cd.option_text(), state, defs)
    return STAR_PREFIX + text if cd.keep else text


def _card_text(state: WorldState, defs: Defs, c: Character) -> str:
    card = defs.card_for(c.persona) or defs.cards.get(c.id)
    drives = [refs.render(G.drive_text(state, defs, d), state, defs) for d in c.drives]
    if card is None:
        return prompting.render("c5_card", name=c.name, intro="", values=[], drives=drives, bottom_lines=[], choices=[])
    return prompting.render(
        "c5_card",
        name=card.name,
        intro=card.self_intro,
        values=card.values,
        drives=drives,
        bottom_lines=[b.text for b in card.bottom_lines if b.text],
        choices=[f"{ch.situation}：{ch.did}" for ch in card.choices if ch.situation],
    )


def c5_payload(state: WorldState, defs: Defs, c: Character, job: Job) -> tuple[list[str], str]:
    groups = G.groups(job.cands)
    goals_view = []
    for gid, cds in groups.items():
        steps = [{"id": sid, "text": refs.render(sd["text"], state, defs)} for sid, sd in G.step_ids(gid, cds).items()]
        goals_view.append({"id": gid, "text": refs.render(cds[0].goal, state, defs), "steps": steps})
    keep = next((cd for cd in job.cands if cd.keep), None)
    if keep is not None:
        goals_view.insert(0, {"id": G.KEEP_ID, "text": "照原来的打算做下去：" + refs.render(keep.option_text(), state, defs), "steps": []})
    user = prompting.render(
        "c5_user",
        what=refs.render(job.what, state, defs),
        situation=j3_state(state, defs, c, job.what),
        goals=goals_view,
    )
    system = [prompting.load("c5_system"), _card_text(state, defs, c)]
    return system, user


# ---------------------------------------------------------------- calls


def _j3_questions(state: WorldState, defs: Defs, cands: list[G.Candidate]) -> dict:
    return {
        "goal": {
            "type": "choice",
            "instructions": prompting.render("jev/j3_goal"),
            "criteria": {cd.id: _option_text(state, defs, cd) for cd in cands},
        },
        "needs_thought": {"type": "noul", "instructions": prompting.render("jev/j3_needs_thought")},
    }


async def _ask_j3(llm: Any, state: WorldState, defs: Defs, job: Job) -> Any:
    c = state.chars[job.cid]
    try:
        text = j3_state(state, defs, c, job.what)
        hints = {"goal": job.cands[0].id, "needs_thought": 0.0}
        return await llm.jev.ask("J3", text, _j3_questions(state, defs, job.cands), hints=hints)
    except LLMError as e:
        return e


async def _ask_c5(llm: Any, state: WorldState, defs: Defs, job: Job, *, escalated: bool) -> Any:
    c = state.chars[job.cid]
    try:
        system, user = c5_payload(state, defs, c, job)
        default = _rule_c5(job)
        obj, _meta = await llm.claude.call_json(
            "C5", system, user, ReplanOut, mock_default=lambda: default, escalated=escalated
        )
        return obj
    except LLMError as e:
        return e


def _rule_c5(job: Job) -> ReplanOut:
    best = job.cands[0]
    if best.keep:
        return ReplanOut(goal=G.KEEP_ID, steps=[], inner="还是照原来的打算做。")
    sids = list(G.step_ids(best.group, [best]))
    return ReplanOut(goal=best.group, steps=sids[:3], inner=f"先{best.first_text}再说。")


def _from_c5(job: Job, out: ReplanOut) -> tuple[G.Candidate, list[dict] | None] | None:
    """C5 的答案对回候选。目标或步骤不在候选里的一律不收（模型只能在候选里挑和排）。"""
    if out.goal == G.KEEP_ID:
        keep = next((cd for cd in job.cands if cd.keep), None)
        return (keep, None) if keep is not None else None
    groups = G.groups(job.cands)
    cds = groups.get(out.goal)
    if not cds:
        return None
    pool = G.step_ids(out.goal, cds)
    picked = [pool[s] for s in out.steps[:3] if s in pool]
    if not picked:
        return cds[0], None
    owner = next((cd for cd in cds if any(sd is picked[0] for sd in cd.steps)), cds[0])
    return owner, picked


# ---------------------------------------------------------------- entry


async def process_queue(llm: Any, state: WorldState, defs: Defs, *, rules_only: bool = False) -> list[Event]:
    """消费 replan_queue。``rules_only`` 为真时不调用模型（基线跑的 --lucky 用）。"""
    if not state.replan_queue:
        return []
    per_hour = max(1, int(llm.settings.limits.replan_per_hour)) if llm is not None else 1
    hour = state.tick // TICKS_PER_HOUR
    book = flag_dict(state, REPLAN_FLAG)
    jobs: list[Job] = []
    for cid in list(state.replan_queue):
        c = state.chars.get(cid)
        if c is None or cid == state.player or not perception.on_map(c) or c.tier == "extra":
            _dequeue(state, cid)
            continue
        h, n = (book.get(cid) or [-1, 0])
        if h == hour and n >= per_hour:
            continue  # 这个时辰已经重新打算过：留在队里，下个时辰再说
        _dequeue(state, cid)
        book[cid] = [hour, (n + 1) if h == hour else 1]
        trigger = classify_trigger(state, c)
        job = Job(cid=cid, trigger=trigger, what=_what(state, c, trigger), important=_whole_plan_failed(c, trigger))
        job.cands = G.candidates(state, defs, cid, allow_keep=(trigger == "event"))
        if job.cands and all(cd.keep or cd.rule in G.GENERIC_GOALS for cd in job.cands):
            # 规则表覆盖不到他眼下的处境：只剩照旧与等一等。记一笔，供补规则用（8.3 第三条保证）
            LOG.info("replan %s: no rule in goals.yaml covers drives %s (%s)", cid, c.drives, job.what)
        jobs.append(job)
    set_flag_dict(state, defs, REPLAN_FLAG, book)
    if not jobs:
        return []
    mode = "rules_only" if rules_only or llm is None else llm.mode("replan")
    th = llm.settings.jev.thresholds if llm is not None else None
    live = [j for j in jobs if j.cands]
    # 先并行问 J3（或 C5），取样与落地按先后逐个做
    if mode == "jev_first":
        answers = await asyncio.gather(*(_ask_j3(llm, state, defs, j) for j in live))
        for j, a in zip(live, answers, strict=True):
            j.j3 = a
        need_c5 = [j for j in live if isinstance(j.j3, LLMError) or should_escalate(j.j3, th, important=j.important)]
        for j in need_c5:
            j.escalated = True
    elif mode in ("claude_first", "claude_only"):
        need_c5 = list(live)
    else:
        need_c5 = []
    if need_c5:
        outs = await asyncio.gather(
            *(_ask_c5(llm, state, defs, j, escalated=(mode == "jev_first")) for j in need_c5)
        )
        for j, o in zip(need_c5, outs, strict=True):
            j.c5 = o
    if mode == "claude_first":
        retry = [j for j in live if isinstance(j.c5, LLMError) or j.c5 is None]
        answers = await asyncio.gather(*(_ask_j3(llm, state, defs, j) for j in retry))
        for j, a in zip(retry, answers, strict=True):
            j.j3 = a
    out: list[Event] = []
    for j in jobs:
        _choose(state, j, mode, th)
        out += _land(state, defs, j)
    return out


def _choose(state: WorldState, job: Job, mode: str, th: Any) -> None:
    if not job.cands:
        return
    if job.c5 is not None and not isinstance(job.c5, LLMError):
        got = _from_c5(job, job.c5)
        if got is not None:
            job.choice, job.steps = got
            job.via = "C5"
            return
        LOG.info("C5 answer outside the candidates for %s: %s", job.cid, job.c5)
    usable_j3 = job.j3 is not None and not isinstance(job.j3, LLMError)
    # jev_first：J3 没把握时已交 C5，C5 也失败就走规则，不再回头用那个没把握的答案（4.3）
    if usable_j3 and (mode == "claude_first" or not job.escalated):
        a = job.j3["goal"]
        probs = {k: v for k, v in a.probabilities.items() if any(cd.id == k for cd in job.cands)}
        if probs:
            floor = th.sample_floor if th is not None else 0.15
            pick = sample_choice(probs, lambda: rng.uniform(state), floor)
            cd = next((x for x in job.cands if x.id == pick), None)
            if cd is not None:
                job.choice = cd
                job.via = "J3"
                return
        LOG.info("J3 answer outside the candidates for %s", job.cid)
    job.choice = job.cands[0]
    job.via = "rule"


def _land(state: WorldState, defs: Defs, job: Job) -> list[Event]:
    c = state.chars[job.cid]
    if job.choice is None:
        LOG.warning("replan: no candidate for %s (%s); waiting", job.cid, job.what)
        ag = G.waiting_agenda(c.agenda.goal if c.agenda else "过一天算一天", "规则表里没有合用的打算，先照日常作息过日子")
        return resolve.run_effects(state, defs, [{"agenda": [c.id, ag]}], Ctx(self_id=c.id), source="rule")
    if job.choice.keep:
        LOG.info("replan %s: keep current agenda (%s)", job.cid, job.via)
        return []
    ag = G.instantiate(state, defs, c.id, job.choice, job.steps)
    LOG.info("replan %s -> %s [%s] via %s", job.cid, job.choice.rule, job.choice.template, job.via)
    return resolve.run_effects(state, defs, [{"agenda": [c.id, ag]}], Ctx(self_id=c.id), source="rule")


def queue(state: WorldState, defs: Defs, cids: list[str]) -> None:
    """把人放进重新打算的队列（经效果 replan）。"""
    effs = [{"replan": cid} for cid in cids if cid in state.chars]
    if effs:
        resolve.run_effects(state, defs, effs, source="rule")


__all__ = ["process_queue", "classify_trigger", "j3_state", "c5_payload", "queue", "Job"]
