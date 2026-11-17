"""评测跑手的公用件：配置、数据与场景、模型门面（带旁路记录）、统计与制表、结果文件。"""

from __future__ import annotations

import contextlib
import contextvars
import datetime as _dt
import json
import math
import shutil
import statistics
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tianlong import prompting
from tianlong.config import Settings
from tianlong.content.loader import load as load_content
from tianlong.kernel import effects
from tianlong.kernel.defs import Defs
from tianlong.kernel.state import Ctx, Event, WorldState
from tianlong.llm import LLM, LLMUnavailable, make_llm

EN_DIR = Path(__file__).resolve().parent / "en"
REPO_DATA = Path(__file__).resolve().parents[3] / "evals" / "data"
EN_FILES = ("jev/j1_questions.md", "jev/j2a_questions.md", "jev/j5_questions.md")
SKIP = "SKIPPED"


# ---------------------------------------------------------------- 配置


@dataclass
class EvalConfig:
    """一次评测的参数（命令行各开关）。"""

    settings: Settings
    mock: bool = False
    limit: int | None = None
    lang: str = "zh"
    model: str | None = None  # Jev 的模型 id；None 取 config.toml 的
    out_dir: Path | None = None  # 结果目录；None 取 evals/results（--mock 时是 evals/results/mock）
    data_dir: Path | None = None
    repeat: int = 5
    parallel: int = 8
    timeout_s: float = 15.0  # 评测的是答得对不对，超时放宽（游戏里是 2 秒，时延另由 latency_cost 量）
    echo: Callable[[str], None] = print
    _world: tuple[Defs, WorldState] | None = field(default=None, repr=False)
    _data: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def root(self) -> Path:
        return self.settings.root

    @property
    def data(self) -> Path:
        """数据集目录：config.toml 旁边的 evals/data；那里没有（例如配置放在别处）就用源码树里的。"""
        if self.data_dir is not None:
            return self.data_dir
        here = self.root / "evals" / "data"
        return here if here.is_dir() else REPO_DATA

    @property
    def out(self) -> Path:
        if self.out_dir is not None:
            return self.out_dir
        base = self.root / "evals" / "results"
        return base / "mock" if self.mock else base

    @property
    def report_path(self) -> Path:
        if self.out_dir is not None or self.mock:
            return self.out / "report.md"
        return self.root / "evals" / "report.md"

    @property
    def jev_model(self) -> str:
        return self.model or self.settings.jev.model

    def take(self, items: Sequence[Any]) -> list[Any]:
        return list(items[: self.limit]) if self.limit else list(items)

    # -- 数据与场景
    def dataset(self, name: str) -> Any:
        if name not in self._data:
            with open(self.data / f"{name}.yaml", encoding="utf-8") as f:
                self._data[name] = yaml.safe_load(f)
        return self._data[name]

    def world(self) -> tuple[Defs, WorldState]:
        if self._world is None:
            self._world = load_content(self.settings.path("content"))
        return self._world

    def scene_setup(self, scene: Any) -> list[dict]:
        """条目的 scene：场景库里的名字，或直接一个 {loc, present, setup} 映射。"""
        if isinstance(scene, Mapping):
            return list(scene.get("setup") or [])
        return list(self.dataset("scenes")["scenes"][scene].get("setup") or [])

    def build(self, setup: Sequence[dict] | None) -> tuple[Defs, WorldState, list[Event]]:
        """开局状态的深拷贝上执行 setup（README 第 1 节）。执行中记下违规就报错。"""
        defs, st0 = self.world()
        st = st0.model_copy(deep=True)
        evs = apply_setup(st, defs, setup)
        return defs, st, evs

    # -- 模型
    def eval_settings(self, **route: str) -> Settings:
        """评测用的配置副本：放宽超时、可换 Jev 版本、可改路由；预算不设上限。"""
        s = self.settings.model_copy(deep=True)
        s.root = self.settings.root
        s.jev.timeout_s = max(float(s.jev.timeout_s), float(self.timeout_s))
        s.jev.max_parallel = max(1, int(self.parallel))
        if self.model:
            s.jev.model = self.model
        s.limits.session_budget_usd = 0.0
        for k, v in route.items():
            setattr(s.route, k, v)
        return s

    def open_llm(self, settings: Settings | None = None, *, run_dir: Path | None = None, claude: bool = True) -> LLM:
        """建一个门面，Jev 换成带旁路记录的那个；``claude=False`` 时 Claude 一律报「没有」。"""
        llm = make_llm(settings or self.eval_settings(), mock=self.mock, run_dir=run_dir)
        llm.jev = JevTap(llm.jev)  # type: ignore[assignment]
        if not claude:
            llm.claude = NoClaude()  # type: ignore[assignment]
        return llm


