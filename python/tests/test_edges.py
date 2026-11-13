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
    RawBlock,
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


def test_a_list_functions_nodes_may_hold_its_list():
    # (the list's contents were replaced in place: the list could end up in itself)
    wrap = Filter()

    @wrap.on_inlines
    def _(xs):
        e = Emph()
        object.__setattr__(e, "content", xs)  # the list itself, as a careless function might
        return [e]

    doc = A.run(Pandoc(Para("a b")), [wrap, exclaim()])
    assert doc.blocks == [Para(Emph("a! b!"))]
    assert A.dumps(doc).count("Emph") == 1


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


# A long list whose nodes are deleted, kept, replaced by one and by several
# (once quadratic: each replacement by none or several moved the rest).
LONG = 30_000


def splice_rule(s):
    """What each Str becomes, by its number: deleted, kept, replaced by one,
    by several (an Emph among them), returned twice. Top-down, the Emph's
    Str is walked too ("x" -> "X")."""
    if s.text.startswith("x"):
        return Str(s.text.upper())
    if not s.text.isdigit():
        return None
    k = int(s.text)
    return [
        [],
        None,
        Str(f"{k}r"),
        [Str(f"{k}a"), Space(), Emph(Str(f"x{k}"))],
        [s, s],
    ][k % 5]


def spliced(k, top_down):
    """What the walk should make of the k-th Str, written plainly."""
    x = "X" if top_down else "x"
    return [
        [],
        [Str(f"{k}")],
        [Str(f"{k}r")],
        [Str(f"{k}a"), Space(), Emph(Str(f"{x}{k}"))],
        [Str(f"{k}"), Str(f"{k}")],
    ][k % 5]


@pytest.mark.parametrize("traverse", ["bottomup", "topdown", "typewise"])
def test_a_long_list_spliced(traverse):
    top_down = traverse == "topdown"
    original = [Str(str(k)) for k in range(LONG)]
    para = Para(*original)
    content = para.content
    f = Filter(traverse=traverse)
    f.on(Str)(splice_rule)
    doc = f(Pandoc(para))
    expected = [x for k in range(LONG) for x in spliced(k, top_down)]
    assert doc.blocks[0].content == expected
    assert doc.blocks[0].content is content  # the same list
    out = doc.blocks[0].content
    assert out[0] is original[1]  # kept: the node itself
    assert out[5] == out[6] == original[4] and out[5] is not out[6]  # twice: two nodes


