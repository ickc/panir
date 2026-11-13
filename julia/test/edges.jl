# The edges: what a review and fuzzing against pandoc found (fuzz/).

const VERSION_ = "\"pandoc-api-version\":[1,23,1,2]"
table(width) = "{$VERSION_,\"meta\":{},\"blocks\":[{\"t\":\"Table\",\"c\":[[\"\",[],[]],[null,[]]," *
    "[[{\"t\":\"AlignDefault\"},{\"t\":\"ColWidth\",\"c\":$width}]],[[\"\",[],[]],[]],[],[[\"\",[],[]],[]]]}]}"
width(doc) = doc.blocks[1].col_specs[1].width.width

@testset "doubles that are not finite, as pandoc writes them" begin
    # (a column `width="0*"` in HTML gives null)
    for (written, value) in [("null", NaN), ("\"+inf\"", Inf), ("\"-inf\"", -Inf)]
        doc = parse(table(written))
        @test isequal(width(doc), value)
        @test norm(JSON.parse(serialize(doc))) == norm(JSON.parse(table(written)))
        made = Pandoc(Table(; col_specs = [ColSpec(; width = ColWidth(value))]))
        @test norm(JSON.parse(serialize(made))) == norm(JSON.parse(table(written)))
    end
    @test width(parse(table("1"))) === 1.0
    for bad in ("\"inf\"", "true", "\"0.5\"")
        @test_throws ASTDecodeError parse(table(bad))
    end
    # equal as they hash
    @test ColWidth(NaN) in Set([ColWidth(NaN)])
    @test !isequal(ColWidth(0.0), ColWidth(-0.0)) && ColWidth(0.0) == ColWidth(-0.0)
end

@testset "what pandoc could not read is not written or read" begin
    @test_throws ArgumentError serialize(Pandoc(Para(Str("a\xffb"))))
    huge = "{$VERSION_,\"meta\":{},\"blocks\":[{\"t\":\"Header\",\"c\":[99999999999999999999,[\"\",[],[]],[]]}]}"
    # (JSON.jl 0.21 wraps such a number itself)
    pkgversion(JSON) >= v"1" && @test_throws ASTDecodeError parse(huge)
end

@testset "deeper than JSON.jl reads unless told" begin
    deep = "{$VERSION_,\"meta\":{},\"blocks\":[" * "{\"t\":\"Div\",\"c\":[[\"\",[],[]],["^300 *
        "{\"t\":\"Para\",\"c\":[]}" * "]]}"^300 * "]}"
    doc = parse(deep)
    @test parse(serialize(doc)) == doc
end

@testset "constructors" begin
    # a product given whole
    @test Link(Str("x"); target = Target("u", "t")).target == Target("u", "t")
    @test Link(Str("x"); url = "u").target == Target("u", "")
    @test Header(1, Str("x"); attr = Attr(; identifier = "i")).attr.identifier == "i"
    @test_throws ArgumentError Link(Str("x"); target = Target("u"), title = "t")
    @test_throws ArgumentError Link(Str("x"))
    @test_throws ArgumentError Div(; attr = Attr(), identifier = "")
    # a list of lists: one vector of nodes is one item
    @test BulletList([Plain("a")]).content == [Block[Plain("a")]]
    @test BulletList([Plain("a")], [Para("b")]).content == [Block[Plain("a")], Block[Para("b")]]
    @test BulletList([[Plain("a")], [Para("b")]]) == BulletList([Plain("a")], [Para("b")])
    @test LineBlock([Str("a")]).content == [Inline[Str("a")]]
    @test OrderedList([Plain("a")]; start = 2).content == [Block[Plain("a")]]
    @test DefinitionList(([Str("a")], [[Para("x")]])).content == [(Inline[Str("a")], [Block[Para("x")]])]
    @test DefinitionList([(Inline[Str("a")], [Block[Para("x")]])]) == DefinitionList(([Str("a")], [[Para("x")]]))
end

@testset "names another module has" begin
    # exported twice, a name is undefined: Panir.Meta, Panir.Period
    m = Module()
    Core.eval(m, :(using Panir, Dates))
    @test Core.eval(m, :(Meta.parse("1 + 1"))) == :(1 + 1)
    @test Core.eval(m, :(Period === Dates.Period))
    @test Core.eval(m, :(Panir.Period)) isa Panir.ListNumberDelim
    @test Panir.Meta === Dict{String, MetaValue}
end

module EdgeFilters
using Panir
# the most specific method wins, with the context or without
shadowed(x, ctx) = nothing
shadowed(s::Str) = Str(uppercase(s.text))
shadowed(xs::Vector{Inline}) = push!(xs, Str("!"))
inline2(x::Inline, ctx) = nothing
inline2(s::Str) = Str(uppercase(s.text))
tie(s::Str) = Str("without")
tie(s::Str, ctx) = Str("with")
# methods with type parameters, and with any number of arguments
bounded(x::T) where {T <: Inline} = x isa Str ? Str(uppercase(x.text)) : nothing
bounded2(x::T, ctx) where {T <: Block} = (push!(SEEN, string(nameof(T))); nothing)
anything(x::T) where {T} = (push!(SEEN, string(nameof(T))); nothing)
varargs(args...) = (push!(SEEN, string(nameof(typeof(args[1])))); nothing)
const SEEN = String[]
end

