using Panir
using Panir: fromjson, tojson, parse, serialize
using JSON
using Test

const CORPUS = joinpath(@__DIR__, "..", "..", "corpus")
# split on "\n" only: strings may hold other line separators
corpus(name) = [JSON.parse(l) for l in split(read(joinpath(CORPUS, name), String), '\n') if !isempty(l)]

# compare parsed JSON regardless of the parser's container types
norm(x::AbstractDict) = Dict(String(k) => norm(v) for (k, v) in x)
norm(x::AbstractVector) = Any[norm(v) for v in x]
norm(x) = x

@testset "corpus" begin
    @testset "round trip" begin
        for (i, j) in enumerate([corpus("arbitrary.jsonl"); corpus("pandoc.jsonl")])
            @test norm(tojson(fromjson(j))) == norm(j)
        end
    end
    @testset "invalid: $(c["name"])" for c in corpus("invalid.jsonl")
        err = try
            fromjson(c["document"])
            nothing
        catch e
            e
        end
        @test err isa ASTDecodeError
        # field names are Symbols, map keys Strings
        err isa ASTDecodeError &&
            @test Any[p isa Symbol ? String(p) : p for p in err.path] == [p isa Integer ? Int(p) : String(p) for p in c["path"]]
    end
end

@testset "constructors" begin
    h = Header(1, Str("Hi"); identifier = "hi", classes = ["x"])
    @test h.level == 1 && h.attr == Attr("hi", ["x"], Tuple{String, String}[]) && h.content == [Str("Hi")]
    @test Div(; attributes = Dict("k" => "v")).attr.attributes == [("k", "v")]
    @test OrderedList([Plain(Str("a"))]; start = 3).list_attributes.start == 3
    @test OrderedList().list_attributes == ListAttributes(1, DefaultStyle, DefaultDelim)
    @test Cell(Para()).row_span == 1
    @test Link(Str("x"); url = "u").target == Target("u", "")
    @test_throws ArgumentError Link(Str("x"))
    @test_throws ArgumentError Code("x"; attr = Attr(), identifier = "y")
    # the content as one vector, as Para(xs) takes it
    xs = Block[Para(Str("a")), Para(Str("b"))]
    @test Div(xs).content == xs && Div(xs; identifier = "d").attr.identifier == "d"
    @test Header(2, [Str("a"), "b"]; identifier = "h").content == [Str("a"), Str("b")]
    @test Link([Str("x")]; url = "u").content == [Str("x")]
    @test Cell(xs, AlignDefault, 1, 2).content == xs && Cell(; content = xs, col_span = 2).col_span == 2
    @test Pandoc(xs).blocks == xs
    @test Row([Cell(), Cell()]).cells == [Cell(), Cell()]
    @test OrderedList([Plain("a")], [Plain("b")]).content == [[Plain(Str("a"))], [Plain(Str("b"))]]
    @test isempty(Test.detect_ambiguities(Panir))
end

@testset "strings convert as in pandoc's Lua" begin
    hw = Inline[Str("hello"), Space(), Str("world")]
    @test Para("hello world").content == hw
    @test Header(1, "a\nb").content == [Str("a"), SoftBreak(), Str("b")]
    @test Para("a b", Emph("c")).content == [Str("a b"), Emph(Str("c"))]  # several: each a Str
    @test Para(["a b", Emph("c")]).content == [Str("a b"), Emph(Str("c"))]
    @test Div("hello world").content == [Plain(hw...)]
    @test Pandoc("x").blocks == [Plain(Str("x"))]
    @test BulletList("a", "b c").content == [[Plain(Str("a"))], [Plain(Str("b"), Space(), Str("c"))]]
    @test LineBlock("a b", [Str("c")]).content == [[Str("a"), Space(), Str("b")], [Str("c")]]
    p = Para()
    p.content = "hello world"
    @test p.content == hw
    push!(p.content, "!")
    @test p.content[end] == Str("!")
    @test inlines(" a  b\n") == [Space(), Str("a"), Space(), Str("b"), SoftBreak()]
    @test blocks("a") == [Plain(Str("a"))]
end

