"""The edges: what a review and fuzzing against pandoc found (fuzz/)."""

import json
import math
import pickle
import subprocess
import sys
import textwrap
import time

import panir as A
import pytest
from panir import (
    ASTDecodeError,
    ASTTypeError,
    Block,
    BulletList,
    ColSpec,
    ColWidth,
    Emph,
    Filter,
    Header,
    Inline,
    MetaList,
    MetaString,
    MetaValue,
    Pandoc,
    Para,
    Space,
    Str,
    Table,
)

VERSION = '"pandoc-api-version":[1,23,1,2]'


def nested(depth, leaf):
    for _ in range(depth):
        leaf = {"t": "Emph", "c": [leaf]}
    return {"pandoc-api-version": [1, 23, 1, 2], "meta": {}, "blocks": [{"t": "Para", "c": [leaf]}]}


def table(width):
    return (
        f'{{{VERSION},"meta":{{}},"blocks":[{{"t":"Table","c":[["",[],[]],[null,[]],'
        f'[[{{"t":"AlignDefault"}},{{"t":"ColWidth","c":{width}}}]],[["",[],[]],[]],[],[["",[],[]],[]]]}}]}}'
    )


# -- JSON --------------------------------------------------------------------


def test_an_error_deep_in_a_document_is_found_at_once():
    # each list used to decode its failing element again to find its index:
    # twice the work per level, hours at this depth
    start = time.perf_counter()
    with pytest.raises(ASTDecodeError) as e:
        Pandoc.from_json(nested(100, {"t": "Str", "c": 42}))
    assert e.value.path == ("blocks", 0, "content", 0) + ("content", 0) * 100 + ("text",)
    bad = Str("x")
    object.__setattr__(bad, "text", 42)
    doc = Pandoc(Para(Str("a"), Emph(Emph(Emph(Str("b"), bad)))))
    with pytest.raises(
        ASTTypeError, match=r"blocks\[0\].content\[1\].content\[0\].content\[0\].content\[1\].text"
    ):
        doc.to_json()
    assert time.perf_counter() - start < 2


def test_an_int_has_64_bits():
    # pandoc's Int is Haskell's: pandoc rejects JSON with a bigger one
    assert Header(2**63 - 1, "x").level == 2**63 - 1
    assert Header(-(2**63), "x").to_json()["c"][0] == -(2**63)
    with pytest.raises(ASTTypeError, match="64 bits"):
        Header(2**63, "x")
    h = Header(1, "x")
    with pytest.raises(ASTTypeError, match="64 bits"):
        h.level = -(2**63) - 1
    with pytest.raises(ASTDecodeError, match="64 bits"):
        A.loads(
            f'{{{VERSION},"meta":{{}},"blocks":[{{"t":"Header","c":[99999999999999999999,["",[],[]],[]]}}]}}'
        )
    object.__setattr__(h, "level", 2**64)
    with pytest.raises(ASTTypeError, match="64 bits"):
        h.to_json()


@pytest.mark.parametrize(
    "written, value", [("null", math.nan), ('"+inf"', math.inf), ('"-inf"', -math.inf)]
)
def test_doubles_that_are_not_finite(written, value):
    # as pandoc writes them (a column `width="0*"` in HTML gives null)
    doc = A.loads(table(written))
    width = doc.blocks[0].col_specs[0].width.width
    assert width == value or (math.isnan(width) and math.isnan(value))
    assert A.dumps(doc) == table(written)
    assert A.dumps(Pandoc(Table(col_specs=[ColSpec(width=ColWidth(value))]))) == table(written)


def test_a_double_beyond_the_range_is_infinite_as_for_pandoc():
    assert A.loads(table("1e999")).blocks[0].col_specs[0].width.width == math.inf
    assert A.loads(table("1" + "0" * 400)).blocks[0].col_specs[0].width.width == math.inf
    with pytest.raises(ASTTypeError, match="too big"):
        ColWidth(10**400)
    for bad in ('"inf"', "true", '"0.5"'):
        with pytest.raises(ASTDecodeError):
            A.loads(table(bad))


