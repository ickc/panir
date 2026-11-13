"""Walking the AST: visiting every node, replacing, splicing, deleting.

Generic over the declarations, like ``_core``: a node's children are the
values of its fields whose type can hold nodes.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Sequence
from typing import Any

from ._core import (
    ASTTypeError,
    Node,
    NodeList,
    _ClassSpec,
    _describe,
    _ListSpec,
    _MapSpec,
    _MaybeSpec,
    _Spec,
    _TupleSpec,
    format_path,
)
from .conversion import Conversion

__all__ = ["Context", "walk"]


class Context:
    """Where a node is, for a filter function that asks for it.

    ``parent`` is the node whose field holds this one (``field``),
    ``container`` the list it is in (or the map, or ``None``), and ``index``
    its position there (or key). ``next`` and ``prev`` are its neighbours in
    that list. ``path`` is its position from the document, as in errors:
    ``blocks[3].content[1]``. A context stays right when kept after the call.

    A function may change ``container`` itself (remove the node before this
    one, say); it may not take its own node out of it (return ``[]``).

    ``conversion`` is the pandoc run the filter is part of (``format`` is its
    output format's name); ``libpandoc.read(text, ctx.conversion)`` parses a
    fragment as that run reads its input.
    """

    __slots__ = (
        "_frames",
        "_keys",
        "_skip",
        "_top_down",
        "container",
        "conversion",
        "doc",
        "field",
        "format",
        "index",
        "parent",
    )

    def __init__(
        self, frames: tuple, doc: Any, conversion: Conversion, top_down: bool = False
    ) -> None:
        self._frames, self._top_down, self._skip = frames, top_down, False
        # each frame's key now: the walker changes them as it goes on
        self._keys = tuple([f[3] for f in frames])
        self.doc, self.conversion, self.format = doc, conversion, conversion.format
        if frames:
            self.parent, self.field, self.container, key = frames[-1]
            # in a list of lists (a BulletList's items), the place in the
            # innermost list; the rest of the key is only in the path
            self.index = key[-1] if type(key) is tuple else key
        else:
            self.parent = self.field = self.container = self.index = None

    @property
    def path(self) -> tuple:
        out: list = []
        for (_, field, _, _), key in zip(self._frames, self._keys):
            if field is not None:
                out.append(field)
            if key is not None:
                out.extend(key if isinstance(key, tuple) else (key,))
        return tuple(out)

    @property
    def where(self) -> str:
        return format_path(self.path) or "the document"

    @property
    def ancestors(self) -> list[Node]:
        """The nodes above this one, the document first, the parent last."""
        seen: list[Node] = []
        for parent, *_ in self._frames:
            if not seen or seen[-1] is not parent:
                seen.append(parent)
        return seen

    def _sibling(self, step: int) -> Any:
        if not isinstance(self.container, list) or not isinstance(self.index, int):
            return None
        i = self.index + step
        return self.container[i] if 0 <= i < len(self.container) else None

    @property
    def next(self) -> Any:
        return self._sibling(1)

    @property
    def prev(self) -> Any:
        return self._sibling(-1)

    def skip_children(self) -> None:
        """Don't walk this node's children, or its replacement's (Lua's
        ``return el, false``). Only top-down: otherwise the children were
        walked first."""
        if not self._top_down:
            raise RuntimeError(
                'skip_children() needs traverse="topdown" '
                "(otherwise the children were walked first)"
            )
        self._skip = True

    def __repr__(self) -> str:
        parent = type(self.parent).__name__ if self.parent is not None else None
        return f"<Context at {self.where}, parent {parent}>"


def _walk_fields(cls: type[Node]) -> list[tuple[str, _Spec]]:
    fields = cls.__dict__.get("_walk_fields_cache")
    if fields is None:
        fields = [(f, s) for f, s in cls._specs.items() if s.has_nodes]
        type.__setattr__(cls, "_walk_fields_cache", fields)
    return fields


def _distinct(items: Sequence) -> Sequence:
    """``items``, each node once: one that is there twice is copied where
    it comes again.

    As in pandoc's Lua filters, whose results are values: the nodes of
    ``return [el, el]`` are two, changed separately from then on. (One node
    in two places of a document would be changed in both by whatever
    changes it later.)
    """
    if len(items) < 2:
        return items
    seen: set[int] = set()
    out: list | None = None
    for k, x in enumerate(items):
        if id(x) not in seen:
            seen.add(id(x))
        elif isinstance(x, Node):
            if out is None:
                out = list(items)
            out[k] = copy.deepcopy(x)
    return items if out is None else out


def _find(lst: list, node: Node, label: str) -> int:
    """Where ``node`` is in a list its function changed."""
    for i, x in enumerate(lst):
        if x is node:
            return i
    raise RuntimeError(
        f"{label}: a filter function took its own node out of the list it is in "
        "(ctx.container); to delete the node, return []"
    )


Action = Callable[[Node, Context], Any]
# on a list of nodes of the given class: its replacement, or None
ListAction = Callable[[list, type, Context], Any]


class _Walker:
    def __init__(
        self,
        action: Action,
        top_down: bool,
        doc: Any,
        conversion: Conversion,
        wants: Callable[[type], bool],
        lists: ListAction | None = None,
        wants_list: Callable[[type], bool] | None = None,
    ) -> None:
        self.action, self.top_down = action, top_down
        self.doc, self.conversion, self.wants = doc, conversion, wants
        self.lists, self.wants_list = lists, wants_list or (lambda ty: False)
        self.frames: list = []

    def context(self) -> Context:
        return Context(tuple(self.frames), self.doc, self.conversion, self.top_down)

    def call(self, node: Node) -> tuple[Any, bool]:
        """The action on ``node``: its result, and whether to skip the children."""
        ctx = self.context()
        return self.action(node, ctx), ctx._skip

    # a node in a position that holds one node
    def one(self, node: Node, where: str) -> Node:
        if self.top_down:
            skip = False
            if self.wants(type(node)):
                result, skip = self.call(node)
                node = self.single(result, node, where)
            if not skip:
                self.children(node)
            return node
        self.children(node)
        if self.wants(type(node)):
            node = self.single(self.call(node)[0], node, where)
        return node

    @staticmethod
    def single(result: Any, node: Node, where: str) -> Node:
        if result is None:
            return node
        if isinstance(result, Node):
            return result
        raise ASTTypeError(where, "one node (this position holds one)", _describe(result))

    def whole(
        self, lst: list, spec: _ListSpec, parent: Node, field: str | None, key: tuple
    ) -> bool:
        """The list function on ``lst``, replacing its contents; whether to skip its elements."""
        cls = spec.item.cls  # type: ignore[attr-defined]
        if self.lists is None or not self.wants_list(cls):
            return False
        self.frames.append([parent, field, None, key or None])
        try:
            ctx = self.context()
            result = self.lists(lst, cls, ctx)
            if result is not None and result is not lst:
                if isinstance(result, (Node, str)) or not isinstance(result, (list, tuple)):
                    raise ASTTypeError(spec.label, f"a list of {cls.__name__}s", _describe(result))
                lst[:] = _distinct(result)  # checked by NodeList
            return ctx._skip
        finally:
            self.frames.pop()

    # the nodes in a list: each may be replaced, deleted, or spliced; then
    # (or first, top-down) the list
    def many(
        self, lst: list, spec: _ListSpec, parent: Node, field: str | None, outer: tuple
    ) -> None:
        if self.top_down and self.whole(lst, spec, parent, field, outer):
            return
        frame = [parent, field, lst, None]
        self.frames.append(frame)
        try:
            i = 0
            while i < len(lst):
                frame[3] = outer + (i,) if outer else i
                node = lst[i]
                if not self.top_down:
                    self.children(node)
                if self.wants(type(node)):
                    result, skip = self.call(node)
                    if i >= len(lst) or lst[i] is not node:
                        # the function changed the list itself (ctx.container)
                        i = _find(lst, node, spec.label)
                        frame[3] = outer + (i,) if outer else i
                    if result is None and skip:
                        i += 1
                        continue
                    if result is not None:
                        if isinstance(result, Node):
                            result = [result]
                        elif isinstance(result, tuple):
                            result = list(result)
                        elif not isinstance(result, list):
                            raise ASTTypeError(
                                spec.label + f"[{i}]",
                                "a node, a list of nodes or None",
                                _describe(result),
                            )
                        lst[i : i + 1] = _distinct(result)  # checked by NodeList
                        if not self.top_down or skip:
                            i += len(result)
                            continue
                        for _ in range(len(result)):
                            frame[3] = outer + (i,) if outer else i
                            self.children(lst[i])
                            i += 1
                        continue
                if self.top_down:
                    self.children(node)
                i += 1
        finally:
            self.frames.pop()
        if not self.top_down:
            self.whole(lst, spec, parent, field, outer)

    def value(self, value: Any, spec: _Spec, parent: Node, field: str | None, key: tuple) -> Any:
        """Walk a field's value; returns it, or its replacement."""
        if isinstance(spec, _ClassSpec):
            self.frames.append([parent, field, None, key or None])
            try:
                return self.one(value, spec.label)
            finally:
                self.frames.pop()
        if isinstance(spec, _MaybeSpec):
            return None if value is None else self.value(value, spec.item, parent, field, key)
        if isinstance(spec, _ListSpec):
            if isinstance(spec.item, _ClassSpec):
                if type(value) is not NodeList:
                    value = spec.coerce(value)
                self.many(value, spec, parent, field, key)
            else:
                for i, x in enumerate(value):
                    new = self.value(x, spec.item, parent, field, key + (i,))
                    if new is not x:
                        list.__setitem__(value, i, new)
            return value
        if isinstance(spec, _MapSpec):
            # in the order of the keys, as pandoc walks its Map
            for k in sorted(value):
                if k not in value:  # a function removed it
                    continue
                x = value[k]
                if isinstance(spec.value, _ClassSpec):
                    self.frames.append([parent, field, value, key + (k,)])
                    try:
                        if not self.top_down:
                            self.children(x)
                        result, skip = self.call(x) if self.wants(type(x)) else (None, False)
                        if isinstance(result, list) and not result:
                            del value[k]
                            continue
                        x = self.single(result, x, f"{spec.label}[{k!r}]")
                        value[k] = x
                        if self.top_down and not skip:
                            self.children(x)
                    finally:
                        self.frames.pop()
                else:
                    new = self.value(x, spec.value, parent, field, key + (k,))
                    if new is not x:
                        dict.__setitem__(value, k, new)
            return value
        if isinstance(spec, _TupleSpec):
            items = list(value)
            changed = False
            for i, (x, s) in enumerate(zip(items, spec.items)):
                if s.has_nodes:
                    new = self.value(x, s, parent, field, key + (i,))
                    if new is not x:
                        items[i] = new
                        changed = True
            return spec.coerce(items) if changed else value
        return value

    def children(self, node: Node) -> None:
        for f, spec in _walk_fields(type(node)):
            old = getattr(node, f)
            new = self.value(old, spec, node, f, ())
            if new is not old:
                setattr(node, f, new)


def walk(
    node: Node,
    action: Action,
    *,
    top_down: bool = False,
    format: str | None = None,
    conversion: Conversion | None = None,
    doc: Any = None,
    wants: Callable[[type], bool] | None = None,
) -> Any:
    """Apply ``action(node, ctx)`` to ``node`` and every node in it.

    Bottom-up by default: a node's children before the node. ``action``
    returns ``None`` to keep the node (changed in place or not), a node to
    replace it, or, in a list, a list of nodes to splice in its place (``[]``
    deletes it). Top-down, children are those of the replacement.

    ``conversion`` (or just its output ``format``) is what ``ctx.conversion``
    tells the action.

    Returns ``node``, or what replaced it.
    """
    conv = conversion if conversion is not None else Conversion(format)
    w = _Walker(action, top_down, node if doc is None else doc, conv, wants or (lambda ty: True))
    return w.one(node, type(node).__name__)