def apply_setup(state: WorldState, defs: Defs, setup: Sequence[dict] | None) -> list[Event]:
    nv = len(state.violations)
    evs = effects.apply_effects(state, defs, list(setup or []), Ctx(), source="rule")
    new = state.violations[nv:]
    if new:
        raise ValueError(f"scene setup logged violations: {new}")
    return evs


# ---------------------------------------------------------------- 模型门面的旁路

_ITEM: contextvars.ContextVar[str | None] = contextvars.ContextVar("evalrun_item", default=None)


@contextlib.contextmanager
def tagged(item_id: str) -> Iterator[None]:
    """这段代码里发出的 Jev 请求记在 item_id 名下（协程各有各的上下文，互不串）。"""
    tok = _ITEM.set(item_id)
    try:
        yield
    finally:
        _ITEM.reset(tok)


@dataclass
class Call:
    kind: str
    state: Any
    questions: dict[str, Any]
    answers: dict[str, Any] | None
    seconds: float
    error: str | None = None


class JevTap:
    """包住 Jev 客户端：照常作答，同时把每个请求的题目、答案、耗时按条目记下来。"""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls: dict[str | None, list[Call]] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def ask(self, kind: str, state: Any, questions: Mapping[str, Mapping[str, Any]], **kw: Any) -> Any:
        t0 = time.perf_counter()
        tag = _ITEM.get()
        try:
            ans = await self.inner.ask(kind, state, questions, **kw)
        except BaseException as e:
            self.calls.setdefault(tag, []).append(
                Call(kind, state, dict(questions), None, time.perf_counter() - t0, f"{type(e).__name__}: {e}"))
            raise
        self.calls.setdefault(tag, []).append(Call(kind, state, dict(questions), dict(ans), time.perf_counter() - t0))
        return ans

    def last(self, tag: str, kind: str | None = None) -> Call | None:
        for c in reversed(self.calls.get(tag, [])):
            if kind is None or c.kind == kind:
                return c
        return None

    async def aclose(self) -> None:
        await self.inner.aclose()


class NoClaude:
    """评测 Jev 自己的答案时用：C 模型一律不可用（4.3 的退让：J1 答了就留着）。"""

    provider = "none"

    async def call_json(self, kind: str, *a: Any, **kw: Any) -> Any:
        raise LLMUnavailable(f"Claude {kind}: switched off for this evaluation route")

    def stream_text(self, kind: str, *a: Any, **kw: Any) -> Any:
        raise LLMUnavailable(f"Claude {kind}: switched off for this evaluation route")

    async def aclose(self) -> None:
        return None


# ---------------------------------------------------------------- 问法的语言


@contextlib.contextmanager
def lang_prompts(lang: str) -> Iterator[None]:
    """``en``：J1、J2a、J5 的问题与选项说明换成英文模板（状态仍是中文）；出来时换回原来的 prompts/。"""
    if lang != "en":
        yield
        return
    old = prompting.root()
    with tempfile.TemporaryDirectory(prefix="tianlong-eval-en-") as tmp:
        dst = Path(tmp) / "prompts"
        shutil.copytree(old, dst)
        for rel in EN_FILES:
            shutil.copyfile(EN_DIR / Path(rel).name, dst / rel)
        prompting.set_root(dst)
        try:
            yield
        finally:
            prompting.set_root(old)


def question_words(name: str) -> dict[str, Any]:
    """prompts/jev/<name>.md 里的 YAML 问法（不走各模块自己的缓存，换语言后立即生效）。"""
    return dict(yaml.safe_load(prompting.load(f"jev/{name}")) or {})


# ---------------------------------------------------------------- 并发


async def gather_limited(n: int, jobs: Sequence[Callable[[], Awaitable[Any]]]) -> list[Any]:
    import asyncio

    sem = asyncio.Semaphore(max(1, n))

    async def one(job: Callable[[], Awaitable[Any]]) -> Any:
        async with sem:
            return await job()

    return list(await asyncio.gather(*(one(j) for j in jobs)))


# ---------------------------------------------------------------- 统计


def rate(hits: int, total: int) -> float | None:
    return hits / total if total else None


def pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{100.0 * x:.{digits}f}%"


def frac(hits: int, total: int) -> str:
    return f"{pct(rate(hits, total))} ({hits}/{total})" if total else "—"


