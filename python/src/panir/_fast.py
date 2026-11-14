"""Whole documents to and from JSON text, as fast as ``json`` allows.

``_core`` decodes and encodes through a tree of dicts and lists
(``Node.from_json``, ``to_json``): general, and it says where a value is
wrong. For a whole document that tree is most of the time and of the memory.
Here the nodes are made while ``json`` parses (its ``object_hook``), and
handed to its encoder as they are (its ``default``), with the same checks.

Either stops at the first thing that isn't the plain case (``_Slow``): a
wrong value, a number that isn't finite, a subclass of a node's class. The
caller then goes the slow way, which takes it or says what is wrong.

Built from the same declarations as ``_core``; nothing here names a pandoc
type.
"""

from __future__ import annotations

import gc
import json
import math
from collections.abc import Callable
from typing import Any

from ._core import (
    _CLASSES,
    _INT_MAX,
    _INT_MIN,
    _SPEC_OF,
    Enum,
    Node,
    NodeDict,
    NodeList,
    _ClassSpec,
    _EnumSpec,
    _is_product,
    _is_sum,
    _ListSpec,
    _MapSpec,
    _MaybeSpec,
    _PrimSpec,
    _Spec,
    _TupleSpec,
    too_deep,
)

__all__ = ["clone", "dumps", "loads", "paused_gc"]


class _Slow(Exception):
    """Not the plain case: for the slow reader or writer."""


Reader = Callable[[Any], Any]
Writer = Callable[[Any], Any]

_new = object.__new__
_new_list, _init_list = NodeList.__new__, list.__init__
_new_dict, _update_dict = NodeDict.__new__, dict.update
_STR = frozenset({str})


class paused_gc:
    """Python's cyclic collector off, within a ``with``.

    Building a document makes hundreds of thousands of objects that are not
    garbage; the collector goes through them again and again, for half of
    the time of reading one. (Two threads may overlap: the collector is on
    again when the last one that found it on leaves.)
    """

    __slots__ = ("was",)

    def __enter__(self) -> None:
        self.was = gc.isenabled()
        gc.disable()

    def __exit__(self, *exc: object) -> None:
        if self.was:
            gc.enable()


def _constructors(cls: type) -> list[type]:
    """A sum type's constructor classes; a product's own class."""
    if _is_sum(cls):
        return list(_SPEC_OF[cls].cons.values())
    return [cls]


def _made(spec: _Spec) -> frozenset[type] | None:
    """The types of the values ``spec`` holds that are whole in a tree: the
    reader's hook has made them, and the writer leaves them to ``default``.
    None for a value in several JSON parts (a list, a product as an array)."""
    if isinstance(spec, _EnumSpec):
        return frozenset({spec.cls})
    if isinstance(spec, _ClassSpec) and _is_sum(spec.cls):
        return frozenset(_constructors(spec.cls))
    return None


# -- reading -------------------------------------------------------------------


