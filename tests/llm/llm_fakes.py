"""测试用的假 anthropic 客户端：不联网，记下每次调用的参数。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any


class FakeObj:
    """有 to_dict() 的简单对象，模仿 SDK 的响应模型。"""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        for k, v in data.items():
            setattr(self, k, v)

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._data))


def message(
    text: str | None = None,
    *,
    stop_reason: str = 'end_turn',
    model: str = 'claude-haiku-5-5',
    usage: dict[str, Any] | None = None,
    thinking_first: bool = True,
    stop_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if thinking_first:
        content.append({'type': 'thinking', 'thinking': '', 'signature': 'sig'})
    if text is not None:
        content.append({'type': 'text', 'text': text})
    return {
        'id': 'msg_fake',
        'type': 'message',
        'role': 'assistant',
        'model': model,
        'content': content,
        'stop_reason': stop_reason,
        'stop_details': stop_details,
        'usage': usage or {'input_tokens': 100, 'output_tokens': 20,
                           'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0},
    }


class FakeStream:
    def __init__(self, pieces: list[str], final: dict[str, Any], fail_after: int | None = None) -> None:
        self.pieces = pieces
        self.final = final
        self.closed = False
        self.yielded = 0
        self.text_stream = self._text()

    async def _text(self) -> AsyncIterator[str]:
        for p in self.pieces:
            self.yielded += 1
            yield p

    async def get_final_message(self) -> FakeObj:
        return FakeObj(self.final)

    @property
    def current_message_snapshot(self) -> FakeObj:
        snap = dict(self.final)
        snap['stop_reason'] = None
        return FakeObj(snap)

    async def __aenter__(self) -> FakeStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.closed = True


class FakeMessages:
    def __init__(self, owner: FakeAnthropic) -> None:
        self.owner = owner
        self.batches = FakeBatches(owner)

    async def create(self, **kwargs: Any) -> FakeObj:
        self.owner.calls.append(('create', kwargs))
        if not self.owner.responses:
            raise AssertionError('FakeAnthropic: no queued response')
        return FakeObj(self.owner.responses.pop(0))

    def stream(self, **kwargs: Any) -> FakeStream:
        self.owner.calls.append(('stream', kwargs))
        pieces, final = self.owner.stream_script
        s = FakeStream(pieces, final)
        self.owner.streams.append(s)
        return s


class FakeBatches:
    def __init__(self, owner: FakeAnthropic) -> None:
        self.owner = owner

    async def create(self, *, requests: list[dict[str, Any]]) -> FakeObj:
        self.owner.calls.append(('batch_create', {'requests': requests}))
        return FakeObj({'id': 'msgbatch_1', 'processing_status': 'in_progress'})

    async def retrieve(self, batch_id: str) -> FakeObj:
        return FakeObj({'id': batch_id, 'processing_status': 'ended',
                        'request_counts': {'succeeded': len(self.owner.batch_results), 'errored': 0}})

    async def results(self, batch_id: str) -> AsyncIterator[FakeObj]:
        async def gen() -> AsyncIterator[FakeObj]:
            for cid, result in self.owner.batch_results:
                yield FakeObj({'custom_id': cid, 'result': FakeObj(result)})

        return gen()


class FakeBeta:
    def __init__(self, owner: FakeAnthropic) -> None:
        self.messages = FakeMessages(owner)


class FakeAnthropic:
    """模仿 `anthropic.AsyncAnthropic` 用到的那几处。beta 与非 beta 共用同一份队列与记录。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: list[dict[str, Any]] = []
        self.stream_script: tuple[list[str], dict[str, Any]] = ([], message(''))
        self.streams: list[FakeStream] = []
        self.batch_results: list[tuple[str, dict[str, Any]]] = []
        self.messages = FakeMessages(self)
        self.beta = FakeBeta(self)
        self.beta.messages = self.messages  # 同一套

    async def close(self) -> None:
        pass
