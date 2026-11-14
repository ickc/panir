"""Walking the AST: visiting every node, replacing, splicing, deleting.

Generic over the declarations, like ``_core``: a node's children are the
values of its fields whose type can hold nodes.
"""

from __future__ import annotations

import copy
import sys
from collections.abc import Callable, Sequence
from typing import Any, TypeVar, overload

from . import _deep
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
    _sum_of,
    _TupleSpec,
    add_note,
    format_path,
    too_deep,
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
        "_container",
        "_doc",
        "_frames",
        "_keys",
        "_parent",
        "_root",
        "_skip",
        "_top_down",
        "_walker",
        "conversion",
        "field",
        "format",
        "index",
    )

    def __init__(
        self,
        frames: tuple,
        doc: Any,
        conversion: Conversion,
        top_down: bool = False,
        walker: _Walker | None = None,
        root: str = "",
    ) -> None:
        self._frames, self._top_down, self._skip = frames, top_down, False
        self._walker, self._root = walker, root
        # each frame's key now: the walker changes them as it goes on
        self._keys = tuple([f[3] for f in frames])
        self._doc, self.conversion, self.format = doc, conversion, conversion.format
        if frames:
            self._parent, self.field, self._container, key = frames[-1]
            # in a list of lists (a BulletList's items), the place in the
            # innermost list; the rest of the key is only in the path
            self.index = key[-1] if type(key) is tuple else key
        else:
            self._parent = self.field = self._container = self.index = None

    def _mend(self) -> None:
        # the lists being walked made what they are now before any is looked
        # at (_Walker.many makes a list's result aside: _Splice)
        w = self._walker
        if w is not None and w.splicing:
            w.mend()

    @property
    def parent(self) -> Any:
        self._mend()
        return self._parent

    @property
    def container(self) -> Any:
        self._mend()
        return self._container

    @property
    def doc(self) -> Any:
        self._mend()
        return self._doc

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
        """``path`` as text, from the document, or from the fragment being
        walked: ``Para.content[1]``, ``inlines[0]``."""
        path = format_path(self.path)
        if not self._root:
            return path or "the document"
        if not path or path.startswith("["):
            return self._root + path
        return f"{self._root}.{path}"

    @property
    def ancestors(self) -> list[Node]:
        """The nodes above this one, the document first, the parent last."""
        self._mend()
        seen: list[Node] = []
        for parent, *_ in self._frames:
            if parent is not None and (not seen or seen[-1] is not parent):
                seen.append(parent)
        return seen

    def _sibling(self, step: int) -> Any:
        container, index = self._container, self.index
        if not isinstance(container, list) or not isinstance(index, int):
            return None
        i = index + step
        if i < 0:
            return None
        w = self._walker
        if w is not None:
            for sp in w.splicing:
                if sp.lst is container:
                    if len(container) != len(sp.was):  # changed from elsewhere
                        w.mend()
                        break
                    return sp.get(i)  # the list as it is now, without making it so
        return container[i] if i < len(container) else None

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
    """A class's fields that can hold nodes."""
    return [(f, spec) for f, spec, _, _ in _kids(cls)]


# by class: (field, spec, the field's getter, whether a list of nodes)
_KIDS: dict[type, tuple] = {}


def _kids(cls: type[Node]) -> tuple:
    """What of a node of ``cls`` a walk goes into: its fields that can hold nodes."""
    kids = _KIDS.get(cls)
    if kids is None:
        kids = _KIDS[cls] = tuple(
            (
                f,
                spec,
                getattr(cls, f).__get__,
                isinstance(spec, _ListSpec) and isinstance(spec.item, _ClassSpec),
            )
            for f, spec in cls._specs.items()
            if spec.has_nodes
        )
    return kids


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


