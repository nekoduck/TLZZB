"""分岔测试（方案 11.4）：每个场景是一段脚本化的玩家动作，接着让世界跑到第十日。

每个场景除了自己的「必须成立」，都统一断言 3.4 的三条保证（``Scene.assert_guarantees``）：
没有幽灵事件、没有回弹、没有停摆。真实的 content/，Mock 模型，真骰子。
"""

from __future__ import annotations

import pytest

from tianlong.config import Settings
from tianlong.kernel import checks, conflict, danger, growth, mood, predicates
from tianlong.kernel.state import TICKS_PER_DAY, TICKS_PER_HOUR, Ctx, is_alive
from tianlong.testing import dead_actors
from tianlong.world.common import REPLAN_FLAG

from .scenario import DAY2, DAY10, PLAYER, Scene, current_steps_touching, scene, spy

SEEDS = (1, 2, 3, 4, 5, 6)  # 真骰子：某个种子下脚本的前提不成立（人已经死了、摔伤了），就换下一个
SCROLL = "silk_scroll"
SCROLL_ARTS = ("lingbo", "beiming")


# ---------------------------------------------------------------- 先取卷轴


async def _take_scroll(sc: Scene) -> int | None:
    """第一日带绳下崖，进洞，取走帛卷。返回取卷那条事件的 id；骰子坏了事（摔伤走不动）返回 None。"""
    drv = sc.drv
    assert "rope" in drv.me.inventory
    if not await sc.goto("cliff_edge") or not await sc.goto("valley_floor"):
        return None
    await drv.do("examine", feature="vine_curtain")  # 垂藤后面是空的：看出洞口
    if not await sc.goto("valley_cave"):
        return None
    for _ in range(6):
        if SCROLL in drv.me.inventory:
            break
        for a in drv.legal("take", item=SCROLL):
            await drv.act(a)
            break
        else:
            await drv.do("search", feature="cushion")
    if SCROLL not in drv.me.inventory:
        return None
    takes = [e for e in sc.events(actor=PLAYER, ok=True) if e.params.get("item") == SCROLL or SCROLL in e.fact_text]
    return takes[-1].id


async def test_take_the_scroll_first(real_settings: Settings) -> None:
    for seed in SEEDS:
        async with scene(real_settings, seed=seed) as sc:
            take = await _take_scroll(sc)
            if take is None:
                continue  # 这个种子下玩家下崖时摔坏了：换一个
            await _scroll_checks(sc, take)
            return
    pytest.fail(f"the scripted descent never works with seeds {SEEDS}")


async def _scroll_checks(sc: Scene, take: int) -> None:
    take_tick = sc.tick
    assert take_tick < TICKS_PER_DAY  # 还在第一日
    sc.park()
    await sc.to_day10()
    st = sc.state
    assert st.ended is None and st.tick >= DAY10
    # 段誉此后从未持有帛卷，也没有学会那两门本事
    assert not sc.ever("duan_yu", lambda s: SCROLL in s.inventory)
    assert not sc.ever("duan_yu", lambda s: any(s.art(a) for a in SCROLL_ARTS))
    assert not any(sc.char("duan_yu").arts.get(a) for a in SCROLL_ARTS)
    assert not any(e.params.get("item") == SCROLL for e in sc.events(actor="duan_yu", after=take))
    # 靠帛卷的那一步没有做成过；轮到它时作废，没轮到就还没动
    assert not any(e.step == "dy_study_scroll" for e in sc.drv.events)
    # m_scroll 记为分岔，起因指向玩家取卷的事件
    d = sc.divergence("m_scroll")
    assert d is not None, [x.mark for x in st.divergences]
    assert d.cause_event == take
    assert d.tick > 128  # 过了参照的时刻，认定为分岔
    # 段誉之后仍有打算在推进：取卷之后他还做完过几步、做过事；只要活着在图内，时时有打算
    steps = sc.steps_of("duan_yu", after_tick=take_tick)
    assert len(steps) >= 2, steps
    assert len(sc.events(actor="duan_yu", after=take)) >= 3, sc.story("duan_yu", after=take)
    for s in sc.hist["duan_yu"]:
        if s.tick > take_tick and s.alive and s.onstage:
            assert s.step is not None or (s.agenda_status == "waiting" and s.waiting_for), s
    # 卷轴一直在玩家手里（没有人、也没有哪条规则把它送回去）
    assert st.items[SCROLL].holder == PLAYER
    sc.assert_guarantees(after_id=take)
    sc.assert_no_rollbacks()


# ---------------------------------------------------------------- 要人身死


