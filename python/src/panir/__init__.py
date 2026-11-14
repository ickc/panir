"""pandoc's document AST in Python, generated from pandoc-types.

- the AST: one class per constructor (``Para``, ``Str``, ...), with checked
  fields; ``Pandoc`` is a document;
- JSON: ``loads``/``dumps`` (and ``Node.from_json``/``to_json``), pandoc's
  own encoding;
- filters: ``Filter``, and ``walk`` underneath; ``Conversion``, the pandoc
  run a filter is part of, which parses fragments as the document was read;
- ``stringify``, and metadata to and from Python (``to_python``,
  ``from_python``).
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
from .filter import Filter, run
from .util import blocks, from_python, inlines, stringify, to_python

__version__ = "0.1.0"


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
    "blocks",
    "dump",
    "dumps",
    "from_python",
    "inlines",
    "load",
    "loads",
    "run",
    "stringify",
    "to_python",
    "walk",
]
__all__ += _types.__all__
