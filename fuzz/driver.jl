# The fuzzer's Julia driver (see fuzz/README.md): tasks on stdin, two lines
# each (a filter spec or `null`, then a document as pandoc wrote it); for
# each, one line on stdout: the document after the spec's filters, as JSON,
# or `!` and the error.
#
# A spec's filter becomes a function with a method per rule, as a filter is
# written in Julia: `f(x::Str, ctx)`, `f(xs::Vector{Inline}, ctx)`.

using Panir
import JSON

const TEXT = Set(["Str", "Code", "Math", "RawInline", "CodeBlock", "RawBlock"])
const UNWRAP = Set([
    "Emph", "Underline", "Strong", "Strikeout", "Superscript", "Subscript",
    "SmallCaps", "Quoted", "Cite", "Link", "Image", "Span",
    "BlockQuote", "Div", "Figure",
])

# Note a call; whether the rule applies to it.
function called(rule, log, entry)
    push!(log, entry)
    haskey(rule, "mod") ? length(log) % rule["mod"] == rule["eq"] : true
end

function element(el, ctx, rule, log)
    me = length(log) + 1
    tag = string(nameof(typeof(el)))
    text = tag in TEXT
    called(rule, log, text ? "$tag:$(el.text)" : tag) || return nothing
    inline = el isa Panir.Inline
    act = rule["do"]
    mark() = (text && (el.text *= "#$me"); nothing)
    wrapped() = inline ? Panir.Emph(el) : Panir.BlockQuote(el)
    act == "keep" && return nothing
    act == "delete" && return []
    act == "dup" && return [el, el]
    act == "replace" && return inline ? Panir.Str("R") : Panir.Para(Panir.Str("R"))
    act == "wrap" && return wrapped()
    act == "wrapskip" && (skip_children!(ctx); return wrapped())
    act == "unwrap" && return tag in UNWRAP ? el.content : nothing
    act == "mark" && return mark()
    act == "markret" && (mark(); return el)
    act == "skip" && return skip_children!(ctx)
    act == "markskip" && (skip_children!(ctx); return mark())
    error("unknown action $act")
end

function list(xs, ctx, rule, log, name)
    called(rule, log, "$name$(length(xs))") || return nothing
    act = rule["do"]
    act == "keep" && return nothing
    act == "reverse" && return reverse(xs)
    act == "dropfirst" && (isempty(xs) || popfirst!(xs); return nothing)
    act == "append" && return vcat(xs, [name == "Inlines" ? Panir.Str("A") : Panir.Para(Panir.Str("A"))])
    act == "empty" && return []
    act == "skip" && return skip_children!(ctx)
    error("unknown action $act")
end

function meta(m, rule, log)
    called(rule, log, "Meta") || return nothing
    act = rule["do"]
    act == "add" && (m["fuzz"] = Panir.MetaBool(true); return nothing)
    act == "empty" && return Panir.Meta()
    nothing
end

function document(doc, rule, log)
    called(rule, log, "Pandoc") || return nothing
    act = rule["do"]
    act == "prepend" && pushfirst!(doc.blocks, Panir.Para(Panir.Str("P")))
    act == "clear" && empty!(doc.blocks)
    nothing
end

# The spec's filters: (function, order) pairs, and the log they share.
function build(spec)
    log = String[]
    filters = Tuple{Any, Symbol}[]
    for f in spec["filters"]
        mod = Module()
        fn = Core.eval(mod, :(function f end))
        for (name, rule) in f["rules"]
            if name == "Inlines" || name == "Blocks"
                T = name == "Inlines" ? Vector{Panir.Inline} : Vector{Panir.Block}
                Core.eval(mod, :(f(xs::$T, ctx) = $list(xs, ctx, $rule, $log, $name)))
            elseif name == "Meta"
                Core.eval(mod, :(f(m::$(Panir.Meta)) = $meta(m, $rule, $log)))
            elseif name == "Pandoc"
                Core.eval(mod, :(f(doc::$(Panir.Pandoc)) = $document(doc, $rule, $log)))
            else
                T = getfield(Panir, Symbol(name))
                Core.eval(mod, :(f(el::$T, ctx) = $element(el, ctx, $rule, $log)))
            end
        end
        push!(filters, (fn, Symbol(f["traverse"])))
    end
    filters, log
end

function run(spec_line, doc_line)
    spec = JSON.parse(spec_line)
    doc = Panir.parse(doc_line)
    if spec !== nothing
        filters, log = build(spec)
        for (fn, traverse) in filters
            doc = Base.invokelatest(walk!, fn, doc; traverse)
        end
        spec["log"] && push!(doc.blocks, Panir.Para(Panir.Str(join(log, " "))))
    end
    Panir.serialize(doc)
end

function main()
    lines = split(read(stdin, String), '\n')
    for i in 1:2:length(lines) - 1
        out = try
            run(lines[i], lines[i + 1])
        catch e
            e isa InterruptException && rethrow()
            "!" * JSON.json(sprint(showerror, e))
        end
        println(stdout, out)
        flush(stdout)
    end
end

main()
