"""规则猜测用的字词表与小工具：动词同义词、称呼匹配、言语行为的口头标记、时长。

这些只用来给 MockJev 出提示、给 C1 的 Mock 出默认值、在 ``rules_only`` 时兜底；
真模型在时不靠它们做决定。这里只有通用的汉语词，没有人名、地名、剧情（方案 10.6）。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from tianlong.kernel import refs
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import WorldState

# ---------------------------------------------------------------- verbs

# (词, 动词)。按词长从长到短匹配：多字词先占住位置，里面的单字不再另认（「打量」不是「打」）。
_VERB_WORDS: list[tuple[str, str]] = [
    # 走动
    ("前往", "go"), ("赶往", "go"), ("走向", "go"), ("奔向", "go"), ("离开", "go"), ("下山", "go"),
    ("上山", "go"), ("进去", "go"), ("出去", "go"), ("回去", "go"), ("回到", "go"), ("去往", "go"),
    ("走到", "go"), ("去", "go"), ("往", "go"),
    ("溜回", "go"), ("溜出", "go"), ("闯进", "go"), ("闯", "go"), ("跳下", "go"), ("纵身", "go"), ("直奔", "go"),
    ("跟在", "follow"), ("跟到", "follow"), ("跟着", "follow"), ("跟上", "follow"), ("尾随", "follow"), ("跟随", "follow"), ("跟踪", "follow"),
    ("逃走", "flee"), ("逃跑", "flee"), ("逃命", "flee"), ("快跑", "flee"), ("溜走", "flee"), ("逃", "flee"),
    ("拔腿就跑", "flee"), ("拔腿", "flee"), ("撒腿", "flee"), ("跑", "flee"),
    ("躲起来", "hide"), ("藏起来", "hide"), ("藏身", "hide"), ("躲进", "hide"), ("藏进", "hide"),
    ("躲到", "hide"), ("藏到", "hide"), ("钻进", "hide"), ("闪到", "hide"), ("缩在", "hide"), ("蹲到", "hide"),
    ("躲", "hide"), ("藏", "hide"),
    # 看与听
    ("看看四周", "look"), ("四下看看", "look"), ("环顾", "look"), ("张望", "look"), ("四下打量", "look"),
    ("看看", "look"), ("观察", "look"),
    ("侧耳", "listen"), ("偷听", "listen"), ("细听", "listen"), ("听听", "listen"), ("倾听", "listen"),
    ("细看", "examine"), ("查看", "examine"), ("端详", "examine"), ("打量", "examine"), ("瞧瞧", "examine"),
    ("偷看", "examine"), ("偷瞧", "examine"), ("偷眼", "examine"),
    ("看一看", "examine"), ("检查", "examine"), ("看", "examine"),
    ("翻找", "search"), ("搜查", "search"), ("搜一搜", "search"), ("翻一翻", "search"), ("找找", "search"),
    ("摸索", "search"), ("摸一摸", "search"), ("翻", "search"), ("搜", "search"), ("找", "search"),
    # 物件
    ("捡起", "take"), ("拾起", "take"), ("拿起", "take"), ("拿走", "take"), ("取下", "take"), ("抄起", "take"),
    ("揣进", "take"),
    ("捡", "take"), ("拾", "take"), ("拿", "take"),
    ("放在", "drop"), ("放回", "drop"), ("放到", "drop"), ("放下", "drop"), ("扔掉", "drop"), ("丢下", "drop"), ("扔下", "drop"), ("丢掉", "drop"), ("扔", "drop"),
    ("交给", "give"), ("递给", "give"), ("送给", "give"), ("塞给", "give"), ("献给", "give"), ("塞到", "give"),
    ("捧给", "give"), ("递", "give"),
    ("给", "give"),
    ("亮出", "show"), ("出示", "show"), ("亮给", "show"), ("拿给.{0,6}看", "show"),
    ("服下", "use"), ("吞下", "use"), ("吃下", "use"), ("喝下", "use"), ("点燃", "use"), ("点火", "use"),
    ("骑上", "use"), ("吃", "use"), ("喝", "use"), ("啃", "use"), ("嚼", "use"), ("服", "use"), ("用", "use"),
    ("顺手牵羊", "steal"), ("偷偷拿", "steal"), ("摸走", "steal"), ("摸过来", "steal"), ("摸回来", "steal"),
    ("牵走", "steal"), ("顺走", "steal"), ("掉包", "steal"), ("调包", "steal"), ("偷", "steal"), ("窃", "steal"),
    # 对人
    ("包扎", "help"), ("搀扶", "help"), ("扶起", "help"), ("照料", "help"), ("照顾", "help"),
    ("救治", "help"), ("敷", "help"), ("揉", "help"), ("喂", "help"), ("扶", "help"), ("搀", "help"),
    ("捆起来", "restrain"), ("绑起来", "restrain"), ("捆住", "restrain"), ("绑住", "restrain"),
    ("按住", "restrain"), ("制住", "restrain"), ("擒住", "restrain"), ("捆", "restrain"), ("绑", "restrain"),
    ("松绑", "release"), ("解开", "release"), ("放开", "release"), ("放了", "release"),
    ("打晕", "attack"), ("打倒", "attack"), ("偷袭", "attack"), ("动手", "attack"), ("攻击", "attack"),
    ("下杀手", "attack"), ("一拳", "attack"), ("一脚", "attack"), ("一刀", "attack"), ("一剑", "attack"),
    ("打一架", "attack"), ("砸", "attack"), ("杀", "attack"), ("揍", "attack"), ("砍", "attack"), ("刺", "attack"),
    ("打", "attack"), ("踢", "attack"),
    ("挡在", "block"), ("挡住", "block"), ("拦住", "block"), ("护住", "block"), ("挡", "block"), ("拦", "block"),
    ("推倒", "push"), ("推开", "push"), ("撞", "push"), ("推", "push"),
    # 本事
    ("研读", "study"), ("读", "study"), ("学", "study"),
    ("练习", "practice"), ("练功", "practice"), ("练", "practice"),
    ("传授", "teach"), ("教", "teach"),
    # 时间
    ("等待", "wait"), ("静观", "wait"), ("候着", "wait"), ("等", "wait"),
    ("歇息", "rest"), ("休息", "rest"), ("打坐", "rest"), ("睡觉", "rest"), ("躺下", "rest"), ("养伤", "rest"),
    ("打个盹", "rest"), ("打盹", "rest"), ("打瞌睡", "rest"), ("睡", "rest"), ("歇", "rest"),
    ("挣脱", "struggle"), ("挣扎", "struggle"), ("挣开", "struggle"),
]
# 含着单字动作词、却不是那个动作的多字词（「打招呼」不是打人，「佩服」不是服药）。它们和动作词一起按词长
# 排队，先占住位置，里面的单字就不再当动作认。第二栏：这个词若是「用叙述口吻写的说话」（打个招呼、
# 打听打听），写它是哪一种言语行为；不是说话的写 None。这里只有通用的汉语词。
_COUNT = "(?:了|过)?(?:一?[个声下])?"
_PHRASE_WORDS: list[tuple[str, str | None]] = [
    # 打
    (f"打{_COUNT}招呼", "greet"), ("打听", "ask"), ("打探", "ask"),
    ("打点", None), ("打算", None), ("打发", None), ("打扰", None), ("打搅", None), ("打赌", None), ("打扮", None),
    ("打岔", None), ("打趣", None), ("打圆场", None), ("打哈哈", None), ("打交道", None), ("打主意", None),
    ("打开", None), ("打扫", None), ("打水", None), ("打柴", None), ("打包", None), ("打结", None), ("打滚", None),
    ("打颤", None), ("打哆嗦", None), ("打喷嚏", None), ("打哈欠", None), ("打手势", None), ("打火", None),
    ("打猎", None), ("打杂", None), ("打下手", None), ("打哪", None), ("打从", None), ("打躬", "greet"),
    # 问候、请教
    (f"问{_COUNT}好", "greet"), ("问候", "greet"), ("问安", "greet"), ("请安", "greet"), ("见礼", "greet"),
    ("寒暄", "greet"), ("请教", "ask"), ("讨教", "ask"), ("喝问", "ask"), ("探问", "ask"),
    # 别的单字
    ("领教", None), ("指教", None), ("教训", None), ("管教", None),
    ("喝道", None), ("喝彩", None), ("吆喝", None), ("喝令", None), ("喝止", None),
    ("佩服", None), ("服气", None), ("衣服", None), ("说服", None), ("服从", None), ("服侍", None), ("服软", None),
    ("吃惊", None), ("吃亏", None), ("吃力", None), ("吃苦", None), ("吃了一惊", None),
    ("推辞", None), ("推说", None), ("推托", None), ("推测", None), ("撞见", None), ("翻脸", None),
    ("找茬", None), ("找麻烦", None), ("拿主意", None), ("拿手", None), ("拿不准", None),
    ("不用", None), ("没用", None), ("有用", None), ("用不着", None), ("用心", None), ("用力", None), ("用功", None),
    ("讽刺", None), ("刺探", None), ("刺耳", None), ("杀价", None), ("砍价", None), ("敷衍", None),
    ("学问", None), ("等闲", None), ("往事", None), ("往常", None), ("以往", None), ("失去", None),
    ("看来", None), ("看样子", None), ("好看", None), ("难看", None), ("偷偷", None), ("偷懒", None),
]
_NARRATED: dict[str, str] = {w: act for w, act in _PHRASE_WORDS if act is not None}
# (正则, 动词 | None)：None 的只占位置，不算动作
VERB_WORDS: list[tuple[re.Pattern[str], str | None]] = [
    (re.compile(w), v)
    for w, v in sorted([*_VERB_WORDS, *((w, None) for w, _ in _PHRASE_WORDS)], key=lambda wv: -len(wv[0]))
]
NARRATED_SPEECH: list[tuple[re.Pattern[str], str]] = [
    (re.compile(w), act) for w, act in sorted(_NARRATED.items(), key=lambda wa: -len(wa[0]))
]
_CLAUSE_END = re.compile(r"[，,。；;！!？?]")
# 凑近一类的词不算动作（不是「走」到别处去）
APPROACH = re.compile(
    r"走过去|走上前|走上去|冲上去|冲过去|扑上去|扑过去|上前|凑过去|凑上前|凑近|靠近|走近|走过来|找个|找了个|"
    r"用[^，。,把]{1,6}(?=把)|给[^，。,]{1,6}(?=包扎|敷|揉|喂|看看)"
)
# 「看看」后面跟着具体的东西时是细看，不是环视
LOOK_AT = re.compile(r"看看|瞧瞧|望望|看一看|读读|读一读")
KILL_WORDS = re.compile(r"杀了|杀死|宰了|刺死|砍死|弄死|结果了|下杀手|要了?他的命|取他性命|取了?.{0,3}性命")

# ---------------------------------------------------------------- speech

QUOTE_RE = re.compile(r"[「“\"『]([^」”\"』]*)[」”\"』]?")
SPEECH_COLON_RE = re.compile(
    r"^(.{0,16}?)(说道|问道|喊道|叫道|笑道|喝道|骂道|求道|劝道|说|问|喊|叫|道|骂|劝|告诉.{0,8}?)[:：，,]\s*(.+)$"
)
SPEECH_VERB_START = re.compile(
    r"^我?(对|向|跟|朝|冲)?.{0,8}?(说|讲|问|喊|告诉|求|劝|骂|请求|恳求|哀求|请教|拜|扯谎|撒谎|谎称|骗)"
)
SPEECH_SPLIT = r"说|讲|问|喊|告诉|求|劝|骂|请教|拜|扯谎|撒谎|谎称|骗"
SECOND_PERSON = re.compile(r"你|您|阁下|诸位|各位")
QUESTION = re.compile(r"[？?]|吗|么[？?]|呢|什么|哪|怎么|怎样|为何|为什么|谁|几时|多少|是否|可曾|打听")
REQUEST_MARK = re.compile(
    r"请|求|让我|给我|帮我|能不能|能否|可否|可不可以|麻烦|劳驾|借我|带我|放我|放了|教我|跟我|随我|陪我|饶|"
    r"别再|住手|停手|把.{0,8}给我|行个方便|咱们.{0,14}吧|我们.{0,14}吧|一起|收了我|收我|(你|您).{0,14}吧[。！!]?$"
)
ACT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("plead", re.compile(r"饶命|饶了|求求|开恩|手下留情|高抬贵手|饶过")),
    ("threaten", re.compile(r"再敢|否则|不然|小心你的|要你的命|叫你后悔|饶不了|休怪|别怪我|拼了|喊人|识相|"
                            r"敢动.{0,8}一|一个不剩|别想活|再往前")),
    ("insult", re.compile(r"蠢|混蛋|混账|王八|狗东西|滚|废物|放屁|不要脸|无耻|蠢材|呸|耗子|怪物|畜生|丑|毒虫")),
    ("taunt", re.compile(r"有种|有本事|敢不敢|胆小|不敢来|缩头|算什么英雄")),
    ("lie", re.compile(r"撒谎|扯谎|谎称|骗.{0,4}说")),
    ("warn", re.compile(r"小心|当心|快跑|快逃|危险|有埋伏|留神|快躲")),
    ("bargain", re.compile(r"除非|得先|条件是|要我答应|也行，先|可以，但|先把.{0,10}(给|放|分)")),
    ("agree", re.compile(r"好吧|依你|就这么办|答应你|成交|行，|好，|可以，|遵命|我去就是")),
    ("refuse", re.compile(r"不行|不干|休想|我不|不答应|办不到|恕难从命|不去")),
    ("promise", re.compile(r"我答应你|我保证|我一定|我发誓|包在我身上|一言为定|一定把|我这就去|之内.{0,4}一定")),
    ("comfort", re.compile(r"别怕|别哭|不要紧|没事的|放心|节哀|莫怕|莫慌|别慌|别往心里去|忍着点")),
    ("greet", re.compile(r"^.{0,6}好[，,！!]|小的是|小人是|在下|我叫|晚辈|幸会|久仰|佩服|辛苦了|见过")),
]
GREET = re.compile(r"^(你好|您好|在下|小人|小的|我叫|晚辈|久仰|幸会|见过|请了|这位)")
SELF_CLAIM = re.compile(r"我(刚才|刚刚|方才|已经|早就|曾经|亲手|亲眼|昨天|上回)|我.{0,8}(过|了)")
# 不靠动作词也算自称做过某事：「方才是我……」「……我已经……了」
SELF_CLAIM_STRONG = re.compile(
    r"我.{0,4}(已经|刚才|刚刚|方才|早就|早已|曾经|亲手)|(已经|刚才|刚刚|方才|早就|早已).{0,4}我|是我.{0,12}(了|的)"
)
# 说的是还没发生的事：只当闲话或算命（6.7 opinion），不对事实
FUTURE = re.compile(r"过几天|过两天|再过|明天|后天|将来|日后|之内.{0,8}(会|就)|就会|会被|将要|早晚")
URGENT_WORDS = re.compile(r"快|急|救|解药|赶紧|尽快|早些|及早|来得及|性命")
WHISPER = re.compile(r"悄悄|低声|小声|耳语|附耳|压低声音|凑到.{0,4}耳边")
STANCES: list[tuple[str, re.Pattern[str]]] = [
    ("pleading", re.compile(r"求求|恳请|求您|求你|行行好|开恩|拜托|饶命")),
    ("angry", re.compile(r"混账|大胆|放肆|找死|滚|！！")),
    ("mocking", re.compile(r"哈哈|嘿嘿|就凭你|可笑|笑死")),
    ("warm", re.compile(r"多谢|谢谢|好兄弟|辛苦|恩公|劳驾")),
    ("cold", re.compile(r"哼|少废话|与你无关|关你什么事")),
    ("evasive", re.compile(r"这个嘛|说来话长|不好说")),
]
META = re.compile(r"存档|读档|怎么玩|帮助|命令|游戏|退出|菜单|选项|回合|\bhelp\b|\bsave\b|\bload\b|\bquit\b", re.I)

# ---------------------------------------------------------------- names

HONORIFICS = (
    "公子", "姑娘", "小姐", "大侠", "前辈", "先生", "兄", "大哥", "大叔", "大婶", "婆婆", "夫人", "掌门",
    "帮主", "谷主", "师父", "师傅", "师兄", "师姐", "师弟", "师妹", "老爷", "少爷", "老伯", "道长", "大师",
    "施主", "爷", "娘子", "老前辈", "女侠", "少侠",
)
PRONOUNS = re.compile(r"他|她|那人|此人|对方|这人")
# 叫的是自己的东家、师父：玩家的上司
MASTER_TITLES = re.compile(r"东家|掌柜|老板|主人|师父|师傅|老爷")
# 叫的是女子：在场叫法里带女字旁一类字的人
FEMALE_WORDS = re.compile(r"姑娘|妹妹|小姐|女侠|夫人|婆婆|大娘|大婶|娘子|姑奶奶|小妹|师姐|师妹|嫂")
FEMALE_MARK = re.compile(r"女|娘|妇|婆|姑|妹|姐|妻|嫂")
VOCATIVE_RE = re.compile(r"^([^，,：:！!？?。]{1,10})[，,：:！!]")
SPEECH_OBJ_RE = re.compile(
    r"(?:对|向|朝|冲|问|告诉|求|叮嘱|劝|跟)(.{1,10}?)"
    r"(?:说|道|喝|问|讲|打听|打探|打(?:了|过)?(?:一?[个声下])?招呼|请安|请教|讨教|见礼|寒暄|扯谎|撒谎|[：:，,]|$)"
)
# 「和某人打个招呼」「给某人请安」：只在后面跟着叙述口吻的说话时才认这几个字后面是说话的对象
NARRATED_OBJ_RE = re.compile(
    r"(?:和|同|与|给|找)(.{1,10}?)(?:打听|打探|打(?:了|过)?(?:一?[个声下])?招呼|问(?:了|过)?(?:一?[个声下])?好|请安|讨教|见礼|寒暄)"
)
# 叫的是「管事的人」：在场手下最多的那一位
AUTHORITY_TITLES = re.compile(r"^(帮主|掌门|谷主|头领|大当家|当家的|老大|主人|庄主|堂主|首领|大人|老爷|师父)")
_QUANT = re.compile(r"^(一|几|两|那|这)(个|只|包|卷|处|座|条|匹|段|支|把|件|钱|块|封|张|根|柄|位|名|间|扇|口|盏|片)?")
_CJK = re.compile(r"[一-鿿]")
_GENERIC_CORES = {"东西", "地方", "一个人", "人", "一样东西", "一处地方", "一门功夫"}


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text or ""))


def core(alias: str) -> str:
    """去掉叫法开头的数量词（「一个」「一只」……）。"""
    return _QUANT.sub("", alias or "", count=1).strip("的")


def lcs_len(a: str, b: str) -> int:
    """最长公共子串的长度。"""
    if not a or not b:
        return 0
    best = 0
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0] * (len(b) + 1)
        for j, cb in enumerate(b, 1):
            if ca == cb:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def bigrams(text: str) -> set[str]:
    t = re.sub(r"[^一-鿿A-Za-z0-9]", "", text or "")
    return {t[i : i + 2] for i in range(len(t) - 1)}


def names_of(state: WorldState, defs: Defs, eid: str, *, pov: bool = False) -> list[tuple[str, int]]:
    """一个实体的各种叫法及其分量：真名、玩家的特别叫法、不知其名时的叫法。

    ``pov``：只按玩家的角色叫得出的叫法来——他还不知道名字的，不给真名。
    """
    out: list[tuple[str, int]] = []
    found = refs.lookup(state, defs, eid)
    if found is not None:
        name, alias = found
        if name and (not pov or eid in state.known_terms):
            out.append((name, 10))
        if alias and alias != name:
            out.append((alias, 8))
            c = core(alias)
            if c and c != alias and c not in _GENERIC_CORES and len(c) >= 2:
                out.append((c, 7))
    ta = state.term_alias.get(eid)
    if ta:
        out.append((ta, 9))
    return out


def match_score(state: WorldState, defs: Defs, eid: str, text: str, *, pov: bool = False) -> int:
    """text 提到 eid 的分数；0 表示没提到。``pov``：不知其名的只按玩家眼里的叫法对（见 ``names_of``）。"""
    if not text:
        return 0
    best = 0
    named = not pov or eid in state.known_terms
    for nm, w in names_of(state, defs, eid, pov=pov):
        if not nm:
            continue
        if nm in text:
            best = max(best, w + len(nm))
            continue
        if len(nm) >= 3:
            n = lcs_len(nm, text)
            if n >= 2:
                best = max(best, n)
    c = state.chars.get(eid)
    if named and c is not None and c.name and len(c.name) >= 2:
        sur = c.name[0]
        if any(sur + h in text for h in HONORIFICS):
            best = max(best, 6)
    if best == 0 and (eid in state.items or "#" in eid):
        # 物件、景物的中心字（「麻绳」的「绳」）：弱匹配，只在别无所指时起作用
        for nm, _ in names_of(state, defs, eid, pov=pov):
            head = nm.rstrip("子儿头") if nm else ""
            if head and has_cjk(head[-1]) and head[-1] in text:
                best = 2
                break
    return best


def match_entities(
    state: WorldState, defs: Defs, text: str, ids: Iterable[str], *, floor: int = 2, pov: bool = False
) -> list[tuple[str, int]]:
    """按分数从高到低给出 text 提到的实体。"""
    scored = [(eid, match_score(state, defs, eid, text, pov=pov)) for eid in ids]
    scored = [(e, s) for e, s in scored if s >= floor]
    scored.sort(key=lambda es: -es[1])
    return scored


def first_mention(state: WorldState, defs: Defs, text: str, eid: str) -> int:
    """实体在 text 里第一次出现的位置；没出现返回很大的数。"""
    best = 1 << 20
    for nm, _ in names_of(state, defs, eid):
        if nm and nm in text:
            best = min(best, text.index(nm))
    c = state.chars.get(eid)
    if c is not None and c.name:
        for h in HONORIFICS:
            k = text.find(c.name[0] + h)
            if k >= 0:
                best = min(best, k)
    return best


# ---------------------------------------------------------------- verbs & durations


def verbs_in(text: str) -> list[tuple[int, str, str]]:
    """text 里出现的动作词：(位置, 动词, 词)，按位置排。凑近一类的词先去掉。

    所有词表里对得上的地方一起排队：长的先占位置，一样长的靠前的先占；占住的字不再另认。
    所以多字词总是压过它里面的单字（「打招呼」里的「打」、「偷偷看」里的「偷」都不另算）。
    """
    t = APPROACH.sub(lambda m: "　" * len(m.group(0)), text or "")
    hits: list[tuple[int, int, int, str | None, str]] = []
    for order, (pat, verb) in enumerate(VERB_WORDS):
        hits.extend((m.start() - m.end(), m.start(), order, verb, m.group(0)) for m in pat.finditer(t) if m.group(0))
    hits.sort(key=lambda h: h[:3])
    taken = [False] * len(t)
    out: list[tuple[int, str, str]] = []
    for neg_len, start, _order, verb, word in hits:
        end = start - neg_len
        if any(taken[start:end]):
            continue
        taken[start:end] = [True] * (end - start)
        if verb is not None:
            out.append((start, verb, word))
    out.sort()
    return out


def narrated_speech(text: str) -> tuple[int, int, str] | None:
    """用叙述口吻写的说话（「向他打个招呼」「跟他打听打听」）：最靠前的一处 (起, 止, 言语行为)；没有返回 None。

    直接冲着人说的话（带「你」「您」，或开口先叫人）不算：「替我向你师父问好」是原话，说的是什么由话本身定。
    """
    t = text or ""
    if SECOND_PERSON.search(t) or vocative(t):
        return None
    best: tuple[int, int, str] | None = None
    for pat, act in NARRATED_SPEECH:
        m = pat.search(t)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), m.end(), act)
    return best


def narrated_act(text: str) -> str | None:
    hit = narrated_speech(text)
    return hit[2] if hit else None


def _clause_start(text: str, pos: int) -> int:
    """pos 所在的那个分句从哪里起（上一个逗号、句号之后）。"""
    return max((m.end() for m in _CLAUSE_END.finditer(text[:pos])), default=0)


_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cn_to_int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    if not s:
        return None
    if s == "十":
        return 10
    if s.startswith("十"):
        rest = _CN_DIGITS.get(s[1:], None) if len(s) > 1 else 0
        return 10 + (rest or 0)
    if "十" in s:
        a, _, b = s.partition("十")
        hi = _CN_DIGITS.get(a)
        lo = _CN_DIGITS.get(b, 0) if b else 0
        return hi * 10 + lo if hi is not None else None
    if len(s) == 1:
        return _CN_DIGITS.get(s)
    return None


_DUR_RE = re.compile(r"([零一二两三四五六七八九十\d]+)\s*个?\s*(时辰|刻|天|日|夜|晚)")


def duration_ticks(text: str) -> int | None:
    """文字里写明的时长，换成刻（8 刻一个时辰，一日 96 刻）。没写返回 None。"""
    t = text or ""
    if re.search(r"半个时辰", t):
        return 4
    m = _DUR_RE.search(t)
    if m:
        n = cn_to_int(m.group(1))
        if n is None or n <= 0:
            return None
        unit = m.group(2)
        if unit == "刻":
            return n
        if unit == "时辰":
            return n * 8
        if unit in ("夜", "晚"):
            return n * 64
        return n * 96
    if re.search(r"一夜|一宿|一觉|整夜|到天亮", t):
        return 64
    if re.search(r"一整天|一天|一日", t):
        return 96
    if re.search(r"一会|片刻|一阵|稍等|等等", t):
        return 1
    return None


def stance_of(text: str) -> str:
    for name, pat in STANCES:
        if pat.search(text or ""):
            return name
    return "plain"


_QUESTION_END = re.compile(r"(？|\?|吗|呢|吧|好不好|行不行|成不成|可好|如何)[。！!？?]*$")
_REQUEST_START = re.compile(r"^(请|求|劳驾|麻烦|拜托|烦请|恳请)")


def vocative(text: str) -> bool:
    """开头先叫人：「公子，……」「帮主，……」「这位大哥，……」。"""
    m = re.match(r"^([^，,：:！!？?。]{1,8})[，,：:！!]", text or "")
    if not m:
        return False
    head = m.group(1)
    if verbs_in(head):
        return False
    return (
        any(head.endswith(h) for h in HONORIFICS)
        or bool(AUTHORITY_TITLES.match(head) or MASTER_TITLES.search(head) or FEMALE_WORDS.search(head))
        or head.startswith(("这位", "那位", "老", "小"))
    )


def speechy(text: str) -> bool:
    """有开口说话的迹象：冲着人说（你、您）、先叫人、问句或请求的口气、打招呼、示警求饶一类的口头语。"""
    t = text or ""
    return bool(
        SECOND_PERSON.search(t) or vocative(t) or _QUESTION_END.search(t) or _REQUEST_START.match(t)
        or GREET.match(t) or narrated_speech(t) is not None
        or any(pat.search(t) for name, pat in ACT_PATTERNS if name not in ("insult", "lie"))
    )


def split_speech(text: str) -> tuple[str, str]:
    """把一句输入拆成 (身体动作的部分, 说出口的部分)。拆不准不要紧：只用来出提示。"""
    t = (text or "").strip()
    if not t:
        return "", ""
    quotes = [m.group(1) for m in QUOTE_RE.finditer(t)]
    if quotes:
        narr = QUOTE_RE.sub(" ", t).strip(" ，,：:")
        return narr, "".join(q for q in quotes).strip()
    m = SPEECH_COLON_RE.match(t)
    if m:
        return (m.group(1) + m.group(2)).strip(), m.group(3).strip()
    if SPEECH_VERB_START.match(t) and not verbs_in(re.split(SPEECH_SPLIT, t, maxsplit=1)[0]):
        return "", t
    said = narrated_speech(t)
    if said is not None:
        # 叙述口吻的说话：它所在的分句整个是说话（「去跟那个舞剑的打个招呼」里的「去」「舞」都不是动作）；
        # 前面的分句里另有身体动作，就是又做又说
        head = t[: _clause_start(t, said[0])]
        return (head.strip(" ，,"), t) if verbs_in(head) else ("", t)
    talk = speechy(t)
    if t.startswith("我"):
        if SECOND_PERSON.search(t) or (talk and not verbs_in(t[:6])):
            return "", t
        return t, ""
    vs = verbs_in(t)
    if vs and not talk:
        return t, ""
    if vs and vs[0][0] == 0 and not SECOND_PERSON.search(t) and not vocative(t):
        return t, ""
    return "", t
