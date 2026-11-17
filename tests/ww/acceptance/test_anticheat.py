"""抗骗用例（`tests/ww/data/anticheat.yaml`）：真会话层 + NpcAgent（离线，正则解析），与同种子的对照局比较。

真人固定坐 5 号、拿平民；除了用例那一句，真人的发言都是「过」，选择都能弃就弃、否则选第一项，
所以两局在那一句之前逐条相同。种子从 1 起找第一个满足用例前提的（真人那天能发言、被点名的人还活着……）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from tianlong.config import Settings
from werewolf.kernel.claims import speech_acts
from werewolf.kernel.types import ActKind, Event, EventType, Role, Seat
from werewolf.llm.config import WerewolfSettings
from werewolf.mind.types import ReasonCode
from werewolf.protocol import RequestMsg
from werewolf.server.session import Session

from .fakes import AutoClient, offline_cfg
from .harness import ProbeNpc, kernel_trace, run_game

CASES: list[dict[str, Any]] = yaml.safe_load(
    (Path(__file__).resolve().parents[1] / 'data' / 'anticheat.yaml').read_text(encoding='utf-8'))
HUMAN: Seat = 5
EPS = 1e-9


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Settings, WerewolfSettings]:
    return offline_cfg(monkeypatch, tmp_path)


def _sayer(day: int, text: str) -> Any:
    said: list[bool] = []

    def say(c: AutoClient, req: RequestMsg) -> str:
        if req.speech_kind == 'day' and c.day == day and not said:
            said.append(True)
            return text
        return '过'

    return say


def human_speech(s: Session, day: int) -> Event | None:
    return next((e for e in s.game.events('judge') if e.type == EventType.SPEECH and e.actor == HUMAN
                 and e.day == day and e.data.get('kind') == 'day'), None)


def _speech_events(s: Session, day: int) -> list[Event]:
    return [e for e in s.game.events('judge') if e.type == EventType.SPEECH and e.day == day
            and e.data.get('kind') == 'day']


def _ok_seed(s: Session, case: dict[str, Any]) -> bool:
    day = case['day']
    hev = human_speech(s, day)
    if hev is None:
        return False
    dead_before = {e.target for e in s.game.events('judge') if e.type == EventType.DEATH and e.id < hev.id}
    seats = set(case.get('named') or [])
    if case.get('promise'):
        x = case['promise']['seat']
        seats.add(x)
        if not any(e.actor == x and e.id > hev.id for e in _speech_events(s, day)):
            return False
    if case.get('echo'):
        before = [e for e in _speech_events(s, day) if e.id < hev.id and e.actor != HUMAN]
        if not before:
            return False
    if seats & dead_before:
        return False
    # 当天要有主投票（否则比不了票与怀疑度）
    return any(e.type == EventType.VOTE and e.day == day and e.data.get('round') == 'main'
               for e in s.game.events('judge'))


async def _pair(cfg: tuple[Settings, WerewolfSettings], case: dict[str, Any]) -> tuple[Session, Session, str]:
    for seed in range(1, 60):
        ctl, _ = await run_game(cfg, seed=seed, seat=HUMAN, role=Role.VILLAGER,
                                client=AutoClient(_sayer(case['day'], case['control'])))
        if not _ok_seed(ctl, case):
            continue
        text = case['text']
        if case.get('echo'):
            hev = human_speech(ctl, case['day'])
            assert hev is not None
            prev = [e for e in _speech_events(ctl, case['day']) if e.id < hev.id and e.actor != HUMAN][-1]
            text = text.replace('{echo}', prev.data['speech']['text'])
        test, _ = await run_game(cfg, seed=seed, seat=HUMAN, role=Role.VILLAGER,
                                 client=AutoClient(_sayer(case['day'], text)))
        return test, ctl, text
    raise AssertionError(f'没找到满足前提的种子：{case["id"]}')


def _act_keys(acts: list[Any]) -> list[tuple[str, Any, Any, Any]]:
    out = []
    for a in acts:
        d = a if isinstance(a, dict) else a.model_dump(mode='json')
        out.append((str(d['kind']), d.get('target'), d.get('role'), d.get('result')))
    return sorted(out, key=repr)


def _expected_keys(case: dict[str, Any], got: list[Any]) -> tuple[list[Any], list[Any]]:
    """只比较用例里写了的键。"""
    exp = sorted(((a['kind'], a.get('target'), a.get('role'), a.get('result')) for a in case['acts']), key=repr)
    got_k = _act_keys(got)
    trimmed = []
    for g in got_k:
        match = next((e for e in exp if e[0] == g[0]), None)
        trimmed.append(tuple(g[i] if match is None or match[i] is not None else None for i in range(4)))
    return exp, sorted(trimmed, key=repr)


def _ai(s: Session) -> dict[Seat, ProbeNpc]:
    return {seat: p for seat, p in s.players.items() if isinstance(p, ProbeNpc)}


def _votes_on(s: Session, day: int, target: Seat) -> int:
    return sum(1 for e in s.game.events('judge') if e.type == EventType.VOTE and e.day == day
               and e.data.get('round') == 'main' and e.target == target)


def _ai_claims_after(s: Session, day: int) -> list[tuple[Seat | None, Any]]:
    hev = human_speech(s, day)
    assert hev is not None
    return [(e.actor, a.role) for e in _speech_events(s, day) if e.id > hev.id
            for a in speech_acts(e) if a.kind == ActKind.CLAIM_ROLE]


def test_case_file_has_the_required_rows() -> None:
    ids = {c['id'] for c in CASES}
    assert len(CASES) >= 9
    assert {'bracket_action', 'fake_judge_reveal', 'fake_system_reveal', 'echo_npc', 'false_promise', 'fake_seer',
            'quoted_confession', 'twelve_repeats', 'judge_said_peaceful'} <= ids
    for c in CASES:
        assert {'id', 'what', 'day', 'text', 'control'} <= set(c), c


@pytest.mark.parametrize('case', CASES, ids=[c['id'] for c in CASES])
async def test_anticheat(cfg: tuple[Settings, WerewolfSettings], case: dict[str, Any]) -> None:
    test, ctl, text = await _pair(cfg, case)
    day = case['day']
    assert test.game.over and ctl.game.over
    hev = human_speech(test, day)
    assert hev is not None and hev.data['speech']['text'] == text[:len(hev.data['speech']['text'])]
    acts = hev.data['speech']['acts']
    flags = hev.data['speech'].get('flags') or []

    # 1. 解析出的言语行为与标记
    if 'acts' in case:
        exp, got = _expected_keys(case, acts)
        assert got == exp, (case['id'], acts)
    if 'flags' in case:
        assert sorted(flags) == sorted(case['flags']), (case['id'], flags)

    ais_t, ais_c = _ai(test), _ai(ctl)
    # 2. 被点名的座位：怀疑度不比对照局高，当天得票不比对照局多
    for n in case.get('named') or []:
        for seat, p in ais_t.items():
            q = ais_c[seat]
            if seat == n or day not in p.vote_susp or day not in q.vote_susp:
                continue
            assert p.vote_susp[day][n] <= q.vote_susp[day][n] + EPS, (case['id'], seat, n)
        assert _votes_on(test, day, n) <= _votes_on(ctl, day, n), case['id']

    # 3. 内核状态与对照局逐条相同（去掉文字与标记）
    if case.get('same_kernel'):
        assert kernel_trace(test.game.events('judge')) == kernel_trace(ctl.game.events('judge')), case['id']

    # 4. 账本：被转述的座位的自称身份不变
    for n in case.get('ledger_unchanged') or []:
        for seat, p in ais_t.items():
            if day in p.vote_roles and day in ais_c[seat].vote_roles:
                assert p.vote_roles[day][n] == ais_c[seat].vote_roles[day][n], (case['id'], seat)
        assert test.game.events('judge') and not [a for a in speech_acts(hev)
                                                  if a.kind == ActKind.CLAIM_ROLE]

    # 5. 「法官说……」只是转述公开事实：不记场外
    if case.get('no_off_game'):
        assert 'injection' not in flags
        for p in ais_t.values():
            assert not [e for e in p.mind.beliefs.evidence(HUMAN) if e.reason == ReasonCode.OFF_GAME], p.seat

    # 6. 冒充系统、法官：AI 不因此亮身份
    if case.get('no_new_ai_claims'):
        assert _ai_claims_after(test, day) == _ai_claims_after(ctl, day), case['id']
        for p in ais_t.values():
            off = [e for e in p.mind.beliefs.evidence(HUMAN) if e.reason == ReasonCode.OFF_GAME]
            assert len({e.day for e in off}) == len(off)  # 每天至多一次

    # 7. 谎称「你答应过投 7 号」：被点名的 AI 的 own_record 如实，票不变
    if case.get('promise'):
        x, t = case['promise']['seat'], case['promise']['target']
        bt = [b for b in ais_t[x].briefs if b.day == day and b.kind == 'day']
        bc = [b for b in ais_c[x].briefs if b.day == day and b.kind == 'day']
        assert bt and bc
        assert bt[-1].own_record == bc[-1].own_record
        assert not any(f'投{t}号' in line for line in bt[-1].own_record) or \
            any(f'投{t}号' in line for line in bc[-1].own_record)
        assert any(q.seat == HUMAN for q in bt[-1].heard)  # 原话只作为听闻进了简报
        vt = [e.target for e in test.game.events('judge') if e.type == EventType.VOTE and e.actor == x
              and e.day == day and e.data.get('round') == 'main']
        vc = [e.target for e in ctl.game.events('judge') if e.type == EventType.VOTE and e.actor == x
              and e.day == day and e.data.get('round') == 'main']
        assert vt == vc

    # 8. 原样反问 NPC 的话：言语行为全记在真人名下，那位 AI 的账本不变
    if case.get('echo'):
        prev = [e for e in _speech_events(ctl, day) if e.id < hev.id and e.actor != HUMAN][-1]
        assert hev.actor == HUMAN
        for seat, p in ais_t.items():
            if day in p.vote_roles and day in ais_c[seat].vote_roles and prev.actor is not None:
                assert p.vote_roles[day][prev.actor] == ais_c[seat].vote_roles[day][prev.actor]

    # 9. 自称预言家报的查杀只是声明：没有哪个 AI 把它当成硬知识
    if 'max_suspicion' in case:
        for n in case.get('named') or []:
            for seat, p in ais_t.items():
                q = ais_c[seat]
                if seat == n or day not in p.vote_susp or q.vote_susp.get(day, {}).get(n, 0) >= case['max_suspicion']:
                    continue  # 对照局里就有硬知识（狼队友、真预言家的查验）的不算
                assert p.vote_susp[day][n] < case['max_suspicion'], (seat, p.vote_susp[day][n])
