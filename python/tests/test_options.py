"""A filter's options from a code block's YAML header and the document's
metadata: the split (corpus/yaml-blocks.jsonl, pandoc's verdicts), metadata
as plain data, the options, and yaml_filter."""

from __future__ import annotations

import shutil
import subprocess

import panir
import pytest
from conftest import jsonl
from panir import (
    CodeBlock,
    Div,
    Emph,
    MetaBlocks,
    MetaBool,
    MetaInlines,
    MetaList,
    MetaMap,
    MetaString,
    Options,
    Pandoc,
    Para,
    Space,
    Str,
    to_data,
    yaml_block,
    yaml_filter,
)

PANDOC = shutil.which("pandoc")


@pytest.mark.parametrize("case", jsonl("yaml-blocks.jsonl"), ids=lambda c: c["name"])
def test_yaml_block(case):
    assert yaml_block(case["text"]) == (case["header"], case["data"])


def test_to_data():
    meta = {
        "flag": MetaBool(False),
        "text": MetaString("10"),
        "caption": MetaInlines(Emph(Str("Sales")), Space(), Str("by"), Space(), Str("region")),
        "body": MetaBlocks(Para("a"), Para("b")),
        "list": MetaList(MetaString("1"), MetaBool(True), MetaList()),
        "map": MetaMap({"k": MetaInlines(Str("v")), "m": MetaMap({})}),
    }
    assert to_data(meta) == {
        "flag": False,
        "text": "10",  # (no guessing: text stays text)
        "caption": "Sales by region",
        "body": "ab",  # (stringify's)
        "list": ["1", True, []],
        "map": {"k": "v", "m": {}},
    }
    assert to_data(MetaMap(meta)) == to_data(meta)
    with pytest.raises(TypeError):
        to_data(Str("x"))  # type: ignore[arg-type]


def test_options_of():
    doc = Pandoc(
        meta={
            "pantable": {"caption": "doc", "alignment": "LC", "csv": {"delimiter": ";", "x": "1"}},
            "other": {"caption": "other's"},
        }
    )
    header = Pandoc(meta={"caption": [Str("block")], "csv": {"delimiter": ","}}).meta
    options = Options.of(doc.meta, "pantable", header)
    # a key's value replaced whole: no deeper merge
    assert to_data(options) == {"caption": "block", "alignment": "LC", "csv": {"delimiter": ","}}
    assert Options.of(doc.meta, "pantable") == doc.meta["pantable"].content
    assert Options.of(doc.meta, "missing", header) == header
    # under the name, anything but a map is ignored
    assert Options.of(Pandoc(meta={"pantable": "x"}).meta, "pantable") == {}
    # the document's own values
    assert (
        Options.of(doc.meta, "pantable")["alignment"] is doc.meta["pantable"].content["alignment"]
    )


def test_options_read():
    options = Options(
        Pandoc(
            meta={
                "caption": [Emph(Str("Sales")), Space(), Str("by")],
                "string": "x",
                "blocks": [Para("p")],
                "yes": True,
                "width": "0.4",
                "big": "1000",
                "fraction": "1/2",
                "ms": ["1", "2"],
                "csv": {"delimiter": ";"},
            }
        ).meta
    )
    assert isinstance(options["caption"], MetaInlines)  # raw: its inlines
    assert options.get("missing") is None
    assert (options.text("caption"), options.text("string"), options.text("blocks")) == (
        "Sales by",
        "x",
        "p",
    )
    assert options.boolean("yes") is True
    assert (options.number("width"), options.number("big"), options.number("fraction")) == (
        0.4,
        1000,
        None,
    )
    assert options.list("ms") == ["1", "2"]
    assert options.map("csv") == {"delimiter": ";"}
    # of another shape, or missing: None
    for read in (options.text, options.boolean, options.number, options.list, options.map):
        assert read("missing") is None
    assert options.text("yes") is None
    assert options.text("ms") is None
    assert options.boolean("string") is None
    assert options.number("yes") is None
    assert options.list("csv") is None
    assert options.map("ms") is None


