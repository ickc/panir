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
    # its nodes may hold the list (Emph(xs) has xs itself)
    wrap(xs::Vector{Inline}) = Inline[Emph(xs)]
    @test walk!(exclaim, walk!(wrap, Pandoc(Para("a b")))).blocks == [Para(Emph("a! b!"))]
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
    # top-down, what is below such a node is where the node is now
    paths = Any[]
    drop(p::Para, ctx) = (ctx.index > 1 && deleteat!(ctx.container, ctx.index - 1); nothing)
    drop(s::Str, ctx) = (push!(paths, (s.text, ctx.path)); nothing)
    walk!(drop, Pandoc(Para(Str("a")), Para(Str("b"))); traverse = :topdown)
    @test paths == [("a", Any[:blocks, 0, :content, 0]), ("b", Any[:blocks, 0, :content, 0])]
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

# A long list whose nodes are deleted, kept, replaced by one and by several
# (once quadratic: each replacement by none or several moved the rest).
const LONG = 3_000

# By its number: deleted, kept, replaced by one, by several (an Emph among
# them), returned twice. Top-down, the Emph's Str is walked too.
function splice_rule(s::Str)
    startswith(s.text, "x") && return Str(uppercase(s.text))
    all(isdigit, s.text) || return nothing
    k = Base.parse(Int, s.text)
    (Inline[], nothing, Str("$(k)r"), Inline[Str("$(k)a"), Space(), Emph(Str("x$k"))], Inline[s, s])[k % 5 + 1]
end

# What the walk should make of the k-th Str (from 0), written plainly.
spliced(k, topdown) = (Inline[], Inline[Str("$k")], Inline[Str("$(k)r")],
                       Inline[Str("$(k)a"), Space(), Emph(Str((topdown ? "X" : "x") * "$k"))],
                       Inline[Str("$k"), Str("$k")])[k % 5 + 1]

numbered(n) = Inline[Str("$k") for k in 0:n - 1]

@testset "a long list spliced, $traverse" for traverse in (:bottomup, :topdown, :typewise)
    topdown = traverse === :topdown
    original = numbered(LONG)
    doc = Pandoc(Para(copy(original)))
    content = doc.blocks[1].content
    out = walk!(splice_rule, doc; traverse).blocks[1].content
    @test out == reduce(vcat, (spliced(k, topdown) for k in 0:LONG - 1))
    @test out === content  # the same list
    @test out[1] === original[2]  # kept: the node itself
    @test out[6] == out[7] == original[5] && out[6] !== out[7]  # twice: two nodes
end

@testset "a long list spliced, with contexts, $traverse" for traverse in (:bottomup, :topdown)
    # the context is right after earlier nodes were deleted and multiplied:
    # looked at first halfway
    topdown = traverse === :topdown
    original = numbered(LONG)
    looked = [0, LONG ÷ 2, LONG ÷ 2 + 1, LONG ÷ 2 + 3, LONG ÷ 2 + 4, LONG - 1]
    seen = Dict{Int, Any}()
    function f(s::Str, ctx)
        if all(isdigit, s.text) && Base.parse(Int, s.text) in looked
            seen[Base.parse(Int, s.text)] = (ctx.index, ctx.prev, ctx.next, ctx.container)
        end
        splice_rule(s)
    end
    out = walk!(f, Pandoc(Para(copy(original))); traverse).blocks[1].content
    @test out == reduce(vcat, (spliced(k, topdown) for k in 0:LONG - 1))
    for k in looked
        index, prev, next, container = seen[k]
        before = sum((length(spliced(j, topdown)) for j in 0:k - 1); init = 0)
        @test index == before + 1
        @test container === out
        @test prev === (before > 0 ? out[before] : nothing)
        @test next === (k + 1 < LONG ? original[k + 2] : nothing)
    end
end

