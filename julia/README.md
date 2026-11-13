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

- A sum type is an abstract type (`Inline`, `Block`, `MetaValue`), each
  constructor a mutable struct subtyping it (`Str <: Inline`); products are
  structs (`Attr`, `Cell`, `Pandoc`); enum-like types are `@enum`s
  (`InlineMath`).
- Julia checks field types itself: `push!(para.content, Para())` and
  `h.level = "2"` are errors.
- Two names are not exported, since Julia has them: `Panir.Meta` (the
  metadata's type; `Meta` is `Base.Meta`) and `Panir.Period` (a list's
  delimiter; `Period` is `Dates`'s).
- Constructors: every field positionally (`Header(1, Attr(), Inline[])`),
  or the content as arguments with the rest as keywords:
  `Header(1, Str("Hi"); identifier = "hi")`, `Link(Str("x"); url = "u")`.
  The content may also be one vector: `Div(blocks; identifier = "d")`
  (except where the content is a list of lists: `BulletList([Plain("a")])`
  is one item).
- Strings convert as in pandoc's Lua: where a list of inlines goes, a string
  is its words and spaces (`Para("hello world")`, `h.content = "Intro"`);
  where one inline goes, a `Str` (`push!(p.content, "x")`, `Para("a", Emph("b"))`);
  where blocks go, `Plain` text. `inlines("...")` and `blocks("...")` build
  such lists.
- `walk!(f, doc)` calls `f(x, ctx)` or `f(x)` wherever `f` has a method for
  `x`, so the most specific method wins, by dispatch. Return `nothing` to
  keep a node, a node to replace it, or a vector to splice in its place.
  `ctx` is a `Context`: `parent`, `field`, the `container` the node is in
  and its `index` there (`ctx.container[ctx.index]` is the node), `next` and
  `prev`, `path` (from the document, counting from 0 as pandoc's JSON and
  errors do), `doc`, `format`, and `conversion` (a `Conversion`, as in
  Python: formats with extensions and reader options, when pandoc or
  libpandoc tells them). As in Lua, what a method returns are values:
  `[el, el]` puts two nodes in the document, the second a copy; elsewhere,
  to put a node in a second place, `deepcopy` it.
  As in pandoc's Lua filters, a method for `Vector{Inline}` or
  `Vector{Block}` gets every list of them, and one for `Panir.Meta` the
  metadata (`Meta` alone is `Base.Meta`); methods for anything don't count
  there.
- `walk!(f, doc; traverse)` sets the order, one of the three filter
  frameworks use: `:typewise` (the default, as pandoc's Lua filters and
  Haskell's `walk`: one walk per kind, each bottom-up, every inline before
  any block); `:topdown` (Lua's other order, pandocfilters': a node before
  its children, which `skip_children!(ctx)` skips); `:bottomup` (panflute's:
  one walk, each node after its children). They are checked against pandoc's
  Lua filters, with the scenarios in [`corpus/filters/`](../corpus/filters/).
- `run_filter(f)` runs `f` as a pandoc JSON filter. A host running filter
  scripts in its own process (LibPandoc.jl's `pandocjl`) calls the script
  inside `Panir.handoff`, and then `run_filter` hands `f` over instead.
- `Panir.parse`/`serialize` for pandoc's JSON; parsing reports where
  JSON is wrong (`ASTDecodeError`). `stringify` for text.

Status: a prototype.
