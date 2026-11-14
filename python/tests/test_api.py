"""The API as pandoc's Lua has it: conversions, attributes by key, what a
filter function may return, and filters on fragments (``el:walk``).

Where Lua has an answer, the cases are checked against pandoc itself when
it is on the PATH (``test_*_as_lua``): each is a Lua filter and its Python
version, which must make the same document.
"""

from __future__ import annotations

import shutil
import subprocess

import panir
import pytest
from panir import (
    ASTTypeError,
    Attr,
    Attributes,
    Block,
    BulletList,
    Caption,
    Cell,
    Citation,
    CitationMode,
    Cite,
    ColWidth,
    Div,
    Emph,
    Filter,
    Header,
    Inline,
    Link,
    MetaBool,
    MetaList,
    MetaString,
    NodeList,
    Pandoc,
    Para,
    Plain,
    Row,
    Space,
    Span,
    Str,
    Strong,
    Table,
    TableBody,
    TableFoot,
    TableHead,
    stringify,
    walk,
)

PANDOC = shutil.which("pandoc")
MARKDOWN = "a *b* c\n\npara two\n"


def lua(script: str) -> Pandoc:
    """What pandoc's Lua filter makes of MARKDOWN."""
    assert PANDOC is not None
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "f.lua").write_text(script)
        out = subprocess.run(
            [PANDOC, "-f", "markdown", "-t", "json", "-L", str(Path(d) / "f.lua")],
            input=MARKDOWN,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    return panir.loads(out)


def source() -> Pandoc:
    assert PANDOC is not None
    out = subprocess.run(
        [PANDOC, "-f", "markdown", "-t", "json"],
        input=MARKDOWN,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return panir.loads(out)


def doc() -> Pandoc:
    """MARKDOWN as pandoc reads it (so that it runs without pandoc too)."""
    return Pandoc(
        Para(Str("a"), Space(), Emph(Str("b")), Space(), Str("c")),
        Para(Str("para"), Space(), Str("two")),
    )


# -- constructors and fields convert as pandoc's Lua does ---------------------


def test_one_block_from_an_inline_or_a_list_of_inlines():
    assert Div(Str("x")) == Div(Plain(Str("x")))
    assert Div([Str("a"), "b c"]) == Div(Plain(Str("a"), Str("b c")))
    assert Div("a b", "c") == Div(Plain(Str("a"), Space(), Str("b")), Plain(Str("c")))
    assert BulletList([Para("a")], ["x"]) == BulletList([Para("a")], [Plain(Str("x"))])
    with pytest.raises(ASTTypeError, match="a list; this takes one"):
        Div([Para()])  # (a list of blocks, where one goes)


def test_one_node_where_a_list_of_inlines_or_blocks_goes():
    h = Header(1)
    h.content = Str("y")
    assert h.content == [Str("y")]
    d = Div()
    d.content = Para("y")
    assert d.content == [Para("y")]
    d.content = Str("y")
    assert d.content == [Plain(Str("y"))]
    with pytest.raises(ASTTypeError, match="wrap it in a list"):
        Row().cells = Cell()  # (Lua has no conversion for other lists)


def test_a_lone_string_is_split_and_a_string_among_others_is_one_str():
    assert Para("a b") == Para(Str("a"), Space(), Str("b"))
    assert Para("a b", "c") == Para(Str("a b"), Str("c"))
    p = Para()
    p.content = ["a b"]
    assert p.content == [Str("a b")]


def test_a_set_is_refused_where_order_matters():
    for bad in ({"a", "b"}, frozenset({"a"})):
        with pytest.raises(ASTTypeError, match="a set has no order"):
            Span(classes=bad)
    assert Span(classes={"a": 1, "b": 2}.keys()).attr.classes == ["a", "b"]


# -- attributes by key -----------------------------------------------------------


def test_attributes_by_key_as_lua():
    a = Attr("id", ["x"], [("k", "v"), ("k", "w"), ("z", "1")])
    attrs = a.attributes
    assert isinstance(attrs, Attributes)
    assert (attrs["k"], attrs["z"]) == ("v", "1")
    assert (attrs.get("missing"), attrs.get("m", "d")) == (None, "d")
    assert "k" in attrs and "missing" not in attrs and ("z", "1") in attrs
    with pytest.raises(KeyError):
        attrs["missing"]
    attrs["k"] = "new"  # the first pair's value, in its place
    assert attrs == [("k", "new"), ("k", "w"), ("z", "1")]
    del attrs["k"]  # the first pair only, as Lua
    assert attrs == [("k", "w"), ("z", "1")]
    attrs["q"] = "2"  # a new key: at the end
    assert attrs == [("k", "w"), ("z", "1"), ("q", "2")]
    with pytest.raises(KeyError):
        del attrs["missing"]
    with pytest.raises(ASTTypeError):
        attrs["k"] = 1  # type: ignore[assignment]
    assert attrs[0] == ("k", "w") and attrs[1:] == [("z", "1"), ("q", "2")]
    del attrs[0]
    assert attrs == [("z", "1"), ("q", "2")]


def test_attributes_are_attributes_however_made():
    j = (
        '{"pandoc-api-version":[1,23,1,2],"meta":{},'
        '"blocks":[{"t":"Div","c":[["",[],[["k","v"]]],[]]}]}'
    )
    import copy
    import pickle

    d = panir.loads(j).blocks[0]
    for x in (d, copy.deepcopy(d), pickle.loads(pickle.dumps(d)), Div(attributes={"k": "v"})):
        assert isinstance(x.attr.attributes, Attributes)
        assert x.attributes["k"] == "v"


def test_a_flattened_products_fields_through_the_node():
    d = Div(identifier="i", classes=["c"])
    assert (d.identifier, d.classes, d.attributes) == ("i", ["c"], [])
    d.identifier = "j"
    d.attributes["k"] = "v"
    assert d.attr == Attr("j", ["c"], [("k", "v")])
    link = Link("x", url="u")
    link.title = "t"
    assert (link.url, link.target.title) == ("u", "t")
    with pytest.raises(AttributeError, match="its fields"):
        d.nothing = 1
    with pytest.raises(AttributeError, match="no attribute 'nothing'"):
        d.nothing  # noqa: B018
    with pytest.raises(ASTTypeError):
        d.classes = {"a"}


# Constructors whose positional order is pandoc's Lua's, not pandoc-types'
LUA_ORDER = """
function Pandoc(d)
  local c = pandoc.Citation("id", "AuthorInText", {pandoc.Str("pre")}, {pandoc.Str("suf")}, 3, 7)
  local body = pandoc.TableBody(
    {pandoc.Row({pandoc.Cell("b")})}, {pandoc.Row({pandoc.Cell("h")})}, 1, pandoc.Attr("tb"))
  return pandoc.Pandoc({
    pandoc.Para({pandoc.Cite({pandoc.Str("txt")}, {c})}),
    pandoc.Table(pandoc.Caption("cap"), {{"AlignLeft", 0.5}}, pandoc.TableHead(), {body},
      pandoc.TableFoot(), pandoc.Attr("t")),
  })
end
"""


def lua_order() -> Pandoc:
    c = Citation("id", "AuthorInText", "pre", "suf", 3, 7)
    body = TableBody([Row(Cell("b"))], [Row(Cell("h"))], 1, identifier="tb")
    table = Table(
        Caption("cap"),
        [("AlignLeft", ColWidth(0.5))],
        TableHead(),
        [body],
        TableFoot(),
        identifier="t",
    )
    return Pandoc(Para(Cite("txt", [c])), table)


def test_constructors_in_luas_order():
    doc = lua_order()
    cite = doc.blocks[0].content[0]
    assert cite.citations[0].mode == CitationMode.AuthorInText
    assert (cite.citations[0].note_num, cite.citations[0].hash) == (3, 7)
    body = doc.blocks[1].bodies[0]
    assert (body.row_head_columns, len(body.head), len(body.body)) == (1, 1, 1)
    assert eval(repr(doc), vars(panir)) == doc
    match cite:
        case Cite(content, [Citation(id, mode)]):
            assert (stringify(content), id, mode) == ("txt", "id", CitationMode.AuthorInText)
        case _:
            raise AssertionError(cite)


@pytest.mark.skipif(PANDOC is None, reason="needs pandoc")
def test_constructors_in_luas_order_as_lua():
    assert lua_order() == lua(LUA_ORDER)


# -- what a filter function may return -------------------------------------------

# (name, Lua filter, Python filter's registration): each must make of
# MARKDOWN the same document
RETURNS: list[tuple[str, str, object]] = []


def case(name: str, script: str):
    def register(fn):
        RETURNS.append((name, script, fn))
        return fn

    return register


@case("an inline: a string is its words", 'function Emph(e) return "x y" end')
def _(f):
    f.on(Emph)(lambda e: "x y")


@case("an inline: a list's strings are a Str each", 'function Emph(e) return {"x y", "z"} end')
def _(f):
    f.on(Emph)(lambda e: ["x y", "z"])


@case("an inline: an empty list deletes it", "function Emph(e) return {} end")
def _(f):
    f.on(Emph)(lambda e: [])


@case("inlines: one inline", 'function Inlines(xs) return pandoc.Str("one") end')
def _(f):
    f.on_inlines(lambda xs: Str("one"))


@case("inlines: a string", 'function Inlines(xs) return "s t" end')
def _(f):
    f.on_inlines(lambda xs: "s t")


@case("inlines: a list of strings", 'function Inlines(xs) return {"s t", "u"} end')
def _(f):
    f.on_inlines(lambda xs: ["s t", "u"])


@case("a block: a string is Plain text", 'function Para(p) return "w v" end')
def _(f):
    f.on(Para)(lambda p: "w v")


@case("a block: an inline is a Plain", 'function Para(p) return pandoc.Str("w") end')
def _(f):
    f.on(Para)(lambda p: Str("w"))


@case(
    "a block: a list's inlines are a Plain each",
    'function Para(p) return {pandoc.Str("w"), pandoc.Space(), "v x"} end',
)
def _(f):
    f.on(Para)(lambda p: [Str("w"), Space(), "v x"])


@case(
    "a block: a list of inlines in the list is a Plain",
    'function Para(p) return {{"a b", "c"}, pandoc.Para("d")} end',
)
def _(f):
    f.on(Para)(lambda p: [["a b", "c"], Para("d")])


@case("blocks: one block", 'function Blocks(bs) return pandoc.Para("only") end')
def _(f):
    f.on_blocks(lambda bs: Para("only"))


@case("blocks: a string", 'function Blocks(bs) return "s t" end')
def _(f):
    f.on_blocks(lambda bs: "s t")


@case("blocks: an inline", 'function Blocks(bs) return pandoc.Str("i") end')
def _(f):
    f.on_blocks(lambda bs: Str("i"))


@case(
    "blocks: inlines are a Plain each",
    'function Blocks(bs) return {pandoc.Str("i"), {pandoc.Str("j"), pandoc.Str("k")}} end',
)
def _(f):
    f.on_blocks(lambda bs: [Str("i"), [Str("j"), Str("k")]])


@case(
    "the metadata: plain values",
    'function Meta(m) return {title = "T", n = 3, b = true, l = {"a", "b"}} end',
)
def _(f):
    f.on_meta(lambda m: {"title": "T", "n": 3, "b": True, "l": ["a", "b"]})


def python_filter(register) -> Filter:
    f = Filter()
    register(f)
    return f


@pytest.mark.parametrize("name, script, register", RETURNS, ids=[r[0] for r in RETURNS])
@pytest.mark.skipif(PANDOC is None, reason="needs pandoc")
def test_returns_as_lua(name, script, register):
    assert python_filter(register)(source()) == lua(script)


def test_returns_as_lua_without_pandoc():
    # (the same, as pandoc 3.14 made them)
    cases = {name: register for name, _, register in RETURNS}
    f = python_filter(cases["a block: a list's inlines are a Plain each"])
    out = f(doc())
    # (a string where a block goes: Plain text, split)
    assert out.blocks == [Plain(Str("w")), Plain(Space()), Plain(Str("v"), Space(), Str("x"))] * 2
    f = python_filter(cases["an inline: a string is its words"])
    assert f(doc()).blocks[0] == Para(
        Str("a"), Space(), Str("x"), Space(), Str("y"), Space(), Str("c")
    )
    f = python_filter(cases["the metadata: plain values"])
    meta = f(doc()).meta
    assert meta["n"] == MetaString("3") and meta["b"] == MetaBool(True)
    assert meta["l"] == MetaList(MetaString("a"), MetaString("b"))


@pytest.mark.parametrize(
    "register, message, note",
    [
        (
            lambda f: f.on(Emph)(lambda e: 42),
            "Para.content[2]: expected a node, a list of nodes or None, got int 42",
            "<lambda>, for the Emph at blocks[0].content[2]",
        ),
        (
            lambda f: f.on(Emph)(lambda e: True),
            "got bool True",
            "for the Emph at blocks[0].content[2]",
        ),
        (
            lambda f: f.on(Emph)(lambda e: Para("x")),
            "Para.content[2]: expected Inline, got Para (a Block)",
            "for the Emph at blocks[0].content[2]",
        ),
        (
            lambda f: f.on(Emph)(lambda e: [Str("a"), 7]),
            "Para.content[3]: expected Inline, got int 7",
            "for the Emph at blocks[0].content[2]",
        ),
        (
            lambda f: f.on(Para)(lambda e: [Para("a"), 7]),
            "Pandoc.blocks[1]: expected Block, got int 7",
            "for the Para at blocks[0]",
        ),
        (
            lambda f: f.on_inlines(lambda xs: Para("b")),
            "Emph.content: expected a list of Inline, got Para (a Block)",
            "for the list of Inlines at blocks[0].content[2].content",
        ),
        (
            lambda f: f.on_blocks(lambda xs: 7),
            "Pandoc.blocks: expected a list of Blocks, got int 7",
            "for the list of Blocks at blocks",
        ),
        (
            lambda f: f.on_meta(lambda m: "x"),
            "Pandoc.meta: expected a dict of str to MetaValue, got str 'x'",
            "for the metadata at meta",
        ),
        (
            lambda f: f.on(Pandoc)(lambda d: [d]),
            "Pandoc: expected a Pandoc or None, got [Pandoc(",
            "for the Pandoc at the document",
        ),
    ],
)
def test_a_wrong_return_names_the_function_and_where(register, message, note):
    # (pandoc's Lua refuses these too)
    f = Filter()
    register(f)
    with pytest.raises(ASTTypeError) as e:
        f(doc())
    assert message in str(e.value)
    assert any(note in n for n in e.value.__notes__), e.value.__notes__


def test_a_wrong_return_in_a_position_of_one_node():
    f = Filter()

    @f.on(MetaBool)
    def wrong(m):
        return [m, m]

    d = Pandoc(meta={"x": True})
    with pytest.raises(ASTTypeError, match=r"Pandoc.meta\['x'\]: expected one node") as e:
        f(d)
    assert e.value.__notes__ == [
        f"returned by filter function {wrong.__qualname__}, for the MetaBool at meta.x"
    ]


# -- filters on fragments, as Lua's el:walk{} -------------------------------------


def logging_filter(log: list[str], traverse="typewise") -> Filter:
    f = Filter(traverse=traverse)

    @f.on(Str, Emph, Para, Div)
    def node(x):
        log.append(type(x).__name__)

    @f.on_inlines
    def inlines(xs):
        log.append(f"Inlines{len(xs)}")

    @f.on_blocks
    def blocks(xs):
        log.append(f"Blocks{len(xs)}")

    @f.on_meta
    def meta(m):
        log.append("Meta")

    @f.on(Pandoc)
    def document(d):
        log.append("Pandoc")

    return f


LUA_WALK = """
local log = {}
local function f(tag) return function(x)
  local t = pandoc.utils.type(x)
  local n = (t == "Inlines" or t == "Blocks") and tostring(#x) or ""
  table.insert(log, tag .. n) end end
local filt = {Str = f("Str"), Emph = f("Emph"), Para = f("Para"), Div = f("Div"),
  Inlines = f("Inlines"), Blocks = f("Blocks"), Meta = f("Meta"), Pandoc = f("Pandoc"),
  traverse = TRAVERSE}
local p = pandoc.Para{pandoc.Str("a"), pandoc.Emph{pandoc.Str("b")}}
for _, x in ipairs{p, pandoc.Div{p}, pandoc.Inlines{pandoc.Str("a"), pandoc.Emph{"b"}},
    pandoc.Blocks{p}, pandoc.Pandoc({p}, {k = "v"}), pandoc.Str("s")} do
  log = {}; x:walk(filt); print(table.concat(log, " "))
end
"""


def python_walks(traverse: str) -> list[str]:
    out = []

    def p() -> Para:
        return Para(Str("a"), Emph(Str("b")))

    fragments = (p(), Div(p()), [Str("a"), Emph(Str("b"))], [p()], Pandoc(p(), meta={"k": "v"}))
    for x in (*fragments, Str("s")):
        log: list[str] = []
        logging_filter(log, traverse)(x)
        out.append(" ".join(log))
    return out


@pytest.mark.parametrize("traverse", ["typewise", "topdown"])
@pytest.mark.skipif(PANDOC is None, reason="needs pandoc")
def test_which_functions_a_fragment_gets_as_lua(traverse, tmp_path):
    script = LUA_WALK.replace("TRAVERSE", repr(traverse).replace("'", '"'))
    (tmp_path / "walk.lua").write_text(script)
    lines = subprocess.run(
        [PANDOC, "lua", str(tmp_path / "walk.lua")], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert python_walks(traverse) == lines


def test_which_functions_a_fragment_gets():
    # (as pandoc 3.14's Lua: a node's own function isn't called; a list's is)
    assert python_walks("typewise") == [
        "Str Str Emph Inlines1 Inlines2",
        "Str Str Emph Inlines1 Inlines2 Para Blocks1",
        "Str Str Emph Inlines1 Inlines2",
        "Str Str Emph Inlines1 Inlines2 Para Blocks1",
        "Str Str Emph Inlines1 Inlines2 Para Blocks1 Meta Pandoc",
        "",
    ]


def test_a_fragment_is_changed_in_place_and_returned():
    f = Filter()

    @f.on(Str)
    def upper(s):
        return s.text.upper()

    @f.on(Emph)
    def strong(e):
        return Strong(*e.content)

    p = Para("a", Emph("b"))
    assert p.walk(f) is p
    assert p == Para(Str("A"), Strong(Str("B")))
    xs = [Str("x"), Emph(Str("y"))]
    assert f(xs) is xs
    assert xs == [Str("X"), Strong(Str("Y"))] and type(xs) is list
    content = Para("q").content
    assert content.walk(f) is content and content == [Str("Q")]
    assert f([]) == []
    e = Emph(Str("e"))
    assert f(e) is e and e == Emph(Str("E"))  # (its own function isn't called)


def test_a_list_function_on_a_fragment_replaces_its_elements_in_place():
    f = Filter()

    @f.on_inlines
    def two(xs):
        return ["one", "two"]

    xs = [Str("a")]
    assert f(xs) is xs and xs == [Str("one"), Str("two")]
    p = Para("a")
    content = p.content
    p.content.walk(f)
    assert p.content is content and p.content == [Str("one"), Str("two")]


def test_a_fragments_context():
    f = Filter()
    seen = []

    @f.on(Str)
    def where(s, ctx):
        seen.append((ctx.where, ctx.doc, type(ctx.parent).__name__, ctx.index))

    Para(Emph("a")).walk(f)
    f([Str("b")])
    Para("c").content.walk(f)
    assert seen == [
        ("Para.content[0].content[0]", None, "Emph", 0),
        ("inlines[0]", None, "NoneType", 0),
        ("Para.content[0]", None, "NoneType", 0),
    ]


def test_a_fragment_that_isnt_one():
    f = Filter()
    with pytest.raises(TypeError, match="of one type"):
        f([Str("a"), Para()])
    with pytest.raises(TypeError, match="applies to a document, a node"):
        f("text")  # type: ignore[call-overload]


def test_walk_on_a_list():
    seen = []

    def action(x, ctx):
        seen.append(type(x).__name__)
        if isinstance(x, Str):
            return [x, Space()]

    xs = [Str("a"), Emph(Str("b"))]
    assert walk(xs, action) is xs
    assert xs == [Str("a"), Space(), Emph(Str("b"), Space())]
    assert seen == ["Str", "Str", "Emph"]
    nodes = Para("p").content
    assert isinstance(nodes, NodeList) and walk(nodes, action) is nodes
    assert nodes == [Str("p"), Space()]
    assert walk([], action) == []
    with pytest.raises(TypeError):
        walk("x", action)  # type: ignore[call-overload]


def test_types_of_fragments():
    assert isinstance(Para(), Block) and isinstance(Str(""), Inline)
