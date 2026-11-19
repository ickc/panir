# A filter's options from a code block's YAML header and the document's
# metadata: the split (corpus/yaml-blocks.jsonl, pandoc's verdicts), metadata
# as plain data, the options, and yaml_filter.

@testset "yaml_block as corpus/yaml-blocks.jsonl" begin
    cases = corpus("yaml-blocks.jsonl")
    @test length(cases) > 30
    for c in cases
        @test yaml_block(c["text"]) == (c["header"], c["data"])
    end
end

meta_of(pairs...) = Dict{String, MetaValue}(pairs...)

@testset "metadata as plain data" begin
    meta = meta_of(
        "flag" => MetaBool(false),
        "text" => MetaString("10"),
        "caption" => MetaInlines(Emph("Sales"), Space(), Str("by"), Space(), Str("region")),
        "body" => MetaBlocks(Para("a"), Para("b")),
        "list" => MetaList(MetaString("1"), MetaBool(true), MetaList()),
        "map" => MetaMap(meta_of("k" => MetaInlines(Str("v")), "m" => MetaMap(meta_of()))),
    )
    want = Dict{String, Any}("flag" => false, "text" => "10", "caption" => "Sales by region", "body" => "ab",
                             "list" => Any["1", true, Any[]], "map" => Dict{String, Any}("k" => "v", "m" => Dict{String, Any}()))
    @test to_data(meta) == want
    @test to_data(MetaMap(meta)) == want
    @test to_data(Options(meta)) == want
end

@testset "the options: the document's defaults, the header's values over them" begin
    csv = MetaMap(meta_of("delimiter" => MetaString(";"), "x" => MetaString("1")))
    meta = meta_of(
        "pantable" => MetaMap(meta_of("caption" => MetaString("doc"), "alignment" => MetaString("LC"), "csv" => csv)),
        "other" => MetaMap(meta_of("caption" => MetaString("other's"))),
    )
    header = meta_of("caption" => MetaInlines(Str("block")), "csv" => MetaMap(meta_of("delimiter" => MetaString(","))))
    # a key's value replaced whole: no deeper merge
    @test to_data(Options(meta, "pantable", header)) ==
          Dict("caption" => "block", "alignment" => "LC", "csv" => Dict("delimiter" => ","))
    @test Options(meta, "pantable").values == meta["pantable"].content
    @test Options(meta, "missing", header).values == header
    # under the name, anything but a map is ignored
    @test isempty(Options(meta_of("pantable" => MetaString("x")), "pantable"))
    # the document's own values
    @test Options(meta, "pantable")["alignment"] === meta["pantable"].content["alignment"]
end

@testset "the options, read" begin
    options = Options(meta_of(
        "caption" => MetaInlines(Emph("Sales"), Space(), Str("by")),
        "string" => MetaString("x"),
        "blocks" => MetaBlocks(Para("p")),
        "yes" => MetaBool(true),
        "width" => MetaString("0.4"),
        "big" => MetaString("1000"),
        "fraction" => MetaString("1/2"),
        "ms" => MetaList(MetaString("1"), MetaString("2")),
        "csv" => MetaMap(meta_of("delimiter" => MetaString(";"))),
    ))
    @test options["caption"] isa MetaInlines  # raw: its inlines
    @test get(options, "missing", nothing) === nothing
    @test haskey(options, "ms") && length(options) == 9
    @test (Panir.text(options, "caption"), Panir.text(options, "string"), Panir.text(options, "blocks")) == ("Sales by", "x", "p")
    @test Panir.boolean(options, "yes") === true
    @test Panir.number(options, "width") == 0.4
    @test Panir.number(options, "big") === 1000
    @test Panir.number(options, "fraction") === nothing
    @test Panir.list(options, "ms") == ["1", "2"]
    @test Panir.dict(options, "csv") == Dict("delimiter" => ";")
    # of another shape, or missing: nothing
    for read in (Panir.text, Panir.boolean, Panir.number, Panir.list, Panir.dict)
        @test read(options, "missing") === nothing
    end
    @test Panir.text(options, "yes") === nothing
    @test Panir.text(options, "ms") === nothing
    @test Panir.boolean(options, "string") === nothing
    @test Panir.number(options, "yes") === nothing
    @test Panir.list(options, "csv") === nothing
    @test Panir.dict(options, "ms") === nothing
