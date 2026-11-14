"""The shared filter corpus: each corpus/filters/NAME.lua, written here as
Python filters, must make of its input what pandoc's Lua filter made."""

from __future__ import annotations

from collections.abc import Callable

import panir as A
import pytest
from conftest import CORPUS, jsonl
from panir import (
    Block,
    BlockQuote,
    Code,
    CodeBlock,
    Context,
    Div,
    Emph,
    Figure,
    Filter,
    Header,
    HorizontalRule,
    Image,
    Inline,
    Link,
    MetaBool,
    MetaInlines,
    MetaList,
    MetaString,
    Note,
    Pandoc,
    Para,
    Space,
    Str,
    Strong,
    Table,
)

FORMAT = "json"
LINES = jsonl("filters.jsonl")
NOTED = (Str, Emph, Note, Link, Code, Para, Header, Div, Table, Figure, BlockQuote)


def noting(f: Filter, seen: list[str], kinds: tuple[type, ...] = NOTED) -> None:
    """Functions noting each call on ``f`` (as the Lua scenarios do)."""

    @f.on(*kinds)
    def note(x):
        name = type(x).__name__
        seen.append(f"{name}:{x.text}" if isinstance(x, (Str, Code)) else name)

    @f.on_inlines
    def inlines(xs):
        seen.append(f"Inlines{len(xs)}")

    @f.on_blocks
    def blocks(xs):
        seen.append(f"Blocks{len(xs)}")

    @f.on_meta
    def meta(m):
        seen.append("Meta")


def listing(seen: list[str]) -> Filter:
    """A last filter, adding a paragraph listing the notes."""
    f = Filter()

    @f.on(Pandoc)
    def add(doc):
        doc.blocks.append(Para(" ".join(seen)))

    return f


def upper() -> list[Filter]:
    f = Filter()

    @f.on(Str)
    def up(s):
        return Str(s.text.upper())

    return [f]


def modify() -> list[Filter]:
    f = Filter()

    @f.on(Header)
    def demote(h):
        h.level += 1

    @f.on(Link)
    def link(x):
        x.target.url = "https://example.org/" + x.target.url

    @f.on(Image)
    def image(i):
        i.target.url = "img/" + i.target.url
        i.attr.attributes.append(("loading", "lazy"))

    @f.on(CodeBlock)
    def code(c):
        c.attr.classes.append("numbered")

    return [f]


def splice() -> list[Filter]:
    f = Filter()

    @f.on(Note, Emph)
    def delete(x):
        return []

    @f.on(Strong, Div)
    def unwrap(x):
        return x.content

    @f.on(HorizontalRule)
    def rule(x):
        return [Para(Str("one")), Para(Str("two"))]

    return [f]


def generic() -> list[Filter]:
    f = Filter()

    @f.on(Str)
    def bang(s):
        return Str(s.text + "!")

    @f.on(Inline)
    def inline(i):
        if isinstance(i, Str):
            return Str("never")
        if isinstance(i, Code):
            return Str(i.text)
        return None

    @f.on(Block)
    def block(b):
        if isinstance(b, CodeBlock):
            return Para(Str(b.text))
        return None

    return [f]


def typewise() -> list[Filter]:
    seen: list[str] = []
    f = Filter()
    noting(f, seen)

    @f.on(Pandoc)
    def pandoc(doc):
        seen.append("Pandoc")

    return [f, listing(seen)]


def topdown() -> list[Filter]:
    seen: list[str] = []
    f = Filter(traverse="topdown")
    noting(f, seen)

    @f.on(Pandoc)
    def pandoc(doc):
        seen.append("Pandoc")

    return [f, listing(seen)]


def bottomup() -> list[Filter]:
    seen: list[str] = []
    f = Filter(traverse="bottomup")
    noting(f, seen, (*NOTED, Strong))

    @f.on(Pandoc)
    def pandoc(doc):
        seen.append("Pandoc")
        doc.blocks.append(Para(" ".join(seen)))

    return [f]


def skip() -> list[Filter]:
    f = Filter(traverse="topdown")

    @f.on(Str)
    def up(s):
        return Str(s.text.upper())

    @f.on(Header)
    def header(h):
        return Para(*h.content)

    @f.on(Div, Emph)
    def keep(x, ctx: Context):
        ctx.skip_children()

    @f.on(BlockQuote)
    def quote(q, ctx: Context):
        ctx.skip_children()
        return Div(*q.content)

    return [f]


