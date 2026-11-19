# Panir.jl

pandoc's document AST for Julia, generated from pandoc-types: types,
pandoc's JSON, and filters as methods.

```julia
#!/usr/bin/env julia
# pandoc --filter ./upper.jl
using Panir

upper(s::Str) = Str(uppercase(s.text))
demote(h::Header) = (h.level += 1; nothing)

run_filter(upper)
```

## The AST

A sum type is an abstract type (`Inline`, `Block`, `MetaValue`), each
constructor a mutable struct subtyping it (`Str <: Inline`); products are
structs (`Attr`, `Cell`, `Pandoc`); enum-like types are `@enum`s, whose
values are exported (`InlineMath`, `DoubleQuote`). Julia checks field types
itself: `push!(para.content, Para())` and `h.level = "2"` are errors.

`using Panir` brings in what a filter needs: the node types, the enums'
values, `walk!`, `run_filter`, `Context`, `stringify`, `truthy`, `lookup`,
`items` and the errors.
Everything else is reached as `Panir.X`, or by name (`using Panir: Period,
Alignment`): the names Julia or common packages have too (`Panir.Meta`,
the metadata's type, since `Meta` is `Base.Meta`; `Panir.Period`,
`Panir.Example`, `Panir.Decimal`, a list's style and delimiter), the enums'
types (`Panir.Alignment`, `Panir.MathType`...), pandoc's aliases
(`Panir.Format`, `Panir.RowSpan`...), and `Panir.parse`, `Panir.serialize`,
`Panir.fromjson`, `Panir.tojson`.

Constructors:

- the content as arguments, the rest by keyword: `Header(1, Str("Hi");
  identifier = "hi")`, `Link("pandoc"; url = "https://pandoc.org")`,
  `Div("a"; classes = ["note"])`. Attributes go as `identifier`, `classes`,
  `attributes`, or `attr` whole (as in every constructor that has them,
  and `Attr` itself); a link's target as `url` and `title`, or `target`.
  The content may also be one vector: `Div(blocks; identifier = "d")`
  (where the content is a list of lists, one vector is one item:
  `BulletList([Plain("a")])`).
- every field positionally, in pandoc-types' order: `Header(1, Attr(),
  Inline[])`, `Div(Attr(), blocks)`; or, where pandoc's Lua constructor
  takes every field in an order of its own, in that order:
  `Cite(content, citations)`, `Citation(id, mode, prefix, suffix,
  note_num, hash)` (the last four may be left out), `Table(caption,
  col_specs, head, bodies, foot; attr)`, `TableBody(body, head,
  row_head_columns; attr)` (all but `body` may be left out), `Cell(content,
  alignment, row_span, col_span; attr)` (all but `content` may be left
  out; the content one argument, as in Lua: `Cell([Para("a"), Para("b")],
  AlignRight)`). These five also take every field by keyword: `Table(;
  bodies, identifier = "t")`, `Cell(; content = "a", col_span = 2)`.
- attributes are (key, value) tuples, in order, a key perhaps in two;
  pairs are taken too (`attributes = ["lang" => "en"]`), and a `Dict`, in
  the order of its keys (a Lua table of them has no order either).
- a constructor copies the lists and maps it is given: a node's lists are
  its own, which the caller may go on changing or give to another node.
  The nodes in them are not copied.
- `show` writes what makes the node again; a document a block a line.

An `Attr` by key is its attributes, as Lua's `el.attributes[key]`: the
first pair with the key is read (`attr["k"]`, `get(attr, "k", nothing)`,
`haskey`), set (`attr["k"] = "v"`, in its place, or a pair added at the
end) and removed (`pop!(attr, "k", nothing)` returns its value,
`delete!`):

```julia
cell = Div("x"; attributes = ["colspan" => "2", "lang" => "en"])
span = parse(Int, pop!(cell.attr, "colspan", "1"))  # 2
cell.attr["lang"] = "fr"
cell.attr.attributes  # [("lang", "fr")]
```

What converts, converts as in pandoc's Lua, in constructors and where a
field is set:

- where one inline goes, a string is a `Str` (`push!(p.content, "x")`);
  where inlines go, a string is its words and spaces (`Para("hello
  world")`, `h.content = "Intro"`), and one inline a list of it;
- where one block goes, a string is `Plain` text, and an inline, or a
  vector of inlines (and strings, a `Str` each), a `Plain` of them
  (`Div(Str("x"))` is `Div(Plain(Str("x")))`); where blocks go, a string is
  `Plain` text, and one block (or inline) a list of it;
- where metadata goes (`doc.meta["draft"] = true`, `Pandoc(; meta =
  Dict("title" => "T"))`, `MetaList`, `MetaMap`), a string is a
  `MetaString`, a boolean a `MetaBool`, a number a `MetaString` of it, an
  inline or a vector of inlines `MetaInlines`, a block or a vector of
  blocks `MetaBlocks`, another vector a `MetaList`, a `Dict` a `MetaMap`.

So, as in Lua, a lone string is split into words, and a string among
others is one `Str`: `Para("a b")` is three inlines, `Para("a b", "c")` two.
`inlines("...")` and `blocks("...")` build such lists. To parse markup, use
LibPandoc.jl.

## Filters

`walk!(f, doc)` calls `f(x, ctx)` or `f(x)` wherever `f` has a method for
`x`, so the most specific method wins, by dispatch. A method returns
`nothing` to keep the node (changed in place or not), a node to replace
it, or, in a list, a vector to splice in its place (empty: delete); a
metadata value in a map (a field of the metadata, or of a `MetaMap`) is
replaced by one value (an empty vector there is an error: a key is deleted
by the method for the metadata or the `MetaMap`). What
it returns converts as pandoc's Lua converts it: for an inline, a string
is its words (`s::Str -> "two words"`), and a vector's strings a `Str`
each; for a block, a string is `Plain` text, an inline a `Plain` of it,
and a vector's inlines, or vectors of inlines, a `Plain` each. Anything
else is an `ASTTypeError` that says where the value was to go and which
method returned it:

```
blocks[0].content[0]: expected Inline, a vector of them, a string, or nothing, got Int64 42
  returned by wrong(s::Str) @ Main upper.jl:4
```

As in Lua, what one call returns are values: `[el, el]` puts two nodes in
the document, the second a copy. Not across calls: a node returned by two
calls is in two places (Lua's `local br = pandoc.RawBlock(...)`, returned
for every marker, is a copy each time; here it is one node). Everywhere
but within one return a node belongs in one place: to put it in a second,
`deepcopy` it (`deepcopy(BR)`), or whatever changes one later changes
both. That is the filter's author's to get right, as anything a program
does; for tests and debugging, `check_unique(doc)` throws an
`ArgumentError` naming a node it finds in two places, and both (by
identity: equal nodes are fine; a vector in two places by what is in it).

```julia
const BR = RawBlock("html", "<hr/>")  # made once
page(::HorizontalRule) = deepcopy(BR)  # a copy for each rule
check_unique(walk!(page, Pandoc(HorizontalRule(), HorizontalRule())))  # (without the copy, it throws)
```

As in pandoc's Lua filters, a method for `Vector{Inline}` or
`Vector{Block}` gets every list of them, and one for `Panir.Meta` the
metadata; methods for anything don't count there, and no other list (of
rows, of cells...) gets one, in any order. Each returns a replacement, or
`nothing` (converted as Lua does: a string, or one node, is a list of it;
for the metadata, a `Dict` of what converts to metadata). What a list
method returns, unless the list itself, becomes a new vector in the old
one's place.