@testset "which method" begin
    F = EdgeFilters
    para() = Pandoc(Para(Str("a")))
    @test walk!(F.shadowed, para()).blocks == [Para(Str("A"), Str("!"))]
    @test walk!(F.inline2, para()).blocks == [Para(Str("A"))]
    @test walk!(F.tie, para()).blocks == [Para(Str("with"))]
    for traverse in (:typewise, :topdown, :bottomup)
        @test walk!(F.bounded, para(); traverse).blocks == [Para(Str("A"))]
        empty!(F.SEEN)
        walk!(F.bounded2, para(); traverse)
        @test F.SEEN == ["Para"]
        # a method for anything gets the nodes, not the lists or the metadata
        empty!(F.SEEN)
        walk!(F.anything, para(); traverse)
        @test sort(F.SEEN) == ["Pandoc", "Para", "Str"]
        empty!(F.SEEN)
        walk!(F.varargs, para(); traverse)
        @test sort(F.SEEN) == ["Pandoc", "Para", "Str"]
    end
end

@testset "methods newer than the caller" begin
    # a host that includes a filter script and walks at once, as handoff's
    host(code, doc) = (f = Core.eval(Module(), code); walk!(f, doc))
    doc = host(:(using Panir; up(s::Str) = Str(uppercase(s.text)); up), Pandoc(Para(Str("a"))))
    @test doc.blocks == [Para(Str("A"))]
end

@testset "a node returned twice is two nodes" begin
    # as in Lua, where a function's results are values
    twice(p::Para) = [p, p]
    exclaim(s::Str) = (s.text *= "!"; nothing)
    doc = walk!(exclaim, walk!(twice, Pandoc(Para(Str("a")))))
    @test doc.blocks == [Para(Str("a!")), Para(Str("a!"))]
    @test doc.blocks[1] !== doc.blocks[2]
    both(e::Emph) = [e, e]
    both(s::Str) = (s.text *= "!"; nothing)
    @test walk!(both, Pandoc(Para(Emph(Str("b")))); traverse = :topdown).blocks ==
        [Para(Emph(Str("b!")), Emph(Str("b!")))]
    doubled(xs::Vector{Inline}) = length(xs) == 1 ? [xs; xs] : nothing
    @test walk!(exclaim, walk!(doubled, Pandoc(Para(Str("c"))))).blocks == [Para(Str("c!"), Str("c!"))]
    # a list method may return its own list, changed
    more(xs::Vector{Inline}) = push!(xs, Str("e"))
    @test walk!(more, Pandoc(Para(Str("d")))).blocks == [Para(Str("d"), Str("e"))]
    # or a string, as in pandoc's Lua
    words(xs::Vector{Inline}) = "two words"
    @test walk!(words, Pandoc(Para(Str("d")))).blocks == [Para("two words")]
end

@testset "the context" begin
    found = Dict{String, Any}()
    note(p::Para, ctx) = (found[stringify(p)] = ctx; nothing)
    a, b, c = Para("a"), Para("b"), Para("c")
    doc = walk!(note, Pandoc(BulletList([a, b], [c])))
    items = doc.blocks[1].content
    @test found["a"].index == 1 && found["a"].next === b && found["a"].prev === nothing
    @test found["b"].index == 2 && found["b"].prev === a && found["b"].next === nothing
    @test found["c"].index == 1 && found["c"].container === items[2]
    @test found["b"].container[found["b"].index] === b
    @test found["b"].path == Any[:blocks, 0, :content, 0, 1]
    # in a map: the map and the key
    keys_ = Any[]
    meta(m::MetaValue, ctx) = (push!(keys_, (ctx.index, ctx.container === ctx.doc.meta, ctx.path)); nothing)
    walk!(meta, Pandoc(; meta = Dict{String, MetaValue}("title" => MetaString("t"))))
    @test keys_ == [("title", true, Any[:meta, "title"])]
    # the document a Pandoc method returned is the one walked, and the context's
    fresh = Pandoc(Para(Str("new")))
    docs = Any[]
    replaced(d::Pandoc) = fresh
    replaced(s::Str, ctx) = (push!(docs, ctx.doc); Str(uppercase(s.text)))
    @test walk!(replaced, Pandoc(Para(Str("old"))); traverse = :topdown).blocks == [Para(Str("NEW"))]
    @test docs == [fresh] && docs[1] === fresh
end

@testset "a method may change the list its node is in" begin
    function merge(s::Str, ctx)
        ctx.prev isa Space && deleteat!(ctx.container, ctx.index - 1)
        Str(uppercase(s.text))
    end
    @test walk!(merge, Pandoc(Para("a b c d"))).blocks == [Para(Str("A"), Str("B"), Str("C"), Str("D"))]
    gone(s::Str, ctx) = (empty!(ctx.container); nothing)
    @test_throws ArgumentError walk!(gone, Pandoc(Para("a b")))
    later(m::MetaValue, ctx) = (delete!(ctx.container, "b"); nothing)
    doc = walk!(later, Pandoc(; meta = Dict{String, MetaValue}("a" => MetaBool(true), "b" => MetaBool(true))))
    @test collect(keys(doc.meta)) == ["a"]
end

@testset "stringify goes through a map in the order of its keys" begin
    meta = Dict{String, MetaValue}(k => MetaString(k) for k in split("theta alpha epsilon gamma zeta eta delta beta"))
    @test stringify(meta) == "alphabetadeltaepsilonetagammathetazeta"
end