def stub(meta_of: dict[str, dict], calls: list[list[str]]):
    """A reader that reads each header as metadata from ``meta_of`` (by its
    text), or, if missing there, as pandoc reads a YAML scalar: blocks."""

    def read(texts: list[str]) -> list[Pandoc]:
        calls.append(texts)
        return [Pandoc(meta=meta_of[t]) if t in meta_of else Pandoc(Para(t)) for t in texts]

    return read


def test_yaml_filter():
    doc = Pandoc(
        CodeBlock("---\ncaption: one\n---\na,b\n", classes=["table"]),
        Div(CodeBlock("plain,data", classes=["table"])),
        CodeBlock("---\ncaption: x\n---\nnot ours", classes=["other"]),
        CodeBlock("---\ncaption\n---\nc,d", classes=["csv"]),  # a scalar: no header
        CodeBlock("---\n---\ne,f", classes=["table", "x"]),
        meta={"pantable": {"caption": "default", "alignment": "LC"}},
    )
    calls: list[list[str]] = []
    read = stub({"---\ncaption: one\n---\n": {"caption": [Str("one")]}, "---\n---\n": {}}, calls)
    seen = []

    def table(options: Options, data: str, block: CodeBlock):
        seen.append((to_data(options), data, block.classes[0]))
        if data.startswith("plain"):
            return []
        return [Para(options.text("caption") or ""), Para(data)]

    out = yaml_filter(doc, ["table", "csv"], "pantable", read, table)
    assert out is doc
    # one call, every header, in the document's order
    assert calls == [["---\ncaption: one\n---\n", "---\ncaption\n---\n", "---\n---\n"]]
    assert seen == [
        ({"caption": "one", "alignment": "LC"}, "a,b\n", "table"),
        ({"caption": "default", "alignment": "LC"}, "plain,data", "table"),
        ({"caption": "default", "alignment": "LC"}, "---\ncaption\n---\nc,d", "csv"),
        ({"caption": "default", "alignment": "LC"}, "e,f", "table"),
    ]
    assert doc.blocks == [
        Para("one"),
        Para("a,b\n"),
        Div(),
        CodeBlock("---\ncaption: x\n---\nnot ours", classes=["other"]),
        Para("default"),
        Para("---\ncaption\n---\nc,d"),
        Para("default"),
        Para("e,f"),
    ]


def test_yaml_filter_without_headers_reads_nothing():
    calls: list[list[str]] = []
    doc = Pandoc(CodeBlock("a,b", classes=["table"]), Para("x"))
    yaml_filter(doc, "table", "pantable", stub({}, calls), lambda o, d, b: None)
    assert calls == []
    assert doc.blocks == [CodeBlock("a,b", classes=["table"]), Para("x")]


@pytest.mark.skipif(PANDOC is None, reason="needs pandoc")
def test_yaml_filter_with_pandoc():
    """With the pandoc command as the reader: the header read as markdown."""
    pandoc = PANDOC or "pandoc"

    def read(texts: list[str]) -> list[Pandoc]:
        def one(text: str) -> Pandoc:
            out = subprocess.run(
                [pandoc, "-f", "markdown", "-t", "json"],
                input=text.encode(),
                capture_output=True,
                check=True,
            )
            return panir.loads(out.stdout)

        return [one(t) for t in texts]

    text = '---\ncaption: "*Sales* by region"\nwidth: 0.4\nn: 010\nmarkdown: true\n---\na,b\n'
    doc = Pandoc(CodeBlock(text, classes=["table"]), meta={"pantable": {"markdown": False}})
    got = []
    yaml_filter(doc, "table", "pantable", read, lambda o, d, b: got.append((o, d)))
    [(options, data)] = got
    assert data == "a,b\n"
    assert options["caption"] == MetaInlines(
        Emph(Str("Sales")), Space(), Str("by"), Space(), Str("region")
    )
    assert (options.text("caption"), options.number("width"), options.text("n")) == (
        "Sales by region",
        0.4,
        "10",
    )
    assert options.boolean("markdown") is True
