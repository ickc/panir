"""stringify, as pandoc's Lua pandoc.utils.stringify; what UTF-8 can't
hold, when written; metadata to and from Python."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys

import panir
import pytest
from conftest import jsonl
from panir import (
    ASTTypeError,
    Div,
    Emph,
    Filter,
    LineBreak,
    MetaBlocks,
    MetaBool,
    MetaInlines,
    MetaList,
    MetaMap,
    MetaString,
    Note,
    Pandoc,
    Para,
    Quoted,
    QuoteType,
    RawInline,
    Str,
    stringify,
    to_python,
)

PANDOC = shutil.which("pandoc")

# for each document: stringify of the whole, of its metadata, of each
# metadata value, of each block
LUA = """
for line in io.lines() do
  local doc = pandoc.read(line, "json")
  local out = {whole = pandoc.utils.stringify(doc), metamap = pandoc.utils.stringify(doc.meta),
    meta = {}, blocks = {}}
  for k, v in pairs(doc.meta) do out.meta[k] = pandoc.utils.stringify(v) end
  for i, b in ipairs(doc.blocks) do out.blocks[i] = pandoc.utils.stringify(b) end
  print(pandoc.json.encode(out))
end
"""


def every_kind_of_metadata() -> Pandoc:
    """Metadata of every kind, its keys not in order (as pandoc's JSON has
    them), and in maps."""
    return Pandoc(
        Para("body", LineBreak(), Quoted(QuoteType.DoubleQuote, Str("q"))),
        meta={
            "z": MetaString("zs"),
            "b": MetaBool(True),
            "m": MetaMap(
                {
                    "y": MetaString("my"),
                    "a": MetaInlines(Str("ma")),
                    "f": MetaBool(False),
                    "l": MetaList(MetaString("l1"), MetaBlocks(Para("l2"))),
                }
            ),
            "i": MetaInlines(Str("in"), Note(Para("note")), RawInline("html", "<br/>")),
            "k": MetaBlocks(Para("bl")),
        },
    )


def python_side(doc: Pandoc) -> dict:
    return {
        "whole": stringify(doc),
        "metamap": stringify(doc.meta),
        "meta": {k: stringify(v) for k, v in doc.meta.items()},
        "blocks": [stringify(b) for b in doc.blocks],
    }


def test_stringify_metadata_of_every_kind():
    # as pandoc 3.14 (and against pandoc itself, below): a map in the order
    # of its keys; a whole document is its inlines' text only (no
    # MetaString, no MetaBool), but metadata stringified has its strings
    # and booleans too
    assert python_side(every_kind_of_metadata()) == {
        "whole": "in blmal2body “q”",
        "metamap": "truein blmafalsel1l2myzs",
        "meta": {"z": "zs", "b": "true", "m": "mafalsel1l2my", "i": "in ", "k": "bl"},
        "blocks": ["body “q”"],
    }


@pytest.mark.skipif(PANDOC is None, reason="needs pandoc")
@pytest.mark.parametrize("corpus", ["arbitrary.jsonl", "pandoc.jsonl", "made"])
def test_stringify_as_pandoc(corpus):
    if corpus == "made":
        texts = [panir.dumps(every_kind_of_metadata())]
    else:
        texts = [json.dumps(j, ensure_ascii=False) for j in jsonl(corpus)]
    out = subprocess.run(
        [PANDOC, "lua", "-e", LUA],
        input="\n".join(texts) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    want = [json.loads(line) for line in out.split("\n") if line]
    for w in want:  # (Lua's empty table is written {})
        w["blocks"], w["meta"] = w["blocks"] or [], w["meta"] or {}
    got = [python_side(panir.loads(t)) for t in texts]
    assert got == want


def test_a_string_utf8_cant_hold_is_an_error_when_written():
    doc = Pandoc(Para("a", Emph(Str("b\ud800"))))
    text = panir.dumps(doc)  # (a Python str holds it)
    assert "\ud800" in text
    out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    with pytest.raises(ASTTypeError) as e:
        panir.dump(doc, out)
    assert str(e.value) == (
        "Pandoc.blocks[0].content[1].content[0].text: expected a str UTF-8 can hold "
        "(no lone surrogate), got 'b\\ud800'"
    )
    for bad, where in [
        (Pandoc(meta={"k\udfff": "x"}), "Pandoc.meta key"),
        (Pandoc(Div(attributes={"a": "\udc00"})), "Pandoc.blocks[0].attr.attributes[0][1]"),
    ]:
        with pytest.raises(ASTTypeError, match=r"^" + where.replace("[", r"\[")):
            panir.dump(bad, io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))
    # another encoding's error is its own
    with pytest.raises(UnicodeEncodeError):
        panir.dump(Pandoc(Para("é")), io.TextIOWrapper(io.BytesIO(), encoding="ascii"))


def test_main_says_where_a_string_utf8_cant_hold_is(tmp_path):
    script = tmp_path / "f.py"
    script.write_text(
        "from panir import Filter, Str\n"
        "f = Filter()\n"
        "f.on(Str)(lambda s: Str(s.text + '\\ud800'))\n"
        "f.main()\n"
    )
    doc = panir.dumps(Pandoc(Para("x")))
    run = subprocess.run(
        [sys.executable, str(script)], input=doc, capture_output=True, text=True, env=None
    )
    assert run.returncode != 0
    assert "ASTTypeError: Pandoc.blocks[0].content[0].text: expected a str UTF-8" in run.stderr
    assert "UnicodeEncodeError" in run.stderr  # (the cause)


def test_to_python():
    doc = every_kind_of_metadata()
    assert to_python(doc.meta) == {
        "z": "zs",
        "b": True,
        "m": {"y": "my", "a": "ma", "f": False, "l": ["l1", "l2"]},
        "i": "in ",
        "k": "bl",
    }
    assert to_python(MetaBlocks(Para("a"), Para("b"))) == "ab"
    with pytest.raises(TypeError, match="not metadata"):
        to_python(Str("x"))  # type: ignore[arg-type]


def test_filter_metadata_function_sees_metadata():
    f = Filter()
    seen = []
    f.on_meta(lambda m: seen.append(sorted(m)))
    f(every_kind_of_metadata())
    assert seen == [["b", "i", "k", "m", "z"]]


def test_lua_truth():
    """A metadata value is false only if it is MetaBool(False), as Lua's
    `if meta.x then`: an empty string, list or map is true."""
    true = [MetaBool(True), MetaString(""), MetaString("false"), MetaInlines(), MetaBlocks()]
    true += [MetaList(), MetaMap({})]
    assert all(bool(v) for v in true)
    assert not MetaBool(False)
    meta = Pandoc(meta={"off": False, "empty": ""}).meta
    assert not meta.get("off") and not meta.get("missing")
    assert (meta.get("off") or MetaString("d")) == MetaString("d")  # Lua's `meta.off or "d"`
    assert (meta.get("empty") or MetaString("d")) == MetaString("")


def test_lookup():
    """A nested option, through maps: None where a key is missing or a value
    on the way is not a map."""
    toc = {"depth": "2", "style": {"name": "x"}}
    meta = Pandoc(meta={"toc": toc, "flag": True, "l": ["a"]}).meta
    assert panir.lookup(meta, "toc", "depth") == MetaString("2")
    assert panir.lookup(meta, "toc", "style", "name") == MetaString("x")
    assert panir.lookup(meta["toc"], "depth") == MetaString("2")  # from a MetaMap
    assert panir.lookup(meta, "toc") is meta["toc"]
    missing = [("toc", "missing"), ("missing", "depth"), ("flag", "x"), ("l", "a")]
    for path in [*missing, ("toc", "depth", "x")]:
        assert panir.lookup(meta, *path) is None
    assert panir.lookup(None, "x") is None


def test_items():
    """One value or a list of them, as a list."""
    meta = Pandoc(meta={"one": "a", "l": ["a", "b"], "e": [], "off": False}).meta
    assert panir.items(meta.get("one")) == [MetaString("a")]
    assert panir.items(meta.get("l")) == [MetaString("a"), MetaString("b")]
    assert panir.items(meta.get("e")) == []
    assert panir.items(meta.get("off")) == [MetaBool(False)]
    assert panir.items(meta.get("missing")) == []
    listed = panir.items(meta["l"])
    listed.pop()  # a new list: the document's is as it was
    assert len(meta["l"].content) == 2 and listed[0] is meta["l"].content[0]  # type: ignore[attr-defined]
