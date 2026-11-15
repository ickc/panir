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
        Pandoc(Header(1, deepcopy(title.content)), Para("made anew"); meta = d.meta)
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

function scenario_attributes()
    function f(s::Span)
        if haskey(s.attr, "k")
            s.attr["first"] = s.attr["k"]
            s.attr["k"] *= "!"
        end
        delete!(s.attr, "drop")
        "mark" in s.attr.classes && push!(s.attr.classes, "seen")
        nothing
    end
    Filters([(f, :typewise)])
end

function scenario_fails()
    n = Ref(0)
    function f(p::Para)
        (n[] += 1) == 2 && error("the second paragraph")
        push!(p.content, Str("!"))
        nothing
    end
    Filters([(f, :typewise)])
end

function scenario_prepend()
    mark = RawInline("tex", "\\raw{}")
    function f(meta::Panir.Meta)
        for k in ["one", "list", "map", "off", "missing"]
            meta[k] = [RawBlock("html", "<$k>"); items(get(meta, k, nothing))]
        end
        nothing
    end
    f(s::Str) = s.text == "raw" ? deepcopy(mark) : nothing  # (one node, returned twice: a copy each)
    Filters([(f, :typewise)])
end

function scenario_helperfails()
    size(config::MetaMap) = get(config.content, "size", nothing)
    size(config) = error("indent: a map of options, not $(repr(config))")
    function f(meta::Panir.Meta)
        s = size(get(meta, "indent", nothing))
        s === nothing || (meta["size"] = s)
        nothing
    end
    Filters([(f, :typewise)])
end

function scenario_replaced()
    seen = String[]
    f(x::Union{Emph, Div}) = (push!(seen, string(nameof(typeof(x)))); x.content)
    function f(s::Strong)
        text = stringify(s)
        push!(seen, "Strong:$text")
        text == "other" && return Underline(Strong(Str("in")), Emph(Str("e")))
        text == "two" && return Inline[Strong(Str("s")), Underline(Strong(Str("u")))]
        nothing
    end
    f(::Underline) = (push!(seen, "Underline"); nothing)
    f(s::Str) = (push!(seen, "Str:$(s.text)"); nothing)
    function f(xs::Vector{Inline})
        !isempty(xs) && xs[1] isa Str && xs[1].text == "list" || return nothing
        push!(seen, "Inlines")
        Inline[Emph(Str("new")), Strong(Emph(Str("g")))]
    end
    Filters([(f, :topdown), (listing(seen), :typewise)])
end

function scenario_metavalues()
    seen = String[]
    function f(v::MetaInlines)
        text = stringify(v)
        push!(seen, "MetaInlines:$text")
        text == "drop" && return MetaValue[]
        text == "two" && return MetaValue[MetaString("two-a"), MetaString("two-b")]
        nothing
    end
    f(::MetaBlocks) = (push!(seen, "MetaBlocks"); nothing)
    f(v::MetaList) = (push!(seen, "MetaList:$(length(v.content))"); nothing)
    f(::MetaMap) = (push!(seen, "MetaMap"); nothing)
    f(v::MetaString) = (push!(seen, "MetaString:$(v.text)"); nothing)
    f(v::MetaBool) = (push!(seen, "MetaBool"); MetaString(string(v.value)))
    f(s::Str) = (push!(seen, "Str:$(s.text)"); nothing)
    Filters([(f, :typewise), (f, :topdown), (listing(seen), :typewise)])
end

function scenario_metadelete()
    f(::MetaBool) = MetaValue[]
    Filters([(f, :typewise)])
end

function scenario_truthy()
    function f(d::Pandoc)
        keys = ["yes", "no", "text", "empty", "null", "noword", "zero", "list", "map", "block", "missing"]
        words = ["$k=$(truthy(get(d.meta, k, nothing)))" for k in keys]
        or(v) = truthy(v) ? v : MetaString("d")
        push!(words, "or=" * join([stringify(or(get(d.meta, k, nothing))) for k in keys], ","))
        push!(d.blocks, Para(join(words, " ")))
        nothing
    end
    Filters([(f, :typewise)])
