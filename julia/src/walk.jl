# Walking a document, and filters as functions with a method per node type.

"""
    Conversion(; format, input_format, output_format, reader_options, options)

The pandoc conversion a filter runs in, as panir's Python `Conversion`: the
output format's name (`format`, what pandoc passes a JSON filter), and,
when known, the input and output formats with extensions, the reader's
options and the conversion's options (defaults-file keys). A filter method
finds it in `ctx.conversion`. `Conversion(args, env)` reads it as a JSON
filter under pandoc gets it: the output format as the first argument,
`\$PANDOC_READER_OPTIONS`, and `\$PANDOC_INPUT_FORMAT` and
`\$PANDOC_OUTPUT_FORMAT` where set (libpandoc sets them; proposed to
pandoc in jgm/pandoc#11016).
"""
Base.@kwdef struct Conversion
    format::Union{Nothing, String} = nothing
    input_format::Union{Nothing, String} = nothing
    output_format::Union{Nothing, String} = nothing
    reader_options::Union{Nothing, AbstractDict} = nothing
    options::Union{Nothing, AbstractDict} = nothing
end

function Conversion(args::AbstractVector{<:AbstractString}, env::AbstractDict)
    nonempty(k) = (v = get(env, k, ""); isempty(v) ? nothing : String(v))
    reader = nonempty("PANDOC_READER_OPTIONS")
    Conversion(; format = isempty(args) ? nothing : String(args[1]),
               input_format = nonempty("PANDOC_INPUT_FORMAT"),
               output_format = nonempty("PANDOC_OUTPUT_FORMAT"),
               reader_options = reader === nothing ? nothing : JSON.parse(reader))
end

_conversion(c::Conversion) = c
_conversion(format::Nothing) = Conversion()
_conversion(format::AbstractString) = Conversion(; format = String(format))

"""
Where a node is, for a filter method that takes it: `parent` (the node whose
field holds it), `field`, `container` (the list it is in, or the map) and
its `index` there (or key), so that `ctx.container[ctx.index]` is the node;
`next` and `prev`, its neighbours in that list (or `nothing`); `path` from
the document, as in errors and in pandoc's JSON, which count from 0
(`[:blocks, 0, :content, 3]`); `doc`, `conversion` (the pandoc run, a
`Conversion`) and `format` (its output format's name).

A method may change `container` itself (remove the node before this one,
say); it may not take its own node out of it (return an empty vector).
"""
struct Context
    parent::Any
    field::Union{Nothing, Symbol}
    container::Any
    index::Any
    path::Vector{Any}
    doc::Any
    conversion::Conversion
    format::Union{Nothing, String}
    topdown::Bool
    skip::Base.RefValue{Bool}
end

"The neighbour of a node in its list, `step` after it, or `nothing`."
function sibling(ctx::Context, step::Integer)
    container, index = getfield(ctx, :container), getfield(ctx, :index)
    container isa AbstractVector && index isa Integer || return nothing
    i = index + step
    1 <= i <= length(container) ? container[i] : nothing
end

Base.propertynames(::Context) = (fieldnames(Context)..., :next, :prev)
Base.getproperty(ctx::Context, name::Symbol) =
    name === :next ? sibling(ctx, 1) : name === :prev ? sibling(ctx, -1) : getfield(ctx, name)

"""
    skip_children!(ctx)

Don't walk the node's children, or its replacement's (Lua's `return el,
false`). Only with `traverse = :topdown`: otherwise the children were walked
first.
"""
function skip_children!(ctx::Context)
    ctx.topdown || throw(ArgumentError(
        "skip_children! needs traverse = :topdown (otherwise the children were walked first)"))
    ctx.skip[] = true
    nothing
end

Base.show(io::IO, ctx::Context) =
    print(io, "Context(at ", _format_path(ctx.path), ", parent ", typeof(ctx.parent), ")")

# Where the walk is, a step at a time: a field or an index, after what came
# before. (A vector copied and extended for every node took time by the
# depth of the document: a second for 8,000 nested Emph.)
struct _Path
    before::Union{Nothing, _Path}
    step::Any
end

function _steps(path::Union{Nothing, _Path})
    steps = Any[]
    while path !== nothing
        push!(steps, path.step)
        path = path.before
    end
    reverse!(steps)
