"""pandoc's document AST in Python, generated from pandoc-types.

- the AST: one class per constructor (``Para``, ``Str``, ...), with checked
  fields; ``Pandoc`` is a document;
- JSON: ``loads``/``dumps`` (and ``Node.from_json``/``to_json``), pandoc's
  own encoding;
- filters: ``Filter``, and ``walk`` underneath; ``Conversion``, the pandoc
  run a filter is part of, which parses fragments as the document was read;
- ``stringify`` and ``to_roman_numeral``, as pandoc's Lua ``pandoc.utils``;
  ``lua_number``, as Lua's ``tonumber`` reads text;
  metadata to and from Python (``to_python``, ``from_python``), and as Lua
  reads it (``lookup``, ``items``, ``inlines_of``, ``blocks_of``);
- a filter's options from a code block's YAML header and the document's
  metadata (``yaml_block``, ``Options``, ``yaml_filter``, ``to_data``).
"""

from __future__ import annotations

from typing import IO, Any

from . import _fast, _types
from ._core import (
    ASTDecodeError,
    ASTDepthError,
    ASTError,
    ASTTypeError,
    Attributes,
    Node,
    NodeDict,
    NodeList,
    unwritable,
)
from ._types import *  # noqa: F403  the AST classes
from ._types import Pandoc
from ._walk import Context, walk
from .conversion import Conversion
from .filter import Filter, main, run
from .options import Options, yaml_block, yaml_filter
from .util import (
    blocks,
    blocks_of,
    check_unique,
    from_python,
    inlines,
    inlines_of,
    items,
    lookup,
    lua_number,
    stringify,
    to_data,
    to_python,
    to_roman_numeral,
)

__version__ = "0.2.0"


def loads(text: str | bytes) -> Pandoc:
    """A document from pandoc's JSON text."""
    return _fast.loads(text, Pandoc)


def load(fp: IO[Any]) -> Pandoc:
    """A document from a file of pandoc's JSON."""
    return _fast.loads(fp.read(), Pandoc)


def dumps(doc: Pandoc) -> str:
    """A document as pandoc's JSON text."""
    if not isinstance(doc, Pandoc):
        raise TypeError(f"dumps() takes a document (Pandoc), got {type(doc).__name__}")
    return _fast.dumps(doc)


def dump(doc: Pandoc, fp: IO[str]) -> None:
    """Write a document as pandoc's JSON (``fp`` in UTF-8, as pandoc reads
    it). A string UTF-8 can't hold (a lone surrogate) is an ``ASTTypeError``
    that says where."""
    try:
        fp.write(dumps(doc))
    except UnicodeEncodeError as e:
        error = unwritable(doc)
        if error is None:
            raise
        raise error from e


__all__ = [
    "ASTDecodeError",
    "ASTDepthError",
    "ASTError",
    "ASTTypeError",
    "Attributes",
    "Context",
    "Conversion",
    "Filter",
    "Node",
    "NodeDict",
    "NodeList",
    "Options",
    "blocks",
    "blocks_of",
    "check_unique",
    "dump",
    "dumps",
    "from_python",
    "inlines",
    "inlines_of",
    "items",
    "load",
    "loads",
    "lookup",
    "lua_number",
    "main",
    "run",
    "stringify",
    "to_data",
    "to_python",
    "to_roman_numeral",
    "walk",
    "yaml_block",
    "yaml_filter",
]
__all__ += _types.__all__