@pytest.mark.parametrize("traverse", ["bottomup", "topdown"])
def test_a_long_list_spliced_with_contexts(traverse):
    # the context is right after earlier nodes were deleted and multiplied:
    # looked at first halfway (until then the list is spliced the fast way)
    top_down = traverse == "topdown"
    original = [Str(str(k)) for k in range(LONG)]
    looked = [0, LONG // 2, LONG // 2 + 1, LONG // 2 + 3, LONG // 2 + 4, LONG - 1]
    seen = {}
    f = Filter(traverse=traverse)

    @f.on(Str)
    def _(s, ctx):
        if s.text.isdigit() and int(s.text) in looked:
            seen[int(s.text)] = (ctx.index, ctx.prev, ctx.next, ctx.container)
        return splice_rule(s)

    doc = f(Pandoc(Para(*original)))
    out = doc.blocks[0].content
    assert out == [x for k in range(LONG) for x in spliced(k, top_down)]
    for k in looked:
        index, prev, next, container = seen[k]
        assert index == sum(len(spliced(j, top_down)) for j in range(k))
        assert container is out
        assert prev is (out[index - 1] if index else None)
        assert next is (original[k + 1] if k + 1 < LONG else None)


@pytest.mark.parametrize("traverse", ["bottomup", "topdown"])
def test_a_long_list_seen_from_below_and_after_an_error(traverse):
    # a function below the list sees it whole, through its context
    n = 2_000
    lengths = []
    f = Filter(traverse=traverse)

    @f.on(Str)
    def _(s, ctx):
        if s.text.startswith("in"):
            lengths.append((s.text, len(ctx.ancestors[-2].content), len(ctx.doc.blocks[0].content)))
            return None
        return [] if int(s.text) % 2 == 0 else [s, Str(s.text)]

    items = [Emph(Str(f"in{k}")) if k % 100 == 50 else Str(str(k)) for k in range(n)]
    doc = f(Pandoc(Para(*items)))

    # before the k-th: half of those deleted, half doubled (Emph: one)
    def made(k):
        return sum(1 if j % 100 == 50 else 0 if j % 2 == 0 else 2 for j in range(k))

    assert lengths == [(f"in{k}", made(k) + n - k, made(k) + n - k) for k in range(50, n, 100)]
    assert len(doc.blocks[0].content) == made(n)

    # an error halfway: the list as far as it went, then the rest as it was
    g = Filter(traverse=traverse)

    @g.on(Str)
    def _(s):
        if s.text == "1001":
            raise ValueError("stop")
        return [] if int(s.text) % 2 == 0 else [s, Str(s.text)]

    original = [Str(str(k)) for k in range(n)]
    doc = Pandoc(Para(*original))
    with pytest.raises(ValueError, match="stop"):
        g(doc)
    expected = [Str(str(k)) for k in range(1001) if k % 2 for _ in range(2)] + original[1001:]
    assert doc.blocks[0].content == expected


def same(xs, ys):
    """Whether two lists hold the same nodes (not equal ones), in order."""
    return len(xs) == len(ys) and all(x is y for x, y in zip(xs, ys))


def from_node(xs, node):
    """The nodes of ``xs`` from ``node`` (itself, not an equal one) on."""
    k = next((k for k, x in enumerate(xs) if x is node), None)
    assert k is not None, f"{node!r} is not in the list"
    return xs[k:]


@pytest.mark.parametrize("traverse", ["bottomup", "topdown", "typewise"])
def test_a_list_being_walked_seen_from_a_closure(traverse):
    # A function deletes or doubles blocks; a later one looks at the document
    # it closed over (not ctx): its block and every one after it are there,
    # in order. (Earlier deletions and doublings may show only at the end.)
    original = [
        RawBlock("html", "del"),
        Para("p1"),
        RawBlock("html", "dbl"),
        Para("p3"),
        RawBlock("html", "del"),
        Para("p5"),
        Header(1, "h"),
    ]
    doc = Pandoc(*original)
    seen = []
    f = Filter(traverse=traverse)

    @f.on(RawBlock)
    def _(b):
        return [] if b.text == "del" else [b, RawBlock("html", "dbl2")]

    @f.on(Para)
    def _(p):
        seen.append(A.stringify(p))
        assert same(from_node(doc.blocks, p), from_node(original, p))

    f(doc)
    assert seen == ["p1", "p3", "p5"]
    dbl = [RawBlock("html", "dbl"), RawBlock("html", "dbl2")]
    assert doc.blocks == [Para("p1"), *dbl, Para("p3"), Para("p5"), Header(1, "h")]

    # one level down: a Para's inlines, seen from a function on them
    para = Para("a b c d e")
    original = list(para.content)
    doc = Pandoc(para)
    seen = []
    g = Filter(traverse=traverse)

    @g.on(Space)
    def _(s):
        return []

    @g.on(Str)
    def _(s):
        seen.append(s.text)
        assert same(from_node(para.content, s), from_node(original, s))
        assert doc.blocks[0].content is para.content
        return [s, Str(s.text.upper())] if s.text == "c" else None

    g(doc)
    assert seen == ["a", "b", "c", "d", "e"]
    assert doc.blocks == [Para(*map(Str, ["a", "b", "c", "C", "d", "e"]))]


@pytest.mark.parametrize("traverse", ["bottomup", "topdown"])
def test_neighbours_while_a_long_list_is_spliced(traverse):
    # ctx.prev and ctx.next, of the node's context and of contexts kept from
    # earlier nodes, while nodes are deleted and doubled: as the list is now
    # (the nodes made so far, then the rest), which ctx.container then shows
    n = 3_000
    original = [Str(str(k)) for k in range(n)]
    made = []  # what the walk made of the nodes so far
    kept = []
    f = Filter(traverse=traverse)

    def check(ctxs, now):
        for c in ctxs:
            i = c.index
            assert c.prev is (now[i - 1] if i else None)
            assert c.next is (now[i + 1] if i + 1 < len(now) else None)

    @f.on(Str)
    def _(s, ctx):
        k = int(s.text)
        now = made + original[k:]
        assert ctx.index == len(made)
        check([ctx], now)
        if k % 100 == 0:
            check(kept, now)
        if k == n // 2:
            assert same(ctx.container, now)  # (made what it is now; in place from then on)
        kept.append(ctx)
        result = [] if k % 3 == 0 else [s, Str(s.text)] if k % 3 == 1 else None
        made.extend([s] if result is None else result)
        return result

    doc = f(Pandoc(Para(*original)))
    assert same(doc.blocks[0].content, made)
    check(kept, made)


@pytest.mark.parametrize("traverse", ["bottomup", "topdown", "typewise"])
def test_a_list_being_walked_changed_from_a_closure(traverse):
    # after an earlier block was deleted and one doubled, a function changes
    # the document it closed over (not ctx.container): what it changed, it
    # changed in the list as it is
    def run(change):
        doc = Pandoc(
            RawBlock("html", "del"),
            Para("a"),
            RawBlock("html", "dbl"),
            Header(1, "One"),
            Para("x"),
            Header(1, "Two"),
        )
        calls = []
        f = Filter(traverse=traverse)

        @f.on(RawBlock)
        def _(b):
            return [] if b.text == "del" else [b, RawBlock("html", "dbl2")]

        @f.on(Header)
        def _(h):
            calls.append(A.stringify(h))
            change(doc.blocks, A.stringify(h))

        f(doc)
        assert calls == ["One", "Two"]
        return [A.stringify(b) or b.text for b in doc.blocks]

    def find(blocks, text):
        return next(k for k, b in enumerate(blocks) if A.stringify(b) == text)

    def lengths(blocks, at):
        if at == "One":  # before it, after it, at the end
            blocks.insert(0, Para("TOC"))
            del blocks[find(blocks, "a")]
            del blocks[find(blocks, "x")]
            blocks.append(Para("end"))

    assert run(lengths) == ["TOC", "dbl", "dbl2", "One", "Two", "end"]

    def assign(blocks, at):
        if at == "One":  # the same length: seen at the end of the walk
            blocks[find(blocks, "a")] = Para("A")

    assert run(assign) == ["A", "dbl", "dbl2", "One", "x", "Two"]


def test_a_context_kept_from_an_earlier_pass_shows_the_list_as_it_is():
    kept = []
    seen = []
    f = Filter()  # typewise: the inlines, then the blocks

    @f.on(Str)
    def _(s, ctx):
        kept.append(ctx)

    @f.on(RawBlock)
    def _(b):
        return []

    @f.on(Para)
    def _(p):
        seen.append([type(b).__name__ for b in kept[0].doc.blocks])

    doc = Pandoc(RawBlock("html", "x"), Para("a"), RawBlock("html", "y"), Para("b"))
    f(doc)
    assert seen == [["Para", "RawBlock", "Para"], ["Para", "Para"]]


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
            print("saw", len(s.text))

        f.main()
        assert not sys.stdin.buffer.closed
        print("after")
        """)
    )
    doc = Pandoc(Para("é b"))
    p = subprocess.run(
        [sys.executable, str(script)], input=A.dumps(doc).encode(), capture_output=True, check=True
    )
    out = p.stdout.decode().replace("\r\n", "\n")  # (print's line end, on Windows)
    assert out.endswith("after\n")
    assert A.loads(out.removesuffix("after\n")) == doc
    assert p.stderr.decode().splitlines() == ["saw 1", "saw 1"]
    assert json.loads(out.removesuffix("after\n"))["blocks"]