@pytest.mark.parametrize("victim", ["sikong_xuan", "zhong_ling", "duan_yu"])
async def test_a_death_on_day_two(real_settings: Settings, victim: str) -> None:
    for seed in SEEDS:
        async with scene(real_settings, seed=seed) as sc:
            sc.park()
            await sc.run_to(DAY2 + TICKS_PER_HOUR)
            c = sc.state.chars[victim]
            if not (is_alive(c) and c.onstage):
                continue  # 这个种子下他没活到第二日（没有受保护的人）
            await _kill_and_check(sc, victim)
            return
    pytest.fail(f"{victim} is never alive on day 2 with seeds {SEEDS}")


async def _kill_and_check(sc: Scene, victim: str) -> None:
    drv, st = sc.drv, sc.state
    await drv.debug(f"kill {victim}")
    assert not is_alive(st.chars[victim]), drv.notices()
    deaths = [e for e in drv.events if e.verb == "die" and f"⟦{victim}⟧" in e.fact_text]
    assert len(deaths) == 1
    death = deaths[0]
    kill_tick = st.tick
    assert TICKS_PER_DAY <= kill_tick < 2 * TICKS_PER_DAY  # 第二日
    # 内核把与死者相关的人放进了重新打算的队列
    related = [c for c in st.replan_queue if is_alive(st.chars[c])]
    assert related, "nobody was queued to replan"
    # 与死者有关的步骤全部作废：死者自己的打算一步不剩
    ag = st.chars[victim].agenda
    assert ag is None or all(s.state != "pending" for s in ag.steps)
    touching = [(cid, sid) for cid, sid, _ in current_steps_touching(st, victim)]
    touching_ids = {sid for _, sid in touching}
    await sc.run_to(kill_tick + TICKS_PER_HOUR)
    # 相关的人在一个时辰内重新打算
    replanned: list[str] = []
    for cid in related:
        c = st.chars[cid]
        if not is_alive(c) or not c.onstage:
            continue
        assert cid not in st.replan_queue, (cid, st.replan_queue)
        if c.tier == "extra":
            continue  # 路人按作息表过日子，不重新打算
        # world/replan 的记账：这个人上一次重新打算是在哪个时辰（照旧做原来的打算也算打算过了）
        at = (st.flags.get(REPLAN_FLAG) or {}).get(cid)
        assert at is not None and at[0] * TICKS_PER_HOUR >= kill_tick - TICKS_PER_HOUR, (cid, at, kill_tick)
        replanned.append(cid)
    assert replanned, related
    await sc.to_day10()
    # 世界继续
    assert st.ended is None and st.tick >= DAY10
    for day in range(3, 11):
        lo, hi = (day - 1) * TICKS_PER_DAY, min(day * TICKS_PER_DAY, DAY10)
        assert any(lo <= e.tick < hi and e.source != "player" for e in drv.events), f"day {day} is silent"
    # 别人打算里指名要对死者做的步骤：死后没有一步产生过事件，没有一步做成；
    # 轮到了的已经作废（前提在动手那一刻查，8.2），还没轮到的前提已不成立——轮到时只会作废
    assert not any(e.step in touching_ids for e in drv.events if e.id > death.id)
    for cid, sid in touching:
        ag = st.chars[cid].agenda
        left = [x for x in (ag.steps if ag else []) if x.id == sid]
        assert all(x.state != "done" for x in left), (cid, sid)
    for cid, sid, _ in current_steps_touching(st, victim):
        step = next(x for x in st.chars[cid].agenda.steps if x.id == sid)
        assert not predicates.holds(st, drv.defs, step.pre, Ctx(self_id=cid)), (cid, sid, step.pre)
    # 死者不再出现在任何事件里（做事的、被做事的、听话的都不是他）
    assert dead_actors(drv.events, {victim: death.id}) == []
    assert not sc.ever(victim, lambda s: s.step is not None, after_tick=kill_tick)
    sc.assert_guarantees(after_id=death.id)
    sc.assert_no_rollbacks()


# ---------------------------------------------------------------- 替人送信


