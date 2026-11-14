# Compiled ahead, into the package's image: reading and writing pandoc's
# JSON and walking, for a document with every kind of node, so that a
# filter's first document isn't seconds of compiling.

# A value of type `T`: lists hold one of each kind (`depth` levels deep),
# a sum type is its first kind.
function _example(T::Type, depth::Int)
    T === String && return "x"
    T === Int && return 1
    T === Float64 && return 0.5
    T === Bool && return true
    T <: Enum && return first(instances(T))
    if T <: Vector
        E = eltype(T)
        return depth <= 0 ? E[] : E[_examples(E, depth - 1)...]
    end
    if T <: Dict
        V = valtype(T)
        return depth <= 0 ? T() : T("k" => first(_examples(V, depth - 1)))
    end
    T isa Union && return _example(Base.typesplit(T, Nothing), depth)
    T <: Tuple && return T(Tuple(_example(F, depth) for F in fieldtypes(T)))
    isabstracttype(T) && return first(_examples(T, depth))
    fieldcount(T) == 0 ? T() : T(_FIELDS, (_example(F, depth) for F in fieldtypes(T))...)
end

_kinds(T) = sort!([S for (S, sum) in values(_tags()) if sum === T]; by = string)
_examples(T::Type, depth::Int) = isabstracttype(T) ? Any[_example(S, depth) for S in _kinds(T)] : Any[_example(T, depth)]

# a filter with methods of each shape walk! looks for
_pc(s::Str) = nothing
_pc(s::Emph, ctx) = nothing
_pc(b::Para) = nothing
_pc(h::Header, ctx) = (h.level += 0; nothing)
_pc(l::Vector{Inline}) = nothing
_pc(l::Vector{Block}, ctx) = nothing
_pc(c::Cell) = nothing
_pc(m::Meta) = nothing
_pc(d::Pandoc, ctx) = nothing
_pc_str(s::Str) = Str(uppercase(s.text))
# what a method may return besides a node
_pc_returns(s::Str) = s.text == "x" ? "two words" : Inline[s, "!"]
_pc_returns(c::Code) = Inline[]
_pc_returns(p::Plain) = "text"
_pc_returns(xs::Vector{Block}) = xs
_pc_returns(m::Meta) = Dict("k" => true)
_pc_wrong(s::Str) = 42

@setup_workload begin
    doc = _example(Pandoc, 3)
    @compile_workload begin
        d = parse(serialize(doc))
        for traverse in (:typewise, :topdown, :bottomup)
            walk!(_pc, d; traverse, format = Conversion(; format = "html"))
        end
        walk!(_pc_str, d)
        walk!(_pc_returns, d)
        walk!(_pc_str, d.blocks)  # a fragment
        stringify(d)
        d == doc
        # a filter's run, as run_filter does it
        _run(_pc_str, :typewise, IOBuffer(serialize(d)), IOBuffer(), String["html"], ENV)
        # the errors, shown
        for bad in ("{", "{\"pandoc-api-version\":[1,23,1,2],\"meta\":{},\"blocks\":[1]}",
                    "{\"pandoc-api-version\":[1,23,1,2],\"meta\":{},\"blocks\":[{\"t\":\"Para\",\"c\":[{\"t\":\"Str\",\"c\":42}]}]}")
            try parse(bad) catch e; sprint(showerror, e) end
        end
        try walk!(_pc_wrong, d) catch e; sprint(showerror, e) end
        # constructors, with keywords, as filters make them
        made = Pandoc(Header(1, "Title"; identifier = "t", classes = ["c"], attributes = ["k" => "v"]),
                      Para("a b", Emph("e"), Link("l"; url = "u", title = "t"), Image("i"; url = "u"),
                           Span(Str("s"); attributes = Dict("k" => "v")), Code("c"; classes = ["x"])),
                      Div(Para("p"); identifier = "d"), CodeBlock("c"; classes = ["x"]);
                      meta = Dict("title" => "T", "draft" => true))
        made.meta["k"] = "v"
        sprint(show, MIME"text/plain"(), made)
        # every kind of node, also where the document has only one
        for (T, sum) in values(_tags())
            x = fromjson(sum, tojson(_example(T, 1)))
            walk!(_pc, x)
            stringify(x)
            stringify(Any[x])
        end
    end
end
