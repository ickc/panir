# Metadata as pandoc's Lua filters read it: Lua's truth, a lookup through
# maps, one value or a list of them.

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
