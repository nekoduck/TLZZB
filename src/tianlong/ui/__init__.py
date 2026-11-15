"""终端界面（方案 10.1；ARCHITECTURE 8）。

``ui.events`` 与 ``ui.text`` 不依赖 rich 与 prompt_toolkit；``ui.render``、``ui.repl`` 用到它们时才 import。
"""

from tianlong.ui.events import Break, Ended, Header, Notice, Options, Text, UIEvent

__all__ = ["Break", "Ended", "Header", "Notice", "Options", "Text", "UIEvent"]