def test_a_type_is_not_a_constructor():
    for cls in (Block, Inline, MetaValue, A.Node):
        with pytest.raises(TypeError, match="not a constructor"):
            cls()
    assert Space() == Space()

    class Mine(Inline):  # not one of pandoc's constructors
        __slots__ = ()
        _encoding = "none"
        _tag = ""

    assert Mine() is not None

    class Sub(Block):
        __slots__ = ()

    with pytest.raises(TypeError, match="not a constructor"):
        Sub()


def test_errors_pickle():
    for e in (
        ASTDecodeError(("blocks", 1), "a string", "42"),
        ASTTypeError("Para.content", "x", "y"),
    ):
        back = pickle.loads(pickle.dumps(e))
        assert type(back) is type(e) and str(back) == str(e)
        assert back.where == e.where
    assert pickle.loads(pickle.dumps(ASTDecodeError(("a", 1), "b", "c"))).path == ("a", 1)


# -- nodes -------------------------------------------------------------------


def test_metadata_updated_in_place_is_checked():
    doc = Pandoc()
    doc.meta |= {"draft": True}
    assert doc.meta == {"draft": A.MetaBool(True)}
    with pytest.raises(ASTTypeError):
        doc.meta |= {"x": object()}


def test_one_string_for_a_list_that_takes_no_text():
    assert MetaList("x") == MetaList(MetaString("x"))
    assert MetaList("x", "y").content == [MetaString("x"), MetaString("y")]
    assert Para("a b").content == [Str("a"), Space(), Str("b")]


def test_fields_cannot_be_deleted():
    h = Header(1, "x")
    with pytest.raises(AttributeError, match="can't be deleted"):
        del h.level
    assert h.level == 1


# -- filters -----------------------------------------------------------------


def exclaim(traverse="typewise"):
    f = Filter(traverse=traverse)

    @f.on(Str)
    def _(s):
        s.text += "!"

    return f


def test_a_node_returned_twice_is_two_nodes():
    # as in Lua, where a function's results are values: [el, el] is two nodes
    twice = Filter()

    @twice.on(Para)
    def _(p):
        return [p, p]

    doc = A.run(Pandoc(Para(Str("a"))), [twice, exclaim()])
    assert doc.blocks == [Para(Str("a!")), Para(Str("a!"))]
    assert doc.blocks[0] is not doc.blocks[1]

    f = Filter(traverse="topdown")

    @f.on(Emph)
    def _(e):
        return [e, e]

    @f.on(Str)
    def _(s):
        s.text += "!"

    doc = f(Pandoc(Para(Emph(Str("b")))))
    assert doc.blocks == [Para(Emph(Str("b!")), Emph(Str("b!")))]

    lists = Filter()

    @lists.on_inlines
    def _(xs):
        return [*xs, *xs] if len(xs) == 1 else None

    doc = A.run(Pandoc(Para(Str("c"))), [lists, exclaim()])
    assert doc.blocks == [Para(Str("c!"), Str("c!"))]


def test_metadata_is_walked_in_the_order_of_its_keys():
    # as pandoc's Map; a function that counts its calls depends on it
    seen = []
    f = Filter()

    @f.on(Str)
    def _(s):
        seen.append(s.text)

    doc = Pandoc(meta={"zzz": A.MetaInlines(Str("z")), "mmm": A.MetaInlines(Str("m"))})
    doc.meta["aaa"] = A.MetaInlines(Str("a"))
    f(doc)
    assert seen == ["a", "m", "z"]


def test_a_function_that_removes_a_later_key():
    f = Filter()

    @f.on(MetaValue)
    def _(m, ctx):
        ctx.container.pop("b", None)

    doc = f(Pandoc(meta={"a": True, "b": True, "c": True}))
    assert sorted(doc.meta) == ["a", "c"]