end

const _Frame = Tuple{Any, Symbol, Any, Any, Union{Nothing, _Path}}  # parent, field, container, index, path

# Which nodes a walk calls the filter on, and which lists: bits. (As
# closures they were compiled again for every type of node.)
const _INLINES, _BLOCKS, _OTHERS, _DOCUMENT = 0x01, 0x02, 0x04, 0x08
_kind(::Inline) = _INLINES
_kind(::Block) = _BLOCKS
_kind(::Pandoc) = _DOCUMENT
_kind(::Node) = _OTHERS
_kind(::Vector{Inline}) = _INLINES
_kind(::Vector{Block}) = _BLOCKS
_kind(::Any) = 0x00  # (other lists: no function of Lua's is for them)

# Not parametric on the filter: the call into it is dynamic anyway (a method
# per node type), and one walker compiled once serves every filter.
mutable struct _Walker
    f::Any
    topdown::Bool
    doc::Any
    conversion::Conversion
    stack::Vector{_Frame}
    wants::UInt8   # which nodes this walk calls `f` on (_INLINES | ...)
    lists::UInt8   # which lists
    arity::Dict{DataType, Int}   # by node type: 2 for f(x, ctx), 1 for f(x), 0 for neither
end

# `f`'s method for `x`: with the context (2), without (1), or none (0);
# found once per type (`applicable` for every node is slow).
_arity(w::_Walker, x) = get!(() -> _method_arity(w.f, typeof(x)), w.arity, typeof(x))

# With both, the method that is more specific for the node (f(s::Str) over
# f(x, ctx)): the most specific method wins, as Julia dispatches. A tie
# goes to the one with the context.
function _method_arity(@nospecialize(f), T::Type)
    with, without = hasmethod(f, Tuple{T, Context}), hasmethod(f, Tuple{T})
    with && without || return with ? 2 : without ? 1 : 0
    a, b = _first(which(f, Tuple{T, Context})), _first(which(f, Tuple{T}))
    b <: a && !(a <: b) ? 1 : 2
end

# The type of a method's first argument: `T`'s bound in f(x::T) where T, the
# element's type in f(xs...), `Union{}` for a method without arguments.
function _first(m::Method)
    ps = Base.unwrap_unionall(m.sig).parameters
    length(ps) >= 2 || return Union{}
    p = Base.rewrap_unionall(Base.unwrapva(ps[2]), m.sig)
    p isa Type ? p : Any
end

function _context(w::_Walker)
    parent, field, container, index, path = isempty(w.stack) ? (nothing, :_, nothing, nothing, nothing) : w.stack[end]
    Context(parent, isempty(w.stack) ? nothing : field, container, index, _steps(path), w.doc, w.conversion,
            w.conversion.format, w.topdown, Ref(false))
end

# `f` on `x` (with the context if it has such a method); the result, and
# whether to skip the children
function _call(w::_Walker, x)
    w.wants & _kind(x) == 0x00 && return nothing, false
    n = _arity(w, x)
    n == 0 && return nothing, false
    n == 1 && return w.f(x), false
    ctx = _context(w)
    r = w.f(x, ctx)
    r, ctx.skip[]
end

# Whether `f` has a method for `x` itself, not only one for anything: lists
# and the metadata are values any method would take.
function _specific(@nospecialize(f), x)
    for args in (Tuple{typeof(x), Context}, Tuple{typeof(x)})
        hasmethod(f, args) || continue
        _first(which(f, args)) === Any || return true
    end
    false
end

# `f` on a list or the metadata, if it has a method for it: the result, and
# whether to skip what is in it
function _call_whole(w::_Walker, x)
    _specific(w.f, x) || return nothing, false
    ctx = _context(w)
    r = _arity(w, x) == 2 ? w.f(x, ctx) : w.f(x)
    r, ctx.skip[]
end

# `items`, each node once: one that is there twice is copied where it comes
# again. As in pandoc's Lua filters, whose results are values: the nodes of
# `return {el, el}` are two, changed separately from then on. (One node in
# two places of a document would be changed in both by whatever changes it
# later.)
function _distinct(items::Vector)
    length(items) < 2 && return items
    seen = Base.IdSet{Any}()
    out = items
    for (k, x) in enumerate(items)
        if x in seen
            out === items && (out = copy(items))
            out[k] = deepcopy(x)
        else
            push!(seen, x)
        end
    end
    out