def _reader(spec: _Spec) -> Reader:
    """What makes a field's value of its JSON, the hook's work done."""
    made = _made(spec)
    if made is not None:

        def read_made(j: Any) -> Any:
            if type(j) in made:
                return j
            raise _Slow

        return read_made
    if isinstance(spec, _PrimSpec):
        ty = spec.ty
        if ty is int:

            def read_int(j: Any) -> Any:
                if type(j) is int and _INT_MIN <= j <= _INT_MAX:
                    return j
                raise _Slow

            return read_int

        def read_prim(j: Any) -> Any:
            if type(j) is ty:  # (a float: finite or not, as json read it)
                return j
            raise _Slow

        return read_prim
    if isinstance(spec, _ListSpec):
        item = spec.item
        whole = _made(item) or (_STR if isinstance(item, _PrimSpec) and item.ty is str else None)
        cls = spec.cls
        if whole is not None:
            ok = whole.issuperset

            def read_whole(j: Any) -> Any:
                if type(j) is list and ok(map(type, j)):
                    lst = _new_list(cls)
                    lst._spec = spec
                    _init_list(lst, j)
                    return lst
                raise _Slow

            return read_whole
        read_item = _reader(item)

        def read_list(j: Any) -> Any:
            if type(j) is not list:
                raise _Slow
            lst = _new_list(cls)
            lst._spec = spec
            _init_list(lst, map(read_item, j))
            return lst

        return read_list
    if isinstance(spec, _MaybeSpec):
        read_some = _reader(spec.item)
        return lambda j: None if j is None else read_some(j)
    if isinstance(spec, _MapSpec):
        made = _made(spec.value)
        if made is None:
            raise TypeError(f"{spec.label}: no fast reader for such a map")
        ok = made.issuperset

        def read_map(j: Any) -> Any:
            if type(j) is dict and ok(map(type, j.values())):
                d = _new_dict(NodeDict)
                d._spec = spec
                _update_dict(d, j)
                return d
            raise _Slow

        return read_map
    if isinstance(spec, _TupleSpec):
        readers = [_reader(s) for s in spec.items]
        n = len(readers)

        def read_tuple(j: Any) -> Any:
            if type(j) is not list or len(j) != n:
                raise _Slow
            return tuple([r(x) for r, x in zip(readers, j)])

        return read_tuple
    if isinstance(spec, _ClassSpec):  # a product
        return _product_reader(spec.cls)
    raise TypeError(f"{spec.label}: no fast reader")


def _fields_reader(cls: type[Node]) -> Reader:
    """A node of ``cls`` from the JSON array of its fields."""
    readers = [_reader(s) for s in cls._spec_list]
    setters = cls._setters
    n = len(readers)
    if n == 2:
        (r0, r1), (s0, s1) = readers, setters

        def read2(c: Any) -> Any:
            if type(c) is not list or len(c) != 2:
                raise _Slow
            node = _new(cls)
            s0(node, r0(c[0]))
            s1(node, r1(c[1]))
            return node

        return read2
    if n == 3:
        (r0, r1, r2), (s0, s1, s2) = readers, setters

        def read3(c: Any) -> Any:
            if type(c) is not list or len(c) != 3:
                raise _Slow
            node = _new(cls)
            s0(node, r0(c[0]))
            s1(node, r1(c[1]))
            s2(node, r2(c[2]))
            return node

        return read3

    def read_fields(c: Any) -> Any:
        if type(c) is not list or len(c) != n:
            raise _Slow
        node = _new(cls)
        for s, r, x in zip(setters, readers, c):
            s(node, r(x))
        return node

    return read_fields


_PRODUCT_READERS: dict[type, Reader] = {}


def _product_reader(cls: type[Node]) -> Reader:
    """A product's node from its JSON: an array, its one field's, or an object."""
    found = _PRODUCT_READERS.get(cls)
    if found is not None:
        return found
    enc = cls._encoding
    if enc == "array":
        read = _fields_reader(cls)
    elif enc == "value":
        read = _value_reader(cls)
    else:
        read = _object_reader(cls)
    _PRODUCT_READERS[cls] = read
    return read


def _object_reader(cls: type[Node]) -> Reader:
    """A node of ``cls`` from a JSON object of its fields (the document's, too)."""
    readers = [_reader(s) for s in cls._spec_list]
    keys = [cls._keys[f] for f in cls._fields]
    setters = cls._setters
    version = list(_SPEC_OF[cls].api_version[:2]) if cls._encoding == "root" else None

    def read_object(j: Any) -> Any:
        if type(j) is not dict:
            raise _Slow
        if version is not None:
            v = j.get("pandoc-api-version")
            if type(v) is not list or v[:2] != version:
                raise _Slow
        node = _new(cls)
        for s, r, k in zip(setters, readers, keys):
            if k not in j:
                raise _Slow
            s(node, r(j[k]))
        return node

    return read_object


def _value_reader(cls: type[Node]) -> Reader:
    """A node of ``cls`` from the JSON of its one field."""
    (r0,), (s0,) = [_reader(s) for s in cls._spec_list], cls._setters

    def read1(c: Any) -> Any:
        node = _new(cls)
        s0(node, r0(c))
        return node

    return read1


