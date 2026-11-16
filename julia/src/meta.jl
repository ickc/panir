# Metadata as pandoc's Lua filters read it: Lua's truth, a lookup through
# maps, one value or a list of them, a value as inlines or blocks; and
# pandoc.utils.to_roman_numeral.

"""
    truthy(v) -> Bool

Whether a metadata value is true as pandoc's Lua sees it (`if meta.x then`,
`meta.x or d`): there (not `nothing`), and not `MetaBool(false)`. An empty
string, list or map is true, as the text `false` is. (`if v` takes a
`Bool` only: a node there is an error.)

```julia
truthy(get(doc.meta, "draft", nothing))
```
"""
truthy(::Nothing) = false
truthy(v::MetaBool) = v.value
truthy(::MetaValue) = true

"""
    lookup(v, key, keys...) -> MetaValue or nothing

The metadata value at a path of keys, each but the last in a map: Lua's
`((meta.a or {}).b or {}).c`, `nothing` where a key is missing or a value
on the way is not a map (where Lua would fail, on `true`, or find a
method, on a string or a list). `v` is the metadata (`doc.meta`), a
`MetaMap`, or `nothing`.

```julia
depth = lookup(doc.meta, "toc", "depth")
```
"""
function lookup(v, key::AbstractString, keys::AbstractString...)
    for k in (key, keys...)
        map = _entries(v)
        map === nothing && return nothing
        v = get(map, k, nothing)
    end
    v
end
_entries(m::AbstractDict) = m
_entries(m::MetaMap) = m.content
_entries(_) = nothing

"""
    items(v) -> Vector{MetaValue}

One value or a list of them, as a list: a `MetaList`'s items, nothing for
`nothing`, any other value alone (a filter's `ensure_list`; the shapes
`header-includes` takes). A new vector, of the document's values.

```julia
for author in items(get(doc.meta, "author", nothing)) ... end
```
"""
items(::Nothing) = MetaValue[]
items(v::MetaList) = copy(v.content)
items(v::MetaValue) = MetaValue[v]

const _AS_INLINES = "inlines (MetaInlines, MetaString, MetaMap, or a MetaList of MetaStrings)"

"""
    inlines_of(v) -> Vector{Inline}

A metadata value as inlines, as pandoc's Lua converts it where inlines go
(`pandoc.Inlines(meta.x)`, `pandoc.Span(meta.x)`): inlines as they are
(copies, as Lua's are values), text its words, a map none, a list a `Str`
of each item's text, unsplit, if every item is text (a `MetaString`, as
`-M` makes); blocks, a boolean or a list of anything else fail, as in Lua
(`ASTTypeError`).

```julia
symbol = inlines_of(doc.meta["qed-symbol"])
```
"""
inlines_of(v::MetaValue) = _inlines_of(v, Any[], _AS_INLINES)

_inlines_of(v::MetaInlines, _, _) = deepcopy(v.content)
_inlines_of(v::MetaString, _, _) = inlines(v.text)
_inlines_of(::MetaMap, _, _) = Inline[]
function _inlines_of(v::MetaList, path, _)
    out = Inline[]
    for (i, x) in enumerate(v.content)
        x isa MetaString || throw(ASTTypeError(Any[(isempty(path) ? ("MetaList",) : path)..., :content, i - 1],
                                               "an inline (a MetaString)", string(nameof(typeof(x))), ""))
        push!(out, Str(x.text))
    end
    out
end
_inlines_of(v::MetaValue, path, expected) = throw(ASTTypeError(path, expected, string(nameof(typeof(v))), ""))

"""
    blocks_of(v) -> Vector{Block}

A metadata value as blocks, as pandoc's Lua converts it where blocks go
(`pandoc.Blocks(meta.x)`, `pandoc.Div(meta.x)`): blocks as they are
(copies), text a `Plain` of its words, a map none, inlines a `Plain` of
each inline (Lua's, as inlines are a list), a list a `Plain` of each item
as `inlines_of` makes it; a boolean, or a list holding blocks, a boolean
or a list of other than text, fail, as in Lua (`ASTTypeError`). So
`blocks_of(MetaList(items(v)))` is each of `v`'s items as one block, as a
Lua list of them given to `pandoc.Blocks` (or `pandoc.MetaBlocks`, as
filters write `header-includes`).
"""
blocks_of(v::MetaBlocks) = deepcopy(v.content)
blocks_of(v::MetaInlines) = Block[Plain(Inline[x]) for x in deepcopy(v.content)]
blocks_of(v::MetaString) = blocks(v.text)
blocks_of(::MetaMap) = Block[]
function blocks_of(v::MetaList)
    expected = "a block (" * chopprefix(_AS_INLINES, "inlines (")
    Block[Plain(_inlines_of(x, Any["MetaList", :content, i - 1], expected)) for (i, x) in enumerate(v.content)]
end
blocks_of(v::MetaValue) = throw(ASTTypeError(Any[], "blocks (MetaBlocks, MetaInlines, MetaString, MetaMap, or a MetaList)",
                                             string(nameof(typeof(v))), ""))

const _ROMAN = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
                (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))

"""
    to_roman_numeral(n::Integer) -> String

An integer in Roman numerals, as pandoc's Lua
`pandoc.utils.to_roman_numeral`: 1 to 3999 (`"MMXXVI"`), `""` for 0, and
`"?"` for anything else, negative or 4000 and more. (Not a `Bool`: Lua
fails on one.)
"""
function to_roman_numeral(n::Union{Signed, Unsigned})
    0 <= n < 4000 || return "?"
    out = IOBuffer()
    for (value, letters) in _ROMAN
        count, n = divrem(n, value)
        print(out, letters^count)
    end
    String(take!(out))
end

"""
    lua_number(text) -> Union{Int, Float64, Nothing}

A number as Lua's `tonumber(text)` reads one: decimal (`"12"`, `"1.5"`,
`".5"`, `"1e3"`) or a hexadecimal integer (`"0x1F"`), a sign before it,
ASCII blanks around it; an `Int` where Lua's is an integer, else a
`Float64`; `nothing` for anything else (`""`, `"1 2"`, `"inf"`), where
Lua's is `nil`. Not Lua's hexadecimal fractions and exponents
(`0x1.8p1`), nor its wrapping of a hexadecimal integer beyond 64 bits. For
ports reading a number from an attribute or the metadata as their Lua
original did (`tonumber(el.attributes.n)`).
"""
function lua_number(text::AbstractString)
    t = strip(text, (' ', '\t', '\n', '\v', '\f', '\r'))
    if occursin(r"^[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$", t)
        n = occursin(r"^[+-]?[0-9]+$", t) ? tryparse(Int, t) : nothing
        return n === nothing ? Base.parse(Float64, t) : n
    end
    m = match(r"^([+-]?)0[xX]([0-9a-fA-F]+)$", t)
    m === nothing && return nothing
    n = something(tryparse(Int, m[2]; base = 16), Float64(Base.parse(BigInt, m[2]; base = 16)))
    m[1] == "-" ? -n : n
end
