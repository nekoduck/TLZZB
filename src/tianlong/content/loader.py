"""读 ``content/`` → ``(Defs, WorldState)``（格式见 docs/CONTENT_FORMAT.md）。

缺了的可选文件一律当作空。``content_hash`` 是 content/ 全部文件内容的 sha256。
隐藏出口自动生成事实 ``exit:<from>:<to>``。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from tianlong.kernel.defs import (
    ArtDef,
    BaselineMark,
    CardDef,
    Defs,
    Lexicon,
    LexTerm,
    SituationDef,
    StyleDef,
    WorldRules,
)
from tianlong.kernel.state import (
    Agenda,
    Belief,
    Character,
    Fact,
    Feature,
    Item,
    Location,
    Relation,
    Status,
    Thread,
    WorldState,
)

DEFAULT_PLAYER_ID = "player"
DEFAULT_PLAYER_NAME = "阿福"


class ContentError(Exception):
    pass


def content_hash(root: Path) -> str:
    h = hashlib.sha256()
    if not root.exists():
        return h.hexdigest()
    for p in sorted(root.rglob("*")):
        if not p.is_file() or any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        h.update(p.relative_to(root).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _read(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ContentError(f"{path}: {e}") from e


def _dir_files(root: Path, sub: str) -> list[Path]:
    d = root / sub
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix in (".yaml", ".yml") and not p.name.startswith("."))


def _model(cls: Any, data: Any, where: str) -> Any:
    try:
        return cls.model_validate(data)
    except ValidationError as e:
        raise ContentError(f"{where}: {e}") from e


def _locations(raw: Any) -> dict[str, Location]:
    out: dict[str, Location] = {}
    for lid, d in (raw or {}).items():
        d = dict(d or {})
        d["id"] = lid
        feats: dict[str, Feature] = {}
        for fid, fd in (d.get("features") or {}).items():
            fd = dict(fd or {})
            fd["id"] = fid
            feats[fid] = _model(Feature, fd, f"locations.{lid}.features.{fid}")
        d["features"] = feats
        desc = d.get("desc")
        if isinstance(desc, str):
            d["desc"] = {"day": desc}
        elif desc is None:
            d["desc"] = {}
        d.pop("locked", None)
        out[lid] = _model(Location, d, f"locations.{lid}")
    return out


def _belief_holders(spec: list[str], chars: dict[str, Character]) -> list[str]:
    out: list[str] = []
    for k in spec or []:
        if k == "*":
            out += [c.id for c in chars.values() if c.onstage]
        elif isinstance(k, str) and k.startswith("role:"):
            role = k[5:]
            out += [c.id for c in chars.values() if role in c.roles]
        else:
            out.append(k)
    return out


def _rel(raw: dict | None) -> dict[str, Relation]:
    return {k: Relation.model_validate(v or {}) for k, v in (raw or {}).items()}


def _statuses(raw: Any) -> list[Status]:
    out = []
    for s in raw or []:
        out.append(Status.model_validate({"kind": s} if isinstance(s, str) else s))
    return out


def _agenda(raw: Any, where: str) -> Agenda | None:
    if raw is None:
        return None
    d = dict(raw)
    d.pop("locked", None)
    d.pop("id", None)
    d.pop("char", None)
    d.setdefault("goal", "")
    return _model(Agenda, d, where)


def load(
    content_dir: str | Path,
    *,
    player_name: str | None = None,
    seed: int = 0,
) -> tuple[Defs, WorldState]:
    root = Path(content_dir)
    world = _model(WorldRules, _read(root / "world.yaml") or {}, "world.yaml")
    start = world.calendar.start_tick

    locations = _locations(_read(root / "locations.yaml"))

    facts: dict[str, Fact] = {}
    known_by: dict[str, list[str]] = {}
    for fid, d in (_read(root / "facts.yaml") or {}).items():
        d = dict(d or {})
        known_by[fid] = list(d.pop("known_by", []) or [])
        d.pop("locked", None)
        d["id"] = fid
        facts[fid] = _model(Fact, d, f"facts.{fid}")
    for lid, loc in locations.items():
        for e in loc.exits:
            if e.hidden:
                fid = f"exit:{lid}:{e.to}"
                if fid not in facts:
                    facts[fid] = Fact(id=fid, text=f"从⟦{lid}⟧有一条不易察觉的路通往⟦{e.to}⟧", tags=["exit"])

    items: dict[str, Item] = {}
    for iid, d in (_read(root / "items.yaml") or {}).items():
        d = dict(d or {})
        d.pop("locked", None)
        d["id"] = iid
        items[iid] = _model(Item, d, f"items.{iid}")
    for lid, loc in locations.items():
        for fid, feat in loc.features.items():
            for iid in feat.contains:
                it = items.get(iid)
                if it is not None and it.holder is None and it.loc is None:
                    it.loc = lid
                    it.feature = fid
    for it in items.values():
        if it.holder is None and it.loc is None:
            it.in_world = False

    arts: dict[str, ArtDef] = {}
    for aid, d in (_read(root / "arts.yaml") or {}).items():
        d = dict(d or {})
        d.pop("locked", None)
        d["id"] = aid
        arts[aid] = _model(ArtDef, d, f"arts.{aid}")

    cards: dict[str, CardDef] = {}
    for p in _dir_files(root, "characters"):
        d = dict(_read(p) or {})
        d.pop("locked", None)
        d.setdefault("id", p.stem)
        d.setdefault("persona", p.stem)
        cards[d["id"]] = _model(CardDef, d, str(p.relative_to(root)))

    agendas: dict[str, Agenda] = {}
    for p in _dir_files(root, "agendas"):
        a = _agenda(_read(p) or {}, str(p.relative_to(root)))
        if a is not None:
            agendas[p.stem] = a

    situations: dict[str, SituationDef] = {}
    for p in _dir_files(root, "situations"):
        d = dict(_read(p) or {})
        d.pop("locked", None)
        d.setdefault("id", p.stem)
        situations[d["id"]] = _model(SituationDef, d, str(p.relative_to(root)))

    opening = dict(_read(root / "opening.yaml") or {})
    pl = dict(opening.get("player") or {})
    pid = pl.get("id", DEFAULT_PLAYER_ID)
    chars: dict[str, Character] = {}

    name = player_name or pl.get("name") or pl.get("default_name") or DEFAULT_PLAYER_NAME
    chars[pid] = _model(
        Character,
        {
            "id": pid,
            "name": name,
            "unknown_alias": pl.get("unknown_alias") or "一个年轻人",
            "tier": "soul",
            "base_power": pl.get("base_power", 0),
            "neili": pl.get("neili", 0),
            "arts": pl.get("arts") or {},
            "literacy": pl.get("literacy", 0),
            "aptitude": pl.get("aptitude", 1),
            "luck": pl.get("luck", 0),
            "roles": pl.get("roles") or [],
            "loc": pl.get("loc") or next(iter(locations), ""),
            "superior": pl.get("superior"),
            "statuses": _statuses(pl.get("statuses")),
            "rel": _rel(pl.get("rel")),
            "drives": pl.get("drives") or [],
            "awake_since": start,
            "last_meal": start,
        },
        "opening.player",
    )

    placed = dict(opening.get("chars") or {})
    for cid, card in cards.items():
        pos = dict(placed.get(cid) or {})
        agenda_name = card.agenda or cid
        ag = agendas.get(agenda_name)
        chars[cid] = _model(
            Character,
            {
                "id": cid,
                "name": card.name,
                "unknown_alias": card.unknown_alias or card.name,
                "tier": card.tier,
                "base_power": card.base_power,
                "neili": card.neili,
                "arts": dict(card.arts),
                "literacy": card.literacy,
                "aptitude": card.aptitude,
                "luck": card.luck,
                "roles": list(card.roles),
                "loc": pos.get("loc") or (card.model_extra or {}).get("loc") or "",
                "onstage": pos.get("onstage", bool(pos.get("loc"))),
                "superior": pos.get("superior", card.superior),
                "statuses": _statuses(pos.get("statuses")),
                "rel": _rel(card.rel),
                "drives": list(card.drives),
                "agenda": ag.model_copy(deep=True) if ag is not None else None,
                "persona": card.persona or cid,
                "initiative": card.initiative,
                "awake_since": start,
                "last_meal": start,
            },
            f"characters/{cid}",
        )
    for d in opening.get("extras") or []:
        d = dict(d)
        eid = d["id"]
        ag = agendas.get(d.pop("agenda", eid))
        extra = {
            "id": eid,
            "name": d.get("name", eid),
            "unknown_alias": d.get("unknown_alias") or d.get("name", eid),
            "tier": d.get("tier", "extra"),
            "base_power": d.get("base_power", 0),
            "neili": d.get("neili", 0),
            "literacy": d.get("literacy", 0),
            "aptitude": d.get("aptitude", 1),
            "luck": d.get("luck", 0),
            "roles": d.get("roles") or [],
            "loc": d.get("loc", ""),
            "onstage": d.get("onstage", True),
            "superior": d.get("superior"),
            "statuses": _statuses(d.get("statuses")),
            "rel": _rel(d.get("rel")),
            "drives": d.get("drives") or [],
            "routine": d.get("routine"),
            "agenda": ag.model_copy(deep=True) if ag is not None else None,
            "awake_since": start,
            "last_meal": start,
        }
        chars[eid] = _model(Character, extra, f"opening.extras.{eid}")

    # 开局知道的事
    def believe(cid: str, fid: str) -> None:
        c = chars.get(cid)
        if c is not None and fid in facts:
            c.beliefs[fid] = Belief(confidence=1.0, source="saw", tick=start)

    for fid, spec in known_by.items():
        for cid in _belief_holders(spec, chars):
            believe(cid, fid)
    for fid in pl.get("knows") or []:
        believe(pid, fid)
    for cid, card in cards.items():
        for fid in card.knows:
            believe(cid, fid)
    for d in opening.get("extras") or []:
        for fid in d.get("knows") or []:
            believe(d["id"], fid)

    for it in items.values():
        if it.holder and it.holder in chars:
            chars[it.holder].inventory.append(it.id)

    lex_raw = dict(_read(root / "lexicon.yaml") or {})
    lexicon = _model(Lexicon, {k: v for k, v in lex_raw.items() if k != "names"}, "lexicon.yaml")
    names: dict[str, LexTerm] = {}
    for c in chars.values():
        names[c.id] = LexTerm(name=c.name, alias=c.unknown_alias)
    for loc in locations.values():
        names[loc.id] = LexTerm(name=loc.name, alias=loc.unknown_alias)
    for it in items.values():
        names[it.id] = LexTerm(name=it.name, alias=it.unknown_alias)
    for a in arts.values():
        names[a.id] = LexTerm(name=a.name, alias=a.unknown_alias)
    lexicon.names = names

    style_raw = _read(root / "style" / "samples.yaml") or {}
    guide_path = root / "style" / "guide.md"
    style = StyleDef(
        guide=guide_path.read_text(encoding="utf-8") if guide_path.exists() else "",
        samples={k: list(v or []) for k, v in dict(style_raw).items()},
    )

    baseline = [
        _model(BaselineMark, d, f"baseline[{i}]") for i, d in enumerate(_read(root / "baseline.yaml") or [])
    ]

    h = content_hash(root)
    defs = Defs(
        world=world,
        arts=arts,
        cards=cards,
        situations=situations,
        goals=_read(root / "goals.yaml"),
        routines=_read(root / "routines.yaml"),
        opportunities=_read(root / "opportunities.yaml"),
        baseline=baseline,
        lexicon=lexicon,
        style=style,
        content_hash=h,
    )

    scheduled = []
    for s in opening.get("scheduled") or []:
        s = dict(s)
        if "at" not in s:
            s["at"] = start + int(s.pop("delay", 0))
        s.setdefault("effects", [])
        s.setdefault("pre", None)
        scheduled.append(s)
    threads: dict[str, Thread] = {}
    for i, t in enumerate(opening.get("threads") or [], 1):
        t = dict(t)
        t.setdefault("id", f"T0{i}")
        threads[t["id"]] = _model(Thread, t, f"opening.threads[{i}]")

    known = list(dict.fromkeys([pid, *(opening.get("known_terms") or [])]))
    p_loc = chars[pid].loc
    state = WorldState(
        tick=start,
        seed=seed,
        content_hash=h,
        player=pid,
        locations=locations,
        chars=chars,
        items=items,
        facts=facts,
        flags=dict(opening.get("flags") or {}),
        scheduled=scheduled,
        known_terms=known,
        term_alias=dict(opening.get("term_alias") or {}),
        threads=threads,
        visited=[p_loc] if p_loc else [],
    )
    return defs, state
