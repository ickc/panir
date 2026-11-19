# A filter's options, from the document's metadata and from a code block's
# YAML header, read by pandoc (panflute's `yaml_filter`): `yaml_block` splits
# a block's text into its header and its data, by pandoc's rule for a YAML
# metadata block; pandoc reads the header (panir reads no YAML); `Options`
# puts it over the document's metadata under the filter's name;
# `yaml_filter` does it all for a document, with one call of the reader for
# all of its blocks.

"""
    yaml_block(text) -> (header, data)

A text's YAML header and its data, by pandoc's rule for a YAML metadata
block, where one starts the text: the first line is `---`, the next is not
blank, and a later line is `---` or `...` (each perhaps followed by spaces
or tabs; not indented, not four of them). The header is the lines between,
each with its newline (`""` for `---` then `---`); the data is everything
after the closing line. No header: `(nothing, text)`. A `---` line later in
the text opens nothing: text appended to the block (an include) is never
read as options.

```julia
yaml_block("---\\ncaption: Sales\\n---\\na,b\\n")  # ("caption: Sales\\n", "a,b\\n")
```
"""
function yaml_block(text::AbstractString)
    s = String(text)
    newline = findfirst('\n', s)
    (newline === nothing || !occursin(r"^---[ \t]*$", s[1:prevind(s, newline)])) && return (nothing, s)
    start = at = newline + 1
    while true
        e = findnext('\n', s, at)
        line = e === nothing ? s[at:end] : s[at:prevind(s, e)]
        at == start && occursin(r"^[ \t]*$", line) && return (nothing, s)
        occursin(r"^(?:---|\.\.\.)[ \t]*$", line) && return (s[start:prevind(s, at)], e === nothing ? "" : s[e+1:end])
        e === nothing && return (nothing, s)
        at = e + 1
    end
end

"""
    to_data(v)

Metadata as plain data, as a filter reads its options: a `MetaBool` a
`Bool`, a `MetaString` a `String`, `MetaInlines` and `MetaBlocks` their
text (`stringify`), a `MetaList` a `Vector{Any}`, a `MetaMap` (or the
metadata, or `Options`) a `Dict{String, Any}` (panflute's
`get_metadata(builtin=True)`). Nothing is guessed: text stays text, `"10"`
too (`lua_number` reads a number).
"""
to_data(v::MetaBool) = v.value
to_data(v::MetaString) = v.text
to_data(v::Union{MetaInlines, MetaBlocks}) = stringify(v)
to_data(v::MetaList) = Any[to_data(x) for x in v.content]
to_data(v::MetaMap) = to_data(v.content)
to_data(m::AbstractDict) = Dict{String, Any}(k => to_data(x) for (k, x) in m)

"""
    Options(meta, name, header = nothing)

A filter's options: the options under `name` in the document's metadata
(`meta`; ignored unless a map), each key's value replaced whole by the
header's (`header`: a block's header, read as a document's metadata), as
pandoc merges metadata blocks: no deeper merge. The values are the
documents' own, not copies.

A dictionary of metadata values, read raw (`options["caption"]`, `get`: a
caption wants its inlines), or converted on request: `Panir.text`,
`Panir.boolean`, `Panir.number`, `Panir.list`, `Panir.dict`, each
`nothing` where the key is missing or its value is of another shape.

What pandoc makes of a header (read as a metadata block, `-f markdown`;
checked with pandoc 3.14): `true` and `True` are booleans (`yes` is text);
numbers are text, as pandoc writes them (`010` is `"10"`, `1e3` `"1000"`);
`null` and `~` are empty text; text is read as markdown, so a caption
arrives as inlines, but a text of only a tab is lost, `'` is a curly quote
and `...` an ellipsis (`-f markdown-smart` keeps them), escapes, entities
and raw HTML are read; a `|` scalar is blocks.

```julia
options = Options(doc.meta, "pantable", header_doc.meta)
Panir.number(options, "width")  # 0.4, from "0.4"
```
"""
struct Options <: AbstractDict{String, MetaValue}
    values::Dict{String, MetaValue}
