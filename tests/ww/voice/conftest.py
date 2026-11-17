"""口吻测试共用：随机合法简报的生成器。"""

from __future__ import annotations

import random

import pytest

from werewolf.kernel.types import ActKind, Role, Tone
from werewolf.mind.types import (
    PRIVATE_REASONS,
    PublicAct,
    PublicMood,
    PublicPlan,
    ReasonCode,
    Situation,
    SpeechBrief,
)
from werewolf.testing import duel_scenario
from werewolf.voice.brief import build_brief
from werewolf.voice.personas import load_personas, voice_of

PUBLIC_REASONS = [r for r in ReasonCode if r not in PRIVATE_REASONS]


@pytest.fixture(scope='session')
def pv():
    return duel_scenario().public_view()


@pytest.fixture(scope='session')
def voices():
    return [voice_of(p) for p in load_personas()]


def random_plan(rnd: random.Random, pv_events: int) -> PublicPlan:
    seat = rnd.randint(1, 9)
    day = rnd.randint(1, 4)
    others = [s for s in range(1, 10) if s != seat]
    rnd.shuffle(others)
    acts: list[PublicAct] = []

    def reason() -> dict:
        if rnd.random() < 0.6:
            return {}
        return {
            'reason': rnd.choice(PUBLIC_REASONS),
            'evidence': [rnd.randint(1, pv_events) for _ in range(rnd.randint(0, 2))],
            'fuzzy': rnd.random() < 0.3,
        }

    if rnd.random() < 0.5:
        acts.append(PublicAct(kind=ActKind.CLAIM_ROLE, role=rnd.choice(list(Role)), weight=rnd.randint(1, 3), **reason()))
    for _ in range(rnd.choice([0, 0, 1, 1, 2])):
        acts.append(PublicAct(kind=ActKind.CLAIM_CHECK, target=others.pop(), result=rnd.choice(['wolf', 'good']),
                              night=rnd.randint(1, day), weight=rnd.randint(1, 3), **reason()))
    if rnd.random() < 0.2:
        acts.append(PublicAct(kind=ActKind.CLAIM_SAVE, target=others.pop(), night=rnd.randint(1, day), **reason()))
    if rnd.random() < 0.2:
        acts.append(PublicAct(kind=ActKind.CLAIM_POISON, target=others.pop(), night=rnd.randint(1, day), **reason()))
    for kind in (ActKind.ACCUSE, ActKind.ACCUSE, ActKind.VOUCH, ActKind.DEFEND, ActKind.BACK_CLAIM,
                 ActKind.DOUBT_CLAIM, ActKind.QUESTION):
        if rnd.random() < 0.3 and others:
            tgt = None if kind == ActKind.DEFEND and rnd.random() < 0.6 else others.pop()
            acts.append(PublicAct(kind=kind, target=tgt, weight=rnd.randint(1, 3), **reason()))
    if rnd.random() < 0.5 and others:
        acts.append(PublicAct(kind=ActKind.VOTE_INTENT, target=others.pop(), weight=rnd.randint(1, 3), **reason()))
    if not acts or rnd.random() < 0.05:
        acts = [PublicAct(kind=ActKind.PASS)]
    rnd.shuffle(acts)
    addressed = [others.pop()] if others and rnd.random() < 0.3 else []
    return PublicPlan(
        seat=seat, day=day, kind=rnd.choice(['day', 'day', 'pk', 'last_words']), acts=acts,
        tone=rnd.choice(list(Tone)), length=rnd.choice(['short', 'medium', 'long']),
        cover_role=rnd.choice([None, *Role]), addressed=addressed,
    )


def random_brief(rnd: random.Random, pv, voices) -> SpeechBrief:
    plan = random_plan(rnd, len(pv.events))
    sit = Situation(index=rnd.randint(0, 3), total=4,
                    died=rnd.choice([None, 'night', 'vote', 'shot']) if plan.kind == 'last_words' else None)
    mood = PublicMood(state=rnd.choice(['steady', 'confident', 'tense', 'tilted']))
    return build_brief(plan, pv, rnd.choice(voices), mood, sit, [], rnd.choice([60, 90, 120, 180]))
