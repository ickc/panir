"""Deep documents: plain recursion, as deep as Python's recursion limit
allows (about 190 levels at the default; the deepest real document found
has 38); deeper is ``ASTDecodeError`` reading, ``ASTDepthError`` otherwise."""

from __future__ import annotations

import copy
import pickle
import sys
import threading

import panir
import pytest
from panir import (
    ASTDecodeError,
    ASTDepthError,
    ASTError,
    Div,
    Filter,
    Pandoc,
    Para,
    Str,
    stringify,
    walk,
)

V = '"pandoc-api-version":[1,23,1,2]'
OPEN = {
    "div": ('{"t":"Div","c":[["",[],[]],[', "]]}"),
    # (a table in a table is four levels: Table, TableBody, Row, Cell)
    "table": (
        '{"t":"Table","c":[["",[],[]],[null,[]],[],[["",[],[]],[]],[[["",[],[]],0,[],'
        '[[["",[],[]],[[["",[],[]],{"t":"AlignDefault"},1,1,[',
        ']]]]]]],[["",[],[]],[]]]}',
    ),
}


def text(kind: str, count: int) -> str:
    """``count`` of a kind of node in one another, around a paragraph."""
    o, c = OPEN[kind]
    inner = o * count + '{"t":"Para","c":[{"t":"Str","c":"x"}]}' + c * count
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
    assert repr(doc).startswith("Pandoc(")
    assert stringify(doc) == "x"
    for traverse in ("typewise", "topdown", "bottomup"):
        f = Filter(traverse=traverse)
        f.on(Str)(lambda s: Str(s.text.upper() if s.text == "x" else "x"))
        assert f(other) is other
        assert stringify(other) == "X"
        f(other)
    count: list = []
    walk(doc, lambda x, ctx: count.append(1))
    assert len(count) > 100


# 100 levels: more than twice the deepest real document found
@pytest.mark.parametrize(("kind", "count"), [("div", 100), ("table", 25)])
def test_100_levels(kind, count):
    everything(text(kind, count))


@pytest.mark.parametrize(("kind", "count"), [("div", 100), ("table", 25)])
def test_100_levels_in_another_thread(kind, count):
    errors: list[BaseException] = []

    def run():
        try:
            everything(text(kind, count))
        except BaseException as e:
            errors.append(e)

    t = threading.Thread(target=run)
    t.start()
    t.join()
    assert not errors, errors


def nested(count: int) -> Pandoc:
    x = Para("x")
    for _ in range(count):
        x = Div(x)
    return Pandoc(x)


def test_deeper_than_the_recursion_limit_is_a_clean_error():
    deep = nested(3000)
    f = Filter()
    f.on(Str)(lambda s: None)
    with pytest.raises(ASTDecodeError, match="recursion limit"):
        Pandoc.from_json(_nested_json(3000))
    for op, what in [
        (lambda: deep.to_json(), "to_json"),
        (lambda: copy.deepcopy(deep), "copy"),
        (lambda: repr(deep), "repr"),
        (lambda: stringify(deep), "stringify"),
        (lambda: f(deep), "walk"),
        (lambda: walk(deep, lambda x, ctx: None), "walk"),
    ]:
        with pytest.raises(ASTDepthError) as e:
            op()
        assert str(e.value).startswith(what + ": ")
        assert "sys.setrecursionlimit" in str(e.value)
        assert isinstance(e.value, RecursionError)
    # json in C may go deeper (3.14's, bounded by the stack): it works, or
    # it is the same error
    for op, error in [
        (lambda: panir.loads(text("div", 3000)), ASTDecodeError),
        (lambda: panir.dumps(deep), ASTDepthError),
        (lambda: pickle.dumps(deep), ASTDepthError),
    ]:
        try:
            op()
        except ASTError as e:
            assert isinstance(e, error)
    # comparing doesn't recurse
    assert deep == nested(3000)
    assert deep != nested(2999)


def _nested_json(count: int) -> dict:
    j: dict = {"t": "Para", "c": [{"t": "Str", "c": "x"}]}
    for _ in range(count):
        j = {"t": "Div", "c": [["", [], []], [j]]}
    return {"pandoc-api-version": [1, 23, 1, 2], "meta": {}, "blocks": [j]}


@pytest.mark.parametrize("traverse", ["typewise", "topdown", "bottomup"])
def test_a_filter_functions_own_recursion_is_its_own(traverse):
    """A filter function that calls itself without end: its RecursionError as
    it is, which says which function; not the document's depth."""
    f = Filter(traverse=traverse)

    @f.on(Str)
    def endless(s):
        return endless(s)

    with pytest.raises(RecursionError) as e:
        f(Pandoc(Para("x")))
    assert not isinstance(e.value, ASTError)
    assert "in filter function" in e.value.__notes__[0]
    assert "endless" in e.value.__notes__[0]

    def endless_too(x, ctx):
        return endless_too(x, ctx)

    with pytest.raises(RecursionError) as e:
        walk(Pandoc(Para("x")), endless_too, top_down=traverse == "topdown")
    assert not isinstance(e.value, ASTError)
    assert "endless_too" in e.value.__notes__[0]
    # a deep document, with a filter function at each level, is still too deep
    g = Filter(traverse=traverse)
    g.on(Div)(lambda d: None)
    with pytest.raises(ASTDepthError):
        g(nested(3000))


def test_a_higher_recursion_limit_goes_deeper():
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(5_000)
    try:
        everything(text("div", 400))
    finally:
        sys.setrecursionlimit(limit)


def test_error_in_a_callable_object_keeps_its_error():
    """A callable object has no ``__qualname__``: its error is its own,
    noted (it was an AttributeError about ``__qualname__``)."""

    class Boom:
        def __call__(self, el, ctx):
            raise ValueError("mine")

    with pytest.raises(ValueError, match="mine") as e:
        walk(Para("a"), Boom())
    assert any("in filter function" in n for n in e.value.__notes__)
