"""Deep documents: read, written, walked, copied and compared to 256 levels
(the Rust crate's MAX_DEPTH) and beyond, in any thread, without touching
the recursion limit; deeper than panir can go is a clean error."""

from __future__ import annotations

import contextvars
import copy
import pickle
import sys
import threading

import panir
import pytest
from panir import (
    ASTDecodeError,
    ASTDepthError,
    Div,
    Emph,
    Filter,
    Inline,
    Pandoc,
    Para,
    Str,
    _deep,
    stringify,
    walk,
)

V = '"pandoc-api-version":[1,23,1,2]'
OPEN = {
    "div": ('{"t":"Div","c":[["",[],[]],[', "]]}"),
    "emph": ('{"t":"Emph","c":[', "]}"),
    "table": (
        '{"t":"Table","c":[["",[],[]],[null,[]],[],[["",[],[]],[]],[[["",[],[]],0,[],'
        '[[["",[],[]],[[["",[],[]],{"t":"AlignDefault"},1,1,[',
        ']]]]]]],[["",[],[]],[]]]}',
    ),
}


def text(kind: str, depth: int) -> str:
    """``depth`` levels of a kind of node, around a paragraph (or a Str)."""
    o, c = OPEN[kind]
    if kind == "emph":
        inner = o * depth + '{"t":"Str","c":"x"}' + c * depth
        return f'{{{V},"meta":{{}},"blocks":[{{"t":"Para","c":[{inner}]}}]}}'
    inner = o * depth + '{"t":"Para","c":[{"t":"Str","c":"x"}]}' + c * depth
    return f'{{{V},"meta":{{}},"blocks":[{inner}]}}'


def everything(t: str) -> None:
    """Every operation, on the document in ``t``."""
    doc = panir.loads(t)
    assert panir.dumps(doc) == t
    other = panir.loads(t)
    assert doc == other
    assert copy.deepcopy(doc) == doc
    assert pickle.loads(pickle.dumps(doc)) == doc
    assert Pandoc.from_json(doc.to_json()) == doc
    # (Python's parser stops at 200 parentheses: its eval, not the repr)
    assert repr(doc).startswith("Pandoc(") and repr(doc).count("(") > 256
    assert stringify(doc) == "x"
    for traverse in ("typewise", "topdown", "bottomup"):
        f = Filter(traverse=traverse)

        @f.on(Str)
        def upper(s, ctx):
            assert ctx.where.endswith("content[0]")
            return Str(s.text.upper())

        @f.on(Div, Emph)
        def keep(x):
            return None

        assert f(other) is other
    assert stringify(other) == "X"
    count = []
    walk(doc, lambda x, ctx: count.append(1))
    assert len(count) > 2


@pytest.mark.parametrize("kind", ["div", "emph", "table"])
def test_256_levels_of_each_kind(kind):
    limit = sys.getrecursionlimit()
    everything(text(kind, 256))
    assert sys.getrecursionlimit() == limit


@pytest.mark.parametrize("kind", ["div", "table"])
def test_256_levels_in_a_thread_with_a_small_stack(kind):
    # (512 kB: a thread's stack on macOS; 3.12 and 3.13's json would crash
    # in it, deep enough)
    errors: list[BaseException] = []

    def run():
        try:
            everything(text(kind, 256))
        except BaseException as e:
            errors.append(e)

    old = threading.stack_size(512 << 10)
    try:
        t = threading.Thread(target=run)
        t.start()
    finally:
        threading.stack_size(old)
    t.join()
    assert not errors, errors


def test_deeper_still():
    # 2,000 divs: deeper than the caller's thread goes, with any Python
    # (6,000 levels of JSON; json on 3.12 and 3.13 goes to 10,000)
    everything(text("div", 2000))


def test_a_deep_walk_goes_on_in_another_thread():
    doc = panir.loads(text("div", 1000))
    var = contextvars.ContextVar("var", default="unset")
    var.set("set")
    seen = []
    f = Filter()

    @f.on(Para)
    def deepest(p, ctx):
        seen.append((threading.current_thread().name, var.get(), len(ctx.ancestors)))
        return [p, Para("after")]

    f(doc)
    # in panir's thread, with the caller's context; the walk as usual
    assert seen == [("panir-deep", "set", 1001)]
    inner = doc.blocks[0]
    for _ in range(999):
        inner = inner.content[0]
    assert inner.content == [Para("x"), Para("after")]


def test_an_error_deep_in_a_walk_is_the_functions():
    doc = panir.loads(text("div", 1000))
    f = Filter()

    @f.on(Str)
    def fail(s):
        raise KeyError("mine")

    with pytest.raises(KeyError, match="mine") as e:
        f(doc)
    assert "in filter function" in e.value.__notes__[0]
    assert "blocks[0]" + ".content[0]" * 1000 in e.value.__notes__[0]


@pytest.fixture
def shallow(monkeypatch):
    """panir's thread as if it couldn't go deeper than 3,000 calls."""
    monkeypatch.setattr(_deep, "LIMIT", 3000)
    monkeypatch.setattr(sys, "getrecursionlimit", lambda: 1000)  # (the caller's)
    real = sys.setrecursionlimit
    monkeypatch.setattr(sys, "setrecursionlimit", lambda n: real(min(n, 3000)))
    yield
    real(1000)


def nested_json(depth: int) -> dict:
    j: dict = {"t": "Str", "c": "x"}
    for _ in range(depth):
        j = {"t": "Emph", "c": [j]}
    return {"pandoc-api-version": [1, 23, 1, 2], "meta": {}, "blocks": [{"t": "Para", "c": [j]}]}


def nested(depth: int) -> Inline:
    x: Inline = Str("x")
    for _ in range(depth):
        x = Emph(x)
    return x


def test_too_deep_is_a_clean_error(shallow):
    # deeper than panir's thread can go (as if; really, tens of thousands
    # of levels): ASTDecodeError reading, ASTDepthError otherwise, never a
    # bare RecursionError
    with pytest.raises(ASTDecodeError, match="JSON nested less deeply"):
        Pandoc.from_json(nested_json(5000))
    deep = Pandoc(Para(nested(5000)))
    for op, what in [
        (lambda: deep.to_json(), "JSON of Pandoc"),
        (lambda: copy.deepcopy(deep), "copy of Pandoc"),
        (lambda: stringify(deep), "stringify"),
        (lambda: repr(deep), "repr of"),
    ]:
        with pytest.raises(ASTDepthError) as e:
            op()
        assert str(e.value).startswith(what), str(e.value)
        assert isinstance(e.value, RecursionError)
    f = Filter()
    f.on(Str)(lambda s: None)
    with pytest.raises(ASTDepthError, match="walk at blocks"):
        f(deep)
    # comparing has no recursion
    assert deep == Pandoc(Para(nested(5000)))
    assert deep != Pandoc(Para(nested(4999)))


def test_the_recursion_limit_is_restored():
    before = sys.getrecursionlimit()
    everything(text("div", 300))
    assert sys.getrecursionlimit() == before


def test_equality_without_recursion():
    a, b = nested(100_000), nested(100_000)
    assert a == b
    b2 = b
    for _ in range(99_999):
        b2 = b2.content[0]  # type: ignore[attr-defined]
    b2.content[0] = Str("y")  # type: ignore[attr-defined]
    assert a != b
    assert Div(Para("x")) != Div(Para("y")) and Div(Para("x")) == Div(Para("x"))
    nan = float("nan")
    from panir import ColWidth

    w = ColWidth(nan)
    assert w == w and ColWidth(float("nan")) != ColWidth(float("nan"))  # (as [nan] == [nan])