def toplists() -> list[Filter]:
    f = Filter(traverse="topdown")

    @f.on_inlines
    def reverse(xs, ctx: Context):
        if len(xs) == 1:
            ctx.skip_children()
            return None
        return xs[::-1]

    @f.on(Str)
    def up(s):
        return Str(s.text.upper())

    return [f]


def once() -> list[Filter]:
    f = Filter()

    @f.on(Str)
    def some(s):
        if s.text == "Some":
            return Emph(Str("new"))
        return None

    @f.on(Emph)
    def strong(e):
        return Strong(*e.content)

    return [f]


def lists() -> list[Filter]:
    f = Filter()

    @f.on_inlines
    def spaces(xs):
        return [x for x in xs if not isinstance(x, Space)] + [Str(f"<{len(xs)}>")]

    @f.on_blocks
    def rules(bs):
        out: list[Block] = []
        for b in bs:
            out.append(b)
            if isinstance(b, Header):
                out.append(HorizontalRule())
        return out

    return [f]


def meta() -> list[Filter]:
    f = Filter()

    @f.on_meta
    def change(m, ctx: Context):
        m["draft"] = MetaBool(True)
        m["format"] = MetaString(ctx.format or "")
        m["tags"] = MetaList(MetaString("a"), MetaInlines(Str("b")))
        del m["count"]

    @f.on(Pandoc)
    def first(doc):
        draft = doc.meta.get("draft")
        text = "draft" if isinstance(draft, MetaBool) and draft.value else "final"
        doc.blocks.insert(0, Para(Str(text)))

    return [f]


def newdoc() -> list[Filter]:
    f = Filter(traverse="topdown")

    @f.on(Pandoc)
    def anew(doc):
        title = doc.meta["title"]
        assert isinstance(title, MetaInlines)
        return Pandoc(Header(1, *title.content), Para("made anew"), meta=doc.meta)

    @f.on(Str)
    def up(s):
        return Str(s.text.upper())

    return [f]


def twice() -> list[Filter]:
    f = Filter()

    @f.on(Code)
    def double(c):
        return [c, c]

    @f.on(Para)
    def first(p):
        for x in p.content:
            if isinstance(x, Code):
                x.text = "changed"
                break

    return [f]


def metacount() -> list[Filter]:
    first = Filter()

    @first.on_meta
    def add(m):
        m["added"] = MetaInlines(Str("new"))

    n = 0
    second = Filter()

    @second.on(Str)
    def number(s):
        nonlocal n
        n += 1
        return Str(f"{s.text}{n}")

    @second.on_meta
    def calls(m):
        nonlocal n
        n += 1
        m["calls"] = MetaString(str(n))

    @second.on(Pandoc)
    def last(doc):
        nonlocal n
        n += 1
        doc.blocks.append(Para(f"calls: {n}"))

    return [first, second]


SCENARIOS: dict[str, Callable[[], list[Filter]]] = {
    f.__name__: f
    for f in (upper, modify, splice, generic, typewise, topdown, bottomup, skip, toplists)
    + (once, lists, meta, newdoc, twice, metacount)
}


def test_every_scenario_has_python_filters():
    lua = sorted(p.stem for p in (CORPUS / "filters").glob("*.lua"))
    assert sorted(line["name"] for line in LINES) == lua, (
        "corpus/filters.jsonl is stale: run scripts/filters-corpus.sh"
    )
    assert sorted(SCENARIOS) == lua


RUNS = [(line, None) for line in LINES] + [(line, t) for line in LINES for t in line["also"]]


@pytest.mark.parametrize(
    ("line", "traverse"),
    RUNS,
    ids=[line["name"] + (f"-{t}" if t else "") for line, t in RUNS],
)
def test_as_pandocs_lua(line, traverse):
    filters = SCENARIOS[line["name"]]()
    if traverse is not None:
        for f in filters:
            assert f.traverse == "typewise", "marked stateless but sets traverse"
            f.traverse = traverse
    doc = A.run(Pandoc.from_json(line["input"]), filters, FORMAT)
    assert doc.to_json() == line["output"]
