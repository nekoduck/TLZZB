"""P0 切分（方案 9.1）：`corpus/tianlong.txt` → `build/paragraphs.jsonl`。不用模型。

- 回目：独占一行的「第X回」（中文数字或阿拉伯数字），后面跟回目名；回目名单独成行时取下一行。
- 段落：空行分段；行首缩进（全角空格、两个以上空格或制表符）也算新的一段。一回里既没有空行也没有缩进、
  却有多行的，按一行一段。行内断行直接拼接（中文不加空格）。
- 卷首目录：同一回出现多次时，取正文最多的那一次；没有正文的回目（目录行）丢掉。
- 段落编号 `ch01_p0001`：回数两位、回内序号四位，从 1 起。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tianlong.pipeline.common import (
    Ctx,
    Paragraph,
    PipelineError,
    StepResult,
    cn_to_int,
    int_to_cn,
    read_corpus_text,
    rel,
    write_json,
    write_paragraphs,
)

_WS = ' \t　'
HEADING_RE = re.compile(
    r'^[ \t　]*第[ \t　]*([零〇一二两三四五六七八九十百千\d]+)[ \t　]*回'
    r'(?:[ \t　:：、.．]+(.*?))?[ \t　]*$'
)
MAX_TITLE = 40


@dataclass
class _Chapter:
    n: int
    title: str
    lines: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(len(x.strip(_WS)) for x in self.lines)


def _heading(line: str) -> tuple[int, str] | None:
    m = HEADING_RE.match(line)
    if not m:
        return None
    title = (m.group(2) or '').strip(_WS)
    if len(title) > MAX_TITLE:
        return None
    try:
        return cn_to_int(m.group(1)), title
    except ValueError:
        return None


def _looks_like_title(line: str) -> bool:
    s = line.strip(_WS)
    return 0 < len(s) <= 24 and not any(c in s for c in '。！？；」”')


def split_paragraphs(lines: list[str]) -> list[str]:
    paras: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            text = ''.join(buf).strip(_WS)
            if text:
                paras.append(text)
            buf.clear()

    nonblank = 0
    for raw in lines:
        line = raw.rstrip('\r\n')
        if not line.strip(_WS):
            flush()
            continue
        nonblank += 1
        if buf and (line[:1] in ('　', '\t') or line.startswith('  ')):
            flush()
        buf.append(line.strip(_WS))
    flush()
    if len(paras) == 1 and nonblank > 3:  # 既无空行也无缩进：按一行一段
        paras = [x.strip(_WS) for x in lines if x.strip(_WS)]
    return paras


@dataclass
class SplitResult:
    paragraphs: list[Paragraph]
    chapters: list[tuple[int, str, int, int]]  # 回数、回目、段数、字数
    skipped_front: int = 0  # 第一回之前丢掉的字数
    dropped_headings: list[str] = field(default_factory=list)

    @property
    def total_chars(self) -> int:
        return sum(len(p.text) for p in self.paragraphs)


def split_text(text: str) -> SplitResult:
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    chapters: list[_Chapter] = []
    front = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        h = _heading(line)
        if h is not None:
            n, title = h
            if not title:  # 回目名单独成行
                j = i + 1
                while j < len(lines) and not lines[j].strip(_WS):
                    j += 1
                if j < len(lines) and _heading(lines[j]) is None and _looks_like_title(lines[j]):
                    title = lines[j].strip(_WS)
                    i = j
            chapters.append(_Chapter(n, title))
        elif chapters:
            chapters[-1].lines.append(line)
        else:
            front += len(line.strip(_WS))
        i += 1
    best: dict[int, _Chapter] = {}
    dropped: list[str] = []
    for ch in chapters:
        cur = best.get(ch.n)
        if cur is None or ch.size > cur.size:
            if cur is not None:
                dropped.append(f'第{int_to_cn(cur.n)}回 {cur.title}'.strip())
            best[ch.n] = ch
        else:
            dropped.append(f'第{int_to_cn(ch.n)}回 {ch.title}'.strip())
    paragraphs: list[Paragraph] = []
    summary: list[tuple[int, str, int, int]] = []
    for n in sorted(best):
        ch = best[n]
        texts = split_paragraphs(ch.lines)
        if not texts:
            dropped.append(f'第{int_to_cn(n)}回 {ch.title}（无正文）'.strip())
            continue
        for seq, t in enumerate(texts, 1):
            paragraphs.append(Paragraph(id=f'ch{n:02d}_p{seq:04d}', chapter=n, title=ch.title, seq=seq, text=t))
        summary.append((n, ch.title, len(texts), sum(len(t) for t in texts)))
    return SplitResult(paragraphs, summary, front, dropped)


async def run(ctx: Ctx) -> StepResult:
    text = read_corpus_text(ctx.paths.corpus)
    res = split_text(text)
    if not res.paragraphs:
        raise PipelineError(
            f'{ctx.paths.corpus} 里没找到「第一回」这样的回目行，切不出段落。回目要独占一行，如「第一回 回目名」。')
    write_paragraphs(ctx.paths.paragraphs, res.paragraphs)
    write_json(ctx.paths.build / 'p0_summary.json', {
        'chapters': [{'chapter': n, 'title': t, 'paragraphs': k, 'chars': c} for n, t, k, c in res.chapters],
        'paragraphs': len(res.paragraphs),
        'chars': res.total_chars,
        'skipped_front_chars': res.skipped_front,
        'dropped_headings': res.dropped_headings,
    })
    ctx.out(f'P0 切分：{len(res.chapters)} 回，{len(res.paragraphs)} 段，共 {res.total_chars} 字'
            f' → {rel(ctx.paths.paragraphs, ctx.settings.root)}')
    for n, t, k, c in res.chapters:
        ctx.out(f'  第{int_to_cn(n)}回 {t}：{k} 段，{c} 字')
    if res.skipped_front:
        ctx.out(f'  第一个回目之前的 {res.skipped_front} 字未收（卷首、序言之类）')
    if res.dropped_headings:
        ctx.out(f'  丢掉的回目行（目录或重复）：{len(res.dropped_headings)} 处')
    have = {n for n, *_ in res.chapters}
    missing = [n for n in range(1, ctx.max_chapter + 1) if n not in have]
    problems = [f'缺第{int_to_cn(n)}回' for n in missing]
    for p in problems:
        ctx.out(f'  注意：{p}；管线需要第一至{int_to_cn(ctx.max_chapter)}回。')
    return StepResult('p0', ok=True, problems=problems, written=[rel(ctx.paths.paragraphs, ctx.settings.root)],
                      summary=f'{len(res.chapters)} 回，{len(res.paragraphs)} 段，{res.total_chars} 字')
