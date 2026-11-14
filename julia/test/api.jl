# The API as pandoc's Lua has it: constructors' order and conversions, what
# a filter method may return, errors, fragments, stringify. (What Lua does,
# each checked with pandoc 3.14's `pandoc lua` and `--lua-filter`.)

function words(ws...)
    out = Inline[]
    for (k, w) in enumerate(ws)
        k > 1 && push!(out, Space())
        push!(out, Str(w))
    end
    out
end

# what an expression shows as, read back
shown(x) = Core.eval(Main, Meta.parse(repr(x)))

@testset "constructors copy the lists and maps they are given" begin
    xs = Inline[Str("a")]
    bs = Block[Para("p")]
    @test Para(xs).content !== xs && Para(xs).content == xs
    @test Emph(xs).content !== xs && Header(1, xs).content !== xs && Header(1, Attr(), xs).content !== xs
    @test Div(bs).content !== bs && Pandoc(bs).blocks !== bs && Pandoc(Dict{String, MetaValue}(), bs).blocks !== bs
    items = [bs]
    b = BulletList(items)
    @test b.content !== items && b.content[1] !== bs && b.content[1][1] === bs[1]  # (nodes: the caller's)
    m = Dict{String, MetaValue}("k" => MetaBool(true))
    @test Pandoc(; meta = m).meta !== m && MetaMap(m).content !== m
    classes = ["c"]
    @test Attr("", classes, []).classes !== classes && Div(; classes).attr.classes !== classes
    cs = [Citation("k", NormalCitation)]
    @test Cite(xs, cs).citations !== cs && Cite(xs, cs).content !== xs
end

@testset "constructors take attributes as pairs, or a Dict in key order" begin
    @test Attr(; attributes = Dict("z" => "1", "a" => "2", "m" => "3")).attributes == [("a", "2"), ("m", "3"), ("z", "1")]
    @test Div(; attributes = Dict("z" => "1", "a" => "2")).attr == Attr(; attributes = ["a" => "2", "z" => "1"])
    @test Attr("i", ["c"], ["k" => "v", "a" => "b"]).attributes == [("k", "v"), ("a", "b")]
    @test Span("x"; attributes = [("k", "v")]).attr.attributes == [("k", "v")]
    @test_throws MethodError Attr(; classes = Set(["a", "b"]))  # (no order)
end

@testset "constructors take their arguments in the order of Lua's" begin
    # pandoc.Cite(content, citations)
    cite = Cite("a b", [Citation("k", NormalCitation)])
    @test cite.content == words("a", "b") && cite.citations[1].id == "k"
    @test Cite(; citations = cite.citations, content = "a b") == cite
    # pandoc.Citation(id, mode, prefix, suffix, note_num, hash)
    c = Citation("k", AuthorInText, "p", "s", 3, 4)
    @test (c.id, c.mode, c.prefix, c.suffix, c.note_num, c.hash) == ("k", AuthorInText, [Str("p")], [Str("s")], 3, 4)
    @test Citation("k"; note_num = 3) == Citation("k", NormalCitation, Inline[], Inline[], 3)
    # pandoc.TableBody(body, head, row_head_columns, attr)
    body = TableBody([Row(), Row()], [Row()], 2; identifier = "b")
    @test (length(body.body), length(body.head), body.row_head_columns, body.attr.identifier) == (2, 1, 2, "b")
    @test TableBody([Row()]) == TableBody(; body = [Row()])
    # pandoc.Table(caption, colspecs, head, bodies, foot, attr)
    t = Table(Caption("c"), [ColSpec()], TableHead(Row(Cell("h"))), [body], TableFoot(); identifier = "t")
    @test (t.caption.content, length(t.col_specs), length(t.head.rows), t.bodies, t.attr.identifier) ==
          ([Plain("c")], 1, 1, [body], "t")
    @test Table(; caption = Caption("c"), col_specs = [ColSpec()], head = TableHead(Row(Cell("h"))),
                bodies = [body], identifier = "t") == t
    # shown in that order, read back
    for x in (cite, c, body, t)
        @test shown(x) == x
    end
end

