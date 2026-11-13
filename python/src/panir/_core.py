"""The generic part of the AST: everything that isn't a declaration.

``_types.py`` is generated from the schema and only declares classes: their
fields, as annotations; how they are encoded; their constructors'
signatures. This module reads those declarations (``finalize``) and does the
rest the same way for every class:

- checks every value put into a node, a list or a map of the AST, and says
  where it went wrong (``ASTTypeError``);
- decodes and encodes pandoc's JSON, saying where the JSON is wrong
  (``ASTDecodeError``);
- equality, ``repr`` (constructor syntax, ``eval``-able), copying, pickling.

Nothing here knows a pandoc type by name.
"""

from __future__ import annotations

import enum
import inspect
import math
import re
import sys
import types
import typing
from collections.abc import Iterable, Mapping
from operator import length_hint
from typing import Any, ClassVar

__all__ = [
    "ASTDecodeError",
    "ASTError",
    "ASTTypeError",
    "Enum",
    "Node",
    "NodeDict",
    "NodeList",
    "finalize",
]


# -- errors ------------------------------------------------------------------


class ASTError(Exception):
    """A value that doesn't fit pandoc's AST.

    ``where`` says where, as in ``Para.content[3]`` or, for JSON,
    ``blocks[0].content[3]``; ``expected`` and ``got`` say what.
    """

    def __init__(self, where: str, expected: str, got: str) -> None:
        self.where, self.expected, self.got = where, expected, got
        super().__init__(
            f"{where}: expected {expected}, got {got}"
            if where
            else f"expected {expected}, got {got}"
        )

    def __reduce__(self) -> tuple:  # the default would call __init__ with the message alone
        return (type(self), (self.where, self.expected, self.got))


class ASTTypeError(ASTError, TypeError):
    """A Python value put where the AST doesn't allow it."""


class ASTDecodeError(ASTError, ValueError):
    """JSON that isn't a pandoc document (or node) of this API version.

    ``path`` is the offending value's position in field names and indices,
    e.g. ``("blocks", 0, "content", 3)``.
    """

    def __init__(self, path: tuple, expected: str, got: str) -> None:
        self.path = path
        super().__init__(format_path(path), expected, got)

    def __reduce__(self) -> tuple:
        return (type(self), (self.path, self.expected, self.got))


def format_path(path: Iterable) -> str:
    out = ""
    for p in path:
        if isinstance(p, int):
            out += f"[{p}]"
        elif p.isidentifier() or not out:
            out += f".{p}" if out else p
        else:
            out += f"[{p!r}]"
    return out


class _Bad(Exception):
    """Raised while decoding; the path is collected on the way up."""

    def __init__(self, expected: str, got: Any, got_text: str | None = None) -> None:
        self.expected, self.got, self.path = expected, got, []
        self.got_text = got_text


def _describe(value: Any) -> str:
    """How a value is named in error messages."""
    if isinstance(value, Node):
        kind = _sum_of(type(value))
        if kind is not None and kind.__name__ != type(value).__name__:
            name = kind.__name__.removesuffix("Base")
            return f"{type(value).__name__} ({_article(name)} {name})"
        return type(value).__name__
    if isinstance(value, Enum):
        return repr(value)
    text = repr(value)
    if len(text) > 60:
        text = text[:57] + "..."
    return f"{type(value).__name__} {text}" if not isinstance(value, (list, dict)) else text


def _article(word: str) -> str:
    return "an" if word[0] in "AEIOU" else "a"


def _describe_json(j: Any) -> str:
    if isinstance(j, dict) and isinstance(j.get("t"), str):
        tag = j["t"]
        owner = _TAG_OWNER.get(tag)
        if owner is not None:
            return f"{tag} ({_article(owner)} {owner})"
        return f'"t": {tag!r} (no such constructor)'
    if j is None:
        return "null"
    if isinstance(j, bool):
        return "true" if j else "false"
    text = repr(j)
    return text if len(text) <= 60 else text[:57] + "..."


# -- the base classes ----------------------------------------------------------