def _hook_table() -> dict[str, Reader] | None:
    """By tag, what makes of ``{"t": tag, "c": ...}`` its node or enum value.

    None if a tag is two things (a constructor of two types): the hook sees
    an object without knowing where it is, and could not tell.
    """
    table: dict[str, Reader] = {}

    def add(tag: str, make: Reader) -> bool:
        if tag in table:
            return False
        table[tag] = make
        return True

    for cls in _CLASSES.values():
        if not _is_sum(cls):
            continue
        for con in _constructors(cls):
            enc = con._encoding
            if enc == "none":
                make = _without_contents(con)
            else:
                make = _with_contents(_value_reader(con) if enc == "value" else _fields_reader(con))
            if not add(con._tag, make):
                return None
    enums = {spec.cls for spec in _all_specs() if isinstance(spec, _EnumSpec)}
    for enum in sorted(enums, key=lambda e: e.__name__):
        for name, member in enum.__members__.items():
            if not add(name, _always(member)):
                return None
    return table


def _without_contents(con: type[Node]) -> Reader:
    return lambda d: _new(con)


def _with_contents(inner: Reader) -> Reader:
    def make(d: Any) -> Any:
        if "c" in d:
            return inner(d["c"])
        raise _Slow

    return make


def _always(value: Any) -> Reader:
    return lambda d: value


def _all_specs() -> list[_Spec]:
    out: list[_Spec] = []

    def scan(spec: _Spec) -> None:
        out.append(spec)
        for inner in (getattr(spec, "item", None), getattr(spec, "value", None)):
            if isinstance(inner, _Spec):
                scan(inner)
        for inner in getattr(spec, "items", ()):
            scan(inner)

    for cls in _CLASSES.values():
        for spec in cls._spec_list:
            scan(spec)
    return out


# -- writing ---------------------------------------------------------------------


def _writer(spec: _Spec) -> Writer:
    """What makes of a field's value what ``json`` writes: itself, where it
    is JSON as it is or a node (``default`` gets it), checked; or its parts."""
    if isinstance(spec, _EnumSpec):
        cls = spec.cls
        as_json = {m: {"t": m.name} for m in cls}

        def write_enum(v: Any) -> Any:
            if type(v) is cls:
                return as_json[v]
            raise _Slow

        return write_enum
    made = _made(spec)
    if made is None and isinstance(spec, _ClassSpec):
        made = frozenset({spec.cls})  # a product: default's too
    if made is not None:

        def write_node(v: Any) -> Any:
            if type(v) in made:
                return v
            raise _Slow

        return write_node
    if isinstance(spec, _PrimSpec):
        ty = spec.ty
        if ty is int:

            def write_int(v: Any) -> Any:
                if type(v) is int and _INT_MIN <= v <= _INT_MAX:
                    return v
                raise _Slow

            return write_int
        if ty is float:
            finite = math.isfinite

            def write_float(v: Any) -> Any:
                if type(v) is float and finite(v):
                    return v
                raise _Slow

            return write_float

        def write_prim(v: Any) -> Any:
            if type(v) is ty:
                return v
            raise _Slow

        return write_prim
    if isinstance(spec, _ListSpec):
        item = spec.item
        whole = None if isinstance(item, _EnumSpec) else _made(item)
        if whole is None and isinstance(item, _ClassSpec):
            whole = frozenset({item.cls})
        if whole is None and isinstance(item, _PrimSpec) and item.ty is str:
            whole = _STR
        if whole is not None:
            ok = whole.issuperset

            def write_whole(v: Any) -> Any:
                if isinstance(v, list) and ok(map(type, v)):
                    return v
                raise _Slow

            return write_whole
        write_item = _writer(item)

        def write_list(v: Any) -> Any:
            if isinstance(v, list):
                return [write_item(x) for x in v]
            raise _Slow

        return write_list
    if isinstance(spec, _MaybeSpec):
        write_some = _writer(spec.item)
        return lambda v: None if v is None else write_some(v)
    if isinstance(spec, _MapSpec):
        made = _made(spec.value)
        if made is None:
            raise TypeError(f"{spec.label}: no fast writer for such a map")
        ok = made.issuperset
        strings = _STR.issuperset

        def write_map(v: Any) -> Any:
            if isinstance(v, dict) and strings(map(type, v)) and ok(map(type, v.values())):
                return v
            raise _Slow

        return write_map
    if isinstance(spec, _TupleSpec):
        writers = [_writer(s) for s in spec.items]
        n = len(writers)

        def write_tuple(v: Any) -> Any:
            if type(v) is not tuple or len(v) != n:
                raise _Slow
            return [w(x) for w, x in zip(writers, v)]

        return write_tuple
    raise TypeError(f"{spec.label}: no fast writer")