async def test_carry_the_message_in_his_place(real_settings: Settings) -> None:
    async with scene(real_settings) as sc:
        drv, st = sc.drv, sc.state
        # 玩家先到营地；被当作闯营的人扣下也无妨，要紧的是貂咬人的时候他在场
        assert await sc.goto("shennong_camp"), sc.story(PLAYER)
        bitten = await sc.wait_until(lambda: bool(st.flags.get("diao_bite_done")), until_tick=160)
        assert bitten and drv.me.loc == "shennong_camp", sc.story("sikong_xuan", limit=20)
        assert st.chars["duan_yu"].loc == "shennong_camp" and "captive" in {x.kind for x in st.chars["duan_yu"].statuses}
        # 说动司空玄：一句自由输入，经解析器与请求裁决（6.6）
        before = set(st.requests)
        await drv.send("我对司空玄说：让我替段公子去万劫谷取解药，我愿意服下那包药")
        asked = [r for rid, r in st.requests.items() if rid not in before and r.frm == PLAYER and r.to == "sikong_xuan"]
        assert asked, (drv.game.last_turn.notes if drv.game.last_turn else None, sc.story(PLAYER, limit=5))
        # 他答应了；答应即授「替人去」的身份（由请求的机制给，测试不代劳）。人质一个也没放
        req = asked[-1]
        assert req.status == "agreed", (req.action, req.verdict, req.status, drv.game.last_turn.notes, sc.story(PLAYER, limit=5))
        assert not drv.cheat_ids, drv.cheat_ids
        assert st.chars["duan_yu"].loc == "shennong_camp" and "captive" in {x.kind for x in st.chars["duan_yu"].statuses}
        assert "errand_volunteer" in drv.me.roles
        # 逼人取药的情境开场：服毒去送信的是玩家，人质是钟灵
        opened = await sc.wait_until(lambda: sc.situation("poison_errand") is not None, until_tick=200)
        assert opened, sc.story("sikong_xuan", limit=20)
        errand = sc.situation("poison_errand")
        assert errand.roles["messenger"] == PLAYER and errand.roles["hostage"] == "zhong_ling"
        swallowed = False
        for _ in range(10):
            if "poisoned_duanchang" in {x.kind for x in drv.me.statuses}:
                swallowed = True
            if sc.situation("poison_errand") is None:
                break
            if not swallowed and await sc.setpiece("swallow"):
                continue
            await sc.wait(1)
        me = {x.kind for x in drv.me.statuses}
        assert "poisoned_duanchang" in me and "captive" not in me, (me, sc.story(PLAYER, limit=12))
        assert "shennong_errand" in drv.me.roles and st.flags.get("errand_sent") is True
        assert "F_zhong_ling_held" in drv.me.beliefs
        sent = sc.last_id()
        # 玩家赶到万劫谷，把消息带到
        assert await sc.goto("zhong_hall"), (drv.me.loc, sc.story(PLAYER, limit=12))
        opened = await sc.wait_until(lambda: sc.situation("wanjie_news") is not None, until_tick=sc.tick + 24)
        assert opened, sc.story("gan_baobao", limit=12)
        assert sc.situation("wanjie_news").roles["messenger"] == PLAYER
        for _ in range(10):
            if st.flags.get("zhong_news_delivered") or sc.situation("wanjie_news") is None:
                break
            if not await sc.setpiece("tell_news"):
                await sc.wait(1)
        # 到万劫谷报信的是玩家
        assert st.flags.get("zhong_news_delivered") is True, sc.story("gan_baobao", limit=15)
        b = st.chars["gan_baobao"].beliefs.get("F_zhong_ling_held")
        assert b is not None and b.confidence >= 0.6
        tells = [e for e in sc.events(actor=PLAYER, after=sent) if e.id in sc.tr.sit_checks and sc.tr.sit_checks[e.id].kind == "wanjie_news"]
        assert tells, sc.story(PLAYER, after=sent, limit=12)
        delivered_tick = sc.tick
        sc.park()
        await sc.to_day10()
        # 玩家服了毒、没有回去换解药：七日一到毒发身死，这一局就此收场（没有受保护的人，玩家也不例外）
        assert st.ended is None or (st.ended == "dead" and st.tick >= errand_tick(sc, sent) + 600), (st.ended, st.tick)
        # 段誉留在营地：从取药的差事定下来到消息送到，他一直在营里；他没服过断肠散，也没领过这趟差事
        for s in sc.hist["duan_yu"]:
            if errand_tick(sc, sent) <= s.tick <= delivered_tick:
                assert s.loc == "shennong_camp", s
        assert not sc.ever("duan_yu", lambda s: "poisoned_duanchang" in s.statuses)
        assert "shennong_errand" not in st.chars["duan_yu"].roles
        assert not sc.ever("duan_yu", lambda s: s.loc in ("zhong_hall", "valley_gate", "zhong_inner"))
        assert not any(e.step in ("dy_leave_camp", "dy_errand_wanjie", "dy_borrow_horse") for e in drv.events)
        # 钟万仇针对姓段的人的那一步没有发生：没有发作，金盒没有托付出去
        assert not any(e.verb in ("rage", "entrust") for e in drv.events), sc.story("zhong_wanchou")
        assert st.items["gold_box"].holder != "duan_yu" and "F_mu_lodging" not in st.chars["duan_yu"].beliefs
        rel = st.chars["zhong_wanchou"].rel.get("duan_yu")
        assert rel is None or rel.affinity > -40
        sc.assert_guarantees(after_id=sent)
        sc.assert_no_rollbacks()