@testset "constructors convert as pandoc's Lua" begin
    @test Div(Str("a")).content == [Plain(Str("a"))]
    @test Div([Str("x"), Inline[Str("y")], "z w"]).content == [Plain(Str("x")), Plain(Str("y")), Plain(words("z", "w"))]
    @test Div([["a b"]]).content == [Plain(Str("a b"))]
    @test BulletList(Para("a"), "b c", Str("s")).content == [[Para("a")], [Plain(words("b", "c"))], [Plain(Str("s"))]]
    @test LineBlock("a b", ["c d"]).content == [words("a", "b"), [Str("c d")]]
    @test MetaBlocks(Str("x")).content == [Plain(Str("x"))]
    # where a field is set
    p, d = Para(), Div()
    p.content = Str("x")
    @test p.content == [Str("x")]
    d.content = Str("x")
    @test d.content == [Plain(Str("x"))]
    d.content = "a b"
    @test d.content == [Plain(words("a", "b"))]
    push!(d.content, Emph("e"))
    @test d.content[end] == Plain(Emph("e"))
    @test_throws MethodError Para(Para())
    @test_throws MethodError push!(p.content, Para())
end

@testset "metadata converts as pandoc's Lua" begin
    doc = Pandoc(; meta = Dict("s" => "a b", "b" => true, "n" => 42, "f" => 1.5, "e" => 1e20, "i" => Str("x"),
                               "il" => [Str("a"), Space(), Str("b")], "bl" => [Para("p")], "blk" => Para("q"),
                               "l" => ["x", "y"], "mixed" => [Str("a"), "b"], "mp" => Dict("k" => "v"),
                               "empty" => Dict()))
    @test doc.meta == Dict{String, MetaValue}(
        "s" => MetaString("a b"), "b" => MetaBool(true), "n" => MetaString("42"), "f" => MetaString("1.5"),
        "e" => MetaString("1.0e20"), "i" => MetaInlines(Str("x")), "il" => MetaInlines(words("a", "b")),
        "bl" => MetaBlocks(Para("p")), "blk" => MetaBlocks(Para("q")),
        "l" => MetaList(MetaString("x"), MetaString("y")),
        "mixed" => MetaList(MetaInlines(Str("a")), MetaString("b")),
        "mp" => MetaMap(; content = Dict{String, MetaValue}("k" => MetaString("v"))),
        "empty" => MetaMap())
    doc.meta["draft"] = true
    @test doc.meta["draft"] == MetaBool(true)
    @test_throws MethodError doc.meta["x"] = :symbol
end

@testset "what a method returns converts as pandoc's Lua" begin
    for traverse in (:typewise, :topdown, :bottomup)
        r(f) = walk!(f, Pandoc(Para("one")); traverse).blocks
        @test r(p::Para -> [Str("a"), Str("b")]) == [Plain(Str("a")), Plain(Str("b"))]
        @test r(p::Para -> "two words") == [Plain(words("two", "words"))]
        @test r(p::Para -> Str("x")) == [Plain(Str("x"))]
        @test r(p::Para -> [[Str("a")], "s t", Para("q")]) == [Plain(Str("a")), Plain(words("s", "t")), Para("q")]
        @test r(s::Str -> "two words") == [Para(words("two", "words"))]
        @test r(s::Str -> ["two words", Code("e")]) == [Para(Str("two words"), Code("e"))]
        @test r(xs::Vector{Inline} -> Str("only")) == [Para(Str("only"))]
        @test r(xs::Vector{Inline} -> "two words") == [Para(words("two", "words"))]
        @test r(xs::Vector{Block} -> Para("only")) == [Para("only")]
        @test r(xs::Vector{Block} -> "two words") == [Plain(words("two", "words"))]
        @test r(xs::Vector{Block} -> Str("i")) == [Plain(Str("i"))]
        @test walk!(m::Panir.Meta -> Dict("a" => "b"), Pandoc(); traverse).meta ==
              Dict{String, MetaValue}("a" => MetaString("b"))
    end
end

module Wrong
using Panir
str_para(s::Str) = Para("x")
str_int(s::Str, ctx) = 42
list_int(xs::Vector{Inline}) = 42
list_bad(xs::Vector{Block}) = [Para("a"), 42]
doc_para(d::Pandoc) = Para("x")
meta_int(m::Panir.Meta) = 42
meta_bad(m::Panir.Meta) = Dict("k" => :symbol)
metavalue_str(m::MetaValue) = Str("x")
cell_para(c::Cell) = Para("x")
end

