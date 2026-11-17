"""不依赖浏览器的回归：真浏览器整局里查出来的前端缺陷。"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[3] / 'src' / 'werewolf' / 'web'


def test_seats_render_after_center():
    """选目标面板在 updateCenter 里才建好 S.pick；座位必须在它之后更新，否则座位永远点不了（直播里没有后续消息触发刷新）。"""
    src = (WEB / 'js' / 'main.js').read_text(encoding='utf-8')
    assert re.search(r'updateCenter\(\);\s*updateSeats\(\)', src)


def test_new_game_dialog_has_dev_seed():
    src = (WEB / 'js' / 'dialogs.js').read_text(encoding='utf-8')
    assert "aria-label': '种子'" in src and 'seed' in src
