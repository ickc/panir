"""Type checkers read the generated declarations."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

SAMPLE = textwrap.dedent("""
    from panir import Header, Para, Str

    h = Header(1, Str("x"))
    h.level = "2"
    Para(Header(1))
    reveal_type(h.content)
""")


@pytest.mark.skipif(shutil.which("pyright") is None, reason="needs pyright")
def test_pyright_sees_field_and_argument_types(tmp_path):
    (tmp_path / "sample.py").write_text(SAMPLE)
    (tmp_path / "pyrightconfig.json").write_text(
        json.dumps({"extraPaths": [str(SRC)], "pythonVersion": "3.10"})
    )
    out = subprocess.run(
        [
            "pyright",
            "--outputjson",
            "-p",
            str(tmp_path / "pyrightconfig.json"),
            str(tmp_path / "sample.py"),
        ],
        capture_output=True,
        text=True,
    ).stdout
    found = [
        (d["range"]["start"]["line"], d["severity"], d["message"].splitlines()[0])
        for d in json.loads(out)["generalDiagnostics"]
    ]
    assert found == [
        (4, "error", 'Cannot assign to attribute "level" for class "Header"'),
        (
            5,
            "error",
            'Argument of type "Header" cannot be assigned to parameter "content" '
            'of type "Inline | str" in function "__init__"',
        ),
        (6, "information", 'Type of "h.content" is "NodeList[Inline, Inline | str]"'),
    ]


FILTERS = textwrap.dedent("""
    from panir import Block, Context, Filter, Header, Inline, Pandoc, Para, Space, Str

    f = Filter()

    @f.on(Header)
    def ok(h: Header) -> Block:
        return Para("x")

    @f.on(Header)
    def wrong_parameter(s: Str) -> None: ...

    @f.on(Str)
    def wrong_return(s):
        return Para("x")

    @f.on(Str)
    def splice(s, ctx: Context):
        return [Str("a"), Space()]

    @f.on(Header, Para)
    def two(b: Header | Para) -> None: ...

    @f.on(Str, Para)
    def mixed(x) -> None: ...

    @f.on_inlines
    def inlines(xs: list[Inline], ctx: Context) -> list[Inline]:
        return xs

    @f.on_inlines
    def blocks_for_inlines(xs: list[Inline]) -> list[Block]:
        return [Para()]

    @f.on_meta
    def meta(m):
        return None

    @f.on(Pandoc)
    def doc_as_list(d: Pandoc):
        return [d]

    @f.on(Str)
    def kept(s: Str, ctx: Context) -> str:
        return "two words"

    reveal_type(kept)
    reveal_type(two)
    reveal_type(inlines)

    @f.on(Para)
    def plain(p):
        return [Str("x"), [Str("y"), "z"]]

    @f.on_blocks
    def one_block(bs):
        return Str("as Plain")

    @f.on_meta
    def plain_values(m):
        return {"draft": True, "tags": ["a", "b"]}

    reveal_type(f(Para()))
    reveal_type(f(Pandoc()))
    reveal_type(Pandoc.from_json({}))
""")


@pytest.mark.skipif(shutil.which("pyright") is None, reason="needs pyright")
def test_pyright_checks_filter_functions(tmp_path):
    """A function registered for a kind of node must take it and return the
    same family (Filter.on's generated signatures)."""
    (tmp_path / "filters.py").write_text(FILTERS)
    (tmp_path / "pyrightconfig.json").write_text(
        json.dumps({"extraPaths": [str(SRC)], "pythonVersion": "3.10"})
    )
    out = subprocess.run(
        [
            "pyright",
            "--outputjson",
            "-p",
            str(tmp_path / "pyrightconfig.json"),
            str(tmp_path / "filters.py"),
        ],
        capture_output=True,
        text=True,
    ).stdout
    found = [
        (d["range"]["start"]["line"], d["severity"], d["message"].splitlines()[0])
        for d in json.loads(out)["generalDiagnostics"]
    ]
    errors = [line for line, severity, _ in found if severity == "error"]
    # the decorators of wrong_parameter and wrong_return; on_inlines's of
    # blocks_for_inlines; on(Pandoc)'s of doc_as_list (a document's function
    # can't return a list)
    assert errors == [9, 12, 30, 38], found
    # a decorated function keeps its type
    revealed = [message for _, severity, message in found if severity == "information"]
    assert revealed == [
        'Type of "kept" is "(s: Str, ctx: Context) -> str"',
        'Type of "two" is "(Header | Para) -> None"',
        'Type of "inlines" is "(xs: list[Inline], ctx: Context) -> list[Inline]"',
        'Type of "f(Para())" is "Para"',
        'Type of "f(Pandoc())" is "Pandoc"',
        'Type of "Pandoc.from_json({})" is "Pandoc"',
    ]