end

function scenario_lookup()
    paths = [["toc"], ["toc", "depth"], ["toc", "style", "name"], ["toc", "missing"], ["missing", "depth"],
             ["flag", "depth"], ["list", "a"], ["text", "map"], ["toc", "depth", "x"], ["toc", "style", "name", "x"]]
    function f(d::Pandoc)
        words = map(paths) do path
            v = lookup(d.meta, path...)
            "$(join(path, "."))=$(v === nothing ? "none" : stringify(v))"
        end
        for (key, keys...) in paths  # from a value
            isempty(keys) && continue
            v = lookup(get(d.meta, key, nothing), keys...)
            push!(words, "$key:$(join(keys, "."))=$(v === nothing ? "none" : stringify(v))")
        end
        push!(d.blocks, Para(join(words, " ")))
        nothing
    end
    Filters([(f, :typewise)])
end

function scenario_items()
    function f(d::Pandoc)
        words = map(["one", "list", "empty", "map", "nested", "off", "inlines", "missing"]) do k
            texts = map(stringify, items(get(d.meta, k, nothing)))
            "$k=$(length(texts)):$(join(texts, ","))"
        end
        push!(d.blocks, Para(join(words, " ")))
        nothing
    end
    Filters([(f, :typewise)])
end

function scenario_chain()
    mark = Ref("?")
    configure(m::Panir.Meta) = (mark[] = stringify(truthy(get(m, "mark", nothing)) ? m["mark"] : "!"); nothing)
    add(s::Str) = Str(s.text * mark[])
    Filters([(configure, :typewise), (add, :typewise)])
end

const SCENARIOS = Dict(
    "upper" => scenario_upper, "modify" => scenario_modify, "splice" => scenario_splice,
    "generic" => scenario_generic, "typewise" => scenario_typewise, "topdown" => scenario_topdown,
    "bottomup" => scenario_bottomup, "skip" => scenario_skip, "toplists" => scenario_toplists,
    "once" => scenario_once, "lists" => scenario_lists, "meta" => scenario_meta,
    "newdoc" => scenario_newdoc, "twice" => scenario_twice, "metacount" => scenario_metacount,
    "attributes" => scenario_attributes, "fails" => scenario_fails, "replaced" => scenario_replaced,
    "metavalues" => scenario_metavalues, "metadelete" => scenario_metadelete, "helperfails" => scenario_helperfails,
    "truthy" => scenario_truthy, "lookup" => scenario_lookup, "items" => scenario_items, "chain" => scenario_chain,
    "prepend" => scenario_prepend,
)

@testset "filters as pandoc's Lua" begin
    lines = corpus("filters.jsonl")
    lua = sort([splitext(f)[1] for f in readdir(joinpath(CORPUS, "filters")) if endswith(f, ".lua")])
    @test sort([l["name"] for l in lines]) == lua  # else run scripts/filters-corpus.sh
    @test sort(collect(keys(SCENARIOS))) == lua
    # (each scenario's filters in one call, as a Lua file returning a list)
    run(filters, input) = walk!([f => traverse for (f, traverse) in filters], fromjson(input); format = FORMAT)
    @testset "$(l["name"])" for l in lines
        if l["output"] === nothing  # pandoc fails on it: so must the filter
            @test_throws Exception run(SCENARIOS[l["name"]](), l["input"])
            continue
        end
        out = run(SCENARIOS[l["name"]](), l["input"])
        @test norm(tojson(out)) == norm(l["output"])
        @test check_unique(out) === nothing  # (Lua's elements are values: no node in two places)
        @testset "$(l["name"]), $traverse" for traverse in l["also"]
            filters = SCENARIOS[l["name"]]()
            @test all(t === :typewise for (_, t) in filters)  # stateless ones set no order
            out = run([(f, Symbol(traverse)) for (f, _) in filters], l["input"])
            @test norm(tojson(out)) == norm(l["output"])
            @test check_unique(out) === nothing
        end
    end
end