end

# Where `node` is in a list its method changed (`ctx.container`).
function _find(list::Vector, node)
    i = findfirst(x -> x === node, list)
    i === nothing && throw(ArgumentError(
        "a filter method took its own node out of the list it is in (ctx.container); " *
        "to delete the node, return an empty vector"))
    i
end

function _one(w::_Walker, x::Node)
    if w.topdown
        r, skip = _call(w, x)
        r === nothing || (r isa Node ? (x = r) : throw(ArgumentError(
            "a filter returned $(typeof(r)) where one node belongs")))
        skip || _children(w, x)
        return x
    end
    _children(w, x)
    r, _ = _call(w, x)
    r === nothing && return x
    r isa Node || throw(ArgumentError("a filter returned $(typeof(r)) where one node belongs"))
    r
end

# The list function on `list`: the list, or a new one of what it returned
# (its nodes may hold the old one: Emph(xs) has xs itself); whether to skip
# the elements.
function _whole(w::_Walker, list::Vector, parent, field, path)
    w.lists & _kind(list) == 0x00 && return list, false
    push!(w.stack, (parent, field, nothing, nothing, path))
    try
        r, skip = _call_whole(w, list)
        (r === nothing || r === list) && return list, skip
        # (a string, as in pandoc's Lua: its words, or Plain text)
        r isa Union{AbstractVector, AbstractString} || throw(ArgumentError(
            "a filter returned $(typeof(r)) for a list of $(eltype(list))s"))
        items = _distinct(convert(typeof(list), r isa AbstractString ? r : collect(r)))
        (items === list ? copy(items) : items), skip
    finally
        pop!(w.stack)
    end
end

# Walk a list's nodes; returns the list, or the one a list method made.
function _many(w::_Walker, list::Vector{T}, parent, field, path) where {T <: Node}
    if w.topdown
        list, skip = _whole(w, list, parent, field, path)
        skip && return list
    end
    i = 1
    while i <= length(list)
        push!(w.stack, (parent, field, list, i, _Path(path, i - 1)))
        try
            node = list[i]
            w.topdown || _children(w, node)
            r, skip = _call(w, node)
            # the method may have changed the list itself (ctx.container)
            if !(i <= length(list) && list[i] === node)
                i = _find(list, node)
                w.stack[end] = (parent, field, list, i, _Path(path, i - 1))  # (for what is below it)
            end
            if r === nothing
                w.topdown && !skip && _children(w, node)
                i += 1
                continue
            end
            items = r isa Node ? T[r] : _distinct(convert(Vector{T}, r))
            # in place, so that the list is as it is now however it is
            # reached (moving what follows: a few milliseconds at 25,000)
            length(items) == 1 ? (list[i] = items[1]) : splice!(list, i, items)
            if w.topdown && !skip
                for k in 0:length(items) - 1
                    w.stack[end] = (parent, field, list, i + k, _Path(path, i - 1 + k))
                    _children(w, list[i + k])
                end
            end
            i += length(items)
        finally
            pop!(w.stack)
        end
    end
    w.topdown ? list : first(_whole(w, list, parent, field, path))
end

function _value(w::_Walker, @nospecialize(x), parent, field, path)
    if x isa Node
        push!(w.stack, (parent, field, nothing, nothing, path))
        try
            return _one(w, x)
        finally
            pop!(w.stack)
        end
    elseif x isa Vector{<:Node}
        return _many(w, x, parent, field, path)
    elseif x isa Vector
        for (i, y) in enumerate(x)
            z = _value(w, y, parent, field, _Path(path, i - 1))
            z === y || (x[i] = z)
        end
    elseif x isa Dict
        for k in sort!(collect(keys(x)))  # in key order, as pandoc's Map
            haskey(x, k) || continue  # a method removed it
            y = x[k]
            if y isa Node  # the map is its container, the key its index
                push!(w.stack, (parent, field, x, k, _Path(path, k)))
                z = try
                    _one(w, y)
                finally
                    pop!(w.stack)
                end
            else
                z = _value(w, y, parent, field, _Path(path, k))
            end
            z === y || (x[k] = z)
        end
    elseif x isa Tuple
        ys = Any[_value(w, y, parent, field, _Path(path, i - 1)) for (i, y) in enumerate(x)]
        all(ys[i] === x[i] for i in eachindex(ys)) || return typeof(x)(Tuple(ys))
    end
    x