class Node:
    """A value of one of pandoc's AST types.

    Fields are checked when set; lists and maps in fields are ``NodeList`` /
    ``NodeDict``, which check what goes into them.
    """

    __slots__ = ()
    __match_args__: ClassVar[tuple[str, ...]] = ()

    # set by the generated declarations
    _encoding: ClassVar[str | None] = None
    _variadic: ClassVar[str | None] = None
    _flatten: ClassVar[tuple[str, ...]] = ()
    _positional: ClassVar[tuple[str, ...]] = ()
    _defaults: ClassVar[dict[str, Any]] = {}
    _keys: ClassVar[dict[str, str]] = {}
    # set by finalize
    _fields: ClassVar[tuple[str, ...]] = ()
    _specs: ClassVar[dict[str, _Spec]] = {}
    _setters: ClassVar[tuple[Any, ...]] = ()
    _spec_list: ClassVar[list[_Spec]] = []
    _tag: ClassVar[str] = ""

    def __init__(self) -> None:
        # a constructor with fields has its own __init__ (generated); what
        # comes here is one without (Space), or a type's own class (Inline)
        if type(self)._encoding is None:
            name = type(self).__name__
            raise TypeError(f"{name} is a type, not a constructor: make one of its subclasses")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__}.{name}: a node's fields can't be deleted")

    def __setattr__(self, name: str, value: Any) -> None:
        spec = type(self)._specs.get(name)
        if spec is None:
            fields = ", ".join(type(self)._fields) or "none"
            raise AttributeError(
                f"{type(self).__name__} has no field {name!r} (its fields: {fields})"
            )
        object.__setattr__(self, name, spec.coerce(value))

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return all(getattr(self, f) == getattr(other, f) for f in self._fields)

    __hash__ = None  # type: ignore[assignment]  # mutable

    def __repr__(self) -> str:
        """Constructor syntax, omitting defaults: ``Header(1, Str('Hi'))``."""
        cls = type(self)
        args = [getattr(self, f) for f in cls._positional]
        while (
            args
            and cls._positional[len(args) - 1] in cls._defaults
            and args[-1] == cls._default_of(cls, cls._positional[len(args) - 1])
        ):
            args.pop()
        out = [repr(v) for v in args]
        if cls._variadic is not None:
            out += [repr(x) for x in getattr(self, cls._variadic)]
        for f in cls._fields:
            if f in cls._positional or f == cls._variadic:
                continue
            value = getattr(self, f)
            if f in cls._flatten:
                inner = type(value)
                for g in inner._fields:
                    v = getattr(value, g)
                    if g not in inner._defaults or v != cls._default_of(inner, g):
                        out.append(f"{g}={v!r}")
            elif f not in cls._defaults or value != cls._default_of(cls, f):
                out.append(f"{f}={value!r}")
        return f"{cls.__name__}({', '.join(out)})"

    @staticmethod
    def _default_of(owner: type[Node], field: str) -> Any:
        return owner._specs[field].decode(owner._defaults[field])

    def __reduce__(self) -> tuple:
        return (_restore, (type(self), tuple(getattr(self, f) for f in self._fields)))

    def __copy__(self) -> Node:
        return _make(type(self), [getattr(self, f) for f in self._fields])

    def to_json(self) -> Any:
        """This value as pandoc's JSON (lists, dicts, str, ...; for json.dumps)."""
        try:
            return _SPEC_OF[_sum_of(type(self)) or type(self)].encode(self)
        except _Bad as e:
            raise ASTTypeError(
                type(self).__name__ + format_path_suffix(e.path), e.expected, _describe(e.got)
            ) from None

    @classmethod
    def from_json(cls, j: Any) -> Any:
        """A value of this type (or constructor) from pandoc's JSON."""
        spec = _SPEC_OF.get(_sum_of(cls) or cls)
        if spec is None:
            raise TypeError(f"{cls.__name__} is not a pandoc type")
        try:
            value = spec.decode(j)
        except _Bad as e:
            raise ASTDecodeError(
                tuple(reversed(e.path)), e.expected, e.got_text or _describe_json(e.got)
            ) from None
        if not isinstance(value, cls):
            raise ASTDecodeError((), cls.__name__, _describe(value))
        return value


def format_path_suffix(path: list) -> str:
    s = format_path(reversed(path))
    return s if s.startswith("[") or not s else "." + s


def _restore(cls: type[Node], values: tuple) -> Node:
    node = object.__new__(cls)
    for f, v in zip(cls._fields, values):
        node.__setattr__(f, v)
    return node


