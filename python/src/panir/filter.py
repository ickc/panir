"""Filters: functions on node types, run as a pandoc filter or in Python.

    from panir import Filter, Header

    f = Filter()

    @f.on(Header)
    def demote(h):
        h.level += 1

    if __name__ == "__main__":
        f.main()  # pandoc --filter ./demote.py

The same ``f`` also runs in Python: ``f(doc)`` on a ``Pandoc``, or
``libpandoc.convert(..., filters=[f])``, in process. A function that takes
a ``Context`` knows the conversion (``ctx.conversion``; see
``panir.conversion``).
"""

from __future__ import annotations

import inspect
import sys
from collections.abc import Callable, Iterable, Sequence
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from . import _fast
from ._core import Node, add_note
from ._types import Block, Inline, Meta, Pandoc
from ._walk import Context, _walk_fields, _Walker
from .conversion import Conversion

if TYPE_CHECKING:
    from ._types import _typed_on

__all__ = ["Filter", "run"]

# Set by a program that runs filter scripts in its own process (pandocpy,
# libpandoc): a script's ``f.main()`` then hands ``f`` to it, instead of
# reading stdin and writing stdout, so that it runs on the program's own
# document objects. Per thread (a context variable), so that scripts may run
# in several threads at once.
handoff: ContextVar[Callable[[Filter], None] | None] = ContextVar(
    "panir_filter_handoff", default=None
)

F = TypeVar("F", bound=Callable[..., Any])

Traverse = Literal["typewise", "topdown", "bottomup"]
_TRAVERSES = ("typewise", "topdown", "bottomup")

_L = TypeVar("_L", Inline, Block)
_ListFn = (
    Callable[[list[_L]], Sequence[_L] | None] | Callable[[list[_L], Context], Sequence[_L] | None]
)
_MetaFn = Callable[[Meta], Meta | None] | Callable[[Meta, Context], Meta | None]


