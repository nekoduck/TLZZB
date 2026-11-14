"""content/loader.py 与 content/validate.py（P7）。"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from tianlong.content.loader import content_hash, load
from tianlong.content.validate import validate
from tianlong.kernel.state import WorldState

from .conftest import MINI, World


def _copy(tmp_path: Path) -> Path:
    dst = tmp_path / "mini"
    shutil.copytree(MINI, dst)
    return dst


def _edit(path: Path, fn) -> None:  # type: ignore[no-untyped-def]
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data = fn(data) or data
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_load_and_validate_clean() -> None:
    defs, state = load(MINI)
    assert validate(defs, state) == []
    assert state.tick == 32
    assert state.player == "player"
    assert state.chars["player"].name == "阿木"
    assert set(state.locations) == {"hall", "yard", "cliff"}
    assert len([c for c in state.chars.values()]) == 4
    assert defs.content_hash == state.content_hash and len(state.content_hash) == 64


def test_player_name_parameter() -> None:
    _, state = load(MINI, player_name="小石头")
    assert state.chars["player"].name == "小石头"


def test_known_by_star_role_and_ids() -> None:
    w = World()
    for cid in ("player", "fighter", "guest", "girl"):
        assert "F_bout" in w.c(cid).beliefs
    assert "F_fighter_temper" in w.c("fighter").beliefs
    assert "F_fighter_temper" not in w.c("guest").beliefs
    assert "F_girl_on_beam" in w.c("girl").beliefs
    assert "F_girl_on_beam" not in w.c("player").beliefs


def test_hidden_exit_fact_items_and_inventory() -> None:
    w = World()
    assert "exit:cliff:hall" in w.s.facts
    m = w.s.items["old_manual"]
    assert (m.loc, m.feature, m.holder) == ("yard", "woodpile", None)
    assert w.s.items["lucky_charm"].in_world is False
    assert set(w.c("player").inventory) == {"rope", "rations"}
    assert "poison" in w.c("fighter").inventory
    assert w.c("fighter").agenda is not None and w.c("fighter").agenda.steps[0].id == "f_watch"
    assert w.c("girl").statuses[0].kind == "hidden"
    assert w.s.known_terms[:2] == ["player", "hall"]
    assert w.s.term_alias == {"guest": "白公子"}
    assert w.s.threads["T_home"].kind == "wish"
    assert w.s.scheduled[0]["at"] == 40
    assert w.defs.lexicon.names["fighter"].alias == "那个黑脸汉子"
    assert w.defs.lexicon.terms["out_road"].name == "山外大路"
    assert isinstance(w.defs.routines, dict) and "idle" in w.defs.routines
    assert isinstance(w.defs.opportunities, dict) and w.defs.goals == {}
    assert w.defs.baseline[0].id == "m_slap"
    assert w.defs.style.guide.startswith("白描")


def test_content_hash_changes_with_content(tmp_path: Path) -> None:
    dst = _copy(tmp_path)
    h1 = content_hash(dst)
    assert h1 == content_hash(MINI)
    (dst / "style" / "guide.md").write_text("换了一句。", encoding="utf-8")
    assert content_hash(dst) != h1


def test_missing_optional_files_are_empty(tmp_path: Path) -> None:
    d = tmp_path / "tiny"
    d.mkdir()
    (d / "locations.yaml").write_text(
        yaml.safe_dump({"room": {"name": "屋子", "exits": [], "desc": "一间屋子。"}}, allow_unicode=True),
        encoding="utf-8",
    )
    (d / "opening.yaml").write_text(yaml.safe_dump({"player": {"loc": "room"}}), encoding="utf-8")
    defs, state = load(d)
    assert defs.arts == {} and defs.cards == {} and defs.situations == {} and defs.baseline == []
    assert defs.goals is None and defs.routines is None and defs.opportunities is None
    assert state.chars["player"].name == "阿福"
    assert state.locations["room"].desc == {"day": "一间屋子。"}
    assert validate(defs, state) == []


def test_extras_loaded(tmp_path: Path) -> None:
    dst = _copy(tmp_path)

    def add(data: dict) -> None:
        data["extras"] = [
            {
                "id": "porter",
                "name": "挑夫",
                "unknown_alias": "一个挑夫",
                "routine": "idle",
                "loc": "yard",
                "base_power": 1,
                "roles": ["hall_sect"],
                "knows": ["F_girl_stranger"],
            }
        ]

    _edit(dst / "opening.yaml", add)
    defs, state = load(dst)
    p = state.chars["porter"]
    assert p.tier == "extra" and p.routine == "idle" and p.loc == "yard"
    assert "F_fighter_temper" in p.beliefs and "F_girl_stranger" in p.beliefs
    assert validate(defs, state) == []


def test_validate_catches_problems(tmp_path: Path) -> None:
    dst = _copy(tmp_path)

    def break_locs(data: dict) -> None:
        data["hall"]["exits"] = []  # 堂上出不去
        data["yard"]["exits"] = [e for e in data["yard"]["exits"] if e["to"] != "hall"]
        data["cliff"]["exits"][1]["hidden"] = False
        data["island"] = {"name": "孤岛", "exits": [{"to": "nowhere", "cost": 1}], "desc": "孤岛。"}

    _edit(dst / "locations.yaml", break_locs)

    def break_sit(data: dict) -> None:
        data["when"] = {"all": [{"flaggg": "x"}, {"at": ["fighter", "moon"]}]}
        data["actions"]["slap"]["effects"].append({"give": ["self", "guest", "no_such_item"]})
        data["actions"]["slap"]["effects"].append({"injure": ["$nobody", 1]})
        data["actions"]["slap"]["effects"].append({"teleport": "x"})
        data["actions"]["laugh"]["effects"].append({"check": {"kind": "dance"}})

    _edit(dst / "situations" / "bout_laugh.yaml", break_sit)

    def break_item(data: dict) -> None:
        data["rope"]["teaches"] = ["no_art"]

    _edit(dst / "items.yaml", break_item)
    defs, state = load(dst)
    problems = "\n".join(validate(defs, state))
    assert "island" in problems and "not reachable" in problems
    assert "nowhere" in problems
    assert "unknown predicate 'flaggg'" in problems
    assert "unknown location 'moon'" in problems
    assert "unknown item 'no_such_item'" in problems
    assert "unknown character '$nobody'" in problems
    assert "unknown effect 'teleport'" in problems
    assert "check needs a kind" in problems
    assert "unknown art 'no_art'" in problems


def test_state_json_round_trip_fresh_and_played() -> None:
    w = World()
    s2 = WorldState.model_validate_json(w.s.model_dump_json())
    assert s2 == w.s
    w.act("player", "go", exit="yard")
    w.act("player", "search", feature="woodpile")
    w.say("player", "greet")
    w.fx([{"open": ["bout_laugh", "hall"]}, {"schedule": [3, [{"set_flag": ["x", 1]}], None]}])
    dumped = w.s.model_dump_json()
    s3 = WorldState.model_validate_json(dumped)
    assert s3 == w.s
    assert s3.model_dump_json() == dumped