class _Splice:
    """The walk of a list once one of its nodes was replaced by none or
    several: the list keeps its nodes (but for those replaced by one) while
    the result is made aside, in ``out``; ``lst[at:]`` are the nodes not in it
    yet, the first perhaps being walked. The list as it is now is ``out +
    lst[at:]``: it becomes that (``mend``) at the end of its walk, or when
    anything reaches it through a Context; ``out`` is then None.

    ``was`` is what the list holds as the walk left it, and the nodes it
    went through: something else may have changed it meanwhile (a function,
    through the document it closed over). ``marks``: from the k-th of those
    nodes on, the results of each begin ``off`` after its own place, ``(k,
    off)``, from where that changes."""

    __slots__ = ("at", "lst", "marks", "out", "was")

    def __init__(self, lst: list, out: list, at: int) -> None:
        self.lst, self.at = lst, at
        self.out: list | None = out
        self.was = list.copy(lst)
        self.marks = [(at, len(out) - at)]

    def mend(self) -> None:
        out = self.out
        if out is None:
            return
        lst = self.lst
        if list.__eq__(lst, self.was):  # (nodes as they were, or equal ones)
            list.__setitem__(lst, slice(0, self.at), out)
        else:
            list.__setitem__(lst, slice(None), self.merged(out))
        self.out = None

    def merged(self, out: list) -> list:
        """The list as changed from elsewhere, each node the walk went
        through replaced by what it made of it."""
        at, was = self.at, self.was
        starts, off, marks, m = [], 0, self.marks, 0
        for k in range(at + 1):
            while m < len(marks) and marks[m][0] <= k:
                off = marks[m][1]
                m += 1
            starts.append(k + off)
        walked = {id(was[k]): k for k in range(at)}
        new: list = []
        for x in self.lst:
            k = walked.get(id(x))
            if k is None:
                new.append(x)
            else:
                new.extend(out[starts[k] : starts[k + 1]])
        return new

    def get(self, p: int) -> Any:
        """The node at ``p`` in the list as it is now, or None."""
        out = self.out
        assert out is not None
        if p < len(out):
            return out[p]
        k = self.at + p - len(out)
        return self.lst[k] if k < len(self.lst) else None


Action = Callable[[Node, Context], Any]
# A function and whether it takes the context, for a class of nodes or for
# lists of a class's nodes.
Handler = tuple[Callable[..., Any], bool]


