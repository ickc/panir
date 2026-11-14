"""Functions for nodes that are neither inlines nor blocks (pandoc's Lua has
none: these are panir's own), and what mutation testing found untested."""

from __future__ import annotations

import io

import panir
import pytest
from panir import (
    ASTTypeError,
    Attr,
    Block,
    Caption,
    Cell,
    Citation,
    Cite,
    Context,
    Emph,
    Filter,
    Inline,
    Math,
    MathType,
    MetaBlocks,
    MetaBool,
    MetaInlines,
    MetaList,
    MetaMap,
    MetaString,
    MetaValue,
    Pandoc,
    Para,
    Plain,
    RawInline,
    Row,
    Str,
    Table,
    TableBody,
    stringify,
    to_python,
    walk,
)


def table() -> Table:
    row = Row(Cell("a"), Cell("b"))
    return Table(
        caption=Caption(Para("cap")),
        bodies=[TableBody(body=[row])],
        identifier="t",
    )


def document() -> Pandoc:
    return Pandoc(
        table(),
        Para(Cite([Citation("one"), Citation("two")], [Str("c")])),
        meta={"m": MetaList(MetaString("x"), MetaBool(True)), "s": MetaString("y")},
    )


TRAVERSES = ["typewise", "topdown", "bottomup"]


@pytest.mark.parametrize("traverse", TRAVERSES)
def test_each_kind_is_visited(traverse):
    seen: list[str] = []
    f = Filter(traverse=traverse)

    @f.on(MetaValue, Cell, Row, Attr, Caption, Citation)
    def note(x, ctx: Context):
        seen.append(f"{type(x).__name__}@{ctx.where}")

    f(document())
    expected = {
        "Attr@blocks[0].attr",
        "Caption@blocks[0].caption",
        "Row@blocks[0].bodies[0].body[0]",
        "Cell@blocks[0].bodies[0].body[0].cells[0]",
        "Cell@blocks[0].bodies[0].body[0].cells[1]",
        "Attr@blocks[0].bodies[0].body[0].cells[0].attr",
        "Attr@blocks[0].bodies[0].body[0].cells[1].attr",
        "Attr@blocks[0].bodies[0].body[0].attr",
        "Attr@blocks[0].bodies[0].attr",
        "Attr@blocks[0].head.attr",
        "Attr@blocks[0].foot.attr",
        "Citation@blocks[1].content[0].citations[0]",
        "Citation@blocks[1].content[0].citations[1]",
        "MetaList@meta.m",
        "MetaString@meta.m.content[0]",
        "MetaBool@meta.m.content[1]",
        "MetaString@meta.s",
    }
    assert set(seen) == expected and len(seen) == len(expected), seen
    # the metadata in the order of its keys; a node's children before it,
    # unless top-down
    first, child = seen.index("MetaList@meta.m"), seen.index("MetaString@meta.m.content[0]")
    assert (first < child) == (traverse == "topdown")
    assert seen.index("MetaString@meta.m.content[0]") < seen.index("MetaString@meta.s")


def test_typewise_other_nodes_after_blocks_before_metadata():
    order: list[str] = []
    f = Filter()
    f.on(Inline)(lambda x: order.append("inline"))
    f.on(Block)(lambda x: order.append("block"))
    f.on(Cell)(lambda x: order.append("cell"))
    f.on_meta(lambda m: order.append("meta"))
    f(document())
    kinds = [k for i, k in enumerate(order) if i == 0 or order[i - 1] != k]
    assert kinds == ["inline", "block", "cell", "meta"]


@pytest.mark.parametrize("traverse", TRAVERSES)
def test_replacing_splicing_and_deleting_them(traverse):
    f = Filter(traverse=traverse)

    @f.on(Cell)
    def two_cells(c: Cell):
        if stringify(c) == "a":
            return [c, Cell("a2")]  # spliced
        return []  # deleted

    @f.on(Citation)
    def rename(c: Citation):
        return Citation(c.id.upper())

    @f.on(Attr)
    def classes(a: Attr):
        return Attr(a.identifier, ["seen"])

    @f.on(Caption)
    def caption(c: Caption):
        return Caption(Plain("new"))

    @f.on(MetaString)
    def meta(m: MetaString):
        return MetaInlines(Str(m.text))

    doc = f(document())
    t = doc.blocks[0]
    assert isinstance(t, Table)
    assert [stringify(c) for c in t.bodies[0].body[0].cells] == ["a", "a2"]
    assert t.attr == Attr("t", ["seen"]) and t.caption == Caption(Plain("new"))
    # (a replacement is walked only top-down: the new cell's attributes)
    new_cell = ["seen"] if traverse == "topdown" else []
    assert [c.attr.classes for c in t.bodies[0].body[0].cells] == [["seen"], new_cell]
    cite = doc.blocks[1].content[0]  # type: ignore[attr-defined]
    assert [c.id for c in cite.citations] == ["ONE", "TWO"]
    assert doc.meta["s"] == MetaInlines(Str("y"))
    assert doc.meta["m"] == MetaList(MetaInlines(Str("x")), MetaBool(True))


