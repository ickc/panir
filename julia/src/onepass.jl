# Reading a document in one pass: the nodes made while JSON.jl reads the
# text, with no tree of JSON values between (`parse`). By the declarations,
# as `_decode` reads (nothing here names a pandoc type), with JSON.jl's and
# StructUtils.jl's public functions only:
#
# - `JSON.parse(text, _Doc)` hands `StructUtils.make` for `_Doc` (below) the
#   text as a `JSON.LazyValue`, with a style of JSON.jl's (opaque here);
# - a list or an object is read with `StructUtils.applyeach`, a call for each
#   member (`arraylike`, `structlike`, `nulllike` say which kind a value is);
# - a string with `StructUtils.make(st, String, x)`, any other scalar with
#   `_any(st, x)` and then `_decode`'s checks;
# - each `_make` returns its value and what the calls that read it returned
#   besides, as StructUtils' own `make` does. JSON.jl continues from there
#   rather than read the value again to skip it (not documented: without it,
#   the result is the same and each value is read again at every level).
#
# It stops at the first thing that isn't the plain case (`_Slow`, or any
# error on the way): a wrong value, "c" before "t", text that isn't JSON or
# nests deeper than `_JSON_DEPTH`. `parse` then reads the text the two-step
# way, which takes it or says what is wrong.

using JSON: StructUtils

struct _Slow <: Exception end

struct _Doc
    doc::Pandoc
end

function StructUtils.make(st::StructUtils.StructStyle, ::Type{_Doc}, x::JSON.LazyValue)
    doc, s = _make(Pandoc, st, x)
    _Doc(doc), s
end

# The document, or `_Slow` (or another error) if it isn't the plain case.
_parse_fast(text::AbstractString) = JSON.parse(text, _Doc; maxdepth = _JSON_DEPTH).doc

# `_make(T, st, x)`: a value of type `T` from `x`, and what JSON.jl returned
# besides (where `x` ends).

# applyeach and make(st, Any, x) return that, or an `EarlyReturn` (which
# nothing here asks for): without the latter, the compiler sees the type
_ended(s) = s isa StructUtils.EarlyReturn ? throw(_Slow()) : s
_each(st, f, x) = _ended(StructUtils.applyeach(st, f, x))
function _any(st, x)
    j, s = StructUtils.make(st, Any, x)
    j, _ended(s)
end

_make(::Type{String}, st, x) = StructUtils.make(st, String, x)

function _make(::Type{T}, st, x) where {T <: Union{Int, Bool, Float64}}
    j, s = _any(st, x)
    _decode(T, j), s
end

struct _Push{E, S}
    out::Vector{E}
    st::S
end
function (f::_Push{E})(_, x) where {E}
    v, s = _make(E, f.st, x)
    push!(f.out, v)
    s
end

function _make(::Type{Vector{E}}, st, x) where {E}
    StructUtils.arraylike(st, x) || throw(_Slow())
    out = E[]
    s = _each(st, _Push(out, st), x)
    out, s
end

struct _Put{E, S}
    out::Dict{String, E}
    st::S
end
function (f::_Put{E})(k, x) where {E}
    v, s = _make(E, f.st, x)
    f.out[StructUtils.liftkey(f.st, String, k)] = v
    s
end

function _make(::Type{Dict{String, E}}, st, x) where {E}
    StructUtils.structlike(st, x) || throw(_Slow())
    out = Dict{String, E}()
    s = _each(st, _Put(out, st), x)
    out, s
end

function _make(::Type{Union{Nothing, T}}, st, x) where {T}
    StructUtils.nulllike(st, x) || return _make(T, st, x)
    _, s = _any(st, x)
    nothing, s
end

# -- fields, by position or by key ----------------------------------------------------

# the `i`th field of `T` (a struct or a tuple type) into `vals`
# (decided where the compiler sees it, for as many fields as a node has: six)
function _field!(::Type{T}, vals, i, st, x) where {T}
    n = fieldcount(T)
    i > n && throw(_Slow())
    i == 1 && return _field!(T, vals, st, x, Val(1))
    i == 2 && return _field!(T, vals, st, x, Val(min(2, n)))
    i == 3 && return _field!(T, vals, st, x, Val(min(3, n)))
    i == 4 && return _field!(T, vals, st, x, Val(min(4, n)))
    i == 5 && return _field!(T, vals, st, x, Val(min(5, n)))
    i == 6 && return _field!(T, vals, st, x, Val(min(6, n)))
    throw(_Slow())
end
@noinline function _field!(::Type{T}, vals, st, x, ::Val{k}) where {T, k}
    v, s = _make(fieldtype(T, k), st, x)
    vals[k] = v
    s
end

# the fields of `T` from a list of them
mutable struct _Positional{T, S}
    const vals::Vector{Any}
    const st::S
    n::Int
end
function (f::_Positional{T})(i, x) where {T}
    f.n = i
    _field!(T, f.vals, i, f.st, x)
end

