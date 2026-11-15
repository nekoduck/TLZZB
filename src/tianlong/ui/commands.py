"""斜杠命令（方案 2.7）：命令表、解析，以及只读画面的纯函数渲染 ``(state, defs) -> str``。

``game`` 负责分派（ARCHITECTURE 8）：``view`` 类的命令由这里渲染；``/牵挂 /年谱`` 归 storyteller；
``/存档 /读档`` 归 store；``/心愿`` 归 parser 加 storyteller；``/收笔`` 是收场；``/退出`` 由 ui 自己处理；
``/debug`` 只在 ``--debug`` 下可用。

这里的画面只给玩家看得到、知道的东西：人名按玩家的称呼表写；别人待你如何只写档位文字，从不写数字。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import NamedTuple

from tianlong.kernel import growth, mood, perception, refs, timeflow
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Character, Relation, WorldState, is_alive

# ---------------------------------------------------------------- 命令表


class CommandSpec(NamedTuple):
    kind: str  # view / storyteller / wish / store / close / exit / debug
    help: str
    arg: str = ""  # 参数的写法，帮助里显示；空表示不带参数
    debug: bool = False  # 只在 --debug 下可用


COMMANDS: dict[str, CommandSpec] = {
    "状态": CommandSpec("view", "此刻的时辰、所在、身子、银钱与身份"),
    "物品": CommandSpec("view", "随身带着的东西，和此处地上看得见的东西"),
    "人物": CommandSpec("view", "见过的人，以及他们待你如何"),
    "本事": CommandSpec("view", "会的本事、练到了哪一层，识字几何"),
    "牵挂": CommandSpec("storyteller", "你心里挂着的事：期限、承诺、线索、心愿"),
    "回顾": CommandSpec("view", "重看最近几回合的文字", "回合数"),
    "年谱": CommandSpec("storyteller", "亲历与听说的大事、各人对你的态度、与原著的分岔"),
    "心愿": CommandSpec("wish", "写下你想要的事，记进牵挂", "你想要的事"),
    "存档": CommandSpec("store", "把此刻存成一个档", "名字"),
    "读档": CommandSpec("store", "读回一个存档", "名字"),
    "收笔": CommandSpec("close", "就此收场，写一段后事"),
    "帮助": CommandSpec("view", "列出全部命令"),
    "退出": CommandSpec("exit", "离开游戏"),
    "debug": CommandSpec("debug", "显示内核状态和本回合的模型调用（开发用）", "", True),
}

ALIASES: dict[str, str] = {
    "help": "帮助",
    "h": "帮助",
    "?": "帮助",
    "？": "帮助",
    "quit": "退出",
    "exit": "退出",
    "q": "退出",
    "status": "状态",
    "items": "物品",
    "inv": "物品",
    "people": "人物",
    "arts": "本事",
    "skills": "本事",
    "threads": "牵挂",
    "review": "回顾",
    "chronicle": "年谱",
    "wish": "心愿",
    "save": "存档",
    "load": "读档",
    "end": "收笔",
}

SLASHES = ("/", "／")
IME_SLASH = "、"  # 中文输入法下按斜杠键打出的是顿号


def _canonical(name: str) -> str | None:
    if name in COMMANDS:
        return name
    low = name.lower()
    if low in COMMANDS:
        return low
    return ALIASES.get(low)


def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿"


def parse_command(raw: str) -> tuple[str, str] | None:
    """``'/存档 甲'`` → ``('存档', '甲')``；不是命令返回 None。

    - 认半角 ``/`` 与全角 ``／``；以顿号 ``、`` 开头时（输入法把斜杠打成顿号）只认得出命令名才算命令。
    - 命令名与参数之间可以用半角、全角空格，也可以不隔开（``/心愿学会认字``）。
    - 英文别名（``/help``、``/save`` ……）换成中文命令名。认不出的命令原样返回名字，由调用方报「没有这个命令」。
    - 只有一个斜杠时返回 ``('', '')``。
    """
    s = (raw or "").strip().replace("　", " ")
    if not s:
        return None
    ime = False
    if s[0] in SLASHES:
        body = s[1:]
    elif s[0] == IME_SLASH:
        body, ime = s[1:], True
    else:
        return None
    body = body.strip()
    if not body:
        return None if ime else ("", "")
    name, _, arg = body.partition(" ")
    arg = arg.strip()
    canon = _canonical(name)
    if canon is None and name and _is_cjk(name[0]):
        # 不隔空格：取最长的、以中文起头的命令名做前缀
        for key in sorted((k for k in COMMANDS if _is_cjk(k[0])), key=len, reverse=True):
            if name.startswith(key):
                canon = key
                arg = (name[len(key) :] + " " + arg).strip()
                break
    if canon is None:
        return None if ime else (name, arg)
    return canon, arg


def is_command(raw: str) -> bool:
    return parse_command(raw) is not None


def help_text(*, debug: bool = False) -> str:
    """/帮助：列出方案 2.7 的全部命令。"""
    lines = ["【帮助】", "输入一、二、三（或 1、2、3）选一个选项，也可以直接写下你想说的话、想做的事。", ""]
    for name, spec in COMMANDS.items():
        if spec.debug and not debug:
            continue
        usage = f"/{name}" + (f" {spec.arg}" if spec.arg else "")
        lines.append(f"{usage}　{spec.help}")
    lines.append("")
    lines.append("斜杠命令不耗游戏里的时间。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 小工具


def _me(state: WorldState) -> Character:
    return state.chars[state.player]


def _name(state: WorldState, defs: Defs, eid: str) -> str:
    """玩家眼里的叫法。"""
    return refs.display_name(state, defs, eid, state.player)


def _list(items: Sequence[str], sep: str = "、") -> str:
    return sep.join(items)


LITERACY_WORDS = ("不识字", "粗识文字", "通文墨", "饱学")
NEILI_WORDS = ("全无", "微薄", "尚可", "深厚", "浑厚之极")


def literacy_text(level: int) -> str:
    return LITERACY_WORDS[max(0, min(len(LITERACY_WORDS) - 1, int(level)))]


def neili_text(neili: int) -> str:
    return NEILI_WORDS[max(0, min(len(NEILI_WORDS) - 1, growth.neili_band(neili)))]


def progress_text(prog: int, need: int | None) -> str:
    """下一层的进度，只写档位。"""
    if need is None:
        return "已到了头"
    if prog <= 0 or need <= 0:
        return ""
    frac = prog / need
    if frac < 0.25:
        return "往下一层才起了个头"
    if frac < 0.5:
        return "往下一层下了些功夫"
    if frac < 0.75:
        return "往下一层已过了一半"
    return "快到下一层了"


def role_label(state: WorldState, defs: Defs, role: str) -> str | None:
    """身份的中文说法：先查 world.yaml 的 labels（``role:<id>`` 或 ``<id>``），再查专名表；查不到返回 None。"""
    labels = defs.world.labels or {}
    for key in (f"role:{role}", role):
        v = labels.get(key)
        if isinstance(v, str) and v:
            return v
    if role.startswith("surname:"):
        return f"姓{role.split(':', 1)[1]}"
    found = refs.lookup(state, defs, role)
    if found is not None:
        return found[0]
    return None


def _money(state: WorldState, defs: Defs, me: Character) -> list[str]:
    out: list[str] = []
    for iid in me.inventory:
        it = state.items.get(iid)
        if it is not None and it.in_world and ("money" in it.tags or it.props.get("money")):
            out.append(_name(state, defs, iid))
    return out


# ---------------------------------------------------------------- /状态


def render_status(state: WorldState, defs: Defs) -> str:
    me = _me(state)
    lines = ["【状态】"]
    lines.append(f"时辰　{timeflow.clock_text(state.tick)}")
    lines.append(f"所在　{_name(state, defs, me.loc)}")
    body = perception.body_sensations(state, me)
    if not is_alive(me):
        body = ["已经死了"]
    lines.append(f"身子　{_list(body, '；') if body else '无恙'}")
    money = _money(state, defs, me)
    lines.append(f"银钱　{_list(money) if money else '身无分文'}")
    roles = [lbl for r in me.roles if (lbl := role_label(state, defs, r))]
    if me.superior and me.superior in state.chars:
        roles.append(f"听命于{_name(state, defs, me.superior)}")
    lines.append(f"身份　{_list(roles, '；') if roles else '无依无靠'}")
    return "\n".join(lines)


# ---------------------------------------------------------------- /物品


def render_items(state: WorldState, defs: Defs) -> str:
    me = _me(state)
    lines = ["【物品】"]
    carried = [
        _name(state, defs, iid)
        for iid in me.inventory
        if (it := state.items.get(iid)) is not None and it.in_world
    ]
    if carried:
        lines.append("随身带着：")
        lines += [f"　{n}" for n in carried]
    else:
        lines.append("身上空空如也。")
    here = [
        _name(state, defs, it.id)
        for it in state.items.values()
        if it.in_world and it.holder is None and it.loc == me.loc and it.feature is None
    ]
    if here:
        lines.append("此处看得见的：")
        lines += [f"　{n}" for n in here]
    return "\n".join(lines)


# ---------------------------------------------------------------- /人物

_TRUST_PHRASE = {
    "信不过": "信不过你",
    "不熟": "与你不熟",
    "有几分信": "对你有几分信任",
    "信得过": "信得过你",
}
_AFFINITY_PHRASE = {
    "厌恶": "厌恶你",
    "有些嫌弃": "有些嫌弃你",
    "谈不上好恶": "对你谈不上好恶",
    "有几分好感": "对你有几分好感",
    "很有好感": "对你很有好感",
}
_FEAR_PHRASE = {"有些怕": "有些怕你", "很怕": "很怕你", "怕极了": "怕你怕极了"}


def attitude_text(r: Relation | None) -> str:
    """别人待玩家的态度：只用 kernel.mood 的档位文字，不出数字。"""
    r = r or Relation()
    t = mood.trust_text(r.trust)
    a = mood.affinity_text(r.affinity)
    parts = [_TRUST_PHRASE.get(t, t), _AFFINITY_PHRASE.get(a, a)]
    f = mood.fear_text(r.fear)
    if f in _FEAR_PHRASE:
        parts.append(_FEAR_PHRASE[f])
    return "，".join(parts)


def met_people(state: WorldState) -> list[str]:
    """玩家见过的人：有来往（关系表里有他）、有特别的叫法、此刻看得见，或亲眼见过他的所作所为。"""
    me = _me(state)
    seen: list[str] = []

    def add(cid: str) -> None:
        if cid != me.id and cid in state.chars and cid not in seen:
            seen.append(cid)

    for c in perception.visible_people(state, me):
        add(c.id)
    for cid in me.rel:
        add(cid)
    for cid in state.term_alias:
        add(cid)
    for m in me.memory:
        if m.kind in ("saw", "did"):
            for e in m.entities:
                add(e)
    return seen


def _death_known(state: WorldState, cid: str) -> bool:
    """玩家亲眼见过他死（记忆里有关于他、分量为 10 的亲见）。"""
    me = _me(state)
    return any(m.kind == "saw" and m.salience >= 10 and cid in m.entities for m in me.memory)


def render_people(state: WorldState, defs: Defs) -> str:
    me = _me(state)
    present = {c.id for c in perception.visible_people(state, me)}
    ids = met_people(state)
    if not ids:
        return "【人物】\n你在这里还谁也不认得。"

    def order(cid: str) -> tuple[bool, bool, int, str]:
        acquainted = cid in me.rel or cid in state.known_terms or cid in state.term_alias
        r = state.chars[cid].rel.get(me.id) or Relation()
        return (cid not in present, not acquainted, -(r.trust + r.affinity), _name(state, defs, cid))

    ids.sort(key=order)
    lines = ["【人物】"]
    for cid in ids:
        c = state.chars[cid]
        name = _name(state, defs, cid)
        if not is_alive(c) and _death_known(state, cid):
            lines.append(f"{name}（已死）")
            continue
        tag = "（在此）" if cid in present else ""
        line = f"{name}{tag}：{attitude_text(c.rel.get(me.id))}"
        seen = me.rel.get(cid)
        if seen is not None and seen.regard > 0:
            line += f"；看来{mood.regard_text(seen.regard)}"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------- /本事


def render_arts(state: WorldState, defs: Defs) -> str:
    me = _me(state)
    lines = ["【本事】"]
    lines.append(f"身手　{mood.regard_text(growth.power(state, defs, me))}")
    lines.append(f"内力　{neili_text(me.neili)}")
    lit = literacy_text(me.literacy)
    lit_prog = progress_text(
        me.art_progress.get(growth.LITERACY, 0), growth.stage_need(defs, growth.LITERACY, me.literacy)
    )
    if lit_prog and me.art_progress.get(growth.LITERACY, 0) > 0:
        lit += f"（学字{lit_prog}）"
    lines.append(f"识字　{lit}")
    arts: list[str] = []
    for aid in sorted(set(me.arts) | set(me.art_progress)):
        if aid == growth.LITERACY:
            continue
        a = defs.arts.get(aid)
        if a is None:
            continue
        lvl = me.arts.get(aid, 0)
        prog = me.art_progress.get(aid, 0)
        if lvl >= 1 and a.stages:
            stage = a.stages[min(lvl, len(a.stages)) - 1].name
            extra = progress_text(prog, growth.stage_need(defs, aid, lvl))
            arts.append(f"　{a.name}：{stage}" + (f"（{extra}）" if extra else ""))
        elif prog > 0:
            arts.append(f"　{a.name}：正在入门（{progress_text(prog, growth.stage_need(defs, aid, 0))}）")
    if arts:
        lines.append("会的本事：")
        lines += arts
    else:
        lines.append("还没有学会什么本事。")
    return "\n".join(lines)


# ---------------------------------------------------------------- /回顾 /帮助

REVIEW_DEFAULT = 3
_CN_DIGITS = {ch: i for i, ch in enumerate("零一二三四五六七八九")}


def _parse_count(arg: str, default: int) -> int:
    s = (arg or "").strip()
    if not s:
        return default
    if re.fullmatch(r"\d+", s):
        return max(1, int(s))
    if s == "十":
        return 10
    if len(s) == 1 and s in _CN_DIGITS:
        return max(1, _CN_DIGITS[s])
    return default


def render_review(
    state: WorldState | None = None,
    defs: Defs | None = None,
    turns: Sequence[str] = (),
    n: int | str = REVIEW_DEFAULT,
) -> str:
    """/回顾：最近几回合玩家看到的文字（``turns`` 由调用方从 transcript 缓冲里传入）。"""
    count = _parse_count(n, REVIEW_DEFAULT) if isinstance(n, str) else max(1, int(n))
    picked = [t.strip("\n") for t in list(turns)[-count:] if t and t.strip()]
    if not picked:
        return "【回顾】\n还没有可以回顾的文字。"
    return "【回顾】\n\n" + "\n\n".join(picked)


def render_help(state: WorldState | None = None, defs: Defs | None = None, *, debug: bool = False) -> str:
    return help_text(debug=debug)


VIEWS: dict[str, Callable[..., str]] = {
    "状态": render_status,
    "物品": render_items,
    "人物": render_people,
    "本事": render_arts,
    "回顾": render_review,
    "帮助": render_help,
}


def render_view(
    name: str,
    state: WorldState,
    defs: Defs,
    *,
    arg: str = "",
    turns: Sequence[str] = (),
    debug: bool = False,
) -> str:
    """按命令名渲染只读画面。``name`` 须是 ``VIEWS`` 里的键。"""
    if name == "回顾":
        return render_review(state, defs, turns, arg or REVIEW_DEFAULT)
    if name == "帮助":
        return render_help(state, defs, debug=debug)
    return VIEWS[name](state, defs)


def unknown_command_text(name: str) -> str:
    return f"没有「/{name}」这个命令。输入 /帮助 看看有哪些。" if name else "输入 /帮助 看看有哪些命令。"
