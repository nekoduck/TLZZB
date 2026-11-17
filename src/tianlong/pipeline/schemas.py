"""管线在 `llm/schemas.py` 之上补的几处（只在 pipeline/ 里用）。

- P4：`CardChoice` 只有一句 `text`，而人物卡的 `choices` 要 `situation`（处境）与 `did`（做法）两栏
  （方案 5.4），所以抉择改成两栏；另加 `notes`，让模型把「事实表里缺了的事」「拿不准的地方」报给人看。
P1、P2、P3、P5、P6 直接用 `llm/schemas.py` 的 schema。P3、P5 把一步拆成几条请求，每条只填其中几个字段。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from tianlong.llm.schemas import CardsOut, CharacterCardDraft


class ChoiceDraft(BaseModel):
    model_config = ConfigDict(extra='ignore')

    situation: str = Field(description='用自己的话写的处境，一句')
    did: str = Field(description='用自己的话写的做法，一句')
    para: str | None = Field(description='原文里这次抉择所在的段落编号，如 ch02_p0031')


class CardDraft(CharacterCardDraft):
    choices: list[ChoiceDraft] = Field(description='原著里此人做过的抉择；有名角色留空')
    notes: str = Field(description='给人看的备注：事实表里缺了的事、拿不准的地方；没有为空串')


class CardsDraft(CardsOut):
    cards: list[CardDraft]