@testset "a wrong return value is an ASTTypeError, naming the method and the place" begin
    doc() = Pandoc(Para(Emph("a")); meta = Dict("k" => "v"))
    table() = Pandoc(Table(Caption(), [ColSpec()], TableHead(), [TableBody([Row(Cell("c"))])], TableFoot()))
    for traverse in (:typewise, :topdown, :bottomup)
        for (f, d, path, expected, got) in [
            (Wrong.str_para, doc(), [:blocks, 0, :content, 0, :content, 0], "Inline, a vector of them, a string, or nothing", "Para (a Block)"),
            (Wrong.str_int, doc(), [:blocks, 0, :content, 0, :content, 0], "Inline, a vector of them, a string, or nothing", "Int64 42"),
            (Wrong.list_int, doc(), [:blocks, 0, :content, (traverse === :topdown ? [] : [0, :content])...], "Inline, a vector of them, a string, or nothing", "Int64 42"),
            (Wrong.list_bad, doc(), [:blocks], "Block, a vector of them, a string, or nothing", "Vector{Any} Any[Para(Inline[Str(\"a\")]), 42]"),
            (Wrong.doc_para, doc(), [], "Pandoc or nothing", "Para (a Block)"),
            (Wrong.meta_int, doc(), [:meta], "metadata (a Dict) or nothing", "Int64 42"),
            (Wrong.meta_bad, doc(), [:meta], "metadata (a Dict of what converts to a MetaValue) or nothing", "Dict{String, Symbol} Dict(\"k\" => :symbol)"),
            (Wrong.metavalue_str, doc(), [:meta, "k"], "MetaValue or nothing", "Str (an Inline)"),
            (Wrong.cell_para, table(), [:blocks, 0, :bodies, 0, :body, 0, :cells, 0], "Cell, a vector of them, or nothing", "Para (a Block)"),
        ]
            e = try
                walk!(f, d; traverse)
                nothing
            catch e
                e
            end
            @test e isa ASTTypeError
            e isa ASTTypeError || continue
            @test (e.path, e.expected, e.got) == (path, expected, got)
            @test occursin(string(nameof(f)), e.method) && occursin("Wrong", e.method)
            @test occursin("\n  returned by $(nameof(f))(", sprint(showerror, e))
        end
    end
    e = try walk!(Wrong.str_int, Pandoc(Para(Emph("a")))) catch e; e end
    @test startswith(sprint(showerror, e), "blocks[0].content[0].content[0]: expected Inline, a vector of them, " *
                                           "a string, or nothing, got Int64 42\n  returned by str_int(s::Str, ctx) @ ")
end

@testset "fragments, as Lua's el:walk" begin
    seen = String[]
    note(x) = (push!(seen, string(nameof(typeof(x)))); nothing)
    upper(s::Str) = Str(uppercase(s.text))
    upper(e::Emph) = (push!(seen, "Emph"); nothing)
    upper(xs::Vector{Inline}) = (push!(seen, "Inlines$(length(xs))"); nothing)
    upper(d::Pandoc) = error("not in a fragment")
    upper(m::Panir.Meta) = error("not in a fragment")
    for traverse in (:typewise, :topdown, :bottomup)
        # a node: what is in it, not itself; in place
        empty!(seen)
        e = Emph("a", Str("b"))
        @test walk!(upper, e; traverse) === e
        @test e == Emph(Str("A"), Str("B"))
        @test seen == ["Inlines2"]
        # a list: its own method too; in place
        empty!(seen)
        xs = Inline[Emph("c"), Str("d")]
        @test walk!(upper, xs; traverse) === xs
        @test xs == [Emph("C"), Str("D")]
        @test sort(seen) == ["Emph", "Inlines1", "Inlines2"]
        # a list method's new list: the list's content
        ys = Inline[Str("x"), Str("y")]
        @test walk!(l::Vector{Inline} -> reverse(l), ys; traverse) === ys && ys == [Str("y"), Str("x")]
        # blocks; a node of no sum type; the context: from the fragment, no document
        found = Any[]
        where_(s::Str, ctx) = (push!(found, (ctx.path, ctx.doc, ctx.parent)); nothing)
        bs = Block[Para("a"), Div(Para("b"))]
        walk!(where_, bs; traverse)
        @test found == [([0, :content, 0], nothing, bs[1]), ([1, :content, 0, :content, 0], nothing, bs[2].content[1])]
        empty!(found)
        cell = Cell("c")
        walk!(where_, cell; traverse)
        @test found == [([:content, 0, :content, 0], nothing, cell.content[1])]
        # a Pandoc is a document
        @test walk!(d::Pandoc -> Pandoc(Para("new")), Pandoc(); traverse).blocks == [Para("new")]
    end
end

