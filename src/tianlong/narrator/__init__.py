"""玩家视角的叙述与核对（方案 2.2–2.4、4.4、7.3 J5、7.7 C3、7.8 C4），模板兜底。

    async for piece in narrator.narrate(llm, state, defs, view): ...      # C3 流式 + 逐句检查 + J5
    texts = await narrator.phrase_options(llm, state, defs, options)       # C4 + 模板兜底
    narrator.diegetic_reply('absurd')                                       # 不耗时的戏内回话
    narrator.warning_text(state, defs, action)                              # 致命动作先警示
"""

from tianlong.narrator.checks import (
    Checker,
    SentenceSplitter,
    build_checker,
    check_sentence,
    split_sentences,
    text_len,
    truncate,
)
from tianlong.narrator.narrate import NarrationReport, narrate, narrate_text, player_brief
from tianlong.narrator.options_text import OptionsReport, phrase_options, template_options
from tianlong.narrator.templates import (
    Fact,
    diegetic_reply,
    fact_list,
    option_template,
    template_narration,
    template_sentences,
    travel_summary,
    warning_text,
)

__all__ = [
    'Checker',
    'Fact',
    'NarrationReport',
    'OptionsReport',
    'SentenceSplitter',
    'build_checker',
    'check_sentence',
    'diegetic_reply',
    'fact_list',
    'narrate',
    'narrate_text',
    'option_template',
    'phrase_options',
    'player_brief',
    'split_sentences',
    'template_narration',
    'template_options',
    'template_sentences',
    'text_len',
    'travel_summary',
    'truncate',
    'warning_text',
]