def errand_tick(sc: Scene, sent: int) -> int:
    return next(e.tick for e in sc.drv.events if e.id >= sent)


# ---------------------------------------------------------------- 告发私情

LOVERS = (("gan_guanghao", "gh_slip_out"), ("ge_guangpei", "gp_slip_out"))


async def test_inform_on_the_lovers(real_settings: Settings) -> None:
    async with scene(real_settings) as sc:
        drv, st = sc.drv, sc.state
        # 场景的前提（布景）：玩家亲眼见过那两人的私情；左子穆信得过他。
        # 不布这个景，按 6.7「听人说」的公式，一个陌生伙计的话只有五成，掌门不会当真。
        drv.fx([{"learn": [PLAYER, "F_lovers", 1.0, "saw"]}, {"rel": ["zuo_zimu", PLAYER, "trust", 25]}])
        evs = await sc.say("tell", "zuo_zimu", fact_refs=["F_lovers"])
        told = [e for e in evs if e.actor == PLAYER and e.speech is not None and "F_lovers" in e.speech.fact_refs]
        assert told and told[0].outcome == "ok", [sc.said(e) for e in evs]
        tell = told[0].id
        b = st.chars["zuo_zimu"].beliefs.get("F_lovers")
        assert b is not None and b.confidence >= predicates.BELIEVE and b.source == f"told:{PLAYER}", b
        sc.park()
        await sc.to_day10()
        assert st.ended is None
        # 那两人的私逃：「趁乱溜出去会合」这一步前提已不成立，没有做成；他们被拿，或另寻出路
        for who, step in LOVERS:
            assert not any(e.step == step for e in drv.events), sc.story(who, limit=20)
            ag = st.chars[who].agenda
            assert ag is None or all(s.state != "done" for s in ag.steps if s.id == step)
            seized = sc.ever(who, lambda s: "captive" in s.statuses)
            other_way = sc.ever(who, lambda s: s.step_origin == "replan" or not s.onstage)
            assert seized or other_way, sc.steps_of(who)
        # caught_eloping 没有以段誉为「撞见的人」开场；段誉没有坠崖
        caught = [
            e for e in drv.events
            if e.id in sc.tr.sit_checks and sc.tr.sit_checks[e.id].kind == "caught_eloping" and "⟦duan_yu⟧" in e.fact_text
        ]  # fmt: skip
        fell = sc.ever("duan_yu", lambda s: s.loc in ("valley_floor", "valley_cave")) or any(
            e.verb == "fall" and "⟦duan_yu⟧" in e.fact_text for e in drv.events
        )
        assert not caught, [f"t{e.tick} {sc.said(e)}" for e in caught[:3]]
        assert not fell, sc.story("duan_yu", limit=20)
        sc.assert_guarantees(after_id=tell)
        sc.assert_no_rollbacks()


# ---------------------------------------------------------------- 提前救人