def _make(cls: type[Node], values: list) -> Node:
    """A node from values already checked (or trusted)."""
    node = object.__new__(cls)
    for setter, v in zip(cls._setters, values):
        setter(node, v)
    return node


class Enum(str, enum.Enum):
    """A pandoc type whose constructors have no fields, e.g. ``MathType``."""

    def __repr__(self) -> str:
        return f"{type(self).__name__}.{self.name}"

    __str__ = str.__str__


class NodeList(list):
    """A list in the AST: checks what is put into it.

    Assigning a list to a field copies it into a new ``NodeList``; assigning
    a ``NodeList`` of the right type shares it.
    """

    __slots__ = ("_spec",)

    def __init__(self, spec: _ListSpec, items: Iterable = ()) -> None:
        self._spec = spec
        list.__init__(self, (spec.item.coerce(x, spec.label, i) for i, x in enumerate(items)))

    def _check(self, x: Any, i: int) -> Any:
        return self._spec.item.coerce(x, self._spec.label, i)

    def append(self, x: Any) -> None:
        list.append(self, self._check(x, len(self)))

    def insert(self, i: typing.SupportsIndex, x: Any) -> None:
        list.insert(self, i, self._check(x, int(i)))

    def extend(self, xs: Iterable) -> None:
        n = len(self)
        list.extend(self, [self._check(x, n + i) for i, x in enumerate(_items(xs))])

    def __iadd__(self, xs: Iterable) -> NodeList:  # type: ignore[override]
        self.extend(xs)
        return self

    def __setitem__(self, i: Any, x: Any) -> None:
        if isinstance(i, slice):
            start = i.indices(len(self))[0]
            x = [self._check(v, start + k) for k, v in enumerate(_items(x))]
        else:
            x = self._check(x, i)
        list.__setitem__(self, i, x)

    def copy(self) -> NodeList:
        return _list(self._spec, self)

    def __reduce__(self) -> tuple:
        return (list, (list(self),))

    def __copy__(self) -> list:
        return list(self)

    def __deepcopy__(self, memo: dict) -> list:
        import copy

        return [copy.deepcopy(x, memo) for x in self]

    def __repr__(self) -> str:
        return list.__repr__(self)


def _items(xs: Any) -> Iterable:
    if isinstance(xs, (str, bytes, Node, Mapping)):
        raise TypeError(f"expected a list, got {_describe(xs)}")
    return xs


def _list(spec: _ListSpec, items: Iterable) -> NodeList:
    """A NodeList of values already checked."""
    lst = NodeList.__new__(NodeList)
    lst._spec = spec
    list.__init__(lst, items)
    return lst