def test_a_wrong_replacement_of_one_says_where():
    f = Filter()
    f.on(Caption)(lambda c: [c])
    with pytest.raises(ASTTypeError, match=r"Table.caption: expected one node") as e:
        f(document())
    assert "for the Caption at blocks[0].caption" in e.value.__notes__[0]
    g = Filter()
    g.on(Row)(lambda r: Para("x"))
    with pytest.raises(ASTTypeError, match=r"TableBody.body\[0\]: expected a Row, got Para"):
        g(document())


# -- what mutation testing found untested ------------------------------------------


def test_walk_top_down():
    seen: list[str] = []

    def action(x, ctx):
        seen.append(f"{type(x).__name__}@{ctx.where}")

    walk(Para(Emph(Str("a"))), action, wants=lambda c: c is not Para)
    assert seen == ["Str@Para.content[0].content[0]", "Emph@Para.content[0]"]
    seen.clear()
    doc = Pandoc(Para(Emph(Str("a"))))
    walk(doc, lambda x, ctx: seen.append(type(x).__name__), top_down=True)
    assert seen == ["Pandoc", "Para", "Emph", "Str"]
    seen.clear()

    def replace(x, ctx):
        seen.append(type(x).__name__)
        if isinstance(x, Emph):
            return Emph(Math(MathType.InlineMath, "m"))

    walk(doc, replace, top_down=True)
    assert seen == ["Pandoc", "Para", "Emph", "Math"]


def test_returned_lists_are_checked():
    f = Filter()
    f.on(Str)(lambda s: [s, 1])
    with pytest.raises(ASTTypeError, match=r"Para.content\[1\]: expected Inline, got int 1"):
        f(Pandoc(Para("a")))


def test_setdefault_checks_and_converts():
    doc = Pandoc()
    assert doc.meta.setdefault("draft", True) == MetaBool(True)
    assert doc.meta.setdefault("draft", False) == MetaBool(True)
    with pytest.raises(ASTTypeError, match=r"Pandoc.meta\['x'\]: expected MetaValue"):
        doc.meta.setdefault("x", object())  # type: ignore[arg-type]
    with pytest.raises(ASTTypeError, match="key"):
        doc.meta.setdefault(1, True)  # type: ignore[arg-type]
    assert "x" not in doc.meta


def test_to_python_of_text():
    assert to_python(MetaBlocks(Para("a b"), Para("c"))) == "a bc"
    assert to_python(MetaInlines(Str("a"), Emph(Str("b")))) == "ab"
    assert to_python(MetaMap({"k": MetaInlines(Str("v"))})) == {"k": "v"}


def test_stringify_a_raw_br():
    assert stringify(Para("a", RawInline("html", "<br>"), "b")) == "a b"
    assert stringify(Para("a", RawInline("html", "<b>"), "b")) == "ab"
    assert stringify(Para("a", RawInline("latex", "<br>"), "b")) == "ab"


def test_load_and_dump():
    doc = Pandoc(Para("x"))
    out = io.StringIO()
    assert panir.dump(doc, out) is None
    loaded = panir.load(io.StringIO(out.getvalue()))
    assert isinstance(loaded, Pandoc) and loaded == doc
    assert panir.load(io.BytesIO(out.getvalue().encode())) == doc


@pytest.mark.parametrize("junk", ["Bogus", "inlinemath", 1, None, b"InlineMath", MathType])
def test_an_enum_takes_its_values_and_names_only(junk):
    with pytest.raises(ASTTypeError, match="one of: DisplayMath, InlineMath"):
        Math(junk, "x")
    assert Math("DisplayMath", "x").math_type is MathType.DisplayMath


@pytest.mark.parametrize("traverse", TRAVERSES)
def test_a_documents_and_the_metadatas_replacements(traverse):
    f = Filter(traverse=traverse)
    new = Pandoc(Para("new"))

    @f.on_meta
    def meta(m):
        return {"replaced": True}

    @f.on(Pandoc)
    def document(d: Pandoc):
        assert d.meta == {"replaced": MetaBool(True)} or traverse == "topdown"
        return new

    out = f(Pandoc(Para("old"), meta={"k": "v"}))
    assert out is new
    g = Filter(traverse=traverse)
    g.on_meta(lambda m: {"replaced": True})
    doc = g(Pandoc(meta={"k": "v"}))
    assert doc.meta == {"replaced": MetaBool(True)}