class Filter:
    """Functions to apply to nodes, by type.

    A function takes the node, and optionally a ``Context`` (where the node
    is, the document, the output format), and returns ``None`` to keep the
    node, a node to replace it, or a list of nodes to splice in its place
    (``[]`` deletes it).

    Each node gets the function registered for its class, or else for the
    closest base class (``Inline``, ``Block``, ...); ``Pandoc`` itself too.
    As in pandoc's Lua filters, ``on_inlines`` and ``on_blocks`` register a
    function for every list of them, and ``on_meta`` one for the metadata;
    each returns a replacement, or ``None`` to keep it.

    ``traverse`` is the order:

    - ``"typewise"`` (the default, as pandoc's Lua filters and Haskell's
      ``walk``): one walk per kind, each bottom-up: every ``Inline``, then
      every list of inlines, then every ``Block``, then every list of blocks,
      then the other nodes (``MetaValue``, ``Cell``...), then the metadata,
      then ``Pandoc``.
    - ``"topdown"`` (Lua's other order, and pandocfilters'): ``Pandoc``, the
      metadata, then from the root down, a list before its elements and a
      node before its children, which are walked in its replacement too,
      unless the function calls ``ctx.skip_children()``.
    - ``"bottomup"`` (panflute's): one walk, each node after its children, a
      list after its elements, the metadata after what is in it, ``Pandoc``
      last.
    """

    def __init__(self, *, traverse: Traverse = "typewise", name: str | None = None) -> None:
        if traverse not in _TRAVERSES:
            raise ValueError(f"traverse: expected one of {_TRAVERSES}, got {traverse!r}")
        self.traverse: Traverse = traverse
        self.name = name
        self._handlers: dict[type, tuple[Callable[..., Any], bool]] = {}
        self._lookup: dict[type, tuple[Callable[..., Any], bool] | None] = {}
        # the functions on lists, by the class of their items; on the metadata
        self._lists: dict[type, tuple[Callable[..., Any], bool]] = {}
        self._meta: tuple[Callable[..., Any], bool] | None = None

    def _on(self, *types: type) -> Callable[[F], F]:
        """Register the decorated function for nodes of these types."""
        if not types:
            raise TypeError("Filter.on() takes the AST classes the function is for")
        for ty in types:
            if not (isinstance(ty, type) and issubclass(ty, Node)):
                raise TypeError(f"Filter.on() takes AST classes, got {ty!r}")

        def register(fn: F) -> F:
            wants_ctx = _takes_context(fn)
            for ty in types:  # all or none
                if ty in self._handlers:
                    raise ValueError(
                        f"{ty.__name__} already has a function in this "
                        f"filter ({self._handlers[ty][0].__name__}); "
                        "combine them, or use a second Filter"
                    )
            for ty in types:
                self._handlers[ty] = (fn, wants_ctx)
            self._lookup.clear()
            return fn

        return register

    if TYPE_CHECKING:
        # typed per family of nodes (generated): a function for an Inline
        # must take it and return inlines, ...
        on = _typed_on
    else:
        on = _on

    def _on_list(self, cls: type, fn: Any) -> Any:
        if cls in self._lists:
            raise ValueError(
                f"lists of {cls.__name__}s already have a function in this filter "
                f"({self._lists[cls][0].__name__}); combine them, or use a second Filter"
            )
        self._lists[cls] = (fn, _takes_context(fn))
        return fn

    def on_inlines(self, fn: _ListFn[Inline]) -> _ListFn[Inline]:
        """Register the decorated function for every list of inlines."""
        return self._on_list(Inline, fn)

    def on_blocks(self, fn: _ListFn[Block]) -> _ListFn[Block]:
        """Register the decorated function for every list of blocks."""
        return self._on_list(Block, fn)

    def on_meta(self, fn: _MetaFn) -> _MetaFn:
        """Register the decorated function for the document's metadata."""
        if self._meta is not None:
            raise ValueError(
                f"the metadata already has a function in this filter ({self._meta[0].__name__})"
            )
        self._meta = (fn, _takes_context(fn))
        return fn

    def _handler(self, ty: type) -> tuple[Callable[..., Any], bool] | None:
        try:
            return self._lookup[ty]
        except KeyError:
            found = next((self._handlers[t] for t in ty.__mro__ if t in self._handlers), None)
            self._lookup[ty] = found
            return found

    def __call__(
        self, doc: Pandoc, format: str | None = None, *, conversion: Conversion | None = None
    ) -> Pandoc:
        """Apply the filter to a document (in place), and return it.

        ``conversion`` describes the pandoc run (``ctx.conversion``); given
        only ``format``, that is all it knows.
        """
        if self.traverse not in _TRAVERSES:
            raise ValueError(f"traverse: expected one of {_TRAVERSES}, got {self.traverse!r}")
        conv = conversion if conversion is not None else Conversion(format)
        top_down = self.traverse == "topdown"
        fields = _walk_fields(Pandoc)

        def walker(only: Callable[[type], bool], lists: Callable[[type], bool] | None) -> _Walker:
            """A walk calling the functions of the nodes ``only`` accepts,
            and of the lists (by their items' class) ``lists`` accepts."""
            handler = self._handler
            return _Walker(
                lambda cls: handler(cls) if only(cls) else None,
                top_down,
                doc,
                conv,
                {} if lists is None else {c: h for c, h in self._lists.items() if lists(c)},
            )

        def root(d: Pandoc) -> tuple[Pandoc, bool]:
            handler = self._handler(Pandoc)
            if handler is None:
                return d, False
            ctx = Context((), d, conv, top_down)
            result = _call(*handler, d, ctx, "Pandoc")
            if result is None:
                return d, ctx._skip
            if not isinstance(result, Pandoc):
                raise TypeError(
                    f"the function for Pandoc returned {type(result).__name__}, not a Pandoc"
                )
            return result, ctx._skip

        def meta(d: Pandoc) -> bool:
            if self._meta is None:
                return False
            ctx = Context(((d, "meta", None, None),), d, conv, top_down)
            result = _call(*self._meta, d.meta, ctx, "metadata")
            if result is not None:
                d.meta = result
            return ctx._skip

        def walk_fields(
            w: _Walker, d: Pandoc, skip_meta: bool = False, after_meta: Any = None
        ) -> None:
            for f, spec in fields:
                if f == "meta" and skip_meta:
                    continue
                old = getattr(d, f)
                new = w.value(old, spec, d, f, ())
                if new is not old:
                    setattr(d, f, new)
                if f == "meta" and after_meta is not None:
                    after_meta(d)

        not_root = lambda ty: ty is not Pandoc  # noqa: E731
        if top_down:
            doc, skip = root(doc)
            if not skip:
                skip_meta = meta(doc)
                walk_fields(walker(not_root, self._lists.__contains__), doc, skip_meta=skip_meta)
            return doc
        if self.traverse == "bottomup":
            walk_fields(walker(not_root, self._lists.__contains__), doc, after_meta=meta)
            return root(doc)[0]
        passes: list[tuple[Callable[[type], bool], Callable[[type], bool] | None, bool]] = [
            (_is(Inline), None, self._has(Inline)),
            (_none, lambda ty: ty is Inline, Inline in self._lists),
            (_is(Block), None, self._has(Block)),
            (_none, lambda ty: ty is Block, Block in self._lists),
            (_other, None, any(_other(ty) for ty in self._handlers)),
        ]
        for only, lists, needed in passes:
            if needed:
                walk_fields(walker(only, lists), doc)
        meta(doc)
        return root(doc)[0]

    def _has(self, base: type) -> bool:
        """Whether a function is registered for ``base`` or a class of it."""
        return any(issubclass(ty, base) or issubclass(base, ty) for ty in self._handlers)

    def run_json(
        self,
        text: str | bytes,
        format: str | None = None,
        *,
        conversion: Conversion | None = None,
    ) -> str:
        """Apply the filter to a document in pandoc's JSON."""
        doc = _fast.loads(text, Pandoc)
        out = self(doc, format, conversion=conversion)
        return _fast.dumps(out)

    def main(self, argv: list[str] | None = None) -> None:
        """Run as a pandoc JSON filter: a document on stdin, to stdout.

        pandoc passes the output format as the first argument, and the
        reader's options in the environment. While the filter's functions
        run, ``print`` writes to stderr: pandoc reads stdout as the document.
        """
        take = handoff.get()
        if take is not None:
            take(self)  # a program running this script in its own process
            return
        conversion = Conversion.from_environment(argv)
        stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
        text = stdin.read()
        sys.stdout = sys.stderr
        try:
            # (a process that ends with the document: nothing to collect)
            with _fast.paused_gc():
                out = self.run_json(text, conversion=conversion)
        finally:
            sys.stdout = sys.__stdout__
        stdout.write(out.encode("utf-8"))
        stdout.flush()