class _Walker:
    """One walk. ``find(cls)`` is the function nodes of a class get, if any
    (with whether it takes the context); ``lists``, by a list's item class,
    the function whole lists get.

    A node costs a dictionary lookup (``plans``: by class, where to go in it
    and what to call), a list a frame, and a ``Context`` is made only for a
    function that takes one.
    """

    def __init__(
        self,
        find: Callable[[type], Handler | None],
        top_down: bool,
        doc: Any,
        conversion: Conversion,
        lists: dict[type, Handler] | None = None,
        splicing: list[_Splice] | None = None,
        root: str = "",
    ) -> None:
        self.find, self.top_down, self.root = find, top_down, root
        # how deep (in frames) to walk in this thread; deeper, in panir's own
        self.hop = _hop_at()
        self.doc, self.conversion = doc, conversion
        self.lists = lists or {}
        self.frames: list = []
        self.plans: dict[type, tuple] = {}
        # the lists being walked whose result is made aside: those of every
        # pass of a filter's walk (a context may be kept from one to the next)
        self.splicing: list[_Splice] = [] if splicing is None else splicing

    def deeper(self, node: Node, kids: tuple | None) -> None:
        """Walk what is in ``node`` in panir's own thread (``_deep``): the
        walk is too deep for this one."""
        hop, self.hop = self.hop, sys.maxsize
        try:
            _deep.retry(
                self.children, lambda: too_deep(f"walk at {self.context().where}"), node, kids
            )
        finally:
            self.hop = hop

    def mend(self) -> None:
        """Make every list being walked what it is now (out + the rest)."""
        for sp in self.splicing:
            sp.mend()
        self.splicing.clear()

    def unsplice(self, sp: _Splice) -> None:
        """Make the list ``sp`` is of what it is now; its walk goes on in place."""
        if sp.out is not None:
            sp.mend()
            self.splicing.remove(sp)

    def plan(self, cls: type) -> tuple:
        """For nodes of ``cls``: (kids, function or None, whether it takes the context)."""
        fn, wants_ctx = self.find(cls) or (None, False)
        plan = self.plans[cls] = (_kids(cls), fn, wants_ctx)
        return plan

    def context(self) -> Context:
        return Context(
            tuple(self.frames), self.doc, self.conversion, self.top_down, self, self.root
        )

    def returned(self, e: BaseException, fn: Callable[..., Any], what: str) -> None:
        """Note on an error in what ``fn`` returned which function, and where."""
        if not getattr(e, "_panir_noted", False):
            where = self.context().where
            add_note(e, f"returned by filter function {fn.__qualname__}, for the {what} at {where}")
            e._panir_noted = True  # type: ignore[attr-defined]

    def call(self, fn: Callable[..., Any], wants_ctx: bool, x: Any, what: str) -> tuple[Any, bool]:
        """``fn`` on ``x``: its result, and whether to skip what is in ``x``.
        An exception from it notes which function, and where."""
        ctx = None
        try:
            if wants_ctx:
                ctx = self.context()
                return fn(x, ctx), ctx._skip
            return fn(x), False
        except Exception as e:
            if not getattr(e, "_panir_noted", False):
                where = (ctx or self.context()).where
                add_note(e, f"in filter function {fn.__qualname__}, on the {what} at {where}")
                e._panir_noted = True  # type: ignore[attr-defined]
            raise

    # a node in a position that holds one node
    def one(self, node: Node, where: str) -> Node:
        kids, fn, wants_ctx = self.plans.get(type(node)) or self.plan(type(node))
        if self.top_down:
            skip = False
            if fn is not None:
                result, skip = self.call(fn, wants_ctx, node, type(node).__name__)
                if result is not None and result is not node:
                    node = self.single(result, where, fn, type(node).__name__)
                    kids = (self.plans.get(type(node)) or self.plan(type(node)))[0]
            if kids and not skip:
                self.children(node, kids)
            return node
        if kids:
            self.children(node, kids)
        if fn is not None:
            result = self.call(fn, wants_ctx, node, type(node).__name__)[0]
            if result is not None:
                node = self.single(result, where, fn, type(node).__name__)
        return node

    def single(self, result: Any, where: str, fn: Callable[..., Any], what: str) -> Node:
        """What a function returned for a node in a position that holds one
        (pandoc's Lua has no functions for these: no conversions)."""
        if isinstance(result, Node):
            return result
        e = ASTTypeError(where, "one node (this position holds one)", _describe(result))
        self.returned(e, fn, what)
        raise e

    def whole(
        self, lst: list, spec: _ListSpec, parent: Node, field: str | None, key: tuple
    ) -> tuple[list, bool]:
        """The list function on ``lst``: the list, or a new one of what it
        returned (its nodes may hold the old one); whether to skip the elements."""
        cls = spec.item.cls  # type: ignore[attr-defined]
        handler = self.lists.get(cls)
        if handler is None:
            return lst, False
        self.frames.append([parent, field, None, key or None])
        what = f"list of {cls.__name__}s"
        try:
            result, skip = self.call(*handler, lst, what)
            if result is not None and result is not lst:
                try:
                    # as pandoc's Lua: a string is its words (Plain text,
                    # where blocks go), one node a list of it
                    if isinstance(result, (Node, str)):
                        result = spec.coerce(result)
                    elif not isinstance(result, (list, tuple)):
                        raise ASTTypeError(
                            spec.label, f"a list of {cls.__name__}s", _describe(result)
                        )
                    lst = spec.cls(spec, _distinct(result))  # checked
                except ASTTypeError as e:
                    self.returned(e, handler[0], what)
                    raise
            return lst, skip
        finally:
            self.frames.pop()

    # the nodes in a list: each may be replaced, deleted, or spliced; then
    # (or first, top-down) the list
    def many(
        self, lst: list, spec: _ListSpec, parent: Node, field: str | None, outer: tuple
    ) -> list:
        """Walk a list's nodes; returns the list, or the one a list function made."""
        top_down = self.top_down
        if self.lists and top_down:
            lst, skip = self.whole(lst, spec, parent, field, outer)
            if skip:
                return lst
        plans, plan = self.plans, self.plan
        item = spec.item.cls  # type: ignore[attr-defined]
        frame = [parent, field, lst, None]
        self.frames.append(frame)
        try:
            i = 0
            while i < len(lst):
                node = lst[i]
                kids, fn, wants_ctx = plans.get(type(node)) or plan(type(node))
                if fn is None:
                    if kids:
                        frame[3] = outer + (i,) if outer else i
                        self.children(node, kids)
                    i += 1
                    continue
                frame[3] = outer + (i,) if outer else i
                if kids and not top_down:
                    self.children(node, kids)
                result, skip = self.call(fn, wants_ctx, node, type(node).__name__)
                if i >= len(lst) or lst[i] is not node:
                    # the function changed the list itself (ctx.container)
                    i = _find(lst, node, spec.label)
                    frame[3] = outer + (i,) if outer else i
                if result is None or result is node:
                    if top_down and kids and not skip:
                        self.children(node, kids)
                    i += 1
                    continue
                if not isinstance(result, item):
                    new = self.several(result, lst, spec, i, fn, type(node).__name__)
                    if len(new) != 1:
                        # none or several: the rest of the walk makes the list aside
                        self.aside(lst, spec, frame, outer, i, new, skip)
                        break
                    result = new[0]
                list.__setitem__(lst, i, result)
                if top_down and not skip:
                    kids = (plans.get(type(result)) or plan(type(result)))[0]
                    if kids:
                        self.children(result, kids)
                i += 1
        finally:
            self.frames.pop()
        if self.lists and not top_down:
            return self.whole(lst, spec, parent, field, outer)[0]
        return lst

    def several(
        self, result: Any, lst: list, spec: _ListSpec, i: int, fn: Callable[..., Any], what: str
    ) -> list:
        """What a function returned for ``lst[i]``, not one node of its
        list's class: none, one or several to put there, converted as
        pandoc's Lua converts them (a string where inlines go is its words,
        where blocks go Plain text; an inline where blocks go, Plain)."""
        try:
            if isinstance(result, Node):
                result = [result]  # (one of another class: converted, or an error)
            elif isinstance(result, str):
                result = spec.coerce(result)
            elif isinstance(result, tuple):
                result = list(result)
            elif not isinstance(result, list):
                raise ASTTypeError(
                    spec.label + f"[{i}]", "a node, a list of nodes or None", _describe(result)
                )
            check = lst._check  # type: ignore[attr-defined]
            return [check(x, i + k) for k, x in enumerate(_distinct(result))]
        except ASTTypeError as e:
            self.returned(e, fn, what)
            raise

    def aside(
        self, lst: list, spec: _ListSpec, frame: list, outer: tuple, i: int, new: list, skip: bool
    ) -> None:
        """The rest of ``many``'s walk once ``lst[i]`` was replaced by ``new``
        (none or several nodes): the result is made aside, in ``out``.

        Replacing in place moved all that follows, every time (quadratic).
        Meanwhile the list keeps the nodes still to walk, whoever looks at it,
        and it becomes out + those at the end, after an error too. Anything
        that reaches it through a Context makes it that first (mend: ``sp.out``
        is then None), as does a change to it from elsewhere (seen by its
        length, or the node's place), and the walk goes on in place (``out``
        None), as before. ``i`` is the node's place in the result; aside, the
        node is ``lst[sp.at]``, and ``i == len(out)`` until it is in ``out``.
        """
        top_down, plans, plan = self.top_down, self.plans, self.plan
        item = spec.item.cls  # type: ignore[attr-defined]
        out: list | None = list.__getitem__(lst, slice(0, i))
        out.extend(new)
        sp = _Splice(lst, out, i + 1)
        self.splicing.append(sp)
        n0 = len(lst)  # (as long as nothing else changes it)
        try:
            n = len(new)
            while True:
                # (here the node's n replacements are in the result, from i)
                if top_down and not skip:
                    for _ in range(n):
                        frame[3] = outer + (i,) if outer else i
                        node = lst[i] if out is None else out[i]
                        kids = (plans.get(type(node)) or plan(type(node)))[0]
                        if kids:
                            self.children(node, kids)
                            if out is not None and (sp.out is None or len(lst) != n0):
                                self.unsplice(sp)
                                out = None  # mended: in place
                        i += 1
                else:
                    i += n
                # the next node
                if out is None:
                    if i >= len(lst):
                        break
                    node = lst[i]
                else:
                    if sp.at >= len(lst):
                        break
                    node = sp.was[sp.at] = lst[sp.at]  # (another may have put it there)
                kids, fn, wants_ctx = plans.get(type(node)) or plan(type(node))
                frame[3] = outer + (i,) if outer else i
                if fn is None:
                    if kids:
                        self.children(node, kids)
                        if out is not None and (
                            sp.out is None or len(lst) != n0 or lst[sp.at] is not node
                        ):
                            self.unsplice(sp)
                            out = None  # mended: in place, the node at i
                    if out is not None:
                        out.append(node)
                        sp.at += 1
                    n, skip = 1, True
                    continue
                if kids and not top_down:
                    self.children(node, kids)
                result, skip = self.call(fn, wants_ctx, node, type(node).__name__)
                if out is not None and (sp.out is None or len(lst) != n0 or lst[sp.at] is not node):
                    self.unsplice(sp)
                    out = None  # mended (below, or by the function): in place
                if out is None and (i >= len(lst) or lst[i] is not node):
                    # the function changed the list itself (ctx.container)
                    i = _find(lst, node, spec.label)
                    frame[3] = outer + (i,) if outer else i
                n = 1
                if result is None or result is node:
                    if out is not None:
                        out.append(node)
                        sp.at += 1
                    continue
                if not isinstance(result, item):
                    new = self.several(result, lst, spec, i, fn, type(node).__name__)
                    n = len(new)
                    if n == 1:
                        result = new[0]
                if n == 1:
                    if out is None:
                        list.__setitem__(lst, i, result)
                    else:
                        list.__setitem__(lst, sp.at, result)  # in the list too
                        sp.was[sp.at] = result
                        out.append(result)
                        sp.at += 1
                    continue
                if out is not None:
                    out.extend(new)
                    sp.at += 1
                    sp.marks.append((sp.at, len(out) - sp.at))
                else:
                    list.__setitem__(lst, slice(i, i + 1), new)
        finally:
            # (all walked, or an error: the list whole either way)
            self.unsplice(sp)

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
                return self.many(value, spec, parent, field, key)
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
                        kids, fn, wants_ctx = self.plans.get(type(x)) or self.plan(type(x))
                        if kids and not self.top_down:
                            self.children(x, kids)
                        result, skip = None, False
                        if fn is not None:
                            result, skip = self.call(fn, wants_ctx, x, type(x).__name__)
                        if isinstance(result, list) and not result:
                            del value[k]
                            continue
                        if result is not None and result is not x:
                            x = self.single(result, f"{spec.label}[{k!r}]", fn, type(x).__name__)  # type: ignore[arg-type]
                            value[k] = x
                            kids = (self.plans.get(type(x)) or self.plan(type(x)))[0]
                        if self.top_down and kids and not skip:
                            self.children(x, kids)
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

    def children(self, node: Node, kids: tuple | None = None) -> None:
        """Walk what is in ``node`` (``kids``: its class's, if already known)."""
        if len(self.frames) > self.hop:
            self.deeper(node, kids)
            return
        for f, spec, get, nodes in _kids(type(node)) if kids is None else kids:
            old = get(node)
            if nodes and type(old) is NodeList:
                new = self.many(old, spec, node, f, ())
                if new is not old:
                    object.__setattr__(node, f, new)  # a checked list, the field's own
                continue
            new = self.value(old, spec, node, f, ())
            if new is not old:
                setattr(node, f, new)


