"""Conversion: what a filter knows of its pandoc run."""

from __future__ import annotations

import json

from panir import Conversion, Filter, Pandoc, Para, Str


def test_from_environment(monkeypatch):
    monkeypatch.delenv("PANDOC_INPUT_FORMAT", raising=False)
    monkeypatch.delenv("PANDOC_OUTPUT_FORMAT", raising=False)
    monkeypatch.delenv("PANDOC_WRITER_OPTIONS", raising=False)
    monkeypatch.setenv("PANDOC_READER_OPTIONS", json.dumps({"tab-stop": 3}))
    c = Conversion.from_environment(["html5"])
    assert c.format == "html5"
    assert c.reader_options == {"tab-stop": 3}
    assert c.input_format is None
    assert c.writer_options is None  # (pandoc gives a JSON filter none)


def test_from_environment_with_the_writer_options(monkeypatch):
    """As proposed for pandoc and libpandoc: Lua's PANDOC_WRITER_OPTIONS, keyed
    as pandoc's JSON keys the reader's."""
    writer = {"number-sections": True, "top-level-division": "top-level-chapter"}
    monkeypatch.setenv("PANDOC_WRITER_OPTIONS", json.dumps(writer))
    c = Conversion.from_environment(["latex"])
    assert c.writer_options == writer
    assert Conversion(writer_options=writer).writer_options == writer


def test_from_environment_with_the_formats(monkeypatch):
    """As libpandoc (and, proposed, pandoc) tells JSON filters."""
    monkeypatch.setenv("PANDOC_INPUT_FORMAT", "commonmark_x-smart")
    monkeypatch.setenv("PANDOC_OUTPUT_FORMAT", "html5+smart")
    c = Conversion.from_environment(["html5"])
    assert (c.input_format, c.output_format) == ("commonmark_x-smart", "html5+smart")


def test_ctx_conversion_and_format():
    seen = []
    f = Filter()

    @f.on(Str)
    def s(x, ctx):
        seen.append((ctx.format, ctx.conversion.output_format))

    f(Pandoc(Para(Str("a"))), conversion=Conversion("html", output_format="html+smart"))
    f(Pandoc(Para(Str("a"))), "latex")
    assert seen == [("html", "html+smart"), ("latex", None)]


def test_from_environment_with_the_sandbox(monkeypatch):
    """libpandoc 1.13 tells a JSON filter whether pandoc's sandbox is on;
    pandoc doesn't: unknown."""
    for value, sandbox in (("true", True), ("false", False), ("", None), ("yes", None)):
        monkeypatch.setenv("PANDOC_SANDBOX", value)
        assert Conversion.from_environment(["html"]).sandbox is sandbox
    monkeypatch.delenv("PANDOC_SANDBOX")
    assert Conversion.from_environment(["html"]).sandbox is None


def test_from_context():
    """What libpandoc's context tells a filter in process, and the options
    the host knows."""
    context = {
        "format": "html5",
        "input-format": "commonmark_x-smart",
        "output-format": "html5+smart",
        "reader-options": {"tab-stop": 3},
        "writer-options": {"number-sections": True},
        "sandbox": True,
    }
    c = Conversion.from_context(context, {"to": "html5", "sandbox": True})
    assert (c.format, c.input_format, c.output_format) == (
        "html5",
        "commonmark_x-smart",
        "html5+smart",
    )
    assert c.reader_options == {"tab-stop": 3}
    assert c.writer_options == {"number-sections": True}
    assert c.options == {"to": "html5", "sandbox": True}
    assert c.sandbox is True
    assert Conversion.from_context({**context, "sandbox": False}).sandbox is False
    # an older libpandoc's context: what it lacks is unknown
    old = Conversion.from_context({"format": "latex", "input-format": ""})
    assert (old.format, old.input_format, old.writer_options, old.sandbox) == (
        "latex",
        None,
        None,
        None,
    )
    assert old.options is None
    assert repr(c) == (
        "Conversion(format='html5', input_format='commonmark_x-smart', "
        "output_format='html5+smart', sandbox=True)"
    )


def test_a_walk_inside_a_function_knows_the_conversion():
    """A fragment's walk started from a filter function (Lua's el:walk{...},
    whose functions see the same FORMAT and PANDOC_READER_OPTIONS) knows the
    conversion of the walk it is in, unless given one (gap 43)."""
    from panir import Div, walk

    seen = []
    inner = Filter()

    @inner.on(Str)
    def note(s, ctx):
        seen.append((s.text, ctx.conversion.input_format, ctx.format))

    outer = Filter()

    @outer.on(Div)
    def div(d, ctx):
        d.walk(inner)  # a node's walk
        inner(d.content)  # a list's
        walk(d, lambda x, c: seen.append(("walk", c.conversion.input_format, c.format)) and None)
        d.walk(inner, "latex")  # a format given: that alone
        d.walk(inner, conversion=Conversion("rst"))

    c = Conversion("html", input_format="org", reader_options={"tab-stop": 2})
    outer(Pandoc(Div(Para(Str("a")))), conversion=c)
    assert seen[:2] == [("a", "org", "html")] * 2
    assert ("walk", "org", "html") in seen
    assert seen[-2:] == [("a", None, "latex"), ("a", None, "rst")]
    # outside any walk, a walk knows only what it is given
    seen.clear()
    Div(Para(Str("b"))).walk(inner)
    assert seen == [("b", None, None)]