def test_context_in_a_list_of_lists():
    found = {}
    f = Filter()

    @f.on(Para)
    def _(p, ctx):
        found[A.stringify(p)] = (ctx.index, ctx.path, ctx.prev, ctx.next, ctx.container)

    a, b, c = Para("a"), Para("b"), Para("c")
    doc = f(Pandoc(BulletList([a, b], [c])))
    items = doc.blocks[0].content
    assert found["a"] == (0, ("blocks", 0, "content", 0, 0), None, b, items[0])
    assert found["b"] == (1, ("blocks", 0, "content", 0, 1), a, None, items[0])
    assert found["c"] == (0, ("blocks", 0, "content", 1, 0), None, None, items[1])

    keys = []
    g = Filter()

    @g.on(MetaValue)
    def _(m, ctx):
        keys.append((ctx.index, ctx.path))

    g(Pandoc(meta={"title": "t"}))
    assert keys == [("title", ("meta", "title"))]


def test_a_context_kept_after_the_call_stays_right():
    kept = []
    f = Filter()

    @f.on(Str)
    def _(s, ctx):
        kept.append((s.text, ctx))

    f(Pandoc(Para("a b"), Para(Emph("c d"))))
    assert [(t, c.path, c.index) for t, c in kept] == [
        ("a", ("blocks", 0, "content", 0), 0),
        ("b", ("blocks", 0, "content", 2), 2),
        ("c", ("blocks", 1, "content", 0, "content", 0), 0),
        ("d", ("blocks", 1, "content", 0, "content", 2), 2),
    ]


def test_a_parameter_with_a_default_is_not_the_context():
    f = Filter()

    @f.on(Emph)
    def _(e, mark="*"):
        return [Str(mark), *e.content, Str(mark)]

    assert f(Pandoc(Para(Emph("x")))).blocks == [Para(Str("*"), Str("x"), Str("*"))]

    g = Filter()

    @g.on(Str)
    def _(s, *rest):
        (ctx,) = rest
        s.text += str(ctx.index)

    assert g(Pandoc(Para("a b"))).blocks == [Para(Str("a0"), Space(), Str("b2"))]
    with pytest.raises(TypeError, match="takes 3 arguments"):
        g.on(Para)(lambda a, b, c: None)
    with pytest.raises(TypeError, match="takes 0 arguments"):
        g.on(Para)(lambda: None)


def test_a_function_may_change_the_list_its_node_is_in():
    f = Filter()

    @f.on(Str)
    def _(s, ctx):
        if ctx.index and isinstance(ctx.prev, Space):
            del ctx.container[ctx.index - 1]
        return Str(s.text.upper())

    assert f(Pandoc(Para("a b c d"))).blocks == [Para(*map(Str, "ABCD"))]

    g = Filter()

    @g.on(Str)
    def _(s, ctx):
        ctx.container.remove(s)

    with pytest.raises(RuntimeError, match=r"return \[\]"):
        g(Pandoc(Para("a b")))


def test_registering():
    f = Filter()
    f.on(Str)(lambda s: None)
    with pytest.raises(ValueError, match="already has a function"):
        f.on(Para, Str)(lambda x: None)
    assert Para not in f._handlers  # all or none
    with pytest.raises(TypeError, match="takes the AST classes"):
        f.on()
    f.traverse = "sideways"
    with pytest.raises(ValueError, match="traverse"):
        f(Pandoc())


def test_print_in_a_filter_goes_to_stderr(tmp_path):
    # pandoc reads the filter's stdout as the document
    script = tmp_path / "noisy.py"
    script.write_text(
        textwrap.dedent("""
        import sys
        from panir import Filter, Str

        f = Filter()

        @f.on(Str)
        def note(s):
            print("saw", s.text)

        f.main()
        assert not sys.stdin.buffer.closed
        print("after")
        """)
    )
    doc = Pandoc(Para("é b"))
    p = subprocess.run(
        [sys.executable, str(script)], input=A.dumps(doc).encode(), capture_output=True, check=True
    )
    out = p.stdout.decode()
    assert out.endswith("after\n")
    assert A.loads(out.removesuffix("after\n")) == doc
    assert p.stderr.decode().splitlines() == ["saw é", "saw b"]
    assert json.loads(out.removesuffix("after\n"))["blocks"]