_N = TypeVar("_N", bound=Node)
_L = TypeVar("_L", bound=list)


@overload
def walk(
    node: _L,
    action: Action,
    *,
    top_down: bool = False,
    format: str | None = None,
    conversion: Conversion | None = None,
    doc: Any = None,
    wants: Callable[[type], bool] | None = None,
) -> _L: ...
@overload
def walk(
    node: Node,
    action: Action,
    *,
    top_down: bool = False,
    format: str | None = None,
    conversion: Conversion | None = None,
    doc: Any = None,
    wants: Callable[[type], bool] | None = None,
) -> Node: ...
def _hop_at() -> int:
    """How many frames a walk may go down in the calling thread: what
    Python's recursion limit leaves, at about three calls a frame (and
    some left for the functions it calls)."""
    if _deep.in_deep_thread():
        return sys.maxsize
    depth, f = 0, sys._getframe(1)
    while f is not None:
        depth += 1
        f = f.f_back  # type: ignore[assignment]
    return max(8, (sys.getrecursionlimit() - depth - 300) // 4)


def walk(
    node: Any,
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

    Returns ``node``, or what replaced it. ``node`` may also be a list of
    nodes (``para.content``): each is walked, and the list changed in place
    and returned.
    """
    conv = conversion if conversion is not None else Conversion(format)
    handler = (action, True)
    find = (lambda cls: handler) if wants is None else (lambda cls: handler if wants(cls) else None)
    if isinstance(node, Node):
        # (paths from the document, or else from the node, named)
        root = "" if doc is not None or node._encoding == "root" else type(node).__name__
        w = _Walker(find, top_down, node if doc is None else doc, conv, root=root)
        return w.one(node, type(node).__name__)
    if isinstance(node, NodeList) and isinstance(node._spec.item, _ClassSpec):
        lst, spec = node, node._spec
    elif isinstance(node, list):
        if not node:
            return node
        spec = _nodes_spec(node)
        lst = spec.cls(spec, node)
    else:
        raise TypeError(f"walk() takes a node or a list of nodes, got {_describe(node)}")
    w = _Walker(find, top_down, doc, conv, root=spec.label)
    w.many(lst, spec, None, None, ())  # type: ignore[arg-type]
    if lst is not node:
        node[:] = lst
    return node


_NODES_SPECS: dict[type, _ListSpec] = {}


def _nodes_spec(items: list) -> _ListSpec:
    """How to check a list of nodes not in the AST: by its elements' type
    (the sum type all are of, or their class)."""
    kinds = {_sum_of(type(x)) or type(x) for x in items}
    if len(kinds) != 1 or not issubclass(next(iter(kinds)), Node):
        raise TypeError(f"expected a list of nodes of one type, got {_describe(items)}")
    cls = next(iter(kinds))
    spec = _NODES_SPECS.get(cls)
    if spec is None:
        name = cls.__name__.removesuffix("Base").lower() + "s"
        spec = _NODES_SPECS[cls] = _ListSpec(_ClassSpec(cls, name + "[*]"), name)
    return spec
