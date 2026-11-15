"""模型适配层测试的公用夹具：假密钥、不读仓库的 .env、替身 Jev 与 Claude。"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from tianlong.config import Settings
from tianlong.llm import LLM, MockClaude, MockJev, make_llm
from tianlong.llm.jev import Answers, validate_response
from werewolf.kernel.rng import Rng
from werewolf.llm.config import WerewolfSettings, load
from werewolf.llm.gateway import Gateway

REPO = Path(__file__).resolve().parents[3]
KEY_ENVS = ('JEV_API_KEY', 'JEV_KEY', 'ANTHROPIC_API_KEY', 'CLAUDE_KEY', 'CLAUDE_API_KEY')


class ScriptedJev(MockJev):
    """按函数给答案的 Jev 替身：fn(kind, state, questions) -> {问题: 答案原始 JSON} 或抛异常。"""

    def __init__(self, *args: Any, fn: Callable[[str, Any, Mapping[str, Any]], dict[str, Any]] | None = None,
                 **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.fn = fn
        self.calls: list[tuple[str, Any, Mapping[str, Any]]] = []

    async def ask(self, kind: str, state: Any, questions: Mapping[str, Mapping[str, Any]], *,
                  hints: Mapping[str, Any] | None = None, escalated: bool = False) -> Answers:
        self.calls.append((kind, state, questions))
        if self.fn is None:
            return await super().ask(kind, state, questions, hints=hints, escalated=escalated)
        raw = self.fn(kind, state, questions)
        spec = self.spec(kind)
        data = {'model': spec.model, 'answers': raw, 'usage': {'input_tokens': 0, 'output_tokens': 0}}
        answers = validate_response(data, questions)
        if self.recorder is not None:
            self.recorder.record(kind=kind, provider=self.provider, model=spec.model, request={'state': state},
                                 response=data, usage=data['usage'])
        return answers


class ScriptedClaude(MockClaude):
    """Claude 替身：stream_text 吐出 texts 里的下一段，并记下 (system, user)。"""

    def __init__(self, *args: Any, texts: Sequence[str | BaseException] = (), **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.texts = list(texts)
        self.prompts: list[tuple[Any, str]] = []

    async def stream_text(self, kind: str, system_blocks: Any, user_text: str, **kw: Any) -> AsyncIterator[str]:
        self.prompts.append((system_blocks, user_text))
        item = self.texts.pop(0) if self.texts else ''
        if isinstance(item, BaseException):
            raise item
        async for piece in super().stream_text(kind, system_blocks, user_text, mock_text=item):
            yield piece


def noul(p: float) -> dict[str, Any]:
    return {'type': 'noul', 'noul': p}


def choice(top: str, probs: Mapping[str, float] | None = None) -> dict[str, Any]:
    probs = dict(probs or {top: 1.0})
    return {'type': 'choice', 'choice': top, 'confidence': probs.get(top, 1.0), 'probabilities': probs}


def score(value: float, n: int = 5) -> dict[str, Any]:
    lo = int(value)
    hi = min(lo + 1, n - 1)
    frac = value - lo
    probs = {str(i): 0.0 for i in range(n)}
    probs[str(lo)] = 1 - frac
    probs[str(hi)] = probs[str(hi)] + frac
    return {'type': 'score', 'score': value, 'confidence': 0.9, 'legend': {}, 'probabilities': probs}


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Settings, WerewolfSettings]:
    """仓库的 werewolf.toml（拷到临时目录，所以不读 .env）；两把密钥都是假的。"""
    for k in KEY_ENVS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('JEV_KEY', 'test-jev-key')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-anthropic-key')
    shutil.copy(REPO / 'werewolf.toml', tmp_path / 'werewolf.toml')
    return load(tmp_path / 'werewolf.toml')


@pytest.fixture
def no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in KEY_ENVS:
        monkeypatch.delenv(k, raising=False)


def build_gateway(cfg: tuple[Settings, WerewolfSettings], *, jev_fn: Callable[..., dict[str, Any]] | None = None,
                  claude_texts: Sequence[str | BaseException] = (), offline: bool = False) -> Gateway:
    settings, ww = cfg
    llm: LLM = make_llm(settings, mock=True, run_dir=None)
    llm.jev = ScriptedJev(settings, recorder=llm.recorder, ledger=llm.ledger, fn=jev_fn)
    llm.claude = ScriptedClaude(settings, recorder=llm.recorder, ledger=llm.ledger, texts=claude_texts)
    return Gateway(settings, ww, offline=offline, llm=llm)


@pytest.fixture
def gw(cfg: tuple[Settings, WerewolfSettings]) -> Gateway:
    return build_gateway(cfg)


@pytest.fixture
def rng() -> Rng:
    return Rng(7, 'test')
