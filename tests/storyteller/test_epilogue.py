"""后事（方案 7.11、8.6）：C7 流式，出错退到模板；末尾是规则列出的分岔清单。"""

from __future__ import annotations

from typing import Any

from tianlong.kernel import resolve
from tianlong.llm import LLMRefusal, LLMUnavailable
from tianlong.storyteller import chronicle, epilogue, threads
from tianlong.storyteller.epilogue import CLOSING, divergence_block, material, template_text

from .conftest import calls

SLAP = [
    {"injure": ["duan_yu", 1, "gong_guangjie"]},
    {"emit": {"actor": "gong_guangjie", "verb": "slap", "text": "⟦gong_guangjie⟧一掌掴在⟦duan_yu⟧脸上"}},
]


def _story(w) -> None:
    chronicle.record(w.s, w.defs, [])
    evs = w.fx(SLAP)
    far = resolve.run_effects(
        w.s, w.defs, [{"kill": "sn_guard_1"}], source="agenda", offscreen=True
    )
    w.fx([{"art": ["player", "trade", 2]}])
    threads.update(w.s, w.defs, evs + far)
    chronicle.record(w.s, w.defs, evs + far)
    w.fx([{"diverge": {"mark": "m_slap", "known_to_player": False}}, {"end": "closed"}])
    chronicle.record(w.s, w.defs, [])


async def _collect(gen) -> str:
    return "".join([p async for p in gen])


def test_template_only_uses_what_the_player_knows(w):
    _story(w)
    text = template_text(w.s, w.defs)
    assert "场中舞剑的青年一掌掴在段公子脸上" in text
    assert "龚光杰" not in text and "段誉" not in text
    assert "神农帮帮众死了" not in text  # 玩家不在场、也没听说
    assert "行商的本事" in text and "随东家做完客" in text
    assert text.endswith(CLOSING)
    m = material(w.s, w.defs)
    assert m["seen"] and not m["heard"] and m["growth"]


async def test_mock_epilogue_streams_then_divergence_list(llm, w):
    _story(w)
    out = await _collect(epilogue(llm, w.s, w.defs))
    assert out.startswith(template_text(w.s, w.defs)[:20])
    assert "【与原著的分岔】" in out
    assert "龚光杰当众掴了段誉一记耳光" in out  # 完整清单（收场后）
    c7 = calls(llm, "C7")
    assert len(c7) == 1
    user = str(c7[0]["request"])
    assert "场中舞剑的青年一掌掴在段公子脸上" in user and "龚光杰" not in user


async def test_epilogue_falls_back_without_claude(settings, w, tmp_path):
    from tianlong.llm import make_llm

    real = make_llm(settings, mock=False, run_dir=tmp_path)  # 本环境没有 Claude 的密钥
    _story(w)
    out = await _collect(epilogue(real, w.s, w.defs))
    assert out == template_text(w.s, w.defs) + "\n\n" + divergence_block(w.s, w.defs)
    await real.aclose()


async def test_epilogue_falls_back_on_refusal_mid_stream(llm, w, monkeypatch):
    async def half(kind, system, user, **kw: Any):
        yield "你在山上待了些时日。"
        raise LLMRefusal("C7")

    monkeypatch.setattr(llm.claude, "stream_text", half)
    _story(w)
    out = await _collect(epilogue(llm, w.s, w.defs))
    assert out.startswith("你在山上待了些时日。")
    assert CLOSING in out and "【与原著的分岔】" in out


async def test_epilogue_falls_back_when_unavailable_at_start(llm, w, monkeypatch):
    async def down(kind, system, user, **kw: Any):
        raise LLMUnavailable("no key")
        yield ""  # pragma: no cover

    monkeypatch.setattr(llm.claude, "stream_text", down)
    _story(w)
    out = await _collect(epilogue(llm, w.s, w.defs))
    assert out.startswith(template_text(w.s, w.defs))


async def test_epilogue_length_capped(llm, w, monkeypatch):
    async def chatty(kind, system, user, **kw: Any):
        for _ in range(200):
            yield "你又想起了那天的事。"

    monkeypatch.setattr(llm.claude, "stream_text", chatty)
    out = await _collect(epilogue(llm, w.s, w.defs))
    body = out.split("【与原著的分岔】")[0]
    assert len(body) < epilogue_max() + 40


def epilogue_max() -> int:
    from tianlong.storyteller.epilogue import MAX_CHARS

    return MAX_CHARS


def test_divergence_block_empty(w):
    assert "没有记下" in divergence_block(w.s, w.defs)
