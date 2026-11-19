"""A filter's options, from the document's metadata and from a code
block's YAML header, read by pandoc (panflute's ``yaml_filter``).

    ```{.table}
    ---
    caption: "*Sales* by region"
    ---
    region,sales
    ```

``yaml_block`` splits a block's text into its header and its data, by
pandoc's rule for a YAML metadata block; pandoc reads the header (panir
reads no YAML); ``Options`` puts it over the document's metadata under the
filter's name; ``yaml_filter`` does it all for a document, with one call
of the reader for all of its blocks.
"""

from __future__ import annotations

import builtins
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from ._types import (
    CodeBlock,
    MetaBlocks,
    MetaBool,
    MetaInlines,
    MetaList,
    MetaMap,
    MetaString,
    MetaValue,
    Pandoc,
)
from .filter import Filter
from .util import _stringify, lua_number, to_data

__all__ = ["Options", "yaml_block", "yaml_filter"]

_OPENING = re.compile(r"---[ \t]*")
_CLOSING = re.compile(r"(?:---|\.\.\.)[ \t]*")
_BLANK = re.compile(r"[ \t]*")


def yaml_block(text: str) -> tuple[str | None, str]:
    """A text's YAML header and its data, by pandoc's rule for a YAML
    metadata block, where one starts the text: the first line is ``---``,
    the next is not blank, and a later line is ``---`` or ``...`` (each
    perhaps followed by spaces or tabs; not indented, not four of them).
    The header is the lines between, each with its newline (``""`` for
    ``---`` then ``---``); the data is everything after the closing line.
    No header: ``(None, text)``. A ``---`` line later in the text opens
    nothing: text appended to the block (an include) is never read as
    options.

        >>> yaml_block("---\\ncaption: Sales\\n---\\na,b\\n")
        ('caption: Sales\\n', 'a,b\\n')
    """
    first, newline, _ = text.partition("\n")
    if not newline or not _OPENING.fullmatch(first):
        return None, text
    start = at = len(first) + 1
    while True:
        end = text.find("\n", at)
        line = text[at:] if end < 0 else text[at:end]
        if at == start and _BLANK.fullmatch(line):
            return None, text
        if _CLOSING.fullmatch(line):
            return text[start:at], "" if end < 0 else text[end + 1 :]
        if end < 0:
            return None, text
        at = end + 1


class Options(dict[str, MetaValue]):
    """A filter's options: metadata values by key, read raw (``options["k"]``,
    ``options.get("k")``: a caption wants its inlines) or converted on
    request: ``text``, ``boolean``, ``number``, ``list``, ``map``, each
    ``None`` where the key is missing or its value is of another shape.

    What pandoc makes of a header (read as a metadata block, ``-f
    markdown``; checked with pandoc 3.14): ``true`` and ``True`` are
    booleans (``yes`` is text); numbers are text, as pandoc writes them
    (``010`` is ``"10"``, ``1e3`` ``"1000"``); ``null`` and ``~`` are empty
    text; text is read as markdown, so a caption arrives as inlines, but a
    text of only a tab is lost, ``'`` is a curly quote and ``...`` an
    ellipsis (``-f markdown-smart`` keeps them), escapes, entities and raw
    HTML are read; a ``|`` scalar is blocks.
    """

    @classmethod
    def of(
        cls,
        meta: Mapping[str, MetaValue],
        name: str,
        header: Mapping[str, MetaValue] | None = None,
    ) -> Options:
        """The options under ``name`` in the document's metadata (``meta``;
        ignored unless a map), each key's value replaced whole by the
        header's (``header``: a block's header, read as a document's
        metadata), as pandoc merges metadata blocks: no deeper merge. The
        values are the documents' own, not copies.

            options = Options.of(doc.meta, "pantable", header_doc.meta)
        """
        defaults = meta.get(name)
        options = cls(defaults.content if isinstance(defaults, MetaMap) else {})
        options.update(header or {})
        return options

    def text(self, key: str) -> str | None:
        """Text (a ``MetaString``), or the text of inlines or blocks."""
        value = self.get(key)
        if isinstance(value, MetaString):
            return value.text
        if isinstance(value, (MetaInlines, MetaBlocks)):
            return _stringify(value)
        return None

    def boolean(self, key: str) -> bool | None:
        """A ``MetaBool``'s value."""
        value = self.get(key)
        return value.value if isinstance(value, MetaBool) else None

    def number(self, key: str) -> int | float | None:
        """Text read as a number, as ``lua_number`` reads it."""
        text = self.text(key)
        return None if text is None else lua_number(text)

    def list(self, key: str) -> builtins.list[Any] | None:
        """A ``MetaList``'s items, as plain data (``to_data``)."""
        value = self.get(key)
        return to_data(value) if isinstance(value, MetaList) else None

    def map(self, key: str) -> dict[str, Any] | None:
        """A ``MetaMap``, as plain data (``to_data``)."""
        value = self.get(key)
        return to_data(value) if isinstance(value, MetaMap) else None


def yaml_filter(
    doc: Pandoc,
    classes: str | Iterable[str],
    name: str,
    read: Callable[[list[str]], Sequence[Pandoc]],
    function: Callable[[Options, str, CodeBlock], Any],
) -> Pandoc:
    """Code blocks with a YAML header, panflute's ``yaml_filter``: each code
    block of one of the ``classes`` is replaced by what ``function(options,
    data, block)`` returns, as a filter function's (``None`` keeps it, a
    node replaces it, a list is spliced, ``[]`` deletes it). ``options`` is
    ``Options.of(doc.meta, name, header)``, ``data`` the text after the
    header (``yaml_block``), or all of it.

    ``read`` reads texts of markdown into documents, one each: the host's
    (``libpandoc.read_many``), or the ``pandoc`` command. It is called once,
    with every header as a document (``---``, the header, ``---``); not at
    all if no block has one. A header pandoc reads no metadata from (a YAML
    scalar or list: blocks come back) is no header, and the whole text is
    data, as pandoc would read it. The document is changed in place, and
    returned.

        @f.on(Pandoc)
        def tables(doc, ctx):
            read = lambda texts: libpandoc.read_many(texts, ctx.conversion, {"from": "markdown"})
            yaml_filter(doc, "table", "pantable", read, table)
    """
    wanted = {classes} if isinstance(classes, str) else set(classes)
    found: list[CodeBlock] = []
    find, replace = Filter(), Filter()

    @find.on(CodeBlock)
    def collect(block: CodeBlock) -> None:
        if wanted.intersection(block.classes):
            found.append(block)

    find(doc)
    splits = [yaml_block(block.text) for block in found]
    headers = [f"---\n{h}---\n" for h, _ in splits if h is not None]
    read_docs = list(read(headers)) if headers else []
    todo: dict[int, tuple[Options, str]] = {}
    for block, (header, data) in zip(found, splits):
        meta = None
        if header is not None:
            got = read_docs.pop(0)
            if got.blocks:
                data = block.text
            else:
                meta = got.meta
        todo[id(block)] = (Options.of(doc.meta, name, meta), data)

    @replace.on(CodeBlock)
    def apply(block: CodeBlock) -> Any:
        if id(block) in todo:
            return function(*todo[id(block)], block)
        return None

    return replace(doc)
