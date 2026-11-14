"""Whole documents to and from text the fast way (``_fast``): what the slow
way gives, and the slow way for what it doesn't take."""

import copy
import gc
import json

import panir as A
import pytest
from conftest import jsonl as corpus
from panir import ASTDecodeError, ASTTypeError, NodeDict, NodeList, Pandoc, Para, Str, _fast

JSON = {"ensure_ascii": False, "separators": (",", ":")}


@pytest.fixture(scope="module", autouse=True)
def prepared():
    _fast._prepare()
    assert _fast._hook is not None  # (None: a tag that is two things; then all goes the slow way)


@pytest.mark.parametrize("j", corpus("arbitrary.jsonl") + corpus("pandoc.jsonl"))
def test_as_the_slow_way(j):
    text = json.dumps(j, **JSON)
    slow = Pandoc.from_json(j)
    # the fast reader and writer themselves, not their fallback
    fast = _fast._root(json.loads(text, object_hook=_fast._hook))
    assert fast == slow
    assert type(fast.blocks) is NodeList and type(fast.meta) is NodeDict
    assert _fast._encoder.encode(slow) == json.dumps(slow.to_json(), **JSON) == text
    assert A.loads(text) == slow and A.dumps(slow) == text


def test_what_the_fast_reader_leaves_to_the_slow_one():
    head = '{"pandoc-api-version":[1,23,1,2],"meta":{},"blocks":['
    # an error has its path
    with pytest.raises(ASTDecodeError) as e:
        A.loads(head + '{"t":"Para","c":[{"t":"Str","c":42}]}]}')
    assert e.value.path == ("blocks", 0, "content", 0, "text")
    with pytest.raises(ASTDecodeError, match="pandoc-api-version"):
        A.loads('{"pandoc-api-version":[1,22],"meta":{},"blocks":[]}')
    with pytest.raises(json.JSONDecodeError):
        A.loads(head)
    # JSON pandoc doesn't write, which the slow reader takes
    for text in (
        head + '{"t":"Para","c":[{"c":"x","t":"Str","extra":1}]}]}',
        head + '{"t":"Header","c":[1,["",[],[]],[]],"x":{"t":"Bogus"}}]}',
        '{"t":"Str","pandoc-api-version":[1,23,1,2],"meta":{},"blocks":[]}',
    ):
        assert A.loads(text) == Pandoc.from_json(json.loads(text))
    # a metadata key that is a node's
    tricky = (
        '{"pandoc-api-version":[1,23,1,2],"meta":{"c":{"t":"MetaBool","c":false},'
        '"t":{"t":"MetaMap","c":{"c":{"t":"MetaBool","c":true},"t":{"t":"MetaString","c":"Str"}}}},'
        '"blocks":[]}'
    )
    assert A.dumps(A.loads(tricky)) == tricky
    assert A.loads(tricky) == Pandoc.from_json(json.loads(tricky))


def test_what_the_fast_writer_leaves_to_the_slow_one():
    class Shout(Str):  # a subclass of a constructor's class
        __slots__ = ()

    doc = Pandoc(Para(Shout("a")))
    assert A.dumps(doc) == json.dumps(doc.to_json(), **JSON)
    assert '{"t":"Str","c":"a"}' in A.dumps(doc)
    # an error has its path
    bad = Pandoc(Para(Str("a"), Str("b")))
    object.__setattr__(bad.blocks[0].content[1], "text", 42)
    with pytest.raises(ASTTypeError, match=r"Pandoc.blocks\[0\].content\[1\].text"):
        A.dumps(bad)
    list.append(bad.blocks, Str("not a block"))
    with pytest.raises(ASTTypeError, match=r"Pandoc.blocks"):
        A.dumps(bad)
    with pytest.raises(TypeError, match="takes a document"):
        A.dumps(Para())  # type: ignore[arg-type]


def test_the_collector_is_as_it_was():
    doc = Pandoc(Para("a b"))
    assert gc.isenabled()
    A.loads(A.dumps(doc))
    assert gc.isenabled()
    with pytest.raises(ASTDecodeError):
        A.loads('{"pandoc-api-version":[1,23,1,2],"meta":{},"blocks":[7]}')
    assert gc.isenabled()
    gc.disable()
    try:
        A.loads(A.dumps(doc))
        assert not gc.isenabled()
    finally:
        gc.enable()


@pytest.mark.parametrize("j", corpus("arbitrary.jsonl")[:40] + corpus("pandoc.jsonl"))
def test_a_deep_copy_shares_nothing(j):
    doc = Pandoc.from_json(j)
    twin = copy.deepcopy(doc)
    assert twin == doc and A.dumps(twin) == A.dumps(doc)

    def containers(x, into):
        if isinstance(x, (A.Node, list, dict)):
            into.add(id(x))
        if isinstance(x, A.Node):
            for f in x._fields:
                containers(getattr(x, f), into)
        elif isinstance(x, (list, tuple)):
            for y in x:
                containers(y, into)
        elif isinstance(x, dict):
            for y in x.values():
                containers(y, into)
        return into

    assert not containers(doc, set()) & containers(twin, set())


def test_copies_of_lists_and_maps_are_checked():
    p = Para(Str("a"))
    for other in (copy.copy(p.content), copy.deepcopy(p.content)):
        assert type(other) is NodeList and other == p.content and other is not p.content
        with pytest.raises(ASTTypeError):
            other.append(Para())
    doc = Pandoc(meta={"a": True})
    for other in (copy.copy(doc.meta), copy.deepcopy(doc.meta)):
        assert type(other) is NodeDict and other == doc.meta
        with pytest.raises(ASTTypeError):
            other["x"] = object()