@testset "a long list seen from below, and after an error, $traverse" for traverse in (:bottomup, :topdown)
    # a method below the list sees it whole, through its context
    n = 2_000
    lengths = Any[]
    halve(s) = iseven(Base.parse(Int, s.text)) ? Inline[] : Inline[s, Str(s.text)]
    function f(s::Str, ctx)
        startswith(s.text, "in") || return halve(s)
        push!(lengths, (s.text, length(ctx.doc.blocks[1].content)))
        nothing
    end
    items = Inline[k % 100 == 50 ? Emph(Str("in$k")) : Str("$k") for k in 0:n - 1]
    doc = walk!(f, Pandoc(Para(items)); traverse)
    # before the k-th: half of those deleted, half doubled (Emph: one)
    made(k) = sum((j % 100 == 50 ? 1 : iseven(j) ? 0 : 2 for j in 0:k - 1); init = 0)
    @test lengths == [("in$k", made(k) + n - k) for k in 50:100:n - 1]
    @test length(doc.blocks[1].content) == made(n)

    # an error halfway: the list as far as it went, then the rest as it was
    original = numbered(n)
    failing = Pandoc(Para(copy(original)))
    stop(s::Str) = s.text == "1001" ? error("stop") : halve(s)
    @test_throws ErrorException walk!(stop, failing; traverse)
    kept = reduce(vcat, (Inline[s, s] for (k, s) in enumerate(original[1:1001]) if iseven(k)))
    @test failing.blocks[1].content == vcat(kept, original[1002:end])
end

# Whether two lists hold the same nodes (not equal ones), in order.
same(xs, ys) = length(xs) == length(ys) && all(x === y for (x, y) in zip(xs, ys))

# The nodes of `xs` from `node` (itself, not an equal one) on.
function from_node(xs, node)
    k = findfirst(x -> x === node, xs)
    k === nothing && error("the node is not in the list")
    xs[k:end]
end

@testset "a list being walked seen from a closure, $traverse" for traverse in (:bottomup, :topdown, :typewise)
    # A method deletes or doubles blocks; a later one looks at the document
    # it closed over (not ctx): the list is as it is now, the earlier
    # deletions and doublings made
    original = Block[RawBlock("html", "del"), Para("p1"), RawBlock("html", "dbl"), Para("p3"),
                     RawBlock("html", "del"), Para("p5"), Header(1, Attr(), Inline[Str("h")])]
    doc = Pandoc(original...)
    seen = String[]
    text(b) = b isa RawBlock ? b.text : stringify(b)
    f(b::RawBlock) = b.text == "del" ? Block[] : Block[b, RawBlock("html", "dbl2")]
    function f(p::Para)
        push!(seen, stringify(p) * ": " * join(map(text, doc.blocks), " "))
        @test same(from_node(doc.blocks, p), from_node(original, p))
        nothing
    end
    walk!(f, doc; traverse)
    @test seen == ["p1: p1 dbl p3 del p5 h", "p3: p1 dbl dbl2 p3 del p5 h", "p5: p1 dbl dbl2 p3 p5 h"]
    @test doc.blocks == Block[Para("p1"), RawBlock("html", "dbl"), RawBlock("html", "dbl2"), Para("p3"),
                              Para("p5"), Header(1, Attr(), Inline[Str("h")])]

    # one level down: a Para's inlines, seen from a method on them
    para = Para("a b c d e")
    inlines = copy(para.content)
    doc2 = Pandoc(para)
    texts = String[]
    g(::Space) = Inline[]
    function g(s::Str)
        push!(texts, s.text * ": " * stringify(para.content))
        @test same(from_node(para.content, s), from_node(inlines, s))
        @test doc2.blocks[1].content === para.content
        s.text == "c" ? Inline[s, Str("C")] : nothing
    end
    walk!(g, doc2; traverse)
    @test texts == ["a: a b c d e", "b: ab c d e", "c: abc d e", "d: abcCd e", "e: abcCde"]
    @test doc2.blocks == Block[Para(Inline[Str(t) for t in ["a", "b", "c", "C", "d", "e"]])]
