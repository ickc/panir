# The generic part: everything that isn't a declaration (generated.jl).
# JSON by reflection on the declared field types; nothing here names a
# pandoc type.

"""
JSON that isn't a pandoc document (or node) of this API version. `path` is
where, in field names and indices, e.g. `["blocks", 0, "content", 3]`.
"""
struct ASTDecodeError <: Exception
    path::Vector{Any}
    expected::String
    got::String
end

function _format_path(path)
    out = ""
    for p in path
        if p isa Integer
            out *= "[$p]"
        elseif occursin(r"^[A-Za-z_][A-Za-z0-9_]*$", string(p)) || isempty(out)
            out *= isempty(out) ? string(p) : ".$p"
        else
            out *= "[$(repr(string(p)))]"
        end
    end
    out
end

function Base.showerror(io::IO, e::ASTDecodeError)
    where = _format_path(e.path)
    print(io, isempty(where) ? "" : "$where: ", "expected $(e.expected), got $(e.got)")
end

# raised while decoding; the path is collected on the way up
mutable struct _Bad <: Exception
    expected::String
    got::Any
    got_text::Union{Nothing, String}  # in place of describing `got`
    path::Vector{Any}
end
_Bad(expected, got, got_text = nothing) = _Bad(expected, got, got_text, Any[])

function _at(f, key)
    try
        f()
    catch e
        e isa _Bad && push!(e.path, key)
        rethrow()
    end
end

const _TAGS = Dict{String, Tuple{DataType, Type}}()  # constructor name => (struct, sum)

function _tags()
    if isempty(_TAGS)
        for S in _SUMS, C in _constructors(S)
            _TAGS[string(nameof(C))] = (C, S)
        end
    end
    _TAGS
end

_sumname(S) = replace(string(nameof(S)), r"Base$" => "")

function _describe(j)
    if j isa AbstractDict && get(j, "t", nothing) isa AbstractString
        t = j["t"]
        haskey(_tags(), t) || return "\"t\": $(repr(t)) (no such constructor)"
        s = _sumname(_tags()[t][2])
        return "$t ($(s[1] in "AEIOU" ? "an" : "a") $s)"
    end
    j === nothing && return "null"
    text = sprint(JSON.print, j)
    length(text) > 60 ? text[1:prevind(text, 58)] * "..." : text
end

_article(s) = (s[1] in "AEIOU" ? "an " : "a ") * s

_typename(::Type{String}) = "a string"
_typename(::Type{Int}) = "an integer"
_typename(::Type{Float64}) = "a number"
_typename(::Type{Bool}) = "a boolean"
_typename(::Type{<:Vector}) = "a list"
_typename(::Type{<:Dict}) = "an object"
_typename(T::Type{<:Tuple}) = "a list of $(fieldcount(T))"
_typename(T::Type) = T in _SUMS ? _sumname(T) : _article(string(nameof(T)))

# -- decoding ---------------------------------------------------------------------

_decode(::Type{String}, j) = j isa AbstractString ? String(j) : throw(_Bad("a string", j))
_decode(::Type{Int}, j) = j isa Integer && !(j isa Bool) && typemin(Int) <= j <= typemax(Int) ?
    Int(j) : throw(_Bad("an integer", j))
_decode(::Type{Bool}, j) = j isa Bool ? j : throw(_Bad("a boolean", j))

# A Double that isn't finite has no JSON number: pandoc (aeson) writes null
# for one that is not a number, "+inf" and "-inf", and reads them.
function _decode(::Type{Float64}, j)
    j isa Real && !(j isa Bool) && return Float64(j)
    j === nothing && return NaN
    j == "+inf" && return Inf
    j == "-inf" && return -Inf
    throw(_Bad("a number", j))
end

function _decode(::Type{Vector{T}}, j) where {T}
    j isa AbstractVector || throw(_Bad("a list", j))
    T[_at(() -> _decode(T, x), i - 1) for (i, x) in enumerate(j)]
end

function _decode(::Type{Dict{String, T}}, j) where {T}
    j isa AbstractDict || throw(_Bad("an object", j))
    Dict{String, T}(String(k) => _at(() -> _decode(T, x), String(k)) for (k, x) in j)
