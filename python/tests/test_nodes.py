"""Constructing, checking, printing and copying nodes."""

import copy
import pickle

import panir as A
import pytest
from panir import (
    ASTTypeError,
    Attr,
    Cell,
    Code,
    Div,
    Emph,
    Header,
    Image,
    Inline,
    Link,
    ListNumberStyle,
    Math,
    MathType,
    MetaBool,
    MetaList,
    MetaMap,
    MetaString,
    NodeList,
    OrderedList,
    Pandoc,
    Para,
    Plain,
    SoftBreak,
    Space,
    Str,
    Table,
    Target,
)


def test_constructors_take_content_as_arguments():
    h = Header(2, Str("Intro"), identifier="intro", classes=["x"])
    assert h.level == 2
    assert h.attr == Attr("intro", ["x"], [])
    assert h.content == [Str("Intro")]


def test_defaults():
    assert Div().attr == Attr()
    ol = OrderedList([Para(Str("a"))])
    assert (ol.list_attributes.start, ol.list_attributes.style) == (1, ListNumberStyle.DefaultStyle)
    assert Cell().row_span == 1
    assert Table().bodies == []


def test_flattened_or_whole():
    a = Link(Str("x"), url="https://example.com", title="t", identifier="l")
    b = Link(Str("x"), target=Target("https://example.com", "t"), attr=Attr("l"))
    assert a == b
    with pytest.raises(TypeError, match="url="):
        Link(Str("x"))
    with pytest.raises(TypeError, match="not both"):
        Code("x", attr=Attr(), identifier="y")


def test_enum_values_by_name():
    assert Math("InlineMath", "x").math_type is MathType.InlineMath


def test_tuples_and_dicts_become_products_and_pairs():
    d = Div(attr=("id", ["c"], {"k": "v"}))
    assert d.attr == Attr("id", ["c"], [("k", "v")])


@pytest.mark.parametrize(
    "stmt, message",
    [
        ("p.content.append(Para())", "Para.content[3]: expected Inline, got Para (a Block)"),
        ("p.content.insert(0, Para())", "Para.content[0]: expected Inline, got Para (a Block)"),
        ("p.content.extend([Str('x'), 1])", "Para.content[4]: expected Inline, got int 1"),
        ("p.content += [Para()]", "Para.content[3]: expected Inline"),
        ("p.content[1:2] = [Str('x'), Para()]", "Para.content[2]: expected Inline"),
        ("p.content[0] = 1.5", "Para.content[0]: expected Inline, got float 1.5"),
        ("p.content = Str('x')", "wrap it in a list"),
        ("Header(True, Str('x'))", "Header.level: expected an int, got bool True"),
        ("BulletList([Para()], [Str('y')])", "BulletList.content[*][0]: expected Block"),
        ("Math('Bogus', 'x')", "one of: DisplayMath, InlineMath"),
        ("Pandoc(meta={'x': object()})", "Pandoc.meta['x']: expected MetaValue"),
    ],
)
def test_checks_say_where(stmt, message):
    p = Para(Str("a"), Space(), Str("b"))
    with pytest.raises(ASTTypeError) as e:
        exec(stmt, {**vars(A), "p": p})
    assert message in str(e.value)


def test_unknown_field():
    with pytest.raises(AttributeError, match="its fields: content"):
        Para().level = 1


def test_a_field_has_its_own_list():
    items = [Str("a")]
    p = Para(*items)
    items.append(Str("b"))
    assert len(p.content) == 1
    q = Para()
    q.content = p.content  # copied: a list is in one node
    assert q.content == p.content and q.content is not p.content
    assert isinstance(p.content, NodeList) and isinstance(q.content, NodeList)
    doc, other = Pandoc(meta={"a": True}), Pandoc()
    other.meta = doc.meta
    assert other.meta == doc.meta and other.meta is not doc.meta


def test_metadata_from_python():
    doc = Pandoc(meta={"draft": True, "tags": ["a"], "n": 3, "m": {"k": "v"}})
    assert doc.meta["draft"] == MetaBool(True)
    assert doc.meta["tags"] == MetaList(MetaString("a"))
    assert doc.meta["n"] == MetaString("3")
    assert doc.meta["m"] == MetaMap({"k": MetaString("v")})
    doc.meta["later"] = False
    assert doc.meta["later"] == MetaBool(False)


def test_repr_is_constructor_syntax():
    assert repr(Header(1, Str("x"), identifier="i")) == "Header(1, Str('x'), identifier='i')"
    assert repr(Attr("i")) == "Attr('i')"
    assert repr(Div(Para(), classes=["c"])) == "Div(Para(), classes=['c'])"
    assert repr(Math(MathType.InlineMath, "x")) == "Math(MathType.InlineMath, 'x')"
    assert repr(Image(url="a.png")) == "Image(url='a.png')"


def test_copy_and_pickle():
    doc = Pandoc(Para(Str("x")), meta={"a": True})
    for other in (copy.deepcopy(doc), pickle.loads(pickle.dumps(doc))):
        assert other == doc
        assert isinstance(other.blocks, NodeList)
        other.blocks[0].content.append(Str("y"))
        assert other != doc
        with pytest.raises(ASTTypeError):
            other.blocks.append(Str("x"))
    shallow = copy.copy(doc)
    assert shallow.blocks is doc.blocks


def test_pattern_matching():
    match Header(1, Str("Hi")):
        case Header(1, _, [Str(text)]):
            assert text == "Hi"
        case _:
            pytest.fail("no match")


def test_isinstance_by_type():
    assert isinstance(Str("x"), Inline)
    assert not isinstance(Para(), Inline)


def test_unhashable_and_equality():
    with pytest.raises(TypeError):
        hash(Str("x"))
    assert Str("x") == Str("x")
    assert Str("x") != Code("x")


# Strings, as pandoc's Lua converts them (pandoc-lua-marshal's "fuzzy" rules)


def test_a_string_for_a_list_of_inlines_is_its_words():
    assert Para("hello  world\nnext") == Para(
        Str("hello"), Space(), Str("world"), SoftBreak(), Str("next")
    )
    p = Para()
    p.content = "a b"
    assert p == Para(Str("a"), Space(), Str("b"))


def test_a_string_for_one_inline_is_a_str():
    # as in Lua: pandoc.Para({"a b", pandoc.Str "c"}) is Para [Str "a b", Str "c"]
    assert Para("a b", Emph("c")) == Para(Str("a b"), Emph(Str("c")))
    p = Para("x")
    p.content.append("y z")
    assert p.content[-1] == Str("y z")


def test_a_string_for_blocks_is_plain_text():
    assert Div("x y") == Div(Plain(Str("x"), Space(), Str("y")))
    assert A.blocks("m n") == [Plain(Str("m"), Space(), Str("n"))]


def test_inlines_splits_as_pandoc_types_text():
    assert A.inlines("  a  b\n c\td ") == [
        Space(),
        Str("a"),
        Space(),
        Str("b"),
        SoftBreak(),
        Str("c"),
        Space(),
        Str("d"),
        Space(),
    ]
    assert A.inlines("") == []