end

@testset "neighbours while a long list is spliced, $traverse" for traverse in (:bottomup, :topdown)
    # ctx.prev and ctx.next, of the node's context and of contexts kept from
    # earlier nodes, while nodes are deleted and doubled: as the list is now
    # (the nodes made so far, then the rest), which ctx.container then shows
    n = 3_000
    original = numbered(n)
    made = Inline[]  # what the walk made of the nodes so far
    kept = Any[]
    right(c, now) = (i = c.index; c.prev === (i > 1 ? now[i - 1] : nothing) &&
                                  c.next === (i < length(now) ? now[i + 1] : nothing))
    wrong = Ref(0)
    function f(s::Str, ctx)
        k = Base.parse(Int, s.text)
        now = vcat(made, original[k + 1:end])
        ctx.index == length(made) + 1 || (wrong[] += 1)
        right(ctx, now) || (wrong[] += 1)
        k % 100 == 0 && (wrong[] += count(c -> !right(c, now), kept))
        k == n ÷ 2 && !same(ctx.container, now) && (wrong[] += 1)
        push!(kept, ctx)
        r = k % 3 == 0 ? Inline[] : k % 3 == 1 ? Inline[s, Str(s.text)] : nothing
        append!(made, r === nothing ? Inline[s] : r)
        r
    end
    doc = walk!(f, Pandoc(Para(copy(original))); traverse)
    @test wrong[] == 0
    @test same(doc.blocks[1].content, made)
    @test all(c -> right(c, made), kept)
end

@testset "a context kept from an earlier pass shows the list as it is" begin
    kept, seen = Any[], Any[]
    # typewise: the inlines, then the blocks
    h(::Str, ctx) = (push!(kept, ctx); nothing)
    h(::RawBlock) = Block[]
    h(::Para) = (push!(seen, [nameof(typeof(b)) for b in kept[1].doc.blocks]); nothing)
    walk!(h, Pandoc(RawBlock("html", "x"), Para("a"), RawBlock("html", "y"), Para("b")))
    @test seen == [[:Para, :RawBlock, :Para], [:Para, :Para]]
end

@testset "a list being walked changed from a closure, $traverse" for traverse in (:bottomup, :topdown, :typewise)
    # after an earlier block was deleted and one doubled, a method changes
    # the document it closed over (not ctx.container): what it changed, it
    # changed in the list as it is
    text(b) = b isa RawBlock ? b.text : stringify(b)
    find(blocks, t) = findfirst(b -> stringify(b) == t, blocks)
    function run(change)
        doc = Pandoc(RawBlock("html", "del"), Para("a"), RawBlock("html", "dbl"),
                     Header(1, Attr(), Inline[Str("One")]), Para("x"), Header(1, Attr(), Inline[Str("Two")]))
        calls = String[]
        f(b::RawBlock) = b.text == "del" ? Block[] : Block[b, RawBlock("html", "dbl2")]
        f(h::Header) = (push!(calls, stringify(h)); change(doc.blocks, stringify(h)); nothing)
        walk!(f, doc; traverse)
        @test calls == ["One", "Two"]
        map(text, doc.blocks)
    end
    # before it, after it, at the end
    @test run() do blocks, at
        at == "One" || return
        pushfirst!(blocks, Para("TOC"))
        deleteat!(blocks, find(blocks, "a"))
        deleteat!(blocks, find(blocks, "x"))
        push!(blocks, Para("end"))
    end == ["TOC", "dbl", "dbl2", "One", "Two", "end"]
    # a node replaced through the closure
    @test run((blocks, at) -> at == "One" && (blocks[find(blocks, "a")] = Para("A"))) ==
          ["A", "dbl", "dbl2", "One", "x", "Two"]
end