end

function _decode(::Type{Union{Nothing, T}}, j) where {T}
    j === nothing ? nothing : _decode(T, j)
end

function _decode(T::Type{<:Tuple}, j)
    n = fieldcount(T)
    j isa AbstractVector && length(j) == n || throw(_Bad("a list of $n", j))
    Tuple(_at(() -> _decode(fieldtype(T, i), j[i]), i - 1) for i in 1:n)
end

function _decode(T::Type{<:Enum}, j)
    if j isa AbstractDict && get(j, "t", nothing) isa AbstractString
        for v in instances(T)
            string(v) == j["t"] && return v
        end
    end
    throw(_Bad(_article(string(nameof(T))), j))
end

function _decode(T::Type{<:Node}, j)
    if isabstracttype(T)  # a sum type: the constructor from "t"
        t = j isa AbstractDict ? get(j, "t", nothing) : nothing
        entry = t isa AbstractString ? get(_tags(), t, nothing) : nothing
        (entry === nothing || entry[2] !== T) && throw(_Bad(_typename(T), j))
        C = entry[1]
        enc = _encoding(C)
        enc === :none && return C()
        haskey(j, "c") || throw(_Bad("$C with contents", j, "$C without"))
        return _decode_fields(C, enc, j["c"])
    end
    enc = _encoding(T)
    if enc === :root
        j isa AbstractDict || throw(_Bad("an object (Pandoc)", j))
        v = get(j, "pandoc-api-version", nothing)
        want = PANDOC_API_VERSION
        if !(v isa AbstractVector && length(v) >= 2 && v[1] == want[1] && v[2] == want[2])
            e = _Bad("pandoc-api-version $(want[1]).$(want[2]).*", v)
            push!(e.path, "pandoc-api-version")
            throw(e)
        end
    end
    _decode_fields(T, enc, j)
end

function _decode_fields(T, enc, c)
    names = fieldnames(T)
    n = length(names)
    if enc === :value
        return T(_at(() -> _decode(fieldtype(T, 1), c), names[1]))
    elseif enc === :array
        if !(c isa AbstractVector && length(c) == n)
            got = c isa AbstractVector ? "$T with $(length(c))" : _describe(c)
            throw(_Bad("$T with $n fields", c, got))
        end
        return T((_at(() -> _decode(fieldtype(T, i), c[i]), names[i]) for i in 1:n)...)
    end
    c isa AbstractDict || throw(_Bad("an object ($T)", c))
    keys = enc === :root ? string.(names) : _keys(T)
    vals = map(1:n) do i
        haskey(c, keys[i]) || throw(_Bad("$T with $(repr(keys[i]))", c, "one without"))
        _at(() -> _decode(fieldtype(T, i), c[keys[i]]), names[i])
    end
    T(vals...)
end

"""
    fromjson(T, j)

A value of type `T` (default: a document) from parsed pandoc JSON, checked.
Throws `ASTDecodeError` saying where it is wrong.
"""
function fromjson(T::Type, j)
    try
        _decode(T, j)
    catch e
        if e isa _Bad
            got = e.got_text === nothing ? _describe(e.got) : e.got_text
            throw(ASTDecodeError(reverse(e.path), e.expected, got))
        end
        rethrow()
    end
end
fromjson(j) = fromjson(Pandoc, j)

# JSON.jl stops at 512 levels of JSON, since it has the option (1.13),
# unless told: 170 nested divs.
const _JSON_DEPTH = isdefined(JSON, :LazyOptions) && :maxdepth in fieldnames(JSON.LazyOptions) ?
    (; maxdepth = 2048) : (;)

"A document from pandoc's JSON text."
parse(text::AbstractString) = fromjson(Pandoc, JSON.parse(text; _JSON_DEPTH...))

# -- encoding -----------------------------------------------------------------------

_encode(x::Union{Int, Bool, Nothing}) = x
_encode(x::Float64) = isfinite(x) ? x : isnan(x) ? nothing : x > 0 ? "+inf" : "-inf"
# (a String may hold bytes that aren't UTF-8; pandoc would then read the
# whole document as Latin-1)
_encode(x::String) = isvalid(x) ? x : throw(ArgumentError(
    "a string that isn't valid UTF-8: $(repr(x))"))
