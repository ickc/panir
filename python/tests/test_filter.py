"""Filters and walking."""

import json
import shutil
import subprocess
import sys
import textwrap

import panir as A
import pytest
from panir import (
    ASTTypeError,
    BulletList,
    Emph,
    Filter,
    Header,
    Note,
    Pandoc,
    Para,
    Plain,
    Space,
    Str,
)


def doc():
    return Pandoc(
        Header(1, Str("Title")),
        Para(Str("a"), Space(), Emph(Str("b")), Note(Para(Str("n")))),
        BulletList([Plain(Str("x"))], [Plain(Str("y"))]),
    )


def test_modify_in_place():
    f = Filter()

    @f.on(Header)
    def demote(h):
        h.level += 1

    d = f(doc())
    assert d.blocks[0].level == 2


def test_replace_splice_delete():
    f = Filter()

    @f.on(Str)
    def act(s):
        if s.text == "a":
            return Str("A")
        if s.text == "b":
            return [Str("b1"), Space(), Str("b2")]
        if s.text == "x":
            return []

    d = f(doc())
    assert A.stringify(d.blocks[1]) == "A b1 b2"
    assert d.blocks[2].content[0] == [Plain()]


def test_most_specific_function_wins():
    f = Filter()
    seen = []

    @f.on(A.Inline)
    def any_inline(x):
        seen.append(type(x).__name__)

    @f.on(Str)
    def strs(x):
        seen.append("str")

    f(Pandoc(Para(Str("a"), Space())))
    assert seen == ["str", "Space"]


@pytest.mark.parametrize(
    ("traverse", "expected"),
    [
        ("typewise", ["Str", "Emph", "Para"]),
        ("bottomup", ["Str", "Emph", "Para"]),
        ("topdown", ["Para", "Emph", "Str"]),
    ],
)
def test_orders(traverse, expected):
    order = []
    f = Filter(traverse=traverse)

    @f.on(Para, Emph, Str)
    def visit(x):
        order.append(type(x).__name__)

    f(Pandoc(Para(Emph(Str("x")))))
    assert order == expected


def test_top_down_walks_the_replacement():
    f = Filter(traverse="topdown")

    @f.on(Emph)
    def unwrap(e):
        return e.content

    @f.on(Str)
    def upper(s):
        s.text = s.text.upper()

    # replacements aren't visited again, their children are
    d = f(Pandoc(Para(Emph(Str("x"), Emph(Str("y"))))))
    assert d.blocks[0].content == [Str("x"), Emph(Str("Y"))]


def test_context():
    f = Filter()
    found = {}

    @f.on(Str)
    def where(s, ctx):
        if s.text == "b":
            found.update(
                path=ctx.path,
                parent=type(ctx.parent).__name__,
                field=ctx.field,
                index=ctx.index,
                prev=ctx.prev,
                next=ctx.next,
                format=ctx.format,
                ancestors=[type(a).__name__ for a in ctx.ancestors],
            )

    f(doc(), "html")
    assert found == {
        "path": ("blocks", 1, "content", 2, "content", 0),
        "parent": "Emph",
        "field": "content",
        "index": 0,
        "prev": None,
        "next": None,
        "format": "html",
        "ancestors": ["Pandoc", "Para", "Emph"],
    }


def test_context_in_nested_lists():
    f = Filter()
    paths = []

    @f.on(Str)
    def where(s, ctx):
        paths.append(ctx.where)

    f(Pandoc(BulletList([Plain(Str("x"))], [Plain(Str("y"))])))
    assert paths == ["blocks[0].content[0][0].content[0]", "blocks[0].content[1][0].content[0]"]


def test_wrong_return_type_says_where():
    f = Filter()

    @f.on(Str)
    def bad(s):
        return Para()

    with pytest.raises(ASTTypeError, match=r"Para.content\[0\]: expected Inline, got Para"):
        f(Pandoc(Para(Str("x"))))


def test_errors_name_the_function_and_node():
    f = Filter()

    @f.on(Header)
    def broken(h):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError) as e:
        f(doc())
    notes = getattr(e.value, "__notes__", [])
    assert notes == [
        "in filter function test_errors_name_the_function_and_node.<locals>.broken, "
        "on the Header at blocks[0]"
    ]


def test_one_function_per_type():
    f = Filter()
    f.on(Str)(lambda s: None)
    with pytest.raises(ValueError, match="already has a function"):
        f.on(Str)(lambda s: None)


def test_function_for_the_document():
    f = Filter()

    @f.on(Pandoc)
    def add_meta(d):
        d.meta["filtered"] = True

    assert f(doc()).meta["filtered"] == A.MetaBool(True)


def test_run_several():
    f, g = Filter(), Filter()
    f.on(Str)(lambda s: Str(s.text + "1"))
    g.on(Str)(lambda s: Str(s.text + "2"))
    assert A.run(Pandoc(Para(Str("x"))), [f, g]).blocks[0].content == [Str("x12")]


FILTER = textwrap.dedent("""
    from panir import Filter, Header, Str

    f = Filter()

    @f.on(Header)
    def demote(h, ctx):
        h.level += 1
        h.content.append(Str(ctx.format or "none"))

    if __name__ == "__main__":
        f.main()
""")


def test_main_speaks_pandoc_json(tmp_path):
    script = tmp_path / "demote.py"
    script.write_text(FILTER)
    out = subprocess.run(
        [sys.executable, str(script), "html"],
        check=True,
        input=A.dumps(doc()).encode(),
        capture_output=True,
    ).stdout
    d = A.loads(out)
    assert d.blocks[0].level == 2
    assert d.blocks[0].content[-1] == Str("html")


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="needs pandoc")
def test_as_a_pandoc_filter(tmp_path):
    script = tmp_path / "demote.py"
    script.write_text(f"#!{sys.executable}\n{FILTER}")
    script.chmod(0o755)
    out = subprocess.run(
        ["pandoc", "-f", "markdown", "-t", "json", "--filter", str(script)],
        input="# Hi\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    h = A.loads(out).blocks[0]
    assert (h.level, A.stringify(h)) == (2, "Hijson")


def test_stringify():
    q = A.Quoted(A.QuoteType.DoubleQuote, Str("q"))
    p = Para(
        Str("a"),
        Space(),
        q,
        A.SoftBreak(),
        A.Code("c"),
        Note(Para(Str("n"))),
        A.Math(A.MathType.InlineMath, "m"),
    )
    assert A.stringify(p) == "a “q” cm"


def test_to_python():
    d = Pandoc(meta={"a": [True, "x", {"k": A.MetaInlines(Str("i"), Space(), Str("j"))}]})
    assert A.to_python(d.meta) == {"a": [True, "x", {"k": [Str("i"), Space(), Str("j")]}]}


def test_json_text_round_trip():
    d = doc()
    assert A.loads(A.dumps(d)) == d
    assert json.loads(A.dumps(d))["pandoc-api-version"][:2] == [1, 23]