end

function _children(w::_Walker, x::T; skip::Union{Nothing, Symbol} = nothing,
                   after::Union{Nothing, Symbol} = nothing, then = nothing) where {T <: Node}
    for f in fieldnames(T)
        f === skip && continue
        v = getfield(x, f)
        if v isa Union{Node, Vector, Dict, Tuple}
            z = _value(w, v, x, f, _Path(isempty(w.stack) ? nothing : w.stack[end][5], f))
            z === v || setfield!(x, f, z)
        end
        f === after && then(x)
    end
end

# Whether `f` has a method for some node of type `T`.
_handles(@nospecialize(f), T) = any(m -> typeintersect(_first(m), T) !== Union{}, methods(f))

# Whether `f` has a method for a node that isn't an Inline, a Block or the document.
_handles_other(@nospecialize(f)) = any(methods(f)) do m
    p = _first(m)
    typeintersect(p, Node) !== Union{} && !(p <: Union{Inline, Block, Pandoc})
end

"""
    walk!(f, node; traverse = :typewise, format = nothing)

Call `f(x, ctx)` (or `f(x)`) on `node` and every node in it, where `f` has
a method for `x`: define methods for the node types to change, and the
most specific one is called, as Julia dispatches. `f` returns `nothing` to
keep the node (changed in place or not), a node to replace it, or, in a
list, a vector of nodes to splice in its place (empty: delete). As in
pandoc's Lua filters, a method for `Vector{Inline}` or `Vector{Block}` is
called on every list of them, and one for `Panir.Meta` on the metadata
(methods for anything don't count; `Meta` alone is `Base.Meta` outside
`Panir`); each returns a replacement, or `nothing`.

`traverse` is the order:
- `:typewise` (the default, as pandoc's Lua filters and Haskell's `walk`):
  one walk per kind, each bottom-up: every `Inline`, then every list of
  inlines, then every `Block`, then every list of blocks, then the other
  nodes, then the metadata, then `Pandoc`.
- `:topdown` (Lua's other order, and pandocfilters'): `Pandoc`, the
  metadata, then from the root down, a list before its elements and a node
  before its children, which are walked in its replacement too, unless the
  method calls `skip_children!(ctx)`.
- `:bottomup` (panflute's): one walk, each node after its children, a list
  after its elements, the metadata after what is in it, `Pandoc` last.

`format` is the output format's name, or the whole `Conversion` (then in
`ctx.conversion`). Returns `node` or its replacement.

```julia
demote(h::Header) = (h.level += 1; nothing)
shout(s::Str, ctx) = ctx.format == "html" ? Strong(Str(uppercase(s.text))) : nothing
walk!(demote, doc)
```
"""
function walk!(@nospecialize(f), node::Node; traverse::Symbol = :typewise, format = nothing)
    traverse in (:typewise, :topdown, :bottomup) || throw(ArgumentError(
        "traverse: expected :typewise, :topdown or :bottomup, got $(repr(traverse))"))
    # With the methods there are now: `f`'s may be newer than the caller (a
    # filter script a host just included), and a walk that didn't see them
    # would call none, and say nothing.
    Base.invokelatest(_walk!, f, node, traverse, _conversion(format))
end

