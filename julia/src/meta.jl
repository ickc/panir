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
