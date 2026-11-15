"""不会变的内容定义（ARCHITECTURE 第 3 节）。由 ``content/loader.py`` 生成，运行期只读。

goals / routines / opportunities 的格式由读它们的模块定（D-011），这里只当作原样的数据保存。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

DEFAULT_WEIGHTS: dict[str, dict[str, int]] = {
    "trivial": {"hostile": 20, "stranger": 50, "familiar": 70, "trusted": 90},
    "effort": {"hostile": 0, "stranger": 30, "familiar": 50, "trusted": 80},
    "costly": {"hostile": -20, "stranger": 10, "familiar": 30, "trusted": 60},
    "risky": {"hostile": -40, "stranger": 0, "familiar": 20, "trusted": 50},
    "betray": {"hostile": -60, "stranger": -30, "familiar": -10, "trusted": 20},
}
DEFAULT_LABELS: dict[str, str] = {
    "trivial": "举手之劳",
    "effort": "费事",
    "costly": "有损己利",
    "risky": "冒险",
    "betray": "违背所属",
}
DEFAULT_BANDS: dict[str, list[int]] = {
    "hostile": [-100, -30],
    "stranger": [-30, 20],
    "familiar": [20, 60],
    "trusted": [60, 100],
}
DEFAULT_SALIENCE: dict[str, int] = {
    "death": 10,
    "harmed": 9,
    "rescue": 8,
    "promise": 7,
    "exposed": 7,
    "insult": 6,
    "learn_key": 6,
    "gift": 5,
    "talk": 3,
    "pass": 1,
}


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class Calendar(_Open):
    ticks_per_hour: int = 8
    hours_per_day: int = 12
    start_tick: int = 32


class RequestRules(_Open):
    weights: dict[str, dict[str, int]] = DEFAULT_WEIGHTS
    may: int = 60
    conditional: int = 40
    labels: dict[str, str] = DEFAULT_LABELS
    bands: dict[str, list[int]] = DEFAULT_BANDS
    close_roles: list[str] = ["same_sect", "master_disciple", "master_servant"]
    # 哪些身份算「同门、同一家」（K3）：world.yaml 的 request.sect_roles。两人都有其中同一个身份才算同门。
    # 不写（None）时退回一条不认名字的规则：占着地盘的身份（某个地点的 owner）才算门派或人家，
    # 「同是来客」这类临时身份不算。写成空表即任何共同身份都不算同门。
    sect_roles: list[str] | None = None


class WorldRules(_Open):
    background: str = ""
    calendar: Calendar = Calendar()
    request: RequestRules = RequestRules()
    salience: dict[str, int] = DEFAULT_SALIENCE
    labels: dict[str, str] = {}


class ArtStage(_Open):
    name: str
    hours: int
    power: int = 0
    effects: dict[str, Any] = {}


class ArtDef(_Open):
    id: str
    name: str
    unknown_alias: str = ""
    kind: Literal["combat", "movement", "inner", "craft", "lore"] = "craft"
    sources: list[str] = []
    requires: dict | None = None
    stages: list[ArtStage] = []
    secrecy: Literal["none", "sect"] = "none"
    teacher_roles: list[str] = []


class BlockDef(_Open):
    verb: str
    act: str | None = None
    fact_tag: str | None = None
    target: str | None = None
    when: Any = None


class BottomLine(_Open):
    text: str = ""
    blocks: list[BlockDef] = []


class VoiceDef(_Open):
    habits: list[str] = []
    samples: list[Any] = []


class ChoiceDef(_Open):
    situation: str = ""
    did: str = ""
    para: str | None = None


class CardDef(_Open):
    id: str
    name: str
    unknown_alias: str = ""
    tier: Literal["soul", "named", "extra"] = "named"
    base_power: int = 0
    neili: int = 0
    arts: dict[str, int] = {}
    literacy: int = 0
    aptitude: int = 1
    luck: int = 0
    roles: list[str] = []
    self_intro: str = ""
    values: list[str] = []
    drives: list[str] = []
    voice: VoiceDef = VoiceDef()
    bottom_lines: list[BottomLine] = []
    choices: list[ChoiceDef] = []
    knows: list[str] = []
    rel: dict[str, dict[str, Any]] = {}
    agenda: str | None = None
    superior: str | None = None
    initiative: int = 0
    persona: str | None = None


class SituationAction(_Open):
    role: str
    lean: bool = False
    pre: Any = None
    loud: bool = False
    lethal: bool = False
    cost: int = 1
    desc: str = ""
    effects: list[dict] = []


class OffscreenRule(_Open):
    when: Any = True
    effects: list[dict] = []


class SituationDef(_Open):
    id: str
    origin: Literal["canon", "generic"] = "canon"
    when: Any = None
    roles: dict[str, Any] = {}  # 角色位 → 写死的 id，或 {pick: {where: 谓词}}
    at: str | None = None  # 地点 id，或 '$角色位'
    scene: Literal["calm", "tense"] | None = None
    actions: dict[str, SituationAction] = {}
    exit_when: Any = None
    max_turns: int | None = None
    offscreen: list[OffscreenRule] = []
    paragraphs: list[Any] = []


class BaselineMark(_Open):
    id: str
    text: str = ""
    holds: Any = None
    by: int | None = None


class LexTerm(_Open):
    name: str
    alias: str = ""


class Lexicon(_Open):
    terms: dict[str, LexTerm] = {}  # 不属于任何实体的专名
    banned_modern: list[str] = []
    banned_game: list[str] = []
    names: dict[str, LexTerm] = {}  # 加载器并入的全部实体的名字与叫法


class StyleDef(_Open):
    guide: str = ""
    samples: dict[str, list[dict[str, Any]]] = {}


class Defs(BaseModel):
    world: WorldRules = WorldRules()
    arts: dict[str, ArtDef] = {}
    cards: dict[str, CardDef] = {}
    situations: dict[str, SituationDef] = {}
    goals: Any = None  # goals.yaml，原样（world/replan.py 解释）
    routines: Any = None  # routines.yaml，原样（minds/routines.py 解释）
    opportunities: Any = None  # opportunities.yaml，原样（storyteller/opportunities.py 解释）
    baseline: list[BaselineMark] = []
    lexicon: Lexicon = Lexicon()
    style: StyleDef = StyleDef()
    content_hash: str = ""

    def card_for(self, persona: str | None) -> CardDef | None:
        if persona is None:
            return None
        return self.cards.get(persona)
