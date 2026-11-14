# panir

pandoc's document AST in Python, generated from pandoc-types: checked types,
pandoc's JSON, and filters that run under `pandoc --filter` or in-process
with [libpandoc](https://github.com/ickc/libpandoc-python).

```python
from panir import Filter, Header, Str

f = Filter()


@f.on(Header)
def demote(h: Header) -> None:
    h.level += 1


if __name__ == "__main__":
    f.main()  # pandoc --filter ./demote.py
```

## The AST

One class per pandoc constructor, a subclass of its type: `Para` and `Header`
are `Block`s, `Str` and `Emph` are `Inline`s. Fields have pandoc-types' names
and order, as in pandoc's Lua API: `Header(level, *content, attr=...)`.
Where Lua's constructors take every argument in an order of their own, so
do these: `Cite(content, citations)`, `Citation(id, mode, prefix, suffix,
note_num, hash)`, `Table(caption, col_specs, head, bodies, foot, attr=)`,
`TableBody(body, head, row_head_columns, attr=)`, `Cell(content, alignment,
row_span, col_span, attr=)` (its content one argument, as in Lua:
`Cell([Para("a"), Para("b")], "AlignRight")`).

```python
>>> from panir import *
>>> h = Header(1, Str("Intro"), identifier="intro", attributes={"lang": "en"})
>>> h
Header(1, Str('Intro'), identifier='intro', attributes=[('lang', 'en')])
>>> h.attr
Attr('intro', [], [('lang', 'en')])
>>> h.attributes["lang"] = "fr"
>>> h.attributes["lang"]
'fr'
>>> match h:
...     case Header(1, [Str(text)]):
...         print(text)
Intro
```

- Constructors take the content as arguments (`Para(Str("a"), Space(),
  Str("b"))`), attributes by name (`identifier=`, `classes=`, `attributes=`,
  or `attr=` whole), a link's target by name (`url=`, `title=`). A pattern's
  positional arguments are in the same order.
- As in pandoc's Lua, these are also fields of the element:
  `h.identifier`, `h.classes`, `link.url`.
- Attributes are (key, value) pairs, in order, a key perhaps twice; and,
  as Lua's `el.attributes.key`, by key: `attributes["k"]` is the value of
  the first pair with that key (`KeyError` if none; `.get("k")`), setting
  it replaces that value or adds a pair at the end, `del` removes that
  pair, and `"k" in attributes` asks. Given whole, a mapping is its pairs;
  a `set` (for `classes=`) is refused: it has no order.
- `repr` is constructor syntax that evaluates to an equal value.
- Enum-like types (`MathType`, `Alignment`, ...) are enums; their names are
  accepted too: `Math("InlineMath", "x")`.
- Metadata converts from plain values (`doc.meta["draft"] = True`,
  `Pandoc(meta={"draft": True})`): see `from_python`.

## Checked where you make the mistake

Everything put into a node, a list or a map of the AST is checked, and the
error says where and what:

```python
>>> p = Para(Str("a"))
>>> p.content.append(Para())
ASTTypeError: Para.content[1]: expected Inline, got Para (a Block)
>>> p.content[0] = 1.5
ASTTypeError: Para.content[0]: expected Inline, got float 1.5
```

A field's list is its own: assigning a list to a field copies it, even
another node's (`q.content = p.content`), so that a change to one isn't a
change to the other.

What converts, converts as in pandoc's Lua:

- where one inline goes, a string is a `Str` (`p.content.append("x")`);
- where inlines go, a string is its words and spaces (`Para("hello
  world")`, `h.content = "Intro"`), and one inline a list of it;
- where one block goes, a string is `Plain` text, and an inline, or a
  list of inlines and strings (a `Str` each), a `Plain` of them
  (`Div(Str("x"))` is `Div(Plain(Str("x")))`);
- where blocks go, a string is `Plain` text, and one block (or inline) a
  list of it.

So, as in Lua, a lone string is split into words, and a string among
others is one `Str`: `Para("a b")` is three inlines, `Para("a b", "c")` two.
(Lua's `pandoc.Para{"a b"}`, a list of one string, is one `Str`; in
Python, `Para(*["a b"])` can't be told from `Para("a b")`.) `inlines("...")`
and `blocks("...")` build such lists. To parse markup, use libpandoc
(below).

Reading JSON reports the path of what's wrong:

```python
>>> loads('{"pandoc-api-version":[1,23,1,2],"meta":{},"blocks":[{"t":"Para","c":[{"t":"Str","c":42}]}]}')
ASTDecodeError: blocks[0].content[0].text: expected a string, got 42
```

Type checkers read the same declarations, conversions included: pyright
reports `h.level = "2"` and `Para(Header(1))`, and accepts
`h.content = "Intro"`. (The examples here are checked with pyright.)

## Filters

A function per node type, which returns `None` to keep the node (changed in
place or not), a node to replace it, or a list to splice in its place (`[]`
deletes it). A second parameter, if the function has one (without a
default), is the node's `Context`: its `parent`, the `container` it is in
and its `index` there, its `next`/`prev` siblings, `path`, the `doc`, and
the output `format`. They are right after earlier nodes of the list were
deleted or spliced: a node replaced by none or several is spliced in
place, so a list is as it is now however it is reached.

What a function returns converts as pandoc's Lua converts it: for an
inline, a string is its words (`return "two words"`), and a list's
strings a `Str` each; for a block, a string is `Plain` text, an inline a
`Plain` of it, and a list's inlines, or lists of inlines, a `Plain` each.
Anything else that isn't a node of the family is an error, which names
the function and where in the document it was called:

```python
>>> g = Filter()
>>> @g.on(Str)
... def wrong(s):
...     return 42
>>> g(Pandoc(Para("a")))
ASTTypeError: Para.content[0]: expected a node, a list of nodes or None, got int 42
```

with the note `returned by filter function wrong, for the Str at
blocks[0].content[0]`; an exception raised in a function gets the same.

As in Lua, what a function returns are values: `return [el, el]` puts two
nodes in the document, the second a copy. Elsewhere a node belongs in one
place: to put it in a second, copy it (`copy.deepcopy(el)`), or whatever
changes one later changes both.

```python
@f.on(Str)
def shout(s, ctx):
    if ctx.format == "html":
        return Strong(Str(s.text.upper()))
```

Type checkers check filter functions: one registered for a kind of node
must take that node, and return what converts to its family (a function
for `Str` can't return a `Para`, one for `Pandoc` can't return a list),
and a decorated function keeps its own type. `Filter.on`'s signatures are
generated from the schema.

Each node gets the function for its class, or else for its closest base
class (`Inline`, `Block`, ...). As in pandoc's Lua filters, `@f.on_inlines`
and `@f.on_blocks` register a function for every list of them, and
`@f.on_meta` one for the metadata; each returns a replacement, or `None`
(converted as Lua does: a string, or one node, is a list of it; plain
values are metadata). What a list function returns, unless the list
itself, becomes a new list in the old one's place.

`Filter(traverse=...)` sets the order, one of the three filter frameworks
use:

- `"typewise"` (the default, as pandoc's Lua filters and Haskell's `walk`):
  one walk per kind, each bottom-up: every inline, then every list of
  inlines, then every block, then every list of blocks, then the other
  nodes, then the metadata, then `Pandoc`. So every inline is done before
  any block's function runs.
- `"topdown"` (Lua's other order, and pandocfilters'): `Pandoc`, the
  metadata, then from the root down, a list before its elements and a node
  before its children, which are walked in the node's replacement too,
  unless the function calls `ctx.skip_children()` (Lua's `return el,
  false`). The order in which elements start, as a reader meets them: for
  counters and nesting.
- `"bottomup"` (panflute's): one walk, each node after its children, a
  list after its elements, the metadata after what is in it, `Pandoc`
  last. The fastest.

They are checked against pandoc's Lua filters: each scenario in
[`corpus/filters/`](https://github.com/ickc/panir/tree/main/corpus/filters)
is a Lua filter, and its Python version must make exactly the same
document.

`f.main()` runs a filter under pandoc; `f(doc)` on a document; `run(doc,
[f, g])` several in turn. Under pandoc, a function's `print` goes to
stderr: stdout is the document.

### Fragments

A filter also applies to any node, or a list of inlines or of blocks, as
Lua's `el:walk{...}` does: `el.walk(f)`, `p.content.walk(f)`, or `f(el)`.
As in Lua, the functions apply to what is in a node but not to the node
itself, a list's function also to the list itself, and the metadata and
`Pandoc` functions only to a document. Unlike Lua, the fragment is changed
in place, and returned:

```python
>>> loud = Filter()
>>> @loud.on(Str)
... def upper(s):
...     return s.text.upper()
>>> Emph("a", Str("b")).walk(loud)
Emph(Str('A'), Str('B'))
```

A context's `path` is then from the fragment (`ctx.where` names it, as
`Para.content[0]`), and its `doc` is `None`. `walk(node, action)` is the
walk underneath: `action(node, ctx)` on every node, the node itself
included (or each in a list).

### Calling pandoc from a filter

panir is the data; calling pandoc is
[libpandoc](https://github.com/ickc/libpandoc-python)'s job. A filter that
parses fragments, such as table cells, passes them to libpandoc with the
conversion from its context, and they are read the way the document was:

```python
import libpandoc


@f.on(CodeBlock)
def cells(code, ctx):
    if "cells" in code.attr.classes:
        docs = libpandoc.read_many(code.text.splitlines(), ctx.conversion)
        return [b for d in docs for b in d.blocks]  # read in parallel
```

`ctx.conversion` is what the filter knows of the pandoc run it is part of,
and that depends on who runs it:

- **in process, with libpandoc** (`libpandoc.convert(..., filters=[f])`,
  `pandocpy -F`): the input format pandoc decided on (`input_format`, with
  extensions), the output format and the conversion's `options`.
- **as a JSON filter under libpandoc**: the same formats, from
  `$PANDOC_INPUT_FORMAT` and `$PANDOC_OUTPUT_FORMAT`.
- **as a JSON filter under pandoc** (`pandoc --filter`): the output format's
  name and the reader's options. pandoc doesn't tell JSON filters the input
  format yet (proposed: jgm/pandoc#11016), so reading assumes markdown
  unless given one: `libpandoc.read_many(texts, "rst")`.

## Deep documents

Reading, writing, walking, copying and pickling recurse, as deep as
Python's recursion limit lets them: at the default (1,000), documents
about 190 levels deep work on Python 3.10 to 3.15, in any thread (190
divs in one another, or 48 tables in tables, four levels each; on 3.14
and 3.15, 245). The deepest real document found has 38 levels (a news site's page,
through `pandoc -f html`). Deeper, reading raises `ASTDecodeError` and
anything else `ASTDepthError` (an `ASTError` and a `RecursionError`);
`sys.setrecursionlimit` goes deeper. panir never changes the limit.
Comparing doesn't recurse. A filter function's own runaway recursion (most
of the stack its own calls, below panir's) stays the `RecursionError` it
is, with a note of which function; one that goes through panir (a function
that walks a node with itself, without end) is told from a deep document
only by its note.

## Also

`loads`/`dumps`/`load`/`dump` for pandoc's JSON, `stringify` (as pandoc's
`pandoc.utils.stringify`, metadata and all), `to_python`/`from_python` for
metadata. A string UTF-8 can't hold (a lone surrogate, `Str("\ud800")`) is
an `ASTTypeError` that says where when `dump` or `f.main()` writes it
(`dumps` gives a Python string, which can hold it). A node pickles as its
JSON, read back as `loads` reads it. Python's cyclic
garbage collector is paused while a document is read or written, and for
the whole of `f.main()`: it went through the new nodes again and again,
half of the time.