def _node_writer(cls: type[Node]) -> Writer:
    """What ``json`` writes for a node of ``cls``, its fields' values checked."""
    writers = [_writer(s) for s in cls._spec_list]
    getters = [cls.__dict__[f].__get__ for f in cls._fields]
    enc = cls._encoding
    tag = cls._tag if not _is_product(cls) else None
    if enc == "none":
        nothing = {"t": tag}
        return lambda node: nothing
    if enc == "value":
        (w0,), (g0,) = writers, getters
        if tag is None:
            return lambda node: w0(g0(node))
        return lambda node: {"t": tag, "c": w0(g0(node))}
    if enc == "array":
        if len(writers) == 2:
            (w0, w1), (g0, g1) = writers, getters
            if tag is None:
                return lambda node: [w0(g0(node)), w1(g1(node))]
            return lambda node: {"t": tag, "c": [w0(g0(node)), w1(g1(node))]}
        if len(writers) == 3:
            (w0, w1, w2), (g0, g1, g2) = writers, getters
            if tag is None:
                return lambda node: [w0(g0(node)), w1(g1(node)), w2(g2(node))]
            return lambda node: {"t": tag, "c": [w0(g0(node)), w1(g1(node)), w2(g2(node))]}

        def fields(node: Any) -> list:
            return [w(g(node)) for w, g in zip(writers, getters)]

        if tag is None:
            return fields
        return lambda node: {"t": tag, "c": fields(node)}
    keys = [cls._keys[f] for f in cls._fields]
    version = list(_SPEC_OF[cls].api_version) if enc == "root" else None

    def write_object(node: Any) -> dict:
        out: dict = {} if version is None else {"pandoc-api-version": version}
        for k, w, g in zip(keys, writers, getters):
            out[k] = w(g(node))
        return out

    return write_object


# -- copying -----------------------------------------------------------------------

Cloner = Callable[[Any], Any]
_CLONERS: dict[type, Cloner] = {}


def clone(node: Node) -> Node:
    """A copy of a node and of everything in it, which shares nothing with
    it (``copy.deepcopy`` of a node). Values are copied as they are: they
    were checked when put in."""
    make = _CLONERS.get(type(node))
    if make is None:
        make = _node_cloner(type(node))
    return make(node)


def _node_cloner(cls: type[Node]) -> Cloner:
    cloners: list[Cloner | None] = []
    _CLONERS[cls] = lambda node: make(node)  # (for a class in itself, while this is made)
    fields = [(getattr(cls, f).__get__, getattr(cls, f).__set__) for f in cls._fields]
    cloners = [_cloner(s) for s in cls._spec_list]
    plan = [(g, s, c) for (g, s), c in zip(fields, cloners)]

    def make(node: Any) -> Any:
        new = _new(cls)
        for get, set_, copy_ in plan:
            v = get(node)
            set_(new, v if copy_ is None else copy_(v))
        return new

    _CLONERS[cls] = make
    return make