def run(
    doc: Pandoc,
    filters: Iterable[Filter],
    format: str | None = None,
    *,
    conversion: Conversion | None = None,
) -> Pandoc:
    """Apply filters in turn, as ``pandoc --filter a --filter b`` does."""
    for f in filters:
        doc = f(doc, format, conversion=conversion)
    return doc


def _call(fn: Callable[..., Any], wants_ctx: bool, x: Any, ctx: Context, what: str) -> Any:
    """``fn`` on ``x``; an exception from it notes which function and where."""
    try:
        return fn(x, ctx) if wants_ctx else fn(x)
    except Exception as e:
        if not getattr(e, "_panir_noted", False):
            add_note(e, f"in filter function {fn.__qualname__}, on the {what} at {ctx.where}")
            e._panir_noted = True  # type: ignore[attr-defined]
        raise


def _is(base: type) -> Callable[[type], bool]:
    return lambda ty: issubclass(ty, base)


def _other(ty: type) -> bool:
    return not issubclass(ty, (Inline, Block)) and ty is not Pandoc


def _none(ty: type) -> bool:
    return False


def _takes_context(fn: Callable[..., Any]) -> bool:
    try:
        params = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return False
    positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    # a parameter with a default is the function's own (f(x, mark="*")), not
    # one for the context
    required = [p for p in positional if p.default is p.empty]
    if len(required) > 2 or not positional and not any(p.kind == p.VAR_POSITIONAL for p in params):
        raise TypeError(
            f"a filter function takes the node, and optionally a Context; "
            f"{fn.__qualname__} takes {len(required)} arguments"
        )
    if len(required) == 2:
        return True
    return len(positional) < 2 and any(p.kind == p.VAR_POSITIONAL for p in params)