end

function Options(meta::AbstractDict, name::AbstractString, header::Union{Nothing, AbstractDict} = nothing)
    defaults = get(meta, name, nothing)
    values = defaults isa MetaMap ? copy(defaults.content) : Dict{String, MetaValue}()
    header === nothing || merge!(values, header)
    Options(values)
end

Base.length(o::Options) = length(o.values)
Base.iterate(o::Options, state...) = iterate(o.values, state...)
Base.get(o::Options, key, default) = get(o.values, key, default)
Base.haskey(o::Options, key) = haskey(o.values, key)
Base.getindex(o::Options, key) = o.values[key]

"Text (a `MetaString`), or the text of inlines or blocks; `nothing` for anything else."
function text(o::Options, key)
    v = get(o, key, nothing)
    v isa MetaString ? v.text : v isa Union{MetaInlines, MetaBlocks} ? stringify(v) : nothing
end

"A `MetaBool`'s value; `nothing` for anything else."
boolean(o::Options, key) = (v = get(o, key, nothing); v isa MetaBool ? v.value : nothing)

"Text read as a number, as `lua_number` reads it; `nothing` for anything else."
number(o::Options, key) = (t = text(o, key); t === nothing ? nothing : lua_number(t))

"A `MetaList`'s items, as plain data (`to_data`); `nothing` for anything else."
list(o::Options, key) = (v = get(o, key, nothing); v isa MetaList ? to_data(v) : nothing)

"A `MetaMap`, as plain data (`to_data`); `nothing` for anything else."
dict(o::Options, key) = (v = get(o, key, nothing); v isa MetaMap ? to_data(v) : nothing)

"""
    yaml_filter(f, doc, classes, name, read)

Code blocks with a YAML header, panflute's `yaml_filter`: each code block
with one of `classes` (a string or several) is replaced by what
`f(options, data, block)` returns, as a filter method's (`nothing` keeps
it, a block replaces it, a vector is spliced, an empty one deletes it).
`options` is `Options(doc.meta, name, header)`, `data` the text after the
header (`yaml_block`), or all of it.

`read` reads texts of markdown into documents, one each: the host's
(LibPandoc.jl's `read_many`), or the `pandoc` command. It is called once,
with every header as a document (`---`, the header, `---`); not at all if
no block has one. A header pandoc reads no metadata from (a YAML scalar or
list: blocks come back) is no header, and the whole text is data, as
pandoc would read it. Returns the document, changed in place.

```julia
function tables(doc::Pandoc, ctx)
    read(texts) = LibPandoc.read_many(texts, ctx.conversion; from = "markdown")
    yaml_filter(doc, "table", "pantable", read) do options, data, block
        [Para(something(Panir.text(options, "caption"), "")), CodeBlock(data)]
    end
end
```
"""
function yaml_filter(f, doc::Pandoc, classes, name::AbstractString, read)
    wanted = classes isa AbstractString ? (classes,) : classes
    found = CodeBlock[]
    walk!((b::CodeBlock) -> (any(in(wanted), b.attr.classes) && push!(found, b); nothing), doc)
    splits = [yaml_block(b.text) for b in found]
    headers = ["---\n" * h * "---\n" for (h, _) in splits if h !== nothing]
    docs = isempty(headers) ? Pandoc[] : read(headers)
    todo = IdDict{CodeBlock, Tuple{Options, String}}()
    k = 0
    for (b, (header, data)) in zip(found, splits)
        meta = nothing
        if header !== nothing
            got = docs[k += 1]
            if isempty(got.blocks)
                meta = got.meta
            else
                data = b.text
            end
        end
        todo[b] = (Options(doc.meta, name, meta), data)
    end
    walk!(doc) do b::CodeBlock
        args = get(todo, b, nothing)
        args === nothing ? nothing : f(args..., b)
    end
end