end

# A reader that reads each header as its metadata from `known` (by its
# text), or, if missing there, as pandoc reads a YAML scalar: blocks.
stub(known, calls) = texts -> begin
    push!(calls, copy(texts))
    [haskey(known, t) ? Pandoc(; meta = known[t]) : Pandoc(Para(t)) for t in texts]
end

@testset "yaml_filter replaces the blocks, with one read of every header" begin
    doc = Pandoc(
        CodeBlock("---\ncaption: one\n---\na,b\n"; classes = ["table"]),
        Div(CodeBlock("plain,data"; classes = ["table"])),
        CodeBlock("---\ncaption: x\n---\nnot ours"; classes = ["other"]),
        CodeBlock("---\ncaption\n---\nc,d"; classes = ["csv"]),  # a scalar: no header
        CodeBlock("---\n---\ne,f"; classes = ["table", "x"]);
        meta = meta_of("pantable" => MetaMap(meta_of("caption" => MetaString("default"), "alignment" => MetaString("LC")))),
    )
    calls = Vector{String}[]
    known = Dict("---\ncaption: one\n---\n" => meta_of("caption" => MetaInlines(Str("one"))), "---\n---\n" => meta_of())
    seen = []
    out = yaml_filter(doc, ["table", "csv"], "pantable", stub(known, calls)) do options, data, block
        push!(seen, (to_data(options), data, block.attr.classes[1]))
        startswith(data, "plain") && return Block[]
        Block[Para(something(Panir.text(options, "caption"), "")), Para(data)]
    end
    @test out === doc
    # one call, every header, in the document's order
    @test calls == [["---\ncaption: one\n---\n", "---\ncaption\n---\n", "---\n---\n"]]
    defaults = Dict{String, Any}("caption" => "default", "alignment" => "LC")
    @test seen == [
        (Dict{String, Any}("caption" => "one", "alignment" => "LC"), "a,b\n", "table"),
        (defaults, "plain,data", "table"),
        (defaults, "---\ncaption\n---\nc,d", "csv"),
        (defaults, "e,f", "table"),
    ]
    @test doc.blocks == Block[
        Para("one"), Para("a,b\n"), Div(Block[]), CodeBlock("---\ncaption: x\n---\nnot ours"; classes = ["other"]),
        Para("default"), Para("---\ncaption\n---\nc,d"), Para("default"), Para("e,f"),
    ]
end

@testset "yaml_filter without headers reads nothing" begin
    calls = Vector{String}[]
    doc = Pandoc(CodeBlock("a,b"; classes = ["table"]), Para("x"))
    yaml_filter((o, d, b) -> nothing, doc, "table", "pantable", stub(Dict(), calls))
    @test isempty(calls)
    @test doc.blocks == Block[CodeBlock("a,b"; classes = ["table"]), Para("x")]
end

@testset "yaml_filter with the pandoc command as the reader" begin
    if isempty(PANDOC)
        @info "no pandoc (set PANDOC): yaml_filter not checked against it"
    else
        read_each(texts) = [Panir.parse(read(pipeline(IOBuffer(t), `$PANDOC -f markdown -t json`), String)) for t in texts]
        text = "---\ncaption: \"*Sales* by region\"\nwidth: 0.4\nn: 010\nmarkdown: true\n---\na,b\n"
        doc = Pandoc(CodeBlock(text; classes = ["table"]);
                     meta = meta_of("pantable" => MetaMap(meta_of("markdown" => MetaBool(false)))))
        got = []
        yaml_filter((options, data, _) -> (push!(got, (options, data)); nothing), doc, "table", "pantable", read_each)
        @test length(got) == 1
        options, data = got[1]
        @test data == "a,b\n"
        @test options["caption"] == MetaInlines(Emph("Sales"), Space(), Str("by"), Space(), Str("region"))
        @test (Panir.text(options, "caption"), Panir.number(options, "width"), Panir.text(options, "n")) == ("Sales by region", 0.4, "10")
        @test Panir.boolean(options, "markdown") === true
    end
end