async def test_lead_her_out_before_the_bite(real_settings: Settings) -> None:
    async with scene(real_settings) as sc:
        drv, st = sc.drv, sc.state
        zl = st.chars["zhong_ling"]
        # 场景的前提（布景）：钟灵信得过玩家。陌生人开口叫她跟着走，按 6.6 的表是 30 分，她不会答应。
        drv.fx([{"rel": ["zhong_ling", PLAYER, "trust", 70]}, {"rel": ["zhong_ling", PLAYER, "affinity", 70]}])
        assert await sc.goto("mountain_path")
        assert await sc.wait_until(lambda: zl.loc == "shennong_camp", until_tick=120, step=1), sc.story("zhong_ling")
        assert await sc.goto("shennong_camp")
        held = lambda c: "captive" in {x.kind for x in c.statuses}  # noqa: E731
        assert not st.flags.get("diao_bite_done") and not held(zl) and not held(drv.me), sc.story("zhong_ling", limit=10)
        # 开口叫她跟自己走：自由输入 → 解析 → 请求裁决 → 她答应
        before = set(st.requests)
        await drv.send("我对钟灵说：钟姑娘，跟我走吧，这里要出事")
        asked = [r for rid, r in st.requests.items() if rid not in before and r.frm == PLAYER and r.to == "zhong_ling"]
        assert asked and asked[-1].action.get("verb") == "follow", (drv.game.last_turn.notes, sc.story(PLAYER, limit=5))
        assert asked[-1].status == "agreed" and zl.following == PLAYER, asked[-1]
        lead = sc.last_id()
        # 答应了就跟得住：不靠布景作废她原来「待在营里」那一步，她自己跟着玩家走出营地、走到山门
        assert await sc.goto("mountain_path") and await sc.goto("jianhu_gate")
        assert zl.loc == "jianhu_gate" and not held(zl), (zl.loc, sc.story("zhong_ling", after=lead))
        assert any(e.verb == "follow_move" and e.actor == "zhong_ling" for e in sc.events(after=lead))
        assert not st.flags.get("diao_bite_done")
        sc.park()
        await sc.to_day10()
        assert st.ended is None
        # 司空玄没有中貂毒；poison_errand 不开场
        assert not st.flags.get("diao_bite_done") and not sc.ever("sikong_xuan", lambda s: "poisoned_diao" in s.statuses)
        assert not any(c.kind == "poison_errand" for c in sc.tr.sit_checks.values())
        assert not st.flags.get("errand_sent")
        # 没有人服断肠散
        assert not any(e.verb in ("swallow", "force", "demand") for e in drv.events)  # 情境里逼人服毒的那几拍
        took = [cid for cid in sc.hist if sc.ever(cid, lambda s: "poisoned_duanchang" in s.statuses)]
        how = [e for e in drv.events if "duanchang_san" in e.fact_text or e.params.get("item") == "duanchang_san"]
        assert not took, (took, [f"[{e.id} t{e.tick} {e.source}] {sc.said(e)}" for e in how[:4]])
        # 钟灵没有再落到神农帮手里
        assert not sc.ever("zhong_ling", lambda s: "captive" in s.statuses)
        # 神农帮重新打算：逼人取药、等人取药这两步都没有做成，帮主另有打算在走
        sk = st.chars["sikong_xuan"]
        assert not any(e.step in ("sk_force_errand", "sk_await") for e in drv.events)
        moved_on = [s for s in sc.steps_of("sikong_xuan") if s not in ("sk_besiege", "sk_hold_camp", "sk_force_errand", "sk_await")]
        assert moved_on, sc.steps_of("sikong_xuan")
        assert is_alive(sk)
        sc.assert_guarantees(after_id=lead)
        sc.assert_no_rollbacks()


# ---------------------------------------------------------------- 偷走解药

ANTIDOTE = "diao_antidote"


async def _fetch_antidote(sc: Scene) -> bool:
    """玩家摸进万劫谷，从内院的药柜里取出貂毒的解药。骰子或主人家坏了事返回 False。"""
    drv = sc.drv
    if not await sc.goto("pine_wood"):
        return False
    await drv.do("examine", feature="marked_trees")  # 松树上的记号：看出进谷的路
    if not await sc.goto("valley_gate"):
        return False
    # 谷口的规矩：细看大树，取下小锤敲几下（敲过的人，谷里的人不拦）
    await drv.do("examine", feature="knock_tree")
    for verb, kw in (("search", {"feature": "knock_tree"}), ("take", {"item": "gate_hammer"}), ("use", {"item": "gate_hammer"})):
        for a in drv.legal(verb, **kw)[:1]:
            await drv.act(a)
    if not await sc.goto("zhong_inner"):
        return False
    for _ in range(4):
        if ANTIDOTE in drv.me.inventory:
            return True
        grab = drv.legal("take", item=ANTIDOTE) or drv.legal("steal", item=ANTIDOTE)
        if grab:
            await drv.act(grab[0])
        else:
            await drv.do("search", feature="medicine_cabinet")
    return ANTIDOTE in drv.me.inventory


async def test_steal_the_antidote(real_settings: Settings) -> None:
    for seed in SEEDS:
        async with scene(real_settings, seed=seed) as sc:
            if not await _fetch_antidote(sc):
                continue  # 这个种子下玩家没能把药拿到手：换一个
            await _antidote_checks(sc)
            return
    pytest.fail(f"the antidote could never be fetched with seeds {SEEDS}")


