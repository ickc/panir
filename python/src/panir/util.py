"""Helpers: text of nodes, metadata to and from Python values."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ._core import Node, NodeList, text_inlines, too_deep
from ._types import (
    Block,
    Cite,
    Code,
    Inline,
    LineBreak,
    Math,
    MetaBlocks,
    MetaBool,
    MetaInlines,
    MetaList,
    MetaMap,
    MetaString,
    MetaValue,
    Note,
    Pandoc,
    Plain,
    Quoted,
    QuoteType,
    RawInline,
    SoftBreak,
    Space,
    Str,
)
from ._walk import Context, _walk_fields, walk

__all__ = [
    "blocks",
    "check_unique",
    "from_python",
    "inlines",
    "items",
    "lookup",
    "stringify",
    "to_python",
]

_QUOTES = {QuoteType.SingleQuote: ("‘", "’"), QuoteType.DoubleQuote: ("“", "”")}


def inlines(text: str) -> list[Inline]:
    """A string's words and spaces, as pandoc's Lua ``pandoc.Inlines``:
    ``Str`` for each word, ``Space`` between, ``SoftBreak`` for a newline.

    Constructors and fields convert a string this way themselves
    (``Para("hello world")``); this is for building lists.
    """
    return text_inlines(text)  # type: ignore[return-value]


def blocks(text: str) -> list[Block]:
    """A string as blocks, as pandoc's Lua ``pandoc.Blocks``: one ``Plain``
    of its words and spaces. (To parse markup, use libpandoc:
    ``libpandoc.read(text, ctx.conversion)``.)"""
    out: list[Block] = [Plain(*inlines(text))]
    return out


@too_deep("stringify")
def stringify(value: Any) -> str:
    """The text of a node (or list of nodes), without markup, exactly as
    pandoc's Lua ``pandoc.utils.stringify``.

    Spaces and breaks become " ", quotes curly quotes, notes and citations'
    data are dropped, code and math keep their text. Metadata: a map's
    values in the order of their keys, a ``MetaBool`` ``"true"`` or
    ``"false"``; but a whole document is the text of its inlines only (in
    the metadata too: not its ``MetaString`` and ``MetaBool`` values).
    """
    return _stringify(value)


def _stringify(value: Any) -> str:
    out: list[str] = []
    _text(value, out, isinstance(value, Pandoc))
    return "".join(out)


def _text(value: Any, out: list[str], inlines_only: bool) -> None:
    """``value``'s text into ``out``; ``inlines_only``: that of its inlines
    only (pandoc's ``Shared.stringify``, which ``stringify`` is on a
    document; on metadata, it is what Lua's own is)."""
    if isinstance(value, (list, tuple)):
        for x in value:
            _text(x, out, inlines_only)
        return
    if isinstance(value, dict):
        for k in sorted(value):  # (pandoc's Map: in the order of its keys)
            _text(value[k], out, inlines_only)
        return
    if not isinstance(value, Node):
        return
    if isinstance(value, Str):
        out.append(value.text)
    elif isinstance(value, (Space, SoftBreak, LineBreak)):
        out.append(" ")
    elif isinstance(value, (Code, Math)):
        out.append(value.text)
    elif isinstance(value, RawInline):
        if value.format == "html" and value.text.startswith("<br"):
            out.append(" ")
    elif isinstance(value, Note):
        pass
    elif isinstance(value, Cite):
        _text(value.content, out, inlines_only)
    elif isinstance(value, Quoted):
        left, right = _QUOTES[value.quote_type]
        out.append(left)
        _text(value.content, out, inlines_only)
        out.append(right)
    elif isinstance(value, MetaString):
        if not inlines_only:
            out.append(value.text)
    elif isinstance(value, MetaBool):
        if not inlines_only:
            out.append("true" if value.value else "false")
    else:
        for f, _ in _walk_fields(type(value)):
            _text(getattr(value, f), out, inlines_only)


def lookup(
    value: Mapping[str, MetaValue] | MetaValue | None, key: str, /, *keys: str
) -> MetaValue | None:
    """The metadata value at a path of keys, each but the last in a map:
    Lua's ``((meta.a or {}).b or {}).c``, ``None`` where a key is missing or
    a value on the way is not a map (where Lua would fail, on ``true``, or
    find a method, on a string or a list). ``value`` is the metadata, a
    ``MetaMap``, or ``None``.

        depth = lookup(doc.meta, "toc", "depth")
    """
    found: MetaValue | None = None
    for k in (key, *keys):
        if isinstance(value, MetaMap):
            found = value.content.get(k)
        elif isinstance(value, Mapping):
            found = value.get(k)
        else:
            return None
        value = found
    return found


def items(value: MetaValue | None) -> list[MetaValue]:
    """One value or a list of them, as a list: a ``MetaList``'s items,
    nothing for ``None``, any other value alone (a filter's ``ensure_list``;
    the shapes ``header-includes`` takes). A new list, of the document's
    values.

        for name in items(doc.meta.get("author")): ...
    """
    if value is None:
        return []
    if isinstance(value, MetaList):
        return list(value.content)
    return [value]


@too_deep("to_python")
def to_python(value: MetaValue | Mapping[str, MetaValue]) -> Any:
    """Metadata as plain Python: str, bool, list, dict.

    ``MetaInlines`` and ``MetaBlocks`` become their text (``stringify``).
    """
    return _to_python(value)


def _to_python(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _to_python(v) for k, v in value.items()}
    if isinstance(value, MetaMap):
        return {k: _to_python(v) for k, v in value.content.items()}
    if isinstance(value, MetaList):
        return [_to_python(v) for v in value.content]
    if isinstance(value, MetaBool):
        return value.value
    if isinstance(value, MetaString):
        return value.text
    if isinstance(value, (MetaInlines, MetaBlocks)):
        return stringify(value.content)
    raise TypeError(f"not metadata: {value!r}")


@too_deep("from_python")
def from_python(value: Any) -> MetaValue | None:
    """A Python value as metadata, or None if it has no obvious form.

    str -> MetaString, bool -> MetaBool, list -> MetaList, dict -> MetaMap,
    int and float -> MetaString (YAML numbers read by pandoc are strings too).
    Setting metadata converts with this: ``doc.meta["draft"] = True``.
    """
    return _from_python(value)


def _from_python(value: Any) -> MetaValue | None:
    if isinstance(value, MetaValue):
        return value
    if isinstance(value, bool):
        return MetaBool(value)
    if isinstance(value, str):
        return MetaString(value)
    if isinstance(value, (int, float)):
        return MetaString(str(value))
    if isinstance(value, Mapping):
        return MetaMap({k: _meta(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)) and not isinstance(value, NodeList):
        return MetaList(*[_meta(v) for v in value])
    return None


def _meta(value: Any) -> Any:
    converted = _from_python(value)
    return value if converted is None else converted


MetaValue._convert = staticmethod(from_python)  # type: ignore[attr-defined]


def check_unique(x: Any) -> None:
    """Check that no node is in two places of ``x`` (a document, a node, or
    a list of them): ``ValueError`` naming the first such node and both
    places. A node put in a second place must be a copy, or whatever changes
    one later changes both; that is the filter's to get right, and this is
    for its tests and for debugging (it is called by nothing in panir).

    By identity: two equal nodes are fine. A list (or a map) in two places
    is found by the nodes in it; an empty one, holding none, is not. Strings
    and numbers can't be changed in place, nor can nodes without fields
    (``Space``): they don't count."""
    seen: dict[int, str] = {}

    def check(node: Node, ctx: Context) -> None:
        if not node._fields:  # (a Space: nothing in it to change)
            return
        here = ctx.where
        first = seen.setdefault(id(node), here)
        if first != here:
            raise ValueError(
                f"the {type(node).__name__} at {first} is also at {here}: "
                "copy it before putting it in a second place"
            )

    # (top-down: a node in itself is found before the walk goes round it)
    walk(x, check, top_down=True)