_encode(x::Vector) = Any[_encode(y) for y in x]
_encode(x::Dict) = Dict{String, Any}(k => _encode(v) for (k, v) in x)
_encode(x::Tuple) = Any[_encode(y) for y in x]
_encode(x::Enum) = Dict{String, Any}("t" => string(x))

function _encode(x::T) where {T <: Node}
    enc = _encoding(T)
    vals = Any[_encode(getfield(x, f)) for f in fieldnames(T)]
    if supertype(T) in _SUMS
        enc === :none && return Dict{String, Any}("t" => string(nameof(T)))
        return Dict{String, Any}("t" => string(nameof(T)), "c" => enc === :value ? vals[1] : vals)
    end
    enc === :array && return vals
    enc === :value && return vals[1]
    keys = enc === :root ? string.(fieldnames(T)) : _keys(T)
    out = Dict{String, Any}(zip(keys, vals))
    enc === :root && (out["pandoc-api-version"] = collect(PANDOC_API_VERSION))
    out
end

"A value (a document, or any node) as parsed pandoc JSON."
tojson(x) = _encode(x)

"A document as pandoc's JSON text."
serialize(doc::Pandoc) = sprint(_write, doc; sizehint = 1 << 16)

# Straight to the text: through `tojson`'s tree of Dicts and Vectors, and
# JSON.jl's printing of it, took four times as long and as much memory.
_write(io::IO, x::Union{Int, Bool}) = print(io, x)
_write(io::IO, ::Nothing) = print(io, "null")
_write(io::IO, x::Float64) =
    isfinite(x) ? print(io, x) : print(io, isnan(x) ? "null" : x > 0 ? "\"+inf\"" : "\"-inf\"")
_write(io::IO, x::Enum) = print(io, "{\"t\":\"", x, "\"}")

function _write(io::IO, x::String)
    isvalid(x) || throw(ArgumentError("a string that isn't valid UTF-8: $(repr(x))"))
    write(io, '"')
    from = 1
    for (i, byte) in enumerate(codeunits(x))
        (byte < 0x20 || byte == UInt8('"') || byte == UInt8('\\')) || continue
        write(io, view(codeunits(x), from:i - 1))
        if byte == UInt8('"') || byte == UInt8('\\')
            write(io, '\\', byte)
        elseif byte == UInt8('\n')
            write(io, "\\n")
        elseif byte == UInt8('\t')
            write(io, "\\t")
        elseif byte == UInt8('\r')
            write(io, "\\r")
        else
            write(io, "\\u00", string(byte; base = 16, pad = 2))
        end
        from = i + 1
    end
    write(io, view(codeunits(x), from:ncodeunits(x)), '"')
    nothing
end

function _write_all(io::IO, xs)
    write(io, '[')
    for (i, x) in enumerate(xs)
        i > 1 && write(io, ',')
        _write(io, x)
    end
    write(io, ']')
    nothing
end
_write(io::IO, xs::Union{Vector, Tuple}) = _write_all(io, xs)

function _write(io::IO, d::Dict)
    write(io, '{')
    for (i, k) in enumerate(sort!(collect(keys(d))))  # as pandoc's Map
        i > 1 && write(io, ',')
        _write(io, k)
        write(io, ':')
        _write(io, d[k])
    end
    write(io, '}')
    nothing
end

function _write(io::IO, x::T) where {T <: Node}
    enc = _encoding(T)
    tagged = supertype(T) in _SUMS
    if tagged
        print(io, "{\"t\":\"", nameof(T), '"')
        enc === :none && return (write(io, '}'); nothing)
        write(io, ",\"c\":")
    end
    if enc === :value
        _write(io, getfield(x, 1))
    elseif enc === :array
        _write_all(io, (getfield(x, i) for i in 1:fieldcount(T)))
    else
        keys = enc === :root ? string.(fieldnames(T)) : _keys(T)
        write(io, '{')
        if enc === :root
            write(io, "\"pandoc-api-version\":")
            _write_all(io, PANDOC_API_VERSION)
            write(io, ',')
        end
        for i in 1:fieldcount(T)
            i > 1 && write(io, ',')
            _write(io, keys[i])
            write(io, ':')
            _write(io, getfield(x, i))
        end
        write(io, '}')
    end
    tagged && write(io, '}')
    nothing