async def _antidote_checks(sc: Scene) -> None:
    drv, st = sc.drv, sc.state
    sk = st.chars["sikong_xuan"]
    poisoned = lambda: "poisoned_diao" in {x.kind for x in sk.statuses}  # noqa: E731
    stolen = sc.last_id()
    assert st.items[ANTIDOTE].holder == PLAYER
    # 回到营地，交给司空玄（他这时已经中了貂毒）
    assert await sc.goto("shennong_camp"), (drv.me.loc, sc.story(PLAYER, after=stolen, limit=15))
    assert await sc.wait_until(poisoned, until_tick=sc.tick + 96), sc.story("sikong_xuan", limit=15)
    hostages = [c for c in ("zhong_ling", "duan_yu") if is_alive(st.chars[c]) and "captive" in {x.kind for x in st.chars[c].statuses}]
    give = drv.legal("give", item=ANTIDOTE, target="sikong_xuan")
    assert give, (drv.me.loc, [x.kind for x in drv.me.statuses], sk.loc)
    await drv.act(give[0])
    given = [e for e in sc.events(actor=PLAYER, verb="give", ok=True) if e.params.get("item") == ANTIDOTE]
    assert given and st.items[ANTIDOTE].holder == "sikong_xuan", sc.story(PLAYER, limit=6)
    give_id, give_tick = given[-1].id, sc.tick
    sc.park()
    await sc.to_day10()
    assert st.ended is None
    # 司空玄解毒：他自己服下了那瓶药，此后身上没有貂毒，活到了第十日
    used = [e for e in sc.events(actor="sikong_xuan", after=give_id, ok=True) if e.params.get("item") == ANTIDOTE]
    assert used, sc.story("sikong_xuan", after=give_id, limit=25)
    cure_tick = used[0].tick
    # （方案只要求他解毒。Mock 模型下，得了解药那一刻的重新打算取的是「照旧」，他要等原来那串打算走完才服药——
    #   种子 1 是三日之后；这段耽搁记在 docs/acceptance.md 里，不在这里卡。）
    assert cure_tick < 88 + 672, (give_tick, cure_tick)  # 赶在毒发之前
    assert not sc.ever("sikong_xuan", lambda s: "poisoned_diao" in s.statuses, after_tick=cure_tick)
    assert is_alive(sk) and not poisoned()
    # 「等人取回解药」不再是他的打算；解毒之后他另有打算在走
    assert not sc.ever("sikong_xuan", lambda s: s.step == "sk_await", after_tick=cure_tick + TICKS_PER_HOUR)
    assert len(sc.steps_of("sikong_xuan", after_tick=cure_tick)) >= 1
    # 人质的去留由他按当时的处境决定：没有哪条规则替他放人——人质脱身，只能出自角色的动作或情境的结算
    for cid in hostages:
        free_at = next((s.tick for s in sc.hist[cid] if s.tick > give_tick and s.alive and "captive" not in s.statuses), None)
        if free_at is None:
            continue  # 他没放：人还扣着（或者死在他手里），也是他的决定
        freed = [
            e for e in drv.events
            if give_id < e.id and e.tick <= free_at and f"⟦{cid}⟧" in e.fact_text
            and e.verb in ("release", "let_go", "escape", "slip_free", "free", "struggle", "rescue")
        ]  # fmt: skip
        assert freed, (cid, free_at, sc.story(cid, after=give_id, limit=20))
        assert all(e.source in ("mind", "agenda", "situation", "player") for e in freed), [(e.verb, e.source) for e in freed]
    sc.assert_guarantees(after_id=stolen)
    sc.assert_no_rollbacks()


# ---------------------------------------------------------------- 玩家变强

RIVAL = "gong_guangjie"  # 练武厅里当众动手的对手
BYSTANDER = "duan_yu"  # 亲见的人里不会武的一个


async def _show_strength(settings: Settings, *, buffed: bool) -> dict:
    """同一段脚本走两遍：不作弊的对照，与给了凌波微步第二层、60 点内力的一遍。返回各处记下的数。"""
    out: dict = {}
    async with scene(settings) as sc:
        drv, st, df = sc.drv, sc.state, sc.drv.defs
        me = drv.me
        if buffed:
            await drv.debug("art lingbo 2")
            await drv.debug("stat neili 60")
            assert me.arts.get("lingbo") == 2 and me.neili == 60, drv.notices()
        mark = sc.last_id()
        out["power"] = growth.power(st, df, me)
        out["flee_vs_rival"] = checks.flee_chance(st, df, me, [st.chars[RIVAL]])
        seers = [c.id for c in st.chars.values() if c.loc == me.loc and c.id != PLAYER and "hidden" not in {x.kind for x in c.statuses}]
        # 冲突：当众向对手动手
        with spy(conflict, "fight") as fights:
            acts = drv.legal("attack", target=RIVAL, intent="injure")
            assert acts
            out["attack_lethal"] = acts[0].lethal
            await drv.act(acts[0])
            out["attack_kind"] = drv.game.last_turn.kind
        out["fights"] = [f for _, f in fights if f.attacker == PLAYER]
        out["regard"] = {c: (st.chars[c].rel[PLAYER].regard if PLAYER in st.chars[c].rel else 0) for c in seers}
        # 待他的方式：同一句威胁，对方怕不怕
        by = st.chars[BYSTANDER]
        fear0, anger0 = by.mood.get("fear", 0), by.mood.get("anger", 0)
        await sc.say("threaten", BYSTANDER)
        out["fear_gain"] = by.mood.get("fear", 0) - fear0
        out["anger_gain"] = by.mood.get("anger", 0) - anger0
        # 逃跑：到山下的营地去，被扣下之后拔腿就逃
        assert await sc.goto("shennong_camp"), sc.story(PLAYER, limit=10)
        held = await sc.wait_until(lambda: "captive" in {x.kind for x in me.statuses}, until_tick=sc.tick + 48)
        out["held"] = held
        if held:
            out["power_at_flee"] = growth.power(st, df, me)
            with spy(checks, "roll_against") as rolls:
                flee = drv.legal("flee")
                assert flee, [x.kind for x in me.statuses]
                await drv.act(flee[0])
                if drv.game.last_turn.kind == "warn":  # 凶险的一逃：先被警示，再逃一次才算数
                    await drv.act(drv.legal("flee")[0])
            out["flee_rolls"] = [(a[2], r.ok) for a, r in rolls if a[1] == "flee"]  # (成功率, 成了没有)
        if buffed:
            sc.park()
            await sc.to_day10()
            assert st.ended is None
            sc.assert_guarantees(after_id=mark)
            sc.assert_no_rollbacks()
    return out