`ctx` is a `Context`: `parent`, `field`, the `container` the node is in and
its `index` there (`ctx.container[ctx.index]` is the node), `next` and
`prev`, `path` (from the document, counting from 0 as pandoc's JSON and
errors do), `doc`, `format`, and `conversion` (a `Conversion`, as in
Python: the formats with extensions, the reader's and the writer's
options, and whether pandoc's sandbox is on, when pandoc or libpandoc
tells them; a host calling filters in process makes it from libpandoc's
context, `Conversion(context; options)`). They are right after earlier nodes of the list
were deleted or spliced: a node replaced by none or several is spliced in
place, so a list is as it is now however it is reached.

```julia
shout(s::Str, ctx) = ctx.format == "html" && !(ctx.next isa Space) ? Strong(uppercase(s.text)) : nothing
```

`walk!(f, doc; traverse)` sets the order, one of the three filter
frameworks use:

- `:typewise` (the default, as pandoc's Lua filters and Haskell's `walk`):
  one walk per kind, each bottom-up: every inline, then every list of
  inlines, then every block, then every list of blocks, then the other
  nodes (`MetaValue`, `Cell`...), then the metadata, then `Pandoc`.
- `:topdown` (Lua's other order, and pandocfilters'): `Pandoc`, the
  metadata, then from the root down, a list before its elements and a
  node before its children, which are walked in the node's replacement
  too, unless the method calls `skip_children!(ctx)` (Lua's `return el,
  false`). What a method returns is not given to a method again, nor, if
  a vector, are its elements; their children are walked (as in pandoc's
  Lua and pandocfilters: an emphasis replaced by its content keeps an
  emphasis that was directly in it). What a list method returns is the
  list whose elements are walked.
- `:bottomup` (panflute's): one walk, each node after its children, a list
  after its elements, the metadata after what is in it, `Pandoc` last.

They are checked against pandoc's Lua filters: each scenario in
[`corpus/filters/`](https://github.com/ickc/panir/tree/main/corpus/filters)
is a Lua filter, and its Julia version must make exactly the same document.

`run_filter(f)` runs `f` as a pandoc JSON filter: a document on stdin, to
stdout, `ctx.conversion` read from pandoc's arguments and environment.
`walk!` and `run_filter` also take a vector of filters, run in turn, each
a whole walk (as a Lua filter file returning a list of filters), each a
function or `f => traverse`: `run_filter([configure, quotes])`. A host
running filter scripts in its own process (LibPandoc.jl's `pandocjl`)
runs the script inside `Panir.handoff`, and then `run_filter` hands `f`
(or the vector, which `walk!` takes) over instead; a function like
`run_filter` of the host's own calls `Panir.hand_over(f, traverse)`, which
does the same.

```julia
marks = Ref(("«", "»"))
function configure(meta::Panir.Meta)
    m = get(meta, "quote-marks", nothing)
    m === nothing || (marks[] = Tuple(string.(collect(stringify(m)))))
    nothing
end
quotes(q::Quoted) = [Str(marks[][1]); q.content; Str(marks[][2])]

run_filter([configure, quotes])  # the metadata first, then every quotation
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
  (`deepcopy(x)`), or one node in two places.
- `el.t`, `el.tag`: `nameof(typeof(el))`.
- **Field names** are pandoc's Lua API's (`content`, `text`, `attr`,
  `level`, `citations`, `note_num`, ...), but for pandoc-types' names
  here: `quote_type` (Lua's `quotetype`), `math_type` (`mathtype`),
  `list_attributes` (`listAttributes`), a table's `col_specs` (`colspecs`,
  pairs in Lua, `ColSpec`s of `alignment` and `width` here), a caption's
  `content` (`long`), a cell's `content` (`contents`), an image's
  `content` (`caption`); and a link's or image's `target` is a `Target` of
  `url` and `title` (in Lua `target`, an image's `src`, is the URL, and
  `title` the element's). An element's attributes, target and list
  attributes are fields of their own here: Lua's `el.identifier` is
  `el.attr.identifier`, `link.title` `link.target.title`, `ol.start`
  `ol.list_attributes.start`.
- `el.attributes.k`, `= v`, `= nil`: `el.attr["k"]` (or `get(el.attr,
  "k", nothing)`), `el.attr["k"] = v`, `pop!(el.attr, "k", nothing)`; the
  first pair with the key, as in Lua. A Lua table as attributes (`{id =
  "x", class = "a b"}`) gives the identifier and classes; here
  `attributes = ["class" => ...]` is a pair named `class`: pass
  `identifier` and `classes`.
- `error(...)`: `error(...)`, or throw any exception.
- `return el, false` (top-down): `skip_children!(ctx)`.
- In pandoc's Lua (top-down), `return el, false` from the `Pandoc` or
  `Meta` function skips nothing: the metadata and the blocks are walked
  all the same (checked with pandoc 3.14). Here `skip_children!(ctx)`
  there skips them, as for any node.
- A Lua file returning a list of filters (`return {configure, quotes}`):
  `run_filter([configure, quotes])` (or `walk!([...], doc)`), each a whole
  walk, in turn.
- Vectors count from 1, as Lua's lists (paths in errors, as pandoc's
  JSON, from 0).
- The metadata a Lua filter keeps from its `Meta` function, for the
  functions after: `ctx.doc.meta`.
- `FORMAT`: `ctx.format`, the output format pandoc passed; `nothing` where
  none is known (`walk!` without one; `run_filter` run by hand, without
  pandoc's argument), where Lua's `FORMAT` is always a string:
  `something(ctx.format, "")`.
- Lua's string patterns (`s:match`, `gsub`, `find`) are not regular
  expressions: `%` escapes (`%.`), `-` is a lazy `*`, `%a` is a letter
  (ASCII), `%s` ASCII whitespace, and there is no `|`; a port translates
  each, or escapes a literal string (a function escaping what isn't a
  letter or a digit).
- In `gsub`'s replacement too, `%` is special: `%1` is a capture, `%%` a
  percent sign, and any other `%` an error (checked with pandoc 3.14), so
  a replacement taken from the document (a size of `5%`) fails there.
  Julia's `replace(s, r"..." => text)` puts a plain string in as it is
  (a capture is an `s"\1"` substitution string's).
- Lua's strings are bytes: `#s`, `s:len()`, `s:sub(i, j)` and `s:byte(i)`
  count bytes of UTF-8 (`#"é"` is 2; `s:sub(1, 1)` of it is half a
  character, which pandoc reads back as U+FFFD), and `upper` and `lower`
  change ASCII letters only; `utf8.len(s)` counts characters. Julia's
  strings are UTF-8 indexed by byte too, but `length(s)` counts characters
  and `s[i:j]` fails inside one; where an original depends on bytes,
  `ncodeunits(s)` is `#s` and `codeunit(s, i)` `s:byte(i)` (half a
  character, as pandoc reads it back, is the filter's to make:
  `"\ufffd"`).
- `PANDOC_READER_OPTIONS`: `ctx.conversion.reader_options`, a `Dict`
  keyed as pandoc's JSON (`"track-changes"`, `"tab-stop"`, where Lua's
  fields are `track_changes`, `tab_stop`, and pandoc 2's `trackChanges` is
  gone), with the same values (`"all-changes"`); `nothing` where pandoc
  passed none.
- `PANDOC_WRITER_OPTIONS`: `ctx.conversion.writer_options`, keyed as
  pandoc's JSON keys the reader's (Lua's `number_sections` is
  `"number-sections"`, `top_level_division` `"top-level-division"`,
  `number_offset` `"number-offset"`), with Lua's values
  (`"top-level-chapter"`, `[4, 2]`, `variables` a map of text). libpandoc
  (1.10 and later) gives them to every filter it runs: in process, and as
  `$PANDOC_WRITER_OPTIONS` to a JSON filter, whence `run_filter` reads
  them. pandoc 3.14 gives the writer's options to Lua filters only, so a
  JSON filter under plain pandoc has none (`nothing`), whatever `-N` or
  `--top-level-division` say. Where a host runs the filter in process and
  knows the conversion's options (LibPandoc.jl), `ctx.conversion.options`
  has them in defaults-file keys and values (`"number-sections": true`,
  `"top-level-division": "chapter"`, `"variables"`).
- `pandoc.utils.to_roman_numeral(n)`: `to_roman_numeral(n)`, the same (1
  to 3999; `""` for 0, `"?"` for anything else; not an integer fails).
- `tonumber(s)` on text (an attribute, a metadata value's `stringify`):
  `lua_number(s)`, which reads as Lua does (blanks around, a sign, `"1."`,
  `".5"`, `"1e3"`, `"0x1F"`; an `Int` or a `Float64`; `nothing` for
  anything else, `"inf"` and `"1 2"` included), where `tryparse(Float64,
  s)` reads other text (`"Inf"`, `"NaN"`) and `tryparse(Int, s)` fails on
  some of Lua's (`"1."`, `"1e3"`).
- `pandoc.path`: `path.join({a, b})` is `joinpath(a, b)` (an absolute `b`
  wins, as in pandoc), `path.directory(p)` `dirname(p)` (empty for a bare
  name, where pandoc's is `.`), `path.filename(p)` `basename(p)`,
  `path.is_relative(p)` `!isabspath(p)`. `path.normalize(p)` drops `.`
  and doubled slashes but keeps `..` (`a/b/../c`); `normpath` takes
  `b/..` away too: the same file unless `b` is a symbolic link, and a
  different string, where the original shows the path (a link's rewritten
  target).
- **A filter's globals live as long as the process running it.** pandoc
  loads a Lua filter anew for each document, so its counters start at 0
  each time. A JSON filter under pandoc is a process a document too; but
  LibPandoc.jl (`pandocjl`, `LibPandoc.convert`) loads a script once per
  process and keeps its module: its globals last from one document to the
  next. A filter with state resets it at the start of each document: in a
  first filter of the list (`run_filter([reset, numbering])`, its
  `Pandoc` method), or in a `:topdown` filter's `Pandoc` method, which
  runs before the rest.
- `pandoc.read(text, format, reader_options)` is LibPandoc.jl's
  `LibPandoc.read(text, ctx.conversion)`: the conversion's input format
  and the options that affect reading, as the document was read. As
  `pandoc.read` does, it keeps the text's tabs, where the `pandoc` command
  expands them; and it is confined as `pandoc.read` inside a sandboxed
  conversion is: in pandoc's sandbox (no files read: a LaTeX `\input`, an
  RST or Org include) when the conversion is sandboxed or does not say
  whether (`ctx.conversion.sandbox` is `true` or `nothing`). Many small
  texts (a note each, a table's cells) are one
  `LibPandoc.read_many(texts, ctx.conversion)`: 738 notes take 0.25 s one
  by one, 0.015 s at once.
- pandoc's Lua has no function for a metadata value (its values are
  plain Lua values, reached through the `Meta` function or `doc.meta`);
  here, as in panflute, a method for `MetaValue` (or `MetaList`, ...) is
  called on each one.

#### Metadata

pandoc's Lua gives a filter the metadata as plain Lua values; here each
value is a node, a `MetaValue`, holding what Lua gives in a field
(checked with pandoc 3.14):

| in the document | in Lua (`pandoc.utils.type`) | here |
|---|---|---|
| `x: some text`; also `x: ""`, `x: 1`, `x: no` | `Inlines` | `MetaInlines`: `content`, `Vector{Inline}` |
| `x: \|` and indented lines (blocks) | `Blocks` | `MetaBlocks`: `content`, `Vector{Block}` |
| `-M x=text` on the command line; YAML's null, `x:` | `string` | `MetaString`: `text` |
| `x: true`, `x: false`; `-M x=true` | `boolean` | `MetaBool`: `value` |
| `x: [a, b]` | `List` | `MetaList`: `content`, `Vector{MetaValue}` |
| `x: {k: v}` | `table` | `MetaMap`: `content`, a `Dict{String, MetaValue}` |
| no field | `nil` | `nothing` (`get(meta, "x", nothing)`) |

`pandoc.utils.type(v) == "Inlines"` is `v isa MetaInlines`, or a method
for it (`f(v::MetaInlines) = ...`).

- **Lua's truth**: `if meta.x then` (and `meta.x or d`) is false for a
  missing field and for `false` only; an empty string, list or map is
  true, as is the text `no`. Julia's `if` takes a `Bool` only (a node
  there is an error): `truthy(get(meta, "x", nothing))` is Lua's, and
  Lua's `meta.x or d` is `truthy(x) ? x : d`.
- **A nested option**, Lua's `((meta.a or {}).b or {}).c`: `lookup(meta,
  "a", "b", "c")`, `nothing` where a key is missing or a value on the way
  is not a map (where Lua would fail, on `true`, or find a method, on a
  string or a list). It starts from the metadata or a `MetaMap`.
- **One value or a list of them** (a filter's `ensure_list`, the shapes
  `header-includes` takes): `items(get(meta, "x", nothing))`, a
  `MetaList`'s items, nothing for a missing value, any other value alone;
  a new vector, of the document's values.
- **A value as inlines or blocks**, as Lua converts it where they go
  (`pandoc.Inlines(meta.x)` or `pandoc.Span(meta.x)`; `pandoc.Blocks(...)`
  or `pandoc.Div(...)`): `inlines_of(v)`, `blocks_of(v)`, new vectors
  (copies), or an `ASTTypeError` where Lua fails (checked with pandoc
  3.14):

  | value | `inlines_of(v)` | `blocks_of(v)` |
  |---|---|---|
  | `MetaInlines` | its inlines | a `Plain` of each inline |
  | `MetaString` | its words | a `Plain` of its words |
  | `MetaBlocks` | fails | its blocks |
  | `MetaMap` | none | none |
  | `MetaList` | a `Str` of each item's text, unsplit, if each is a `MetaString`; else fails | a `Plain` of each item's `inlines_of` (failing where it does) |
  | `MetaBool` | fails | fails |

  Each of a value's items as one block (Lua's `pandoc.MetaBlocks{block,
  meta.x}`, as filters write `header-includes`) is
  `blocks_of(MetaList(items(v)))`.
- **Lua's `pairs(v)`** over a metadata value: `pairs(v.content)`, a
  map's entries, or a list's, inlines' or blocks' items by index from 1,
  as Lua's; on text, a boolean or `nil` Lua fails (`bad argument #1 to
  'for iterator'`; checked with pandoc 3.14), as `v.content` does here.
  Lua gives a map's entries in no order, another on each run, so an
  original whose output depends on it isn't deterministic, and a port
  can't reproduce it; here a map is a `Dict`, in no order either:
  `sort!(collect(pairs(m)); by = first)` has pandoc's JSON's (its keys
  sorted, by code point).
- **Writing metadata** (Lua's `meta.x = v`): setting a field converts a
  plain value as Lua does (above, in "The AST"); reading needs no
  conversion: a `MetaMap`'s content is a `Dict`, a `MetaList`'s a
  `Vector`, `MetaInlines`' a `Vector{Inline}`.
- **Lua's tables are references** until pandoc converts the metadata back:
  a table put in two places is two values after (scholarly-metadata merges
  one institute into another). Here a node in two places is one node: copy
  it (`deepcopy`), as any node.
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
  Here the test is `v isa MetaList` (pandoc 3's
  `pandoc.utils.type(v) == "List"`); a port of what the original does now
  takes the other branch too.

```julia
meta = Pandoc(; meta = Dict("draft" => false, "title" => "", "toc" => Dict("depth" => "2"), "tags" => "a")).meta
truthy(get(meta, "draft", nothing)), truthy(meta["title"]), truthy(get(meta, "missing", nothing))  # (false, true, false)
draft = meta["draft"]; truthy(draft) ? draft : MetaString("final")  # MetaString("final")
lookup(meta, "toc", "depth"), lookup(meta, "title", "depth")  # (MetaString("2"), nothing)
items(meta["tags"]), items(get(meta, "missing", nothing))  # ([MetaString("a")], MetaValue[])
meta["name"] = [Str("Ada"), Space(), Str("L.")]  # MetaInlines(Str("Ada"), Space(), Str("L."))
inlines_of(meta["tags"]), blocks_of(meta["name"])  # ([Str("a")], [Plain(Str("Ada")), Plain(Space()), Plain(Str("L."))])
to_roman_numeral(2026), to_roman_numeral(0), to_roman_numeral(4000)  # ("MMXXVI", "", "?")
Conversion(; reader_options = Dict("track-changes" => "all-changes")).reader_options["track-changes"]  # "all-changes"
Conversion(; writer_options = Dict("number-sections" => true)).writer_options["number-sections"]  # true
lua_number(" 0x1F "), lua_number("1e3"), lua_number("1 2")  # (31, 1000.0, nothing)
replace("width: SIZE", "SIZE" => "\\1 5%")  # "width: \\1 5%"
length("«»"), ncodeunits("«»"), codeunit("«»", 1)  # (2, 4, 0xc2)
```

### Options in a code block

A code block may start with a YAML header, its options, before its data
(panflute's `yaml_filter`; pantable's tables):

````markdown
```{.table include="sales.csv"}
---
caption: "*Sales* by region"
alignment: RC
---
```
````

panir reads no YAML: pandoc does, the header read as a document's
metadata block, so a block's options and the document's metadata are the
same type, and one helper takes both: a filter's defaults are the
document's metadata under the filter's name (`pantable:`), a block's
header replaces a key's whole value (as pandoc merges metadata blocks).

- `yaml_block(text)` splits a text by pandoc's rule for a YAML metadata
  block, where one starts the text: `(header, data)`, the header the lines
  between `---` and `---` or `...` (each perhaps followed by spaces or
  tabs; not indented, not four of them; the line after the opening not
  blank), `(nothing, text)` if there is none. A `---` line later opens
  nothing: text appended to the block (an include) is never read as
  options. `corpus/yaml-blocks.jsonl` has the cases, with pandoc's
  verdict on each.
- `Options(meta, name, header)`: the options, a dictionary of metadata
  values, read raw (`options["caption"]`, whose inlines a caption wants)
  or converted: `Panir.text`, `Panir.boolean`, `Panir.number` (text read
  as `lua_number` reads it), `Panir.list`, `Panir.dict` (as plain data),
  each `nothing` for a missing key or a value of another shape (not
  exported: common words).
- `to_data(v)`: metadata (a value, the metadata, `Options`) as plain data,
  a `MetaInlines` or `MetaBlocks` its text (`stringify`); text stays
  text.
- `yaml_filter(f, doc, classes, name, read)` does it all: each code block
  with one of the classes is replaced by what `f(options, data, block)`
  returns, as a filter method's. `read` (texts of markdown in, documents
  out: LibPandoc.jl's `read_many`, or the `pandoc` command) is called
  once, with every header; not at all if no block has one. A header
  pandoc reads no metadata from (a YAML scalar or list) is no header: the
  whole text is data, as pandoc would read it.

```julia
yaml_block("---\ncaption: Sales\n---\nregion,sales\n")  # ("caption: Sales\n", "region,sales\n")
yaml_block("region,sales\n---\ncaption: Sales\n---\n")  # (nothing, "region,sales\n---\ncaption: Sales\n---\n")

# as pandoc reads "---\ncaption: Sales\n---\n" (in a filter:
# LibPandoc.read_many(texts, ctx.conversion; from = "markdown"), or the pandoc command)
read_headers(texts) = [Pandoc(; meta = Dict{String, MetaValue}("caption" => MetaInlines(Str("Sales")))) for _ in texts]
tables = Pandoc(CodeBlock("---\ncaption: Sales\n---\na,b"; classes = ["table"]), CodeBlock("c,d"; classes = ["table"]);
                meta = Dict{String, MetaValue}("pantable" => MetaMap(Dict("caption" => MetaString("Tables")))))
yaml_filter(tables, "table", "pantable", read_headers) do options, data, block
    [Para(something(Panir.text(options, "caption"), "")), CodeBlock(data)]
end
tables.blocks  # [Para("Sales"), CodeBlock("a,b"), Para("Tables"), CodeBlock("c,d")]
```

What pandoc makes of a header (checked with pandoc 3.14): `true` and
`True` are booleans (`yes` is text); numbers are text, as pandoc writes
them (`010` is `"10"`, `1e3` `"1000"`, `0.4` `"0.4"`); `null` and `~`
are empty text; text is read as markdown, so a caption arrives as
inlines, but a text of only a tab is lost, `'` is a curly quote and
`...` an ellipsis (`-f markdown-smart` keeps them), and escapes,
entities and raw HTML are read as markdown reads them; a `|` scalar is
blocks. YAML pandoc can't read fails the read, as it fails the document.

### Fragments

`walk!` also applies to any other node, or a `Vector{Inline}` or
`Vector{Block}`, as Lua's `el:walk{...}` does. As in Lua, the methods
apply to what is in a node but not to the node itself, a list's method
also to the list itself, and the metadata and `Pandoc` methods only to a
document. Unlike Lua, the fragment is changed in place (a list too), and
returned:

```julia
loud(s::Str) = uppercase(s.text)
walk!(loud, Emph("a", Str("b")))  # Emph(Inline[Str("A"), Str("B")])
```

A context's `path` is then from the fragment, and its `doc` is `nothing`.
Its conversion is the one given (`walk!(f, el; format = conversion)`);
none given inside a filter method, the conversion of the walk that
method is in, as Lua's `FORMAT` and `PANDOC_READER_OPTIONS` are the same
everywhere.

## Deep documents

Reading, writing, walking, comparing, copying and `stringify` recurse, a
few calls for each level of the document. Real documents are shallow (38
levels in web pages through `pandoc -f html`, 13 in a thousand Markdown
documents); measured with Julia 1.12 on Linux, with the usual 8 MB stack
(levels of nested divs / tables in tables' cells / nested emphasis),
about:

| | levels |
|---|---|
| `Panir.parse` | 680 / 220 / 1,000 |
| `Panir.fromjson` | 3,100 / 1,100 / 5,100 |
| `walk!`, every order | 255 / 63 / 255 (`Panir.MAX_DEPTH`) |
| `deepcopy` | 10,000 / 2,500 / 10,000 |
| `hash`, `==` | 18,000 / 2,800 / 26,000 |
| `serialize`, `Panir.tojson` | 20,000 / 5,300 / 25,000 |
| `stringify` | 34,000 / 8,100 / 43,000 |

Beyond, `Panir.parse` raises `ASTDecodeError` (JSON.jl stops at 2,048
levels of JSON, which comes first), and anything else Julia's own
`StackOverflowError`. panir doesn't catch that: Julia itself warns that
after one "program state may be corrupted".

`walk!` stops sooner, by itself: at `Panir.MAX_DEPTH` (256) nodes in one
another (as Rust's panir reads and walks; a table in a table's cell is
four, the table, its body, row and cell), counting a walk a method starts
on from where the walk it is in is. Deeper is an `ASTDepthError` with the
path, so that a top-down method whose result holds what it matched (each
result walked in turn, without end: pandocfilters' metavars with a field
naming itself) is an error, where it ran out of stack and then didn't end.
A method that calls itself without end is Julia's `StackOverflowError`,
which panir can't see.

## Also

`Panir.parse`/`Panir.serialize` for pandoc's JSON text;
`Panir.fromjson(T, j)`/`Panir.tojson(x)` for parsed JSON, of any type
(`fromjson(Str, j)`, `fromjson(Inline, j)`). Reading says where the JSON is
wrong (`ASTDecodeError`, text that isn't JSON too). `stringify` is pandoc's
Lua `pandoc.utils.stringify`: a document's text is its inlines' (its
metadata's too, but no `MetaString` or `MetaBool`), metadata's has its
strings and booleans too; `to_roman_numeral` is its
`pandoc.utils.to_roman_numeral`, and `lua_number` Lua's `tonumber` on
text.

Status: a prototype.