# the fields of `T` from an object, by `keys` (others skipped, as `_decode`
# does); the document's "pandoc-api-version" too (`root`), once
mutable struct _Keyed{T, K, S}
    const vals::Vector{Any}
    const keys::K
    const st::S
    const root::Bool
    version::Bool
end
function (f::_Keyed{T})(k, x) where {T}
    if f.root && k == "pandoc-api-version"
        v, s = _any(f.st, x)
        want = PANDOC_API_VERSION
        !f.version && v isa AbstractVector && length(v) >= 2 && v[1] == want[1] && v[2] == want[2] ||
            throw(_Slow())
        f.version = true
        return s
    end
    for i in 1:fieldcount(T)
        k == f.keys[i] && return _field!(T, f.vals, i, f.st, x)
    end
    nothing  # (JSON.jl skips it)
end

# `T`'s fields as a tuple, all there
function _complete(::Type{T}, vals) where {T}
    all(i -> isassigned(vals, i), 1:fieldcount(T)) || throw(_Slow())
    ntuple(i -> vals[i]::fieldtype(T, i), Val(fieldcount(T)))
end

function _make(::Type{T}, st, x) where {T <: Tuple}
    vals, s = _positional(T, st, x)
    _complete(T, vals), s
end

function _positional(::Type{T}, st, x) where {T}
    StructUtils.arraylike(st, x) || throw(_Slow())
    f = _Positional{T, typeof(st)}(Vector{Any}(undef, fieldcount(T)), st, 0)
    s = _each(st, f, x)
    f.n == fieldcount(T) || throw(_Slow())
    f.vals, s
end

# -- nodes --------------------------------------------------------------------------

function _make(::Type{T}, st, x) where {T <: Enum}
    StructUtils.structlike(st, x) || throw(_Slow())
    f = _Tag(st, T)
    s = _each(st, f, x)
    for v in instances(T)
        string(v) == f.tag && return v, s
    end
    throw(_Slow())
end

# A node of `T` from its contents, encoded as `enc` (as `_decode_fields`).
function _contents(::Type{T}, enc, st, x) where {T}
    if enc === :value
        v, s = _make(fieldtype(T, 1), st, x)
        return T(_FIELDS, v), s
    elseif enc === :array
        vals, s = _positional(T, st, x)
        return T(_FIELDS, _complete(T, vals)...), s
    end
    StructUtils.structlike(st, x) || throw(_Slow())
    root = enc === :root
    keys = root ? map(string, fieldnames(T)) : _keys(T)
    f = _Keyed{T, typeof(keys), typeof(st)}(Vector{Any}(undef, fieldcount(T)), keys, st, root, false)
    s = _each(st, f, x)
    root && !f.version && throw(_Slow())
    T(_FIELDS, _complete(T, f.vals)...), s
end

# a constructor's node from its "c", into the `_Tag` reading it, or without
# one (`_Tagged`: in `_tags()`)
function (::_Tagged{C})(f, x) where {C}
    enc = _encoding(C)
    enc === :none && (f.node = C(); return nothing)  # (its "c" skipped)
    node, s = _contents(C, enc, f.st, x)
    f.node = node
    s
end

# A constructor's node without contents, decided among those that have none
# where the compiler sees it (a call decided as it runs, for each Space, took
# a fifth of the time).
const _NONE = Tuple(C for S in _SUMS for C in _constructors(S) if _encoding(C) === :none)
_none(entry) = _none_among(entry, _NONE...)
_none_among(entry, C, more...) = entry isa _Tagged{C} ? C() : _none_among(entry, more...)
_none_among(entry) = throw(_Slow())

# A tagged object: an enum's value, or a node of the sum type `want` (or
# of the constructor `want`). "t" first, as pandoc writes it.
mutable struct _Tag{S}
    const st::S
    const want::Type
    tag::Union{Nothing, String}
    entry::Any  # in `_tags()`
    node::Union{Nothing, Node}
end
_Tag(st, want) = _Tag(st, want, nothing, nothing, nothing)

function (f::_Tag)(k, x)
    if k == "t"
        f.tag === nothing || throw(_Slow())
        t, s = StructUtils.make(f.st, String, x)
        f.tag = t
        f.want <: Enum && return s
        entry = get(_tags(), t, nothing)
        (entry === nothing || !(entry[1] <: f.want)) && throw(_Slow())
        f.entry = entry[3]
        return s
    elseif k == "c" && !(f.want <: Enum)
        (f.entry === nothing || f.node !== nothing) && throw(_Slow())
        return f.entry(f, x)  # (the one call decided as it runs)
    end
    nothing
end

function _make(::Type{T}, st, x) where {T <: Node}
    if isabstracttype(T) || supertype(T) in _SUMS
        StructUtils.structlike(st, x) || throw(_Slow())
        f = _Tag(st, T)
        s = _each(st, f, x)
        f.entry === nothing && throw(_Slow())
        node = f.node === nothing ? _none(f.entry) : f.node
        return node::T, s
    end
    _contents(T, _encoding(T), st, x)
end