async def test_the_player_grows_strong(real_settings: Settings, tmp_path_factory: pytest.TempPathFactory) -> None:
    from tianlong.testing import make_settings

    base = await _show_strength(make_settings(tmp_path_factory.mktemp("control")), buffed=False)
    strong = await _show_strength(real_settings, buffed=True)
    # 数值变了：60 点内力抬高 power；凌波微步第二层给逃跑加 40
    assert base["power"] == 0 and strong["power"] > base["power"], (base["power"], strong["power"])
    gain = strong["power"] - base["power"]
    assert strong["flee_vs_rival"] == checks.clamp(base["flee_vs_rival"] + 40 + 12 * gain)
    # 冲突按新数值结算：对照里这一下是送死（内核先警示，不结算）；变强之后真打了一场，用的是新的 power
    assert base["attack_lethal"] and base["attack_kind"] == "warn" and base["fights"] == []
    assert not strong["attack_lethal"] and strong["attack_kind"] == "turn" and len(strong["fights"]) == 1
    fight = strong["fights"][0]
    assert fight.attacker_power == strong["power"] and fight.defender == RIVAL
    # 亲见的人对玩家的 regard 改变：看见他动手的人，眼里他的斤两就是新的 power
    assert strong["regard"] and all(v == strong["power"] for v in strong["regard"].values()), strong["regard"]
    assert all(v == 0 for v in base["regard"].values()), base["regard"]
    # 待他的方式随之改变：同一句威胁，对照里对方只是着恼；见过他出手之后，对方怕了
    assert base["fear_gain"] == 0 and base["anger_gain"] > 0, base
    assert strong["fear_gain"] > 0 and strong["anger_gain"] == 0, strong
    # 逃跑按新数值结算：被扣在营里拔腿就逃，内核掷骰用的成功率比对照高出「轻功 40 + 12 × power 之差」
    assert base["held"] and strong["held"], (base["held"], strong["held"])
    assert base["flee_rolls"] and strong["flee_rolls"], (base.get("flee_rolls"), strong.get("flee_rolls"))
    c0, c1 = base["flee_rolls"][0][0], strong["flee_rolls"][0][0]
    gain_at_flee = strong["power_at_flee"] - base["power_at_flee"]
    assert c1 > c0 and c1 == checks.clamp(c0 + 40 + 12 * gain_at_flee), (c0, c1, gain_at_flee)


# ---------------------------------------------------------------- 拜入门派

SECT = "wuliang_disciple"
SWORD = "wuliang_sword"
MASTER = "zuo_zimu"
PALACE = ("jianhu_hall", "jianhu_back", "jianhu_hall", "jianhu_gate")
SHENNONG = ("sikong_xuan", "sn_chief_a", "sn_chief_b")


def _challenged_by(sc: Scene, keeper_role: str) -> bool:
    """此刻有没有一场盘问是冲着玩家来的，盘问的人带着 ``keeper_role`` 这个身份。"""
    st = sc.state
    for s in st.situations.values():
        if s.kind == "challenge" and s.roles.get("intruder") == PLAYER:
            if keeper_role in st.chars[s.roles["keeper"]].roles:
                return True
    return False


