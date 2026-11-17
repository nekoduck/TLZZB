"""人设：从 data/personas.yaml 读 12 个原创人设，每局抽 8 个（ARCHITECTURE 5）。"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from werewolf.kernel.rng import Rng
from werewolf.mind.types import Persona, PersonaVoice

PERSONA_FILE = Path(__file__).parent / 'data' / 'personas.yaml'


@lru_cache(maxsize=1)
def _load() -> tuple[Persona, ...]:
    raw = yaml.safe_load(PERSONA_FILE.read_text(encoding='utf-8'))
    out: list[Persona] = []
    for item in raw['personas']:
        item = dict(item)
        item['bio'] = re.sub(r'\s+', '', item['bio'])  # 折叠的多行文字去掉换行产生的空白
        out.append(Persona.model_validate(item))
    return tuple(out)


def load_personas() -> list[Persona]:
    """全部人设（深拷贝，调用方改动不影响缓存）。读取时即按 Persona 模型校验。"""
    return [p.model_copy(deep=True) for p in _load()]


def draft(rng: Rng, n: int = 8) -> list[Persona]:
    """从人设池里无放回抽 n 个，顺序即抽取顺序。"""
    pool = load_personas()
    if not 0 < n <= len(pool):
        raise ValueError(f'draft: n must be in 1..{len(pool)}, got {n}')
    return rng.sample(pool, n)


def voice_of(persona: Persona) -> PersonaVoice:
    """人设里与说话有关的部分（不含性格数值与头像）。"""
    return PersonaVoice(
        id=persona.id, name=persona.name, title=persona.title, tags=list(persona.tags), bio=persona.bio,
        choices=list(persona.choices), style=persona.style.model_copy(deep=True),
    )