@testset "functions for nodes that aren't inlines or blocks" begin
    doc() = Pandoc(Table(Caption(Plain("cap")), [ColSpec()], TableHead(Row(Cell("h"))),
                         [TableBody([Row(Cell("b1"), Cell("b2"))]; identifier = "tb")], TableFoot());
                   meta = Dict("k" => MetaList("x", MetaBool(true))))
    for traverse in (:typewise, :topdown, :bottomup)
        seen = String[]
        f(c::Cell) = (push!(seen, "Cell:" * stringify(c)); c.col_span = 2; nothing)
        f(r::Row) = (push!(seen, "Row$(length(r.cells))"); nothing)
        f(a::Attr) = (a.identifier == "tb" && push!(seen, "Attr:tb"); nothing)
        f(c::Caption) = (push!(seen, "Caption"); Caption(Plain("new")))
        f(m::MetaString) = MetaString(uppercase(m.text))
        f(m::MetaBool) = (push!(seen, "MetaBool"); nothing)
        f(c::Citation) = Citation(uppercase(c.id), c.mode)
        d = walk!(f, doc(); traverse)
        # the metadata first (the document's first field); top-down each node
        # before what is in it, else after
        @test seen == (traverse === :topdown ?
            ["MetaBool", "Caption", "Row1", "Cell:h", "Attr:tb", "Row2", "Cell:b1", "Cell:b2"] :
            ["MetaBool", "Caption", "Cell:h", "Row1", "Attr:tb", "Cell:b1", "Cell:b2", "Row2"])
        t = d.blocks[1]
        @test t.caption == Caption(Plain("new")) && t.bodies[1].body[1].cells[2].col_span == 2
        @test d.meta["k"] == MetaList(MetaString("X"), MetaBool(true))
        c = walk!(f, Pandoc(Para(Cite("x", [Citation("k", NormalCitation)]))); traverse)
        @test c.blocks[1].content[1].citations[1].id == "K"
    end
end

@testset "what Pandoc and metadata methods return replaces them" begin
    for traverse in (:typewise, :topdown, :bottomup)
        @test walk!(d::Pandoc -> Pandoc(Para("new")), Pandoc(Para("old")); traverse) == Pandoc(Para("new"))
        @test walk!(m::Panir.Meta -> Dict("k" => MetaBool(true)), Pandoc(; meta = Dict("a" => "b")); traverse).meta ==
              Dict{String, MetaValue}("k" => MetaBool(true))
    end
end

@testset "fromjson of any type" begin
    @test fromjson(Str, tojson(Str("x"))) == Str("x")
    @test fromjson(Inline, tojson(Str("x"))) == Str("x")
    @test fromjson(ColWidth, tojson(ColWidth(0.5))) == ColWidth(0.5)
    @test fromjson(Attr, tojson(Attr("i", ["c"], []))) == Attr("i", ["c"], [])
    e = try fromjson(Str, tojson(Emph("x"))) catch e; e end
    @test e isa ASTDecodeError && sprint(showerror, e) == "expected a Str, got Emph (an Inline)"
end

@testset "text that isn't JSON is an ASTDecodeError" begin
    for text in ("{", "", "nul", "{\"a\":1}x", "[1,]")
        e = try Panir.parse(text) catch e; e end
        @test e isa ASTDecodeError && isempty(e.path) && e.expected == "JSON text"
    end
end

@testset "a document shows a block a line, as it is made" begin
    d = Pandoc(Header(1, "Title"), Para("a", Emph("b")); meta = Dict("k" => "v"))
    text = sprint(show, MIME"text/plain"(), d)
    @test count('\n', text) == 4
    @test startswith(text, "Pandoc(\n    Header(1, ")
    @test Core.eval(Main, Meta.parse(text)) == d
    for x in (Pandoc(), Pandoc(Para("x")), Pandoc(; meta = Dict("k" => "v")))
        @test Core.eval(Main, Meta.parse(sprint(show, MIME"text/plain"(), x))) == x
    end
end

# The metadata of every kind, its keys out of order, and in maps.
every_kind_of_metadata() = Pandoc(Para("body", LineBreak(), Quoted(DoubleQuote, Str("q")));
    meta = Dict{String, MetaValue}(
        "z" => MetaString("zs"), "b" => MetaBool(true),
        "m" => MetaMap(; content = Dict{String, MetaValue}("y" => MetaString("my"), "a" => MetaInlines(Str("ma")),
                                                         "f" => MetaBool(false),
                                                         "l" => MetaList(MetaString("l1"), MetaBlocks(Para("l2"))))),
        "i" => MetaInlines(Str("in"), Note(Para("note")), RawInline("html", "<br/>")),
        "k" => MetaBlocks(Para("bl"))))

stringified(d::Pandoc) = Dict("whole" => stringify(d), "metamap" => stringify(d.meta),
                              "meta" => Dict(k => stringify(v) for (k, v) in d.meta),
                              "blocks" => [stringify(b) for b in d.blocks])

