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
values, `walk!`, `run_filter`, `Context`, `stringify` and the errors.
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
  `Cell("a"; col_span = 2)`. Attributes go as `identifier`, `classes`,
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
  row_head_columns; attr)` (all but `body` may be left out). These four
  also take every field by keyword: `Table(; bodies, identifier = "t")`.
- attributes are (key, value) tuples, in order; pairs are taken too
  (`attributes = ["lang" => "en"]`), and a `Dict`, in the order of its
  keys (a Lua table of them has no order either).
- a constructor copies the lists and maps it is given: a node's lists are
  its own, which the caller may go on changing or give to another node.
  The nodes in them are not copied.
- `show` writes what makes the node again; a document a block a line.

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
it, or, in a list, a vector to splice in its place (empty: delete). What
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

As in Lua, what a method returns are values: `[el, el]` puts two nodes in
the document, the second a copy. Elsewhere a node belongs in one place: to
put it in a second, `deepcopy` it, or whatever changes one later changes
both.

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
Python: formats with extensions and reader options, when pandoc or
libpandoc tells them). They are right after earlier nodes of the list
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
  false`).
- `:bottomup` (panflute's): one walk, each node after its children, a list
  after its elements, the metadata after what is in it, `Pandoc` last.

They are checked against pandoc's Lua filters: each scenario in
[`corpus/filters/`](https://github.com/ickc/panir/tree/main/corpus/filters)
is a Lua filter, and its Julia version must make exactly the same document.

`run_filter(f)` runs `f` as a pandoc JSON filter: a document on stdin, to
stdout, `ctx.conversion` read from pandoc's arguments and environment. A
host running filter scripts in its own process (LibPandoc.jl's
`pandocjl`) runs the script inside `Panir.handoff`, and then `run_filter`
hands `f` over instead; a function like `run_filter` of the host's own
calls `Panir.hand_over(f, traverse)`, which does the same.

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
| `walk!`, every order | 7,400 / 1,400 / 7,900 |
| `deepcopy` | 10,000 / 2,500 / 10,000 |
| `hash`, `==` | 18,000 / 2,800 / 26,000 |
| `serialize`, `Panir.tojson` | 20,000 / 5,300 / 25,000 |
| `stringify` | 34,000 / 8,100 / 43,000 |

Beyond, `Panir.parse` raises `ASTDecodeError` (JSON.jl stops at 2,048
levels of JSON, which comes first), and anything else Julia's own
`StackOverflowError`. panir doesn't catch that: Julia itself warns that
after one "program state may be corrupted".

## Also

`Panir.parse`/`Panir.serialize` for pandoc's JSON text;
`Panir.fromjson(T, j)`/`Panir.tojson(x)` for parsed JSON, of any type
(`fromjson(Str, j)`, `fromjson(Inline, j)`). Reading says where the JSON is
wrong (`ASTDecodeError`, text that isn't JSON too). `stringify` is pandoc's
Lua `pandoc.utils.stringify`: a document's text is its inlines' (its
metadata's too, but no `MetaString` or `MetaBool`), metadata's has its
strings and booleans too.

Status: a prototype.
