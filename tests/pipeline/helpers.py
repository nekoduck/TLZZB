"""测试用的替身：在 MockClaude 的规则草稿上做改动，模拟模型给出与种子不同的草稿。"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

import yaml
from pydantic import BaseModel

from tianlong.llm import MockClaude


class ScriptedClaude(MockClaude):
    """按调用代号改写 mock_default（直调与 Batch 都经过这里）。"""

    def __init__(self, *args: Any, edits: dict[str, Callable[[Any], Any]] | None = None, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.edits = dict(edits or {})

    def _wrap(self, kind: str, md: Any) -> Any:
        fn = self.edits.get(kind)
        if fn is None:
            return md

        def wrapped() -> BaseModel:
            v = md() if callable(md) else md
            return fn(v.model_copy(deep=True))

        return wrapped

    async def call_json(self, kind, system_blocks, user_text, schema, *, mock_default=None, **kw):  # type: ignore[override]
        return await super().call_json(kind, system_blocks, user_text, schema,
                                       mock_default=self._wrap(kind, mock_default), **kw)

    async def batch_submit(self, kind, items, *, ttl=None):  # type: ignore[override]
        items = [dataclasses.replace(it, mock_default=self._wrap(kind, it.mock_default)) for it in items]
        return await super().batch_submit(kind, items, ttl=ttl)


def scripted(llm: Any, edits: dict[str, Callable[[Any], Any]]) -> Any:
    llm.claude = ScriptedClaude(llm.settings, recorder=llm.recorder, ledger=llm.ledger, edits=edits)
    return llm


def edit_yaml(text: str, fn: Callable[[Any], Any]) -> str:
    data = yaml.safe_load(text)
    fn(data)
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