end

# -- nodes ----------------------------------------------------------------------------

function Base.:(==)(a::T, b::T) where {T <: Node}
    all(getfield(a, f) == getfield(b, f) for f in fieldnames(T))
end

# as `hash` below goes: field by field (NaN is NaN, 0.0 isn't -0.0)
function Base.isequal(a::T, b::T) where {T <: Node}
    all(isequal(getfield(a, f), getfield(b, f)) for f in fieldnames(T))
end

function Base.hash(x::T, h::UInt) where {T <: Node}
    foldr((f, h) -> hash(getfield(x, f), h), fieldnames(T); init = hash(T, h))
end

function Base.copy(x::T) where {T <: Node}
    T((getfield(x, f) for f in fieldnames(T))...)
end

# -- strings, as pandoc's Lua converts them ---------------------------------------

"""
    inlines(text)

A string's words and spaces, as pandoc's Lua `pandoc.Inlines` (and
pandoc-types' `text`): `Str` for each run of non-spaces, and for each run of
spaces `SoftBreak` if it has a newline, else `Space`. Constructors and fields
convert a string this way themselves (`Para("hello world")`).
"""
function inlines(text::AbstractString)
    out = Inline[]
    for m in eachmatch(r"[ \t\n\r]+|[^ \t\n\r]+", text)
        run = m.match
        if run[1] in (' ', '\t', '\n', '\r')
            push!(out, occursin(r"[\n\r]", run) ? SoftBreak() : Space())
        else
            push!(out, Str(String(run)))
        end
    end
    out
end

"""
    blocks(text)

A string as blocks, as pandoc's Lua `pandoc.Blocks`: one `Plain` of its words
and spaces. (To parse markup, use libpandoc.)
"""
blocks(text::AbstractString) = Block[Plain(inlines(text))]

# where one inline goes, a Str; where one block goes, Plain text; where a list
# goes, inlines(s) or blocks(s)
Base.convert(::Type{Inline}, s::AbstractString) = Str(String(s))
Base.convert(::Type{Block}, s::AbstractString) = Plain(inlines(s))
Base.convert(::Type{Vector{Inline}}, s::AbstractString) = inlines(s)
Base.convert(::Type{Vector{Block}}, s::AbstractString) = blocks(s)

# A constructor's variadic arguments: one string is the whole list, as in
# pandoc's Lua (Para("hello world")); several are each a node.
_variadic(::Type{T}, xs) where {T} = collect(T, xs)
_variadic(::Type{Inline}, xs::Tuple{AbstractString}) = inlines(xs[1])
_variadic(::Type{Block}, xs::Tuple{AbstractString}) = blocks(xs[1])

# A product field given whole (attr = ...) or by its parts (identifier =
# ...): each part is `nothing` unless given, and then its default
# (`_default`, generated), if it has one.
function _flat(::Type{P}, given, owner::Symbol, field::Symbol; parts...) where {P}
    names = fieldnames(P)
    if given === nothing
        values = Any[parts[n] === nothing ? _default(P, Val(n)) : parts[n] for n in names]
        missing_ = [n for (n, v) in zip(names, values) if v === nothing]
        isempty(missing_) || throw(ArgumentError(
            "$owner() needs $(join(string.(missing_) .* " =", ", ")) (or $field =)"))
        return P((_attributes(fieldtype(P, i), v) for (i, v) in enumerate(values))...)
    end
    both = [n for n in names if parts[n] !== nothing]
    isempty(both) || throw(ArgumentError(
        "$owner(): give either $field = or $(join(string.(both) .* " =", ", ")), not both"))
    given
end

_default(::Type, ::Val) = nothing  # a part without a default

# attributes may be given as a Dict
_attributes(::Type{Vector{Tuple{String, String}}}, x::AbstractDict) =
    Tuple{String, String}[(String(k), String(v)) for (k, v) in x]
_attributes(::Type, x) = x