@testset "stringify, as pandoc's Lua pandoc.utils.stringify" begin
    # a whole document: its inlines' text only (no MetaString, no MetaBool)
    @test stringified(every_kind_of_metadata()) == Dict(
        "whole" => "in blmal2body “q”", "metamap" => "truein blmafalsel1l2myzs",
        "meta" => Dict("z" => "zs", "b" => "true", "m" => "mafalsel1l2my", "i" => "in ", "k" => "bl"),
        "blocks" => ["body “q”"])
    # only html's <br, as pandoc
    @test stringify(Inline[Str("a"), RawInline("html", "<br>"), RawInline("html5", "<br>"), RawInline("HTML", "<br>"),
                           RawInline("html", "<hr>"), Str("b")]) == "a b"
    @test stringify(Citation("k", NormalCitation, "pre", "suf")) == "presuf"
    @test stringify(Caption(Para("long"); short = [Str("short")])) == "shortlong"
    @test stringify(CodeBlock("code")) == "" && stringify(Attr("i", ["c"], [])) == ""
end

const PANDOC = get(ENV, "PANDOC", something(Sys.which("pandoc"), ""))
const STRINGIFY_LUA = """
for line in io.lines() do
  local doc = pandoc.read(line, "json")
  local out = {whole = pandoc.utils.stringify(doc), metamap = pandoc.utils.stringify(doc.meta),
    meta = {}, blocks = {}}
  for k, v in pairs(doc.meta) do out.meta[k] = pandoc.utils.stringify(v) end
  for i, b in ipairs(doc.blocks) do out.blocks[i] = pandoc.utils.stringify(b) end
  print(pandoc.json.encode(out))
end
"""

@testset "stringify, against pandoc: $name" for name in ("arbitrary.jsonl", "pandoc.jsonl", "made")
    if isempty(PANDOC)
        @info "no pandoc (set PANDOC): stringify not checked against it"
        continue
    end
    texts = name == "made" ? [serialize(every_kind_of_metadata())] : [JSON.json(j) for j in corpus(name)]
    out = read(pipeline(IOBuffer(join(texts, "\n") * "\n"), `$PANDOC lua -e $STRINGIFY_LUA`), String)
    want = map(filter(!isempty, split(out, '\n'))) do line
        w = JSON.parse(line)
        # (Lua's empty table is written {})
        Dict("whole" => w["whole"], "metamap" => w["metamap"],
             "meta" => Dict{String, Any}(String(k) => v for (k, v) in (w["meta"] isa AbstractDict ? w["meta"] : [])),
             "blocks" => Any[b for b in (w["blocks"] isa AbstractVector ? w["blocks"] : [])])
    end
    got = [stringified(Panir.parse(t)) for t in texts]
    @test length(got) == length(want)
    @test all(got[i] == want[i] for i in eachindex(got))
end

@testset "what is exported" begin
    m = Module()
    Core.eval(m, :(using Panir))
    for name in (:Str, :Para, :Inline, :MetaValue, :InlineMath, :DoubleQuote, :AlignLeft, :LowerRoman, :OneParen,
                 :NormalCitation, :ASTTypeError, :walk!, :stringify)
        @test isdefined(m, name)
    end
    # the common words, the aliases and the enums' types: as Panir.X
    for name in (:Example, :Decimal, :Period, :Meta, :Format, :RowSpan, :ColSpan, :ShortCaption, :RowHeadColumns,
                 :Alignment, :MathType, :QuoteType, :CitationMode, :ListNumberStyle, :ListNumberDelim)
        @test !(name in names(Panir)) && isdefined(Panir, name)
    end
end

@testset "a host takes a script's filter" begin
    f(s::Str) = nothing
    @test Panir.handoff(() -> Panir.hand_over(f, :bottomup)) == (f, :bottomup)
    @test Panir.hand_over(f, :typewise) == false  # (not in handoff: the runner goes on)
    @test Panir.handoff(() -> run_filter(f; traverse = :topdown)) == (f, :topdown)
end

@testset "the README's examples run" begin
    readme = read(joinpath(@__DIR__, "..", "README.md"), String)
    blocks = [m[1] for m in eachmatch(r"```julia\n(.*?)```"s, readme)]
    @test length(blocks) >= 3
    for code in blocks
        m = Module()
        # (a script's run_filter hands its filter over instead of reading stdin)
        Panir.handoff(() -> Base.include_string(m, "using Panir\n" * code))
    end
    m = Module()
    Base.include_string(m, "using Panir\n" * blocks[end])
    @test Core.eval(m, :(walk!(loud, Emph("a", Str("b"))))) == Emph(Str("A"), Str("B"))
end