def median(xs: Sequence[float]) -> float | None:
    return statistics.median(xs) if xs else None


def percentile(xs: Sequence[float], q: float) -> float | None:
    """q 取 0 到 100；线性插值。"""
    if not xs:
        return None
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q / 100.0
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def secs(x: float | None) -> str:
    return "—" if x is None else (f"{x * 1000:.0f} ms" if x < 1 else f"{x:.2f} s")


def stdev(xs: Sequence[float]) -> float:
    return statistics.pstdev(xs) if len(xs) > 1 else 0.0


# ---------------------------------------------------------------- 表与结果文件


def table(title: str, headers: Sequence[str], rows: Sequence[Sequence[Any]], note: str = "") -> dict[str, Any]:
    return {"title": title, "headers": list(headers), "rows": [[_cell(c) for c in r] for r in rows], "note": note}


def _cell(c: Any) -> str:
    if c is None:
        return "—"
    if isinstance(c, bool):
        return "是" if c else "否"
    if isinstance(c, float):
        return f"{c:.3f}"
    return str(c).replace("|", "｜").replace("\n", " ")


def render_table(t: Mapping[str, Any]) -> str:
    out = [f"**{t['title']}**", ""] if t.get("title") else []
    out.append("| " + " | ".join(t["headers"]) + " |")
    out.append("| " + " | ".join("---" for _ in t["headers"]) + " |")
    for r in t["rows"]:
        out.append("| " + " | ".join(r) + " |")
    if t.get("note"):
        out += ["", t["note"]]
    return "\n".join(out)


def gate(name: str, passed: bool | None, detail: str, fallback: str = "") -> dict[str, Any]:
    """7.12 / 11.7 的一道闸：passed 为 None 表示没法判（样本为空、被跳过）。"""
    return {"name": name, "passed": passed, "detail": detail, "fallback": fallback}


def gate_text(g: Mapping[str, Any]) -> str:
    mark = {True: "通过", False: "未过", None: "未判"}[g.get("passed")]
    return f"{g['name']}：{mark}。{g['detail']}"


def result_name(cfg: EvalConfig, suite: str) -> str:
    name = suite
    if cfg.lang != "zh":
        name += f".{cfg.lang}"
    if cfg.model and cfg.model != cfg.settings.jev.model:
        name += f".{cfg.model}"
    return name


def make_result(cfg: EvalConfig, suite: str, *, n: int, summary: Mapping[str, Any], tables: Sequence[Mapping[str, Any]],
                gates: Sequence[Mapping[str, Any]] = (), skipped: Sequence[str] = (), notes: Sequence[str] = (),
                items: Any = None, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    res: dict[str, Any] = {
        "suite": suite,
        "meta": {
            "generated": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "mock": cfg.mock,
            "jev_model": cfg.jev_model,
            "lang": cfg.lang,
            "limit": cfg.limit,
            "n": n,
            "thresholds": cfg.settings.jev.thresholds.model_dump(),
            "claude_key": cfg.settings.claude_api_key() is not None,
            "jev_key": cfg.settings.jev_api_key() is not None,
        },
        "summary": dict(summary),
        "gates": [dict(g) for g in gates],
        "skipped": list(skipped),
        "notes": list(notes),
        "tables": [dict(t) for t in tables],
    }
    if extra:
        res.update(extra)
    if items is not None:
        res["items"] = items
    return res


def write_result(cfg: EvalConfig, res: Mapping[str, Any]) -> Path:
    cfg.out.mkdir(parents=True, exist_ok=True)
    path = cfg.out / f"{result_name(cfg, str(res['suite']))}.json"
    path.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    return path


def read_result(cfg: EvalConfig, suite: str) -> dict[str, Any] | None:
    path = cfg.out / f"{suite}.json"
    if not path.is_file():
        return None
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def print_result(cfg: EvalConfig, res: Mapping[str, Any]) -> None:
    m = res["meta"]
    mode = "mock" if m["mock"] else f"{m['jev_model']}"
    cfg.echo(f"== eval {res['suite']}（{mode}，问法 {m['lang']}，{m['n']} 条）")
    for t in res["tables"]:
        cfg.echo("")
        cfg.echo(render_table(t))
    for g in res.get("gates", []):
        cfg.echo("")
        cfg.echo(gate_text(g))
    for s in res.get("skipped", []):
        cfg.echo(f"{SKIP}：{s}")
    for n in res.get("notes", []):
        cfg.echo(f"注：{n}")