class NodeDict(dict):
    """A map in the AST (metadata): checks what is put into it."""

    __slots__ = ("_spec",)

    def __init__(self, spec: _MapSpec, items: Mapping | Iterable = ()) -> None:
        self._spec = spec
        dict.__init__(self)
        self.update(items)

    def __setitem__(self, k: Any, v: Any) -> None:
        if not isinstance(k, str):
            raise ASTTypeError(f"{self._spec.label} key", "a str", _describe(k))
        dict.__setitem__(self, k, self._spec.value.coerce(v, self._spec.label, k))

    def update(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        for k, v in dict(*args, **kwargs).items():
            self[k] = v

    def __ior__(self, other: Any) -> NodeDict:  # type: ignore[override,misc]
        self.update(other)  # dict's own |= doesn't go through __setitem__
        return self

    def setdefault(self, k: Any, v: Any = None) -> Any:
        if k not in self:
            self[k] = v
        return self[k]

    def copy(self) -> NodeDict:
        return _dict(self._spec, self)

    def __reduce__(self) -> tuple:
        return (dict, (dict(self),))

    def __copy__(self) -> dict:
        return dict(self)

    def __deepcopy__(self, memo: dict) -> dict:
        import copy

        return {k: copy.deepcopy(v, memo) for k, v in self.items()}

    def __repr__(self) -> str:
        return dict.__repr__(self)


def _dict(spec: _MapSpec, items: Mapping) -> NodeDict:
    d = NodeDict.__new__(NodeDict)
    d._spec = spec
    dict.update(d, items)
    return d


# -- specs: what a field may hold ---------------------------------------------
#
# Each has coerce (a Python value -> the checked value to store, or
# ASTTypeError), decode (JSON -> value, or _Bad) and encode (value -> JSON,
# or _Bad). ``label`` names the field in errors, e.g. "Para.content".


class _Spec:
    label = ""
    has_nodes = False

    def coerce(self, value: Any, label: str | None = None, key: Any = None) -> Any:
        raise NotImplementedError

    def decode(self, j: Any) -> Any:
        raise NotImplementedError

    def encode(self, value: Any) -> Any:
        raise NotImplementedError

    def fail(self, label: str | None, key: Any, value: Any, hint: str = "") -> typing.NoReturn:
        where = label if label is not None else self.label
        if key is not None:
            where += f"[{key!r}]" if isinstance(key, str) else f"[{key}]"
        raise ASTTypeError(where, self.name(), _describe(value) + hint)

    def name(self) -> str:
        raise NotImplementedError

    def accepts_node_type(self, ty: type) -> bool:
        return False


# pandoc's Int is Haskell's, 64 bits: a bigger one can't be written for it.
_INT_MIN, _INT_MAX = -(2**63), 2**63 - 1
_INT_HINT = " (pandoc's Int has 64 bits)"
# A Double that isn't finite has no JSON number: pandoc (aeson) writes these
# for it, and reads them.
_NOT_FINITE = {"+inf": math.inf, "-inf": -math.inf, None: math.nan}


class _PrimSpec(_Spec):
    NAMES = {str: "a str", int: "an int", float: "a float", bool: "a bool"}
    JSON = {str: "a string", int: "an integer", float: "a number", bool: "a boolean"}

    def __init__(self, ty: type, label: str) -> None:
        self.ty, self.label = ty, label

    def name(self) -> str:
        return self.NAMES[self.ty]

    def coerce(self, value: Any, label: str | None = None, key: Any = None) -> Any:
        ty = self.ty
        if ty is float:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                try:
                    return float(value)
                except OverflowError:
                    self.fail(label, key, value, " (too big for a float)")
        elif isinstance(value, ty) and not (ty is int and isinstance(value, bool)):
            if ty is int and not _INT_MIN <= value <= _INT_MAX:
                self.fail(label, key, value, _INT_HINT)
            return value
        self.fail(label, key, value)

    def decode(self, j: Any) -> Any:
        ty = self.ty
        if type(j) is ty:
            if ty is int and not _INT_MIN <= j <= _INT_MAX:
                raise _Bad(self.JSON[ty] + _INT_HINT, j)
            return j
        if ty is float:
            if type(j) is int:
                try:
                    return float(j)
                except OverflowError:
                    return math.inf if j > 0 else -math.inf  # as pandoc reads 1e999
            if j is None or type(j) is str:
                try:
                    return _NOT_FINITE[j]
                except KeyError:
                    pass
        raise _Bad(self.JSON[ty], j)

    def encode(self, value: Any) -> Any:
        ty = self.ty
        if type(value) is not ty:
            if isinstance(value, bool) or not isinstance(
                value, (int, float) if ty is float else ty
            ):
                raise _Bad(self.NAMES[ty], value)
            try:
                value = ty(value)  # a subclass's value, or an int for a float
            except OverflowError:
                raise _Bad(self.NAMES[ty], value) from None
        if ty is float:
            if not math.isfinite(value):
                return None if value != value else "+inf" if value > 0 else "-inf"
        elif ty is int and not _INT_MIN <= value <= _INT_MAX:
            raise _Bad(self.NAMES[ty] + _INT_HINT, value)
        return value


class _ListSpec(_Spec):
    def __init__(self, item: _Spec, label: str) -> None:
        self.item, self.label = item, label
        self.has_nodes = item.has_nodes

    def name(self) -> str:
        return f"a list of {self.item.name()}"

    def coerce(self, value: Any, label: str | None = None, key: Any = None) -> Any:
        if type(value) is NodeList and value._spec is self:
            return value
        if isinstance(value, Mapping) and isinstance(self.item, _TupleSpec):
            value = list(value.items())  # e.g. attributes given as a dict
        if isinstance(value, str) and isinstance(self.item, _ClassSpec):
            # as pandoc's Lua: a string where a list of inlines goes is its
            # words and spaces, and where a list of blocks goes, Plain text
            item = self.item.cls
            if issubclass(_CLASSES["Str"], item):
                return NodeList(self, text_inlines(value))
            if issubclass(_CLASSES["Plain"], item):
                return NodeList(self, [_new("Plain", *text_inlines(value))])
        if isinstance(value, (str, bytes, Node, Mapping)) or not isinstance(value, Iterable):
            hint = ""
            if self.item.accepts_node_type(type(value)):
                hint = f" (a single {type(value).__name__}; wrap it in a list)"
            self.fail(label, key, value, hint)
        return NodeList(self, value)

    def decode(self, j: Any) -> Any:
        if type(j) is not list:
            raise _Bad("a list", j)
        dec = self.item.decode
        it = iter(j)
        try:
            return _list(self, [dec(x) for x in it])
        except _Bad as e:
            # where the iterator stopped. (Decoding the elements again to
            # find it doubled the work at every level of nesting.)
            e.path.append(len(j) - length_hint(it) - 1)
            raise

    def encode(self, value: Any) -> Any:
        if not isinstance(value, list):
            raise _Bad("a list", value)
        enc = self.item.encode
        it = iter(value)
        try:
            return [enc(x) for x in it]
        except _Bad as e:
            e.path.append(len(value) - length_hint(it) - 1)
            raise


class _MaybeSpec(_Spec):
    def __init__(self, item: _Spec, label: str) -> None:
        self.item, self.label = item, label
        self.has_nodes = item.has_nodes

    def name(self) -> str:
        return f"{self.item.name()} or None"

    def coerce(self, value: Any, label: str | None = None, key: Any = None) -> Any:
        return None if value is None else self.item.coerce(value, label, key)

    def decode(self, j: Any) -> Any:
        return None if j is None else self.item.decode(j)

    def encode(self, value: Any) -> Any:
        return None if value is None else self.item.encode(value)


class _MapSpec(_Spec):
    def __init__(self, value: _Spec, label: str) -> None:
        self.value, self.label = value, label
        self.has_nodes = value.has_nodes

    def name(self) -> str:
        return f"a dict of str to {self.value.name()}"

    def coerce(self, value: Any, label: str | None = None, key: Any = None) -> Any:
        if type(value) is NodeDict and value._spec is self:
            return value
        if not isinstance(value, Mapping):
            self.fail(label, key, value)
        return NodeDict(self, value)

    def decode(self, j: Any) -> Any:
        if type(j) is not dict:
            raise _Bad("an object", j)
        dec = self.value.decode
        out = {}
        for k, x in j.items():
            try:
                out[k] = dec(x)
            except _Bad as e:
                e.path.append(k)
                raise
        return _dict(self, out)

    def encode(self, value: Any) -> Any:
        if not isinstance(value, dict):
            raise _Bad("a dict", value)
        enc = self.value.encode
        out = {}
        for k, x in value.items():
            try:
                if not isinstance(k, str):
                    raise _Bad("a str key", k)
                out[k] = enc(x)
            except _Bad as e:
                e.path.append(k)
                raise
        return out


class _TupleSpec(_Spec):
    def __init__(self, items: list[_Spec], label: str) -> None:
        self.items, self.label = items, label
        self.has_nodes = any(i.has_nodes for i in items)

    def name(self) -> str:
        return f"a {len(self.items)}-tuple ({', '.join(i.name() for i in self.items)})"

    def coerce(self, value: Any, label: str | None = None, key: Any = None) -> Any:
        if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
            self.fail(label, key, value)
        value = tuple(value)
        if len(value) != len(self.items):
            self.fail(label, key, value)
        where = label if label is not None else self.label
        if key is not None:
            where += f"[{key}]"
        return tuple(s.coerce(v, where, i) for i, (s, v) in enumerate(zip(self.items, value)))

    def decode(self, j: Any) -> Any:
        if type(j) is not list or len(j) != len(self.items):
            raise _Bad(f"a list of {len(self.items)}", j)
        out = []
        for i, (s, x) in enumerate(zip(self.items, j)):
            try:
                out.append(s.decode(x))
            except _Bad as e:
                e.path.append(i)
                raise
        return tuple(out)

    def encode(self, value: Any) -> Any:
        if not isinstance(value, tuple) or len(value) != len(self.items):
            raise _Bad(self.name(), value)
        out = []
        for i, (s, x) in enumerate(zip(self.items, value)):
            try:
                out.append(s.encode(x))
            except _Bad as e:
                e.path.append(i)
                raise
        return out


class _EnumSpec(_Spec):
    def __init__(self, cls: type[Enum], label: str) -> None:
        self.cls, self.label = cls, label

    def name(self) -> str:
        return f"a {self.cls.__name__}"

    def coerce(self, value: Any, label: str | None = None, key: Any = None) -> Any:
        if isinstance(value, self.cls):
            return value
        if type(value) is str and value in self.cls.__members__:
            return self.cls[value]
        names = ", ".join(self.cls.__members__)
        self.fail(label, key, value, f" (one of: {names})")

    def decode(self, j: Any) -> Any:
        try:
            return self.cls[j["t"]]
        except (KeyError, TypeError):
            raise _Bad(f"a {self.cls.__name__}", j) from None

    def encode(self, value: Any) -> Any:
        if not isinstance(value, self.cls):
            raise _Bad(self.name(), value)
        return {"t": value.name}


class _ClassSpec(_Spec):
    """A pandoc type that is a class: a sum's base class, or a product."""

    has_nodes = True

    def __init__(self, cls: type[Node], label: str) -> None:
        self.cls, self.label = cls, label

    def name(self) -> str:
        name = self.cls.__name__
        if _is_sum(self.cls):
            return name.removesuffix("Base")
        return f"{_article(name)} {name}"

    def accepts_node_type(self, ty: type) -> bool:
        return issubclass(ty, self.cls)

    def coerce(self, value: Any, label: str | None = None, key: Any = None) -> Any:
        cls = self.cls
        if isinstance(value, cls):
            return value
        convert = getattr(cls, "_convert", None)
        if convert is not None:
            converted = convert(value)
            if converted is not None:
                return converted
        if (
            cls._encoding == "array"
            and _is_product(cls)
            and isinstance(value, (tuple, list))
            and len(value) == len(cls._fields)
        ):
            where = label if label is not None else self.label
            return _make(
                cls, [cls._specs[f].coerce(v, f"{where}.{f}") for f, v in zip(cls._fields, value)]
            )
        if isinstance(value, str) and _is_sum(cls):
            # as pandoc's Lua: a string is a Str where one inline goes, and
            # Plain text where one block goes
            if issubclass(_CLASSES["Str"], cls):
                return _new("Str", value)
            if issubclass(_CLASSES["Plain"], cls):
                return _new("Plain", *text_inlines(value))
        hint = ""
        if isinstance(value, (list, tuple)) and value and all(isinstance(v, cls) for v in value):
            hint = " (a list; this takes one)"
        self.fail(label, key, value, hint)

    def decode(self, j: Any) -> Any:
        return _SPEC_OF[self.cls].decode(j)

    def encode(self, value: Any) -> Any:
        if not isinstance(value, self.cls):
            raise _Bad(self.name(), value)
        return _SPEC_OF[self.cls].encode(value)


# -- codecs of each pandoc type -----------------------------------------------


class _SumCodec:
    """{"t": Con} / {"t": Con, "c": x} / {"t": Con, "c": [x, ...]}."""

    def __init__(self, base: type[Node], cons: list[type[Node]]) -> None:
        self.base = base
        self.cons = {c._tag: c for c in cons}

    def decode(self, j: Any) -> Any:
        try:
            cls = self.cons[j["t"]]
        except (KeyError, TypeError):
            raise _Bad(_ClassSpec(self.base, "").name(), j) from None
        enc = cls._encoding
        specs = cls._spec_list
        if enc == "none":
            return _make(cls, [])
        if "c" not in j:
            raise _Bad(f"{cls.__name__} with contents", j, f"{cls.__name__} without")
        c = j["c"]
        if enc == "value":
            try:
                return _make(cls, [specs[0].decode(c)])
            except _Bad as e:
                e.path.append(cls._fields[0])
                raise
        if type(c) is not list or len(c) != len(specs):
            got = (
                f"{cls.__name__} with {len(c)}"
                if type(c) is list
                else f"{cls.__name__} with {_describe_json(c)}"
            )
            raise _Bad(f"{cls.__name__} with {len(specs)} fields", j, got)
        return _make(cls, _decode_fields(cls, specs, c))

    def encode(self, value: Any) -> Any:
        cls = type(value)
        enc = cls._encoding
        if enc == "none":
            return {"t": cls._tag}
        values = _encode_fields(value)
        if enc == "value":
            return {"t": cls._tag, "c": values[0]}
        if enc is None:  # the type's own class, not one of its constructors'
            raise _Bad(f"one of {_ClassSpec(self.base, '').name()}'s constructors", value)
        return {"t": cls._tag, "c": values}


def _decode_fields(cls: type[Node], specs: list[_Spec], values: Iterable) -> list:
    out = []
    for f, s, x in zip(cls._fields, specs, values):
        try:
            out.append(s.decode(x))
        except _Bad as e:
            e.path.append(f)
            raise
    return out


def _encode_fields(value: Node) -> list:
    out = []
    for f, s in zip(value._fields, value._spec_list):
        try:
            out.append(s.encode(getattr(value, f)))
        except _Bad as e:
            e.path.append(f)
            raise
    return out


class _ProductCodec:
    def __init__(self, cls: type[Node], api_version: tuple[int, ...]) -> None:
        self.cls, self.api_version = cls, api_version

    def decode(self, j: Any) -> Any:
        cls = self.cls
        specs = cls._spec_list
        enc = cls._encoding
        if enc == "array":
            if type(j) is not list or len(j) != len(specs):
                got = f"{len(j)}: {_describe_json(j)}" if type(j) is list else None
                raise _Bad(f"a list of {len(specs)} ({cls.__name__})", j, got)
            return _make(cls, _decode_fields(cls, specs, j))
        if enc == "value":
            try:
                return _make(cls, [specs[0].decode(j)])
            except _Bad as e:
                e.path.append(cls._fields[0])
                raise
        if type(j) is not dict:
            raise _Bad(f"an object ({cls.__name__})", j)
        if enc == "root":
            v = j.get("pandoc-api-version")
            if type(v) is not list or v[:2] != list(self.api_version[:2]):
                err = _Bad(f"pandoc-api-version {'.'.join(map(str, self.api_version[:2]))}.*", v)
                err.path.append("pandoc-api-version")
                raise err
        values = []
        for f in cls._fields:
            key = cls._keys[f]
            if key not in j:
                raise _Bad(f"{cls.__name__} with {key!r}", j, "one without")
            values.append(j[key])
        return _make(cls, _decode_fields(cls, specs, values))

    def encode(self, value: Any) -> Any:
        cls = self.cls
        values = _encode_fields(value)
        enc = cls._encoding
        if enc == "array":
            return values
        if enc == "value":
            return values[0]
        out = {cls._keys[f]: v for f, v in zip(cls._fields, values)}
        if enc == "root":
            out = {"pandoc-api-version": list(self.api_version), **out}
        return out


# -- reading the declarations ---------------------------------------------------

_SPEC_OF: dict[type, Any] = {}  # pandoc type (sum base / product) -> codec
_CLASSES: dict[str, type] = {}
_TAG_OWNER: dict[str, str] = {}


def _is_sum(cls: type) -> bool:
    return cls.__dict__.get("_kind") == "sum"


def _is_product(cls: type) -> bool:
    return cls.__dict__.get("_kind") == "product"


def _sum_of(cls: type) -> type | None:
    """The sum type a constructor class belongs to (None for others)."""
    for base in cls.__mro__:
        if _is_sum(base):
            return base
    return None


def _spec(hint: Any, label: str) -> _Spec:
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if hint in (str, int, float, bool):
        return _PrimSpec(hint, label)
    if origin is list:
        return _ListSpec(_spec(args[0], label + "[*]"), label)
    if origin is dict:
        return _MapSpec(_spec(args[1], label + "[*]"), label)
    if origin is tuple:
        return _TupleSpec([_spec(a, f"{label}[{i}]") for i, a in enumerate(args)], label)
    if origin in (typing.Union, types.UnionType) and len(args) == 2 and type(None) in args:
        inner = args[0] if args[1] is type(None) else args[1]
        return _MaybeSpec(_spec(inner, label), label)
    if isinstance(hint, type) and issubclass(hint, Enum):
        return _EnumSpec(hint, label)
    if isinstance(hint, type) and issubclass(hint, Node):
        return _ClassSpec(hint, label)
    raise TypeError(f"{label}: can't check values of type {hint!r}")


def finalize(namespace: dict[str, Any], api_version: tuple[int, ...]) -> None:
    """Read the declarations in a generated module's namespace."""
    classes = [
        v
        for v in namespace.values()
        if isinstance(v, type)
        and issubclass(v, Node)
        and v is not Node
        and v.__module__ == namespace["__name__"]
    ]
    for cls in classes:
        _CLASSES[cls.__name__] = cls
    for cls in classes:
        if _is_sum(cls):
            continue
        hints = inspect.get_annotations(cls, globals=namespace, eval_str=True)
        cls._fields = tuple(cls.__slots__)
        cls._specs = {f: _spec(hints[f], f"{cls.__name__}.{f}") for f in cls._fields}
        cls._spec_list = [cls._specs[f] for f in cls._fields]
        cls._setters = tuple(cls.__dict__[f].__set__ for f in cls._fields)
        cls._tag = cls.__name__
    for cls in classes:
        if _is_sum(cls):
            cons = [c for c in classes if cls in c.__bases__]
            _SPEC_OF[cls] = _SumCodec(cls, cons)
            for c in cons:
                _TAG_OWNER[c._tag] = cls.__name__.removesuffix("Base")
        elif _is_product(cls):
            _SPEC_OF[cls] = _ProductCodec(cls, api_version)


# -- constructors ---------------------------------------------------------------


def _new(name: str, *args: Any) -> Node:
    """A node of a generated class, by name (``_core`` can't import them)."""
    cls: Any = _CLASSES[name]
    return cls(*args)


def text_inlines(s: str) -> list[Node]:
    """Words and spaces, as pandoc-types' ``text`` (and pandoc's Lua) split
    a string: ``Str`` for each run of non-spaces, and for each run of
    spaces ``SoftBreak`` if it has a newline, else ``Space``."""
    out: list[Node] = []
    for m in _WORDS.finditer(s):
        run = m.group()
        if run[0] in _SPACES:
            out.append(_new("SoftBreak" if "\n" in run or "\r" in run else "Space"))
        else:
            out.append(_new("Str", run))
    return out


_SPACES = " \t\n\r"
_WORDS = re.compile(r"[ \t\n\r]+|[^ \t\n\r]+")


def init(self: Node, values: dict[str, Any]) -> None:
    """Set a node's fields, from a generated ``__init__``.

    ``None`` stands for the field's default where it has one.
    """
    cls = type(self)
    for f in cls._fields:
        v = values[f]
        if (
            f == cls._variadic
            and len(v) == 1
            and isinstance(v[0], str)
            and isinstance(spec := cls._specs[f], _ListSpec)
            and isinstance(spec.item, _ClassSpec)
            and _takes_text(spec.item.cls)
        ):
            v = v[0]  # Para("a b"): the whole list as one string, as in Lua
        if v is None and f in cls._defaults:
            v = cls._specs[f].decode(cls._defaults[f])
        self.__setattr__(f, v)


def _takes_text(cls: type) -> bool:
    """Whether a list of ``cls`` can be given as one string: inlines, blocks."""
    return issubclass(_CLASSES["Str"], cls) or issubclass(_CLASSES["Plain"], cls)


def flat(cls: type[Node], owner: str, field: str, given: Any, parts: dict[str, Any]) -> Any:
    """A product field given whole (``attr=``) or by its parts (``identifier=``)."""
    required = [f for f in cls._fields if f not in cls._defaults]
    if given is None:
        missing = [f for f in required if parts[f] is None]
        if missing:
            raise TypeError(f"{owner}() needs {', '.join(m + '=' for m in missing)} (or {field}=)")
        node = object.__new__(cls)
        init(node, parts)
        return node
    changed = [
        f
        for f, v in parts.items()
        if (v is not None if f in required else cls._specs[f].coerce(v) != Node._default_of(cls, f))
    ]
    if changed:
        raise TypeError(
            f"{owner}(): give either {field}= or {', '.join(c + '=' for c in changed)}, not both"
        )
    return given


if sys.version_info < (3, 11):  # pragma: no cover

    def add_note(exc: BaseException, note: str) -> None:
        exc.__notes__ = [*getattr(exc, "__notes__", []), note]  # type: ignore[attr-defined]
else:

    def add_note(exc: BaseException, note: str) -> None:
        exc.add_note(note)
