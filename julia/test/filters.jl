# The shared filter corpus: each corpus/filters/NAME.lua, written here as
# Julia filters, must make of its input what pandoc's Lua filter made.

const FORMAT = "json"

# Each scenario: filters and their orders, run one after the other.
const Filters = Vector{Tuple{Any, Symbol}}

function scenario_upper()
    f(s::Str) = Str(uppercase(s.text))
    Filters([(f, :typewise)])
end

function scenario_modify()
    f(h::Header) = (h.level += 1; nothing)
    f(l::Link) = (l.target.url = "https://example.org/" * l.target.url; nothing)
    function f(i::Image)
        i.target.url = "img/" * i.target.url
        push!(i.attr.attributes, ("loading", "lazy"))
        nothing
    end
    f(c::CodeBlock) = (push!(c.attr.classes, "numbered"); nothing)
    Filters([(f, :typewise)])
end

function scenario_splice()
    f(::Union{Note, Emph}) = Inline[]
    f(s::Strong) = s.content
    f(d::Div) = d.content
    f(::HorizontalRule) = Block[Para(Str("one")), Para(Str("two"))]
    Filters([(f, :typewise)])
end

function scenario_generic()
    f(s::Str) = Str(s.text * "!")
    f(i::Inline) = i isa Str ? Str("never") : i isa Code ? Str(i.text) : nothing
    f(b::Block) = b isa CodeBlock ? Para(Str(b.text)) : nothing
    Filters([(f, :typewise)])
end

# Methods noting each call (as the Lua scenarios do) on the kinds noted, the
# lists, the metadata and the document; the notes go to `seen`, and with
# `list`, to a last paragraph.
function noting(seen::Vector{String}, kinds; list::Bool = false)
    function f(x::Node)
        typeof(x) <: kinds || return nothing
        name = string(nameof(typeof(x)))
        push!(seen, x isa Union{Str, Code} ? "$name:$(x.text)" : name)
        nothing
    end
    f(xs::Vector{Inline}) = (push!(seen, "Inlines$(length(xs))"); nothing)
    f(xs::Vector{Block}) = (push!(seen, "Blocks$(length(xs))"); nothing)
    f(::Panir.Meta) = (push!(seen, "Meta"); nothing)  # Meta alone is Base.Meta
    function f(d::Pandoc)
        push!(seen, "Pandoc")
        list && push!(d.blocks, Para(join(seen, " ")))
        nothing
    end
    f
end

const NOTED = Union{Str, Emph, Note, Link, Code, Para, Header, Div, Table, Figure, BlockQuote}

# A last filter, adding a paragraph listing the notes.
listing(seen) = d::Pandoc -> (push!(d.blocks, Para(join(seen, " "))); nothing)

function scenario_typewise()
    seen = String[]
    Filters([(noting(seen, NOTED), :typewise), (listing(seen), :typewise)])
end

function scenario_topdown()
    seen = String[]
    Filters([(noting(seen, NOTED), :topdown), (listing(seen), :typewise)])
end

scenario_bottomup() = Filters([(noting(String[], Union{NOTED, Strong}; list = true), :bottomup)])

function scenario_skip()
    f(s::Str) = Str(uppercase(s.text))
    f(h::Header) = Para(h.content)
    f(::Union{Div, Emph}, ctx) = (skip_children!(ctx); nothing)
    f(q::BlockQuote, ctx) = (skip_children!(ctx); Div(q.content...))
    Filters([(f, :topdown)])
end

function scenario_toplists()
    f(xs::Vector{Inline}, ctx) = length(xs) == 1 ? (skip_children!(ctx); nothing) : reverse(xs)
    f(s::Str) = Str(uppercase(s.text))
    Filters([(f, :topdown)])
end

function scenario_once()
    f(s::Str) = s.text == "Some" ? Emph(Str("new")) : nothing
    f(e::Emph) = Strong(e.content)
    Filters([(f, :typewise)])
end

function scenario_lists()
    f(xs::Vector{Inline}) = Inline[filter(x -> !(x isa Space), xs); Str("<$(length(xs))>")]
    f(bs::Vector{Block}) = Block[x for b in bs for x in (b isa Header ? (b, HorizontalRule()) : (b,))]
    Filters([(f, :typewise)])
end

function scenario_meta()
    function f(m::Panir.Meta, ctx)
        m["draft"] = MetaBool(true)
        m["format"] = MetaString(something(ctx.format, ""))
        m["tags"] = MetaList(MetaString("a"), MetaInlines(Str("b")))
        delete!(m, "count")
        nothing
    end
    function f(d::Pandoc)
        draft = get(d.meta, "draft", nothing)
        pushfirst!(d.blocks, Para(Str(draft isa MetaBool && draft.value ? "draft" : "final")))
        nothing
    end
    Filters([(f, :typewise)])
end

function scenario_newdoc()
    function f(d::Pandoc)
        title = d.meta["title"]::MetaInlines
        Pandoc(Header(1, title.content), Para("made anew"); meta = d.meta)
    end
    f(s::Str) = Str(uppercase(s.text))
    Filters([(f, :topdown)])
end

function scenario_twice()
    f(c::Code) = [c, c]
    function f(p::Para)
        i = findfirst(x -> x isa Code, p.content)
        i === nothing || (p.content[i].text = "changed")
        nothing
    end
    Filters([(f, :typewise)])
end

function scenario_metacount()
    add(m::Panir.Meta) = (m["added"] = MetaInlines(Str("new")); nothing)
    n = Ref(0)
    number(s::Str) = Str(s.text * string(n[] += 1))
    number(m::Panir.Meta) = (m["calls"] = MetaString(string(n[] += 1)); nothing)
    number(d::Pandoc) = (push!(d.blocks, Para("calls: $(n[] += 1)")); nothing)
    Filters([(add, :typewise), (number, :typewise)])
end

const SCENARIOS = Dict(
    "upper" => scenario_upper, "modify" => scenario_modify, "splice" => scenario_splice,
    "generic" => scenario_generic, "typewise" => scenario_typewise, "topdown" => scenario_topdown,
    "bottomup" => scenario_bottomup, "skip" => scenario_skip, "toplists" => scenario_toplists,
    "once" => scenario_once, "lists" => scenario_lists, "meta" => scenario_meta,
    "newdoc" => scenario_newdoc, "twice" => scenario_twice, "metacount" => scenario_metacount,
)

@testset "filters as pandoc's Lua" begin
    lines = corpus("filters.jsonl")
    lua = sort([splitext(f)[1] for f in readdir(joinpath(CORPUS, "filters")) if endswith(f, ".lua")])
    @test sort([l["name"] for l in lines]) == lua  # else run scripts/filters-corpus.sh
    @test sort(collect(keys(SCENARIOS))) == lua
    run(filters, input) = foldl(filters; init = fromjson(input)) do doc, (f, traverse)
        walk!(f, doc; traverse, format = FORMAT)
    end
    @testset "$(l["name"])" for l in lines
        @test norm(tojson(run(SCENARIOS[l["name"]](), l["input"]))) == norm(l["output"])
        @testset "$(l["name"]), $traverse" for traverse in l["also"]
            filters = SCENARIOS[l["name"]]()
            @test all(t === :typewise for (_, t) in filters)  # stateless ones set no order
            @test norm(tojson(run([(f, Symbol(traverse)) for (f, _) in filters], l["input"]))) == norm(l["output"])
        end
    end
end
