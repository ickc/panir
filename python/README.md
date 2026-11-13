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

```python
>>> from panir import *
>>> h = Header(1, Str("Intro"), identifier="intro")
>>> h
Header(1, Str('Intro'), identifier='intro')
>>> h.attr
Attr('intro')
>>> match h:
...     case Header(1, _, [Str(text)]):
...         print(text)
Intro
```

- Constructors take the content as arguments (`Para(Str("a"), Space(),
  Str("b"))`), attributes by name (`identifier=`, `classes=`, `attributes=`,
  or `attr=` whole), a link's target by name (`url=`, `title=`).
- `repr` is constructor syntax that evaluates to an equal value.
- Enum-like types (`MathType`, `Alignment`, ...) are enums; their names are
  accepted too: `Math("InlineMath", "x")`.
- Metadata converts from plain values: `doc.meta["draft"] = True`.

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

Strings convert as in pandoc's Lua: where a list of inlines goes, a string
is its words and spaces (`Para("hello world")`, `h.content = "Intro"`);
where one inline goes, a `Str` (`p.content.append("x")`); where blocks go,
`Plain` text. `inlines("...")` and `blocks("...")` build such lists. To
parse markup, use libpandoc (below).

Reading JSON reports the path of what's wrong:

```python
ASTDecodeError: blocks[0].content[1].text: expected a string, got 42
```

Type checkers read the same declarations: pyright reports `h.level = "2"`
and `Para(Header(1))`.

## Filters

A function per node type, which returns `None` to keep the node (changed in
place or not), a node to replace it, or a list to splice in its place (`[]`
deletes it). A second parameter, if the function has one (without a
default), is the node's `Context`: its `parent`, the `container` it is in
and its `index` there, its `next`/`prev` siblings, `path`, the `doc`, and
the output `format`.

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
must take that node, and return nodes of its family (`None`, a node or a
list: a function for `Str` can't return a `Para`). `Filter.on`'s
signatures are generated from the schema.

Each node gets the function for its class, or else for its closest base
class (`Inline`, `Block`, ...). As in pandoc's Lua filters, `@f.on_inlines`
and `@f.on_blocks` register a function for every list of them, and
`@f.on_meta` one for the metadata; each returns a replacement, or `None`. An
exception in a function is annotated with the function and the node's path.

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
[`corpus/filters/`](../corpus/filters/) is a Lua filter, and its Python
version must make exactly the same document.

`f.main()` runs a filter under pandoc; `f(doc)` on a document; `run(doc,
[f, g])` several in turn. `walk(node, action)` is the walk underneath.
Under pandoc, a function's `print` goes to stderr: stdout is the document.

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

Also: `loads`/`dumps`/`load`/`dump` for pandoc's JSON, `stringify`,
`to_python`/`from_python` for metadata.