def _cloner(spec: _Spec) -> Cloner | None:
    """What copies a field's value, or None for one that can't change (a
    string, a number, an enum's value)."""
    if isinstance(spec, (_PrimSpec, _EnumSpec)):
        return None
    if isinstance(spec, _ClassSpec):
        return clone
    if isinstance(spec, _ListSpec):
        item = _cloner(spec.item)
        if item is None:
            return lambda v: _as_list(spec, v)
        if item is clone:
            get = _CLONERS.get
            return lambda v: _as_list(spec, [(get(type(x)) or _node_cloner(type(x)))(x) for x in v])
        return lambda v: _as_list(spec, map(item, v))
    if isinstance(spec, _MaybeSpec):
        some = _cloner(spec.item)
        return None if some is None else (lambda v: None if v is None else some(v))
    if isinstance(spec, _MapSpec):
        value = _cloner(spec.value) or (lambda v: v)

        def copy_map(v: Any) -> Any:
            d = _new_dict(NodeDict)
            d._spec = spec
            _update_dict(d, {k: value(x) for k, x in v.items()})
            return d

        return copy_map
    if isinstance(spec, _TupleSpec):
        items = [_cloner(s) for s in spec.items]
        if all(c is None for c in items):
            return None
        return lambda v: tuple([x if c is None else c(x) for c, x in zip(items, v)])
    raise TypeError(f"{spec.label}: no copy")


def _as_list(spec: _ListSpec, items: Any) -> NodeList:
    lst = _new_list(spec.cls)
    lst._spec = spec
    _init_list(lst, items)
    return lst


# -- the two, made when first used -------------------------------------------------

_ready = False
_hook: Callable[[dict], Any] | None = None
_encoder: json.JSONEncoder | None = None
_JSON = {"ensure_ascii": False, "separators": (",", ":")}


def _prepare() -> None:
    global _ready, _hook, _encoder
    table = _hook_table()
    if table is not None:
        get = table.get

        def hook(d: dict) -> Any:
            t = d.get("t")
            if type(t) is str:
                make = get(t)
                if make is not None:
                    return make(d)
            return d

        _hook = hook
    writers = {
        cls: _node_writer(cls)
        for cls in _CLASSES.values()
        if not _is_sum(cls) and cls._encoding is not None
    }
    find = writers.get

    def default(o: Any) -> Any:
        write = find(type(o))
        if write is None:
            raise _Slow
        return write(o)

    _encoder = json.JSONEncoder(default=default, check_circular=False, **_JSON)  # type: ignore[arg-type]
    _ready = True


@too_deep("read")
def loads(text: str | bytes, root: type[Node]) -> Any:
    """A document from pandoc's JSON text."""
    if not _ready:
        _prepare()
    with paused_gc():
        if _hook is not None:
            try:
                j = json.loads(text, object_hook=_hook)
                if _is_product(root):
                    return _product_reader(root)(j)
                if isinstance(j, Node) and isinstance(j, root):
                    return j  # (made by the hook)
            except RecursionError:
                raise
            except Exception:  # noqa: S110  the slow reader takes it, or says what is wrong
                pass
        return root.from_json(json.loads(text))


def node_text(node: Node) -> str | None:
    """A node as pandoc's JSON text, for pickling: None if it isn't the
    plain case (a subclass of a node's class, a number that isn't finite),
    which is pickled field by field."""
    if not _ready:
        _prepare()
    try:
        with paused_gc():
            return _encoder.encode(node)  # type: ignore[union-attr]
    except (_Slow, ValueError, TypeError):
        return None


@too_deep("dumps")
def dumps(doc: Node) -> str:
    """A document (or node) as pandoc's JSON text."""
    if not _ready:
        _prepare()
    with paused_gc():
        try:
            return _encoder.encode(doc)  # type: ignore[union-attr]
        except RecursionError:
            raise
        except Exception:  # noqa: S110  the slow writer takes it, or says what is wrong
            pass
        return json.dumps(doc._to_json(), **_JSON)  # type: ignore[arg-type]


assert Enum  # (the declarations' enum class, for the specs above)
