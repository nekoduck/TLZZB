"""真实 content/ 上的回归（wave 3 的 C9–C13、C15、C16）：身份的说法、禁用词、情境的标题与守卫、谷口敲树。

只断言内容自己的约定，不依赖剧情的时间表（时间表在 test_baseline.py）。
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

import pytest

from tianlong.content.loader import load
from tianlong.kernel import actions, effects, mood, resolve, timeflow
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Ctx, WorldState, has_status
from tianlong.minds import routines
from tianlong.world import goals

CONTENT = Path(__file__).resolve().parents[2] / "content"
ROLE_PRED = re.compile(r"role: ?\[[^,\]]+, ?'?([A-Za-z_]+)'?[,\]]")


@cache
def _base() -> tuple[Defs, WorldState]:
    return load(CONTENT)


def world() -> tuple[Defs, WorldState]:
    defs, st = _base()
    return defs, st.model_copy(deep=True)


def fx(st: WorldState, defs: Defs, effs: list[dict], self_id: str | None = None) -> None:
    effects.apply_effects(st, defs, effs, Ctx(self_id=self_id), source="rule")


def do(st: WorldState, defs: Defs, who: str, verb: str, **params: str) -> list:
    a = actions.build(st, defs, who, verb, params)
    assert a is not None, (who, verb, params)
    evs = resolve.apply(st, defs, a, source="player" if who == st.player else "mind")
    return evs + timeflow.run_due_scheduled(st, defs)


# ---------------------------------------------------------------- C9：身份的说法


def roles_in_use(st: WorldState) -> set[str]:
    used = {r for c in st.chars.values() for r in c.roles}
    for p in CONTENT.rglob("*.yaml"):
        used |= set(ROLE_PRED.findall(p.read_text(encoding="utf-8")))
    return {r for r in used if ":" not in r}  # surname:… 与 disciple_of:… 由界面自己拼


def test_every_role_has_a_plain_label() -> None:
    defs, st = world()
    labels = defs.world.labels
    # 与专名表里的词同名的身份不另写说法：界面退回去查专名表（ui.commands.role_label）
    missing = sorted(r for r in roles_in_use(st) if not labels.get(f"role:{r}") and r not in defs.lexicon.terms)
    assert missing == []
    for key, text in labels.items():
        if key.startswith("role:"):
            # 界面与后日谈原样打印这句话：不能带要渲染的记号
            assert "⟦" not in text and "{" not in text, key


def test_sect_roles_are_listed_and_exclude_passing_identities() -> None:
    defs, st = world()
    sects = set(defs.world.request.sect_roles or [])
    assert sects
    assert sects <= roles_in_use(st)
    # 占着地盘的身份都算门派、人家
    assert {loc.owner for loc in st.locations.values() if loc.owner} <= sects
    # 来客、马帮、差使、同一个上头：不算同门
    for r in ("jianhu_guest", "ma_caravan_hand", "ma_caravan_master", "shennong_errand", "lingjiu_vassal"):
        assert r not in sects, r
    assert not mood.same_sect(defs, st.chars["sikong_xuan"], st.chars["zuo_zimu"], st)  # 同是下属，不同门
    assert mood.same_sect(defs, st.chars["rui_popo"], st.chars["ping_popo"], st)


# ---------------------------------------------------------------- C10、C15：禁用词


def test_lexicon_keeps_ordinary_words_and_bans_modern_ones() -> None:
    defs, _ = world()
    game, modern = set(defs.lexicon.banned_game), set(defs.lexicon.banned_modern)
    for w in ("回合", "任务"):
        assert w not in game, w
    assert "经验值" in game
    for w in ("可乐", "汽水", "手表", "子弹", "飞机", "火车", "汽车", "电视", "网络", "微信"):
        assert w in modern, w
    # 不误伤寻常说法：这些话里不该有一个禁用词
    ordinary = "小时候跟着老师父走江湖，斗了三个回合；香烟缭绕的庙里，领了师门的任务，背着包袱下山。"
    assert [w for w in game | modern if w in ordinary] == []


def test_player_facing_content_has_no_banned_words() -> None:
    defs, _ = world()
    banned = [*defs.lexicon.banned_modern, *defs.lexicon.banned_game]
    hits: list[str] = []
    for p in CONTENT.rglob("*.yaml"):
        if p.name == "lexicon.yaml":
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            body = line.split("#", 1)[0]
            hits += [f"{p.name}:{n}: {w}" for w in banned if w in body]
    assert hits == []


# ---------------------------------------------------------------- C11


def test_antidote_fact_names_the_antidote() -> None:
    _, st = world()
    text = st.facts["F_duanchang_antidote_sikong"].text
    assert "⟦duanchang_antidote⟧" in text and "⟦sikong_xuan⟧" in text


# ---------------------------------------------------------------- C12、C16：情境的标题与守卫


def test_every_situation_has_title_and_chronicle_lines() -> None:
    defs, _ = world()
    for sid, d in defs.situations.items():
        extra = d.model_extra or {}
        for key in ("title", "chronicle_open", "chronicle_close"):
            text = extra.get(key)
            assert isinstance(text, str) and text.strip(), (sid, key)
            for role in re.findall(r"\{(\w+)\}", text):
                assert role in d.roles, (sid, key, role)


def _flags_set(effs: list | None) -> set[str]:
    out: set[str] = set()
    for e in effs or []:
        if not isinstance(e, dict):
            continue
        if "set_flag" in e:
            out.add(str(e["set_flag"][0]))
        chk = e.get("check")
        if isinstance(chk, dict):
            out |= _flags_set(chk.get("ok")) | _flags_set(chk.get("fail"))
    return out


def _mentions(pred: object, flag: str) -> bool:
    if isinstance(pred, dict):
        return any(_mentions(v, flag) for v in pred.values())
    if isinstance(pred, list):
        return any(_mentions(v, flag) for v in pred)
    return pred == flag


def test_lean_beats_do_not_stay_starred_after_they_happen() -> None:
    """性子里会出的一招（lean）出过之后不该还挂着星：要么它立的旗标让情境收场，要么它自己的前提排除这面旗标。"""
    defs, _ = world()
    bad: list[str] = []
    for sid, d in defs.situations.items():
        for name, a in d.actions.items():
            if not a.lean:
                continue
            flags = _flags_set(a.effects)
            ends = any(_mentions(d.exit_when, f) for f in flags)
            guarded = any(_mentions(a.pre, f) for f in flags)
            if not ends and not guarded:
                bad.append(f"{sid}.{name}")
    assert bad == []


def test_question_is_asked_once() -> None:
    defs, st = world()
    fx(st, defs, [{"move": ["player", "zhong_hall"]}])
    fx(st, defs, [{"open": ["challenge", "zhong_hall", {"keeper": "wj_servant_1", "intruder": "player"}]}])
    sid = next(s.id for s in st.situations.values() if s.kind == "challenge")
    assert actions.build(st, defs, "wj_servant_1", "setpiece", {"situation": sid, "name": "question"})
    do(st, defs, "wj_servant_1", "setpiece", situation=sid, name="question")
    assert actions.build(st, defs, "wj_servant_1", "setpiece", {"situation": sid, "name": "question"}) is None


# ---------------------------------------------------------------- C13：谷口敲树，火折子与水囊


def _knock(st: WorldState, defs: Defs) -> None:
    fx(st, defs, [{"move": ["player", "valley_gate"]}])
    do(st, defs, "player", "examine", feature="knock_tree")
    assert "F_gate_knock" in st.chars["player"].beliefs  # 细看大树，知道了规矩
    do(st, defs, "player", "search", feature="knock_tree")
    do(st, defs, "player", "take", item="gate_hammer")
    evs = do(st, defs, "player", "use", item="gate_hammer")
    assert any(e.verb == "knock" and e.loud for e in evs)


def _servant_verdict(st: WorldState, defs: Defs) -> str:
    """玩家走进正厅，仆役照他的作息表盘问、处置；返回玩家身上 vetted 的来由。"""
    fx(st, defs, [{"move": ["player", "zhong_hall"]}])
    fx(st, defs, [{"open": ["challenge", "zhong_hall", {"keeper": "wj_servant_1", "intruder": "player"}]}])
    verbs: list[str] = []
    for _ in range(3):
        d = routines.act(st, defs, "wj_servant_1")
        evs = resolve.apply(st, defs, d.action, d.speech, source="mind")
        verbs += [e.verb for e in evs]
        if has_status(st.chars["player"], "vetted"):
            break
    assert has_status(st.chars["player"], "vetted"), verbs
    return verbs[-1]


def test_knocking_at_the_gate_gets_you_admitted() -> None:
    defs, st = world()
    _knock(st, defs)
    assert has_status(st.chars["player"], "knocked")
    assert _servant_verdict(st, defs) == "let_pass"


def test_walking_in_without_knocking_gets_you_driven_off() -> None:
    defs, st = world()
    assert _servant_verdict(st, defs) == "drive_off"


def test_knocking_elsewhere_does_nothing() -> None:
    defs, st = world()
    fx(st, defs, [{"spawn": ["gate_hammer", "player"]}, {"move": ["player", "pine_wood"]}])
    evs = do(st, defs, "player", "use", item="gate_hammer")
    assert not has_status(st.chars["player"], "knocked")
    assert not any(e.loud for e in evs)


@pytest.mark.parametrize("item", ["tinder", "waterskin"])
def test_everyday_tools_do_something_when_used(item: str) -> None:
    defs, st = world()
    assert st.items[item].props.get("on_use")
    evs = do(st, defs, "player", "use", item=item)
    assert len(evs) >= 2  # 「用了」之外还有一句它自己的事
    assert st.items[item].holder == "player"  # 不是用一次就没了的东西
    if item == "tinder":
        assert has_status(st.chars["player"], "lit")


# ---------------------------------------------------------------- 重新打算：手里有秘籍


def test_curious_holder_of_the_scroll_gets_a_study_candidate() -> None:
    """手里东西再多，也认得出那卷帛书（原来按 id 挑第一件手里的东西，这条目标从不出现）。"""
    defs, st = world()
    fx(st, defs, [{"spawn": [iid, "duan_yu"]} for iid in ("black_rose", "gold_box", "silk_scroll")])
    cands = {c.rule: c for c in goals.candidates(st, defs, "duan_yu")}
    assert "study_manual" in cands
    step = cands["study_manual"].steps[0]
    assert step["do"] == {"verb": "study", "item": "silk_scroll", "art": "lingbo"}
    assert cands["study_manual"].score > cands["idle"].score
    # 没有帛卷的人没有这一条；被扣着的人也没有
    assert "study_manual" not in {c.rule for c in goals.candidates(st, defs, "zhong_ling")}
    fx(st, defs, [{"add_status": ["duan_yu", "captive", {"source": "sikong_xuan"}]}])
    assert "study_manual" not in {c.rule for c in goals.candidates(st, defs, "duan_yu")}