async def _join_the_sect(settings: Settings, *, disciple: bool) -> dict:
    """同一段脚本走两遍：一个没有门派的外人（对照），与一个无量剑弟子。两遍都先去掉「来客」的身份。"""
    out: dict = {"palace_challenges": 0, "shennong_challenges": 0}
    async with scene(settings) as sc:
        drv, st, df = sc.drv, sc.state, sc.drv.defs
        me = drv.me
        await drv.debug("role jianhu_guest remove")
        if disciple:
            await drv.debug(f"role {SECT} add")
        assert (SECT in me.roles) == disciple and "jianhu_guest" not in me.roles, drv.notices()
        mark = sc.last_id()
        # 求教剑法：能不能学（6.12 的前提），掌门肯不肯教（6.6 的裁决）
        out["requires_ok"] = growth.requires_ok(st, df, me, SWORD)
        out["can_teach"] = growth.can_teach(st, df, st.chars[MASTER], me, SWORD)
        before = set(st.requests)
        await drv.send("我对左子穆说：请掌门教我无量剑法")
        asked = [r for rid, r in st.requests.items() if rid not in before and r.frm == PLAYER and r.to == MASTER]
        out["parsed_teach"] = bool(asked) and asked[-1].action.get("verb") == "teach"
        if not out["parsed_teach"]:  # 解析没对上，就直接按言语行为提这个请求
            before = set(st.requests)
            await sc.say("request", MASTER, ask_action={"verb": "teach", "params": {"art": SWORD, "target": PLAYER}})
            asked = [r for rid, r in st.requests.items() if rid not in before and r.frm == PLAYER and r.to == MASTER]
        assert asked, sc.story(PLAYER, limit=6)
        out["teach_verdict"], out["teach_status"], out["teach_class"] = asked[-1].verdict, asked[-1].status, asked[-1].action_class
        out["band_master"] = mood.band(df, st, MASTER, PLAYER)
        # 在剑湖宫走动：比剑散了之后，厅上、后院、山门各待上一个时辰，看有没有人拦下盘问
        assert await sc.wait_until(lambda: st.flags.get("bout_running") is False, until_tick=120), st.flags
        for place in PALACE:
            if not await sc.goto(place):
                break
            for _ in range(TICKS_PER_HOUR + 1):
                if _challenged_by(sc, SECT):
                    out["palace_challenges"] += 1
                    await sc.setpiece("explain")
                    break
                await sc.wait(1)
        out["palace_end"] = me.loc
        out["held_in_palace"] = "captive" in {x.kind for x in me.statuses}
        # 神农帮怎样待他：路卡上、营地里
        if not out["held_in_palace"] and await sc.goto("mountain_path"):
            for _ in range(TICKS_PER_HOUR + 1):
                if _challenged_by(sc, "shennong_member"):
                    out["shennong_challenges"] += 1
                    break
                await sc.wait(1)
        out["hostile"] = {c: danger.is_hostile(st, st.chars[c], PLAYER) for c in SHENNONG}
        out["band"] = {c: mood.band(df, st, c, PLAYER) for c in SHENNONG}
        out["affinity"] = {c: (st.chars[c].rel[PLAYER].affinity if PLAYER in st.chars[c].rel else 0) for c in SHENNONG}
        hits = [e for e in drv.events if e.verb in ("attack", "restrain", "seize") and e.actor is not None
                and "shennong_member" in st.chars[e.actor].roles and f"⟦{PLAYER}⟧" in e.fact_text]  # fmt: skip
        out["shennong_force"] = len(hits)
        if disciple:
            sc.park()
            await sc.to_day10()
            assert st.ended is None
            sc.assert_guarantees(after_id=mark)
            sc.assert_no_rollbacks()
    return out


async def test_join_the_sect(real_settings: Settings, tmp_path_factory: pytest.TempPathFactory) -> None:
    from tianlong.testing import make_settings

    outsider = await _join_the_sect(make_settings(tmp_path_factory.mktemp("control")), disciple=False)
    member = await _join_the_sect(real_settings, disciple=True)
    # 可以求教剑法：外人学不得、掌门决不肯教（撞上他的底线）；弟子学得，掌门那里不再是「决不」
    assert not outsider["requires_ok"] and outsider["teach_verdict"] == "never", outsider
    assert member["requires_ok"] and member["can_teach"], member
    assert member["teach_verdict"] != "never", member
    assert member["band_master"] in ("familiar", "trusted") and outsider["band_master"] in ("stranger", "hostile")
    # 可以在剑湖宫走动：外人被门下弟子拦下盘问；弟子没有人拦
    assert outsider["palace_challenges"] >= 1, outsider
    assert member["palace_challenges"] == 0 and not member["held_in_palace"], member
    # 神农帮把他当对头：成了无量剑弟子之后，神农帮的人对他有敌意（对照里的外人没有）
    as_enemy = any(member["hostile"].values()) or "hostile" in member["band"].values()
    control_enemy = any(outsider["hostile"].values()) or "hostile" in outsider["band"].values()
    assert as_enemy, (member["hostile"], member["band"], member["affinity"])
    assert not control_enemy, (outsider["hostile"], outsider["band"], outsider["affinity"])