function _walk!(@nospecialize(f), node::Node, traverse::Symbol, conversion::Conversion)
    # not compiled again for each filter: what calls it is dynamic anyway
    topdown = traverse === :topdown
    arity = Dict{DataType, Int}()
    walker(doc, wants, lists) = _Walker(f, topdown, doc, conversion, _Frame[], wants, lists, arity)
    every, nodes, lists = 0xff, _INLINES | _BLOCKS | _OTHERS, _INLINES | _BLOCKS
    node isa Pandoc || return traverse === :typewise ?
        foldl((x, (wants, lists)) -> _one(walker(node, wants, lists), x), _passes(f); init = node) :
        _one(walker(node, every, lists), node)
    doc = node
    function root(d)
        r, skip = _call(walker(d, every, 0x00), d)
        r === nothing && return d, skip
        r isa Pandoc || throw(ArgumentError("a filter returned $(typeof(r)) for the Pandoc"))
        r, skip
    end
    function meta(d)
        w = walker(d, every, 0x00)
        push!(w.stack, (d, :meta, nothing, nothing, _Path(nothing, :meta)))
        r, skip = _call_whole(w, d.meta)
        r === nothing || (d.meta = r)
        skip
    end
    if topdown
        doc, skip = root(doc)  # the method's document, if it returned one, is the one walked
        skip && return doc
        skipmeta = meta(doc)
        _children(walker(doc, nodes, lists), doc; skip = skipmeta ? :meta : nothing)
        return doc
    end
    if traverse === :bottomup
        _children(walker(doc, nodes, lists), doc; after = :meta, then = meta)
        return first(root(doc))
    end
    for (wants, lists) in _passes(f)
        _children(walker(doc, wants, lists), doc)
    end
    meta(doc)
    first(root(doc))
end

# The typewise walks `f` needs: which nodes, and which lists, each calls it
# on. A list's walk only for a method that is the list's own: one for
# anything isn't called on lists (`_specific`).
function _passes(@nospecialize(f))
    own(T) = any(m -> (p = _first(m); p !== Any && typeintersect(p, T) !== Union{}), methods(f))
    passes = [
        (_INLINES, 0x00, _handles(f, Inline)),
        (0x00, _INLINES, own(Vector{Inline})),
        (_BLOCKS, 0x00, _handles(f, Block)),
        (0x00, _BLOCKS, own(Vector{Block})),
        (_OTHERS, 0x00, _handles_other(f)),
    ]
    [(wants, lists) for (wants, lists, needed) in passes if needed]
end

"""
    run_filter(f; traverse = :typewise)

Run `f` (as in `walk!`) as a pandoc JSON filter: a document on stdin, to
stdout. `ctx.conversion` is what pandoc says of the conversion: the output
format (its first argument, also `ctx.format`), and its environment.
"""
function run_filter(f; traverse::Symbol = :typewise)
    h = get(task_local_storage(), _HANDOFF, nothing)
    h === nothing || (h[] = (f, traverse); return nothing)
    doc = parse(read(stdin, String))
    doc = walk!(f, doc; traverse, format = Conversion(ARGS, ENV))
    write(stdout, serialize(doc))
    nothing
end

const _HANDOFF = :panir_handoff

"""
    handoff(run) -> (f, traverse) or nothing

Call `run()`, which runs a filter script, so that the script's
`run_filter(f; traverse)` hands `f` over instead of reading a document from
stdin: for a host that runs filter scripts in its own process, on the
document it has (libpandoc's `pandocjl`). `nothing` if the script called
no `run_filter`. Per task, so scripts may run in several at once.
"""
function handoff(run)
    h = Ref{Any}(nothing)
    task_local_storage(run, _HANDOFF, h)
    h[]
end

const _QUOTES =Dict(SingleQuote => ("‘", "’"), DoubleQuote => ("“", "”"))

"""
The text of a node or nodes, without markup, as pandoc's `stringify`.
"""
stringify(x) = (io = IOBuffer(); _text(io, x); String(take!(io)))

_text(io, x::AbstractVector) = foreach(y -> _text(io, y), x)
_text(io, x::Tuple) = foreach(y -> _text(io, y), x)
_text(io, x::AbstractDict) = foreach(k -> _text(io, x[k]), sort!(collect(keys(x))))  # as pandoc's Map
_text(io, x) = nothing
_text(io, x::Union{Str, Code, Math, MetaString}) = print(io, x.text)
_text(io, ::Union{Space, SoftBreak, LineBreak}) = print(io, " ")
_text(io, x::RawInline) = x.format == "html" && startswith(x.text, "<br") && print(io, " ")
_text(io, ::Note) = nothing
_text(io, x::Cite) = _text(io, x.content)
_text(io, x::MetaBool) = print(io, x.value ? "true" : "false")
function _text(io, x::Quoted)
    l, r = _QUOTES[x.quote_type]
    print(io, l); _text(io, x.content); print(io, r)
end
function _text(io, x::T) where {T <: Node}
    for f in fieldnames(T)
        f === :attr || _text(io, getfield(x, f))
    end
end
