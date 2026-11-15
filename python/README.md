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
(in snake case) and order: `Header(level, *content, attr=...)`; most are
pandoc's Lua API's, and "Coming from Lua" lists those that are not.
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
  pair (`pop("k", default)` too, returning its value, as `dict.pop`), and
  `"k" in attributes` asks. Given whole, a mapping is its pairs;
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
deletes it); a metadata value in a map (a field of the metadata, or of a
`MetaMap`) is replaced by one value (`[]` there is an error: a key is
deleted by the function of the metadata or of the `MetaMap`). A second parameter, if the function has one (without a
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

As in Lua, what one call returns are values: `return [el, el]` puts two
nodes in the document, the second a copy. Not across calls: a node
returned by two calls is in two places (Lua's `local br =
pandoc.RawBlock(...)`, returned for every marker, is a copy each time;
here it is one node). Everywhere but within one return a node belongs in
one place: to put it in a second, copy it (`return copy.deepcopy(BR)`),
or whatever changes one later changes both. That is the filter's author's
to get right, as anything a program does; for tests and debugging,
`check_unique(doc)` raises a `ValueError` naming a node it finds in two
places, and both (by identity: equal nodes are fine; a list in two places
by what is in it).

```python
import copy

BR = RawBlock("html", "<hr/>")  # made once
breaks = Filter()


@breaks.on(HorizontalRule)
def page(rule):
    return copy.deepcopy(BR)  # a copy for each rule


doc = breaks(Pandoc(HorizontalRule(), HorizontalRule()))
check_unique(doc)  # (without the copy: ValueError, the RawBlock at blocks[0] is also at blocks[1])
```

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
  counters and nesting. What a function returns is not given to a function
  again, nor, if a list, are its elements; their children are walked (as
  in pandoc's Lua and pandocfilters: an emphasis replaced by its content
  keeps an emphasis that was directly in it). What a list function returns
  is the list whose elements are walked.
- `"bottomup"` (panflute's): one walk, each node after its children, a
  list after its elements, the metadata after what is in it, `Pandoc`
  last. The fastest.

They are checked against pandoc's Lua filters: each scenario in
[`corpus/filters/`](https://github.com/ickc/panir/tree/main/corpus/filters)
is a Lua filter, and its Python version must make exactly the same
document.

`f.main()` runs a filter under pandoc; `f(doc)` on a document; `run(doc,
[f, g])` several in turn, each a whole walk, as a Lua filter file returning
a list of filters does, and `main([f, g])` so under pandoc. Under pandoc,
a function's `print` goes to stderr: stdout is the document.

```python
from panir import Filter, Meta, Quoted, Str, main, stringify

configure, quotes = Filter(), Filter()
marks = {"left": "«", "right": "»"}


@configure.on_meta
def read(meta: Meta) -> None:
    if "quote-marks" in meta:
        left, right = stringify(meta["quote-marks"])
        marks.update(left=left, right=right)


@quotes.on(Quoted)
def mark(q: Quoted) -> list:
    return [Str(marks["left"]), *q.content, Str(marks["right"])]


if __name__ == "__main__":
    main([configure, quotes])  # the metadata first, then every quotation
```

### Coming from Lua

What a port of a Lua filter trips over:

- **`return nil` undoes a function's changes in Lua; here they stay.**
  In pandoc's Lua a function gets a copy of its element, and what it
  changes counts only if it returns the element: `return nil` (or no
  `return`) leaves the element as it was, children and lists included
  (checked with pandoc 3.14). Here the node itself is changed, and the
  changes stay whatever the function returns. A Lua filter that changes its
  element, then returns `nil` (pandoc-ext/list-table removes a div's class,
  then declines an empty div) is ported by deciding first, and changing
  after.
- **Lua's elements are values**: `pandoc.Span(meta.title)` puts a copy in
  the body. Here a node in a second place is a copy you make
  (`copy.deepcopy(x)`), or one node in two places.
- `el.t`, `el.tag`: `type(el).__name__`.
- **Field names** are pandoc's Lua API's (`content`, `text`, `attr`,
  `level`, `citations`, `note_num`, ...; and, as in Lua, an element's
  attributes', target's and list attributes' fields are its own too:
  `el.identifier`, `link.title`, `ol.start`), but for pandoc-types' names
  here: `quote_type` (Lua's `quotetype`), `math_type` (`mathtype`),
  `list_attributes` (`listAttributes`), a table's `col_specs` (`colspecs`,
  pairs in Lua, `ColSpec`s of `alignment` and `width` here), a caption's
  `content` (`long`), a cell's `content` (`contents`), an image's
  `content` (`caption`); and a link's or image's `target` is a `Target` of
  `url` and `title` (in Lua `target`, an image's `src`, is the URL):
  `link.url`, `image.url`.
- `el.attributes.k`, `= v`, `= nil`: `el.attributes["k"]` (or
  `.get("k")`), `= v`, `.pop("k", None)`; the first pair with the key, as
  in Lua. A Lua table as attributes (`{id = "x", class = "a b"}`) gives
  the identifier and classes; here `attributes={"class": ...}` is a pair
  named `class`: pass `identifier=` and `classes=`.
- `error(...)`: raise; the exception gets a note naming the function and
  the place.
- `return el, false` (top-down): `ctx.skip_children()`.
- In pandoc's Lua (top-down), `return el, false` from the `Pandoc` or
  `Meta` function skips nothing: the metadata and the blocks are walked
  all the same (checked with pandoc 3.14). Here `ctx.skip_children()`
  there skips them, as for any node.
- A Lua file returning a list of filters (`return {configure, quotes}`):
  `main([configure, quotes])` (or `run(doc, [...])`), each a whole walk,
  in turn.
- Lists count from 0 here, from 1 in Lua (`el.content[1]`).
- The metadata a Lua filter keeps from its `Meta` function, for the
  functions after: `ctx.doc.meta`.
- `FORMAT`: `ctx.format`, the output format pandoc passed; `None` where
  none is known (a filter applied without one: `f(doc)`), where Lua's
  `FORMAT` is always a string: `ctx.format or ""`.
- Lua's string patterns (`s:match`, `gsub`, `find`) are not regular
  expressions: `%` escapes (`%.`), `-` is a lazy `*`, `%a` is a letter
  (ASCII), `%s` ASCII whitespace, and there is no `|`; a port translates
  each, or escapes a literal string (`re.escape`).
- In `gsub`'s replacement too, `%` is special: `%1` is a capture, `%%` a
  percent sign, and any other `%` an error (checked with pandoc 3.14), so
  a replacement taken from the document (a size of `5%`) fails there.
  `re.sub` has escapes of its own in a replacement string (`\1`, `\g<0>`):
  `re.sub(p, lambda m: text, s)` puts `text` in as it is.
- Lua's strings are bytes: `#s`, `s:len()`, `s:sub(i, j)` and `s:byte(i)`
  count bytes of UTF-8 (`#"é"` is 2; `s:sub(1, 1)` of it is half a
  character, which pandoc reads back as U+FFFD), and `upper` and `lower`
  change ASCII letters only; `utf8.len(s)` counts characters. Python's
  `len(s)` and `s[i:j]` count characters (code points); where an original
  depends on bytes, `s.encode()` has them (`len(s.encode())`,
  `s.encode()[i - 1:j].decode(errors="replace")`).
- `PANDOC_READER_OPTIONS`: `ctx.conversion.reader_options`, keyed as
  pandoc's JSON (`"track-changes"`, `"tab-stop"`, where Lua's fields are
  `track_changes`, `tab_stop`, and pandoc 2's `trackChanges` is gone),
  with the same values (`"all-changes"`); `None` where pandoc passed none.
- pandoc's Lua has no function for a metadata value (its values are
  plain Lua values, reached through the `Meta` function or `doc.meta`);
  here, as in panflute, `@f.on(MetaValue)` (or `MetaList`, ...) is called
  on each one.

#### Metadata

pandoc's Lua gives a filter the metadata as plain Lua values; here each
value is a node, a `MetaValue`, holding what Lua gives in a field
(checked with pandoc 3.14):

| in the document | in Lua (`pandoc.utils.type`) | here |
|---|---|---|
| `x: some text`; also `x: ""`, `x: 1`, `x: no` | `Inlines` | `MetaInlines`: `content`, a list of inlines |
| `x: \|` and indented lines (blocks) | `Blocks` | `MetaBlocks`: `content`, a list of blocks |
| `-M x=text` on the command line; YAML's null, `x:` | `string` | `MetaString`: `text` |
| `x: true`, `x: false`; `-M x=true` | `boolean` | `MetaBool`: `value` |
| `x: [a, b]` | `List` | `MetaList`: `content`, a list of values |
| `x: {k: v}` | `table` | `MetaMap`: `content`, a dict of values |
| no field | `nil` | `None` (`meta.get("x")`) |

`pandoc.utils.type(v) == "Inlines"` is `isinstance(v, MetaInlines)`, or a
`match` on the classes (`case MetaInlines(content):`).

- **Lua's truth**: `if meta.x then` (and `meta.x or d`) is false for a
  missing field and for `false` only; an empty string, list or map is
  true, as is the text `no`. So is every metadata value here but
  `MetaBool(False)`, so that `if meta.get("x"):` and `meta.get("x") or d`
  are Lua's as written.
- **A nested option**, Lua's `((meta.a or {}).b or {}).c`: `lookup(meta,
  "a", "b", "c")`, `None` where a key is missing or a value on the way is
  not a map (where Lua would fail, on `true`, or find a method, on a
  string or a list). It starts from the metadata or a `MetaMap`.
- **One value or a list of them** (a filter's `ensure_list`, the shapes
  `header-includes` takes): `items(meta.get("x"))`, a `MetaList`'s items,
  nothing for a missing value, any other value alone; a new list, of the
  document's values.
- **Writing metadata** (Lua's `meta.x = v`): setting a field converts a
  plain value as Lua does (`from_python`): a string is a `MetaString`, a
  bool a `MetaBool`, a number a `MetaString` of it, an inline or a list of
  inlines `MetaInlines`, a block or a list of blocks `MetaBlocks`, another
  list a `MetaList`, a dict a `MetaMap`. `to_python(v)` reads metadata as
  Lua gives it: strings, bools, lists, dicts, and inlines and blocks as
  lists of them (copies, as Lua's are values); `stringify(v)` its text.
- **Lua's tables are references** until pandoc converts the metadata back:
  a table put in two places is two values after (scholarly-metadata merges
  one institute into another). Here a node in two places is one node: copy
  it (`copy.deepcopy`), as any node.
- **`return nil` from the `Meta` function** (or no `return`) loses in Lua
  what it changed in the metadata, in place, nested values too, as for
  any function (above; the `Pandoc` function's the same): the metadata
  counts only if returned (checked with pandoc 3.14). Here what a function
  changes stays: an original that changes the metadata, then returns
  nothing in some case (first-line-indent's, unless it sets `indent`) is
  ported by changing it only in the case that returns it.
- **A metadata value's `.t` is `nil`** since pandoc 2.17 (its values are
  plain Lua values; on `true` or `false`, Lua fails): an original testing
  `meta.x.t == "MetaList"` takes its other branch whatever the value.
  Here the test is `isinstance(v, MetaList)` (pandoc 3's
  `pandoc.utils.type(v) == "List"`); a port of what the original does now
  takes the other branch too.

```python
>>> meta = Pandoc(meta={"draft": False, "title": "", "toc": {"depth": "2"}, "tags": "a"}).meta
>>> bool(meta.get("draft")), bool(meta.get("title")), bool(meta.get("missing"))
(False, True, False)
>>> meta.get("draft") or MetaString("final")
MetaString('final')
>>> lookup(meta, "toc", "depth"), lookup(meta, "title", "depth")
(MetaString('2'), None)
>>> items(meta.get("tags")), items(meta.get("missing"))
([MetaString('a')], [])
>>> meta["name"] = [Str("Ada"), Space(), Str("L.")]
>>> meta["name"]
MetaInlines(Str('Ada'), Space(), Str('L.'))
>>> to_python(meta["toc"]), to_python(meta["name"])[0]
({'depth': '2'}, Str('Ada'))
>>> options = Conversion("latex", reader_options={"track-changes": "all-changes"}).reader_options or {}
>>> options["track-changes"]
'all-changes'
>>> import re
>>> re.sub("SIZE", lambda m: r"\1 5%", "width: SIZE")
'width: \\1 5%'
>>> len("«»"), len("«»".encode()), "«»".encode()[0:1].decode(errors="replace") == "\ufffd"
(2, 4, True)
```

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
about 190 levels deep work on Python 3.11 to 3.15, in any thread (190
divs in one another, or 48 tables in tables, four levels each; on 3.14
and 3.15, 245). The deepest real documents found have 38 levels (a news site's page,
through `pandoc -f html`) and 121 (a manual page whose list pandoc's man
reader nests item in item); both work. Deeper, reading raises `ASTDecodeError` and
anything else `ASTDepthError` (an `ASTError` and a `RecursionError`);
`sys.setrecursionlimit` goes deeper. panir never changes the limit.
Comparing doesn't recurse. A filter function that calls itself without
end gets the same error: its message names that cause too, and its note
which function.

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
