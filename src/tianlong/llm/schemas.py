"""Claude 各类 JSON 调用的输出 schema（方案 7.5 至 7.10、9.1）。

这些模型既交给结构化输出（`anthropic.transform_schema` 转成 `output_config.format`），
也用来校验返回的 JSON。结构化输出不支持的约束（条数上限、数值范围）不写进 schema，
改在校验之后截断或夹紧，免得模型多写一条就整次作废。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Out(BaseModel):
    model_config = ConfigDict(extra='ignore')


def _cap(items: list, n: int) -> list:
    return list(items)[:n]


# --------------------------------------------------------------------------- C1（7.5）


class Intent(_Out):
    """J1 与 C1 共用的结果。"""

    kind: Literal['act', 'say', 'both', 'meta', 'unclear', 'impossible']
    verb: str | None = Field(description='候选动词之一；没有动作时为 null')
    target: str | None = Field(description='场景里的对象 id；没有为 null')
    target2: str | None = Field(description='第二个对象 id；没有为 null')
    act: str | None = Field(description='言语行为；没说话为 null')
    to: list[str] = Field(description='说话对象的 id；对众人时为空表')
    asks: str | None = Field(description='候选请求的 id；没有请求为 null')
    cls: str | None = Field(description='所求之事的类别；没有请求为 null')
    urgent: bool
    claim_refs: list[str] = Field(description='断言对上的事实与事件 id，或 self_unlisted')
    attempts: list[str] = Field(description='括号、星号里的舞台说明，原样')
    dropped: str = Field(description='被忽略的多余部分；没有为空串')
    note: str = Field(description='unclear、impossible 时一句戏内口吻的话；否则为空串')


# --------------------------------------------------------------------------- C2（7.6）


class SpeechOut(_Out):
    act: str
    to: list[str]
    stance: str
    gist: str = Field(description='一句第三人称的话：要表达什么。不写台词')
    fact_refs: list[str]
    request_ref: str | None
    whisper: bool


class Decision(_Out):
    inner: str = Field(description='此刻的心思，第一人称一句话，不超过四十字')
    action: str = Field(description='清单里的动作 id，或 none')
    speech: SpeechOut | None
    display: str = Field(description='旁人看得见的神态，不超过十二字')


# --------------------------------------------------------------------------- C4（7.8）


class OptionText(_Out):
    id: str
    text: str


class OptionsOut(_Out):
    options: list[OptionText]


# --------------------------------------------------------------------------- C5（7.9）


class ReplanOut(_Out):
    goal: str = Field(description='候选目标的 id')
    steps: list[str] = Field(description='步骤模板 id，按先后，至多三个')
    inner: str = Field(description='一句心思')

    @field_validator('steps')
    @classmethod
    def _at_most_three(cls, v: list[str]) -> list[str]:
        return _cap(v, 3)


# --------------------------------------------------------------------------- C6（7.10）


class Reflection(_Out):
    text: str
    about: list[str] = Field(description='涉及的实体 id')
    salience: int = Field(description='1 到 10')

    @field_validator('salience')
    @classmethod
    def _clamp(cls, v: int) -> int:
        return max(1, min(10, int(v)))


class Impression(_Out):
    char: str
    text: str


class ReflectOut(_Out):
    reflections: list[Reflection] = Field(description='至多三条')
    impressions: list[Impression]

    @field_validator('reflections')
    @classmethod
    def _at_most_three(cls, v: list[Reflection]) -> list[Reflection]:
        return _cap(v, 3)


# --------------------------------------------------------------------------- 管线（9.1）
# 谓词、效果这类开放结构写成 YAML 文本字段，由管线解析、P7 校验。


class Entity(_Out):
    id: str = Field(description='小写拼音加下划线')
    kind: Literal['character', 'location', 'item', 'art', 'group', 'other']
    name: str
    aliases: list[str]
    paragraphs: list[str] = Field(description='出现的段落编号，如 ch01_p0001；至少一个')


class EntitiesOut(_Out):  # P1
    entities: list[Entity]


class TimelineEvent(_Out):
    who: list[str] = Field(description='实体 id')
    where: str | None
    what: str = Field(description='用自己的话写的一句')
    result: str
    para_from: str
    para_to: str
    time_hint: str | None = Field(description='原文里的时间线索所在的段落编号与要点；没有为 null')


class TimelineOut(_Out):  # P2
    events: list[TimelineEvent]


class CheckConclusion(_Out):
    item: str = Field(description='方案 3.6 的条目')
    paragraphs: list[str]
    conclusion: str


class NamedYaml(_Out):
    id: str
    yaml: str


class OpeningDraft(_Out):  # P3
    opening_yaml: str = Field(description='content/opening.yaml 的全文')
    agendas: list[NamedYaml] = Field(description='每人一份 agendas/<id>.yaml')
    situations: list[NamedYaml] = Field(description='每个情境一份 situations/<id>.yaml')
    baseline_yaml: str
    checks: list[CheckConclusion]
    diff_report: str = Field(description='与种子不符之处，Markdown')


class CardQuote(_Out):
    text: str = Field(description='必须是原文的子串，逐字')
    para: str


class CardChoice(_Out):
    text: str = Field(description='用自己的话概括的一次抉择')
    para: str


class CharacterCardDraft(_Out):  # P4
    id: str
    card_yaml: str = Field(description='characters/<id>.yaml 的全文，不含 voice_samples 与 choices')
    voice_samples: list[CardQuote]
    choices: list[CardChoice]


class CardsOut(_Out):
    cards: list[CharacterCardDraft]


class WorldFilesDraft(_Out):  # P5
    locations_yaml: str
    items_yaml: str
    facts_yaml: str
    arts_yaml: str
    lexicon_yaml: str


class StyleSample(_Out):
    scene: str = Field(description='场面类型，如 打斗、对话、写景')
    text: str = Field(description='必须是原文的子串，逐字')
    para: str


class StyleDraft(_Out):  # P6
    guide_md: str = Field(description='用自己的话写的文风要点')
    samples: list[StyleSample]


# 调用代号 → 默认 schema（调用方也可以自己传）
DEFAULT_SCHEMAS: dict[str, type[BaseModel]] = {
    'C1': Intent,
    'C2': Decision,
    'C2_soul': Decision,
    'C2_named': Decision,
    'C4': OptionsOut,
    'C5': ReplanOut,
    'C6': ReflectOut,
    'P1': EntitiesOut,
    'P2': TimelineOut,
    'P3': OpeningDraft,
    'P4': CardsOut,
    'P5': WorldFilesDraft,
    'P6': StyleDraft,
}