@testset "Julia checks types" begin
    p = Para(Str("a"))
    @test_throws MethodError push!(p.content, Para())
    @test_throws MethodError (p.content = 42)
    h = Header(1)
    @test_throws MethodError (h.level = "2")
    @test_throws MethodError Para(Para())
end

doc() = Pandoc(
    Header(1, Str("Title")),
    Para(Str("a"), Space(), Emph(Str("b")), Note(Para(Str("n")))),
    BulletList([Plain(Str("x"))], [Plain(Str("y"))]),
)

@testset "the conversion" begin
    c = Conversion(; format = "html5", input_format = "commonmark_x-smart")
    seen = Ref{Any}(nothing)
    f(d::Pandoc, ctx) = (seen[] = (ctx.conversion, ctx.format); nothing)
    walk!(f, Pandoc(Para("x")); format = c)
    @test seen[] == (c, "html5")
    walk!(f, Pandoc(Para("x")); format = "latex")
    @test seen[][1].format == "latex" && seen[][1].input_format === nothing
    env = Conversion(["html5"], Dict("PANDOC_READER_OPTIONS" => "{\"columns\":72}",
                                     "PANDOC_INPUT_FORMAT" => "markdown+smart", "PANDOC_OUTPUT_FORMAT" => ""))
    @test (env.format, env.input_format, env.output_format, env.reader_options["columns"]) ==
          ("html5", "markdown+smart", nothing, 72)
end

@testset "walk!" begin
    d = walk!(doc()) do x
        if x isa Header
            x.level += 1
        end
        nothing
    end
    @test d.blocks[1].level == 2

    act(x) = nothing
    act(s::Str) = s.text == "a" ? Str("A") : s.text == "b" ? Inline[Str("b1"), Space(), Str("b2")] :
                  s.text == "x" ? Inline[] : nothing
    d = walk!(act, doc())
    @test stringify(d.blocks[2]) == "A b1 b2"
    @test d.blocks[3].content[1] == Block[Plain()]

    # dispatch: the most specific method
    seen = String[]
    f(x::Inline) = (push!(seen, string(nameof(typeof(x)))); nothing)
    f(x::Str) = (push!(seen, "str"); nothing)
    walk!(f, Pandoc(Para(Str("a"), Space())))
    @test seen == ["str", "Space"]

    for (traverse, expected) in [(:typewise, ["Str", "Emph", "Para"]), (:bottomup, ["Str", "Emph", "Para"]),
                                 (:topdown, ["Para", "Emph", "Str"])]
        order = String[]
        g(x::Union{Para, Emph, Str}) = (push!(order, string(nameof(typeof(x)))); nothing)
        walk!(g, Pandoc(Para(Emph(Str("x")))); traverse)
        @test order == expected
    end
    @test_throws ArgumentError walk!(identity, doc(); traverse = :inside_out)
    skipper(s::Str, ctx) = (skip_children!(ctx); nothing)
    @test_throws ArgumentError walk!(skipper, doc())

    found = Ref{Any}()
    where_(s::Str, ctx) = (s.text == "b" && (found[] = ctx); nothing)
    walk!(where_, doc(); format = "html")
    ctx = found[]
    @test ctx.path == Any[:blocks, 1, :content, 2, :content, 0]
    @test ctx.parent isa Emph && ctx.index == 1 && ctx.format == "html"
    @test ctx.container[ctx.index] == Str("b")
end

@testset "JSON text, stringify" begin
    d = doc()
    @test parse(serialize(d)) == d
    q = Para(Str("a"), Space(), Quoted(DoubleQuote, Str("q")), Code("c"), Note(Para(Str("n"))))
    @test stringify(q) == "a “q”c"
end
@testset "run_filter hands its filter over" begin
    f(s::Str) = nothing
    @test Panir.handoff(() -> run_filter(f; traverse = :topdown)) == (f, :topdown)
    @test Panir.handoff(() -> nothing) === nothing
    # only in that task
    @test fetch(Threads.@spawn Panir.handoff(() -> fetch(Threads.@spawn get(task_local_storage(), Panir._HANDOFF, nothing)))) === nothing
end
include("filters.jl")
include("edges.jl")
include("api.jl")
