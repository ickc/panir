# Changelog

One version for every language (`RELEASING.md`). Python's spelling of a
version is PEP 440's; the others write it as below.

## 0.2.0 (unreleased)

0.1.0 was the version in the repository from the start, never published;
its dependants (libpandoc's bindings, pandadown, panir-filters) followed
`main`. 0.2.0 is what they can require by version instead: everything
since, the review (ickc/panir#3 to #5) and the filter collection's gaps
(#14 to #18, and this release's) included. What they rely on:

### Added

- **A filter's options from a code block's YAML header** (panflute's
  `yaml_filter`), without a YAML library: pandoc reads the header, as a
  document's metadata block.
  - `yaml_block` (TypeScript `yamlBlock`): a text's header and data, by
    pandoc's rule for a YAML metadata block where one starts the text
    (`---`, not followed by a blank line; `---` or `...`; spaces or tabs
    after either); a later `---` line opens nothing, so text an include
    appends is never options. `corpus/yaml-blocks.jsonl` holds the cases,
    each with pandoc 3.14's verdict (`scripts/yaml-blocks-corpus.py`).
  - `to_data` (TypeScript `toData`): metadata as plain data, inlines and
    blocks as their text, text left as text (Rust: a
    `serde_json::Value`).
  - `Options`: the document's metadata under the filter's name, each key
    replaced whole by a block header's; read raw or as text, boolean,
    number (as `lua_number`), list or map (Python `Options.of(meta, name,
    header)` and methods `text`, `boolean`, `number`, `list`, `map`;
    TypeScript the same; Rust `Options::of`, the same methods; Julia
    `Options(meta, name, header)`, an `AbstractDict`, and `Panir.text`,
    `Panir.boolean`, `Panir.number`, `Panir.list`, `Panir.dict`).
  - `yaml_filter` (TypeScript `yamlFilter`; Julia `yaml_filter(f, doc,
    ...)`, for a `do` block): every code block of the given classes
    replaced by the author's function of its options, its data and the
    block, the headers read in one call of a reader the filter gives (its
    host's `read_many`, or the `pandoc` command); a header pandoc reads
    no metadata from (a YAML scalar or list) is no header.
- **The conversion a filter runs in** (`ctx.conversion`):
  - `writer_options` (TypeScript `writerOptions`): the writer's options,
    keyed as pandoc's JSON keys the reader's, with Lua's values, read from
    `$PANDOC_WRITER_OPTIONS`, which libpandoc 1.10 and later sets for the
    filters it runs;
  - `sandbox`: whether pandoc's sandbox is on, as libpandoc 1.13 tells
    filters (`$PANDOC_SANDBOX`, `"true"` or `"false"`; a callback's
    context's `"sandbox"`); unknown where nothing says (plain pandoc):
    Python `bool | None`, TypeScript `sandbox?: boolean`, Rust
    `Option<bool>`, Julia `Union{Nothing, Bool}`. A host reading through
    the conversion treats unknown as sandboxed;
  - made from libpandoc's context, for a host calling filters in process:
    Python `Conversion.from_context(context, options=None)`, TypeScript
    `Conversion.fromContext(context, options?)`, Rust
    `Conversion::from_context(&context, options)`, Julia
    `Conversion(context; options = nothing)`;
  - a walk started inside a filter function (Lua's `el:walk{...}`) knows
    the conversion of the walk it is in, unless given one (Python
    `el.walk(f)`, `f(el)`, `walk(el, action)`; TypeScript `applyFilter`,
    `walk`; Julia `walk!`; Rust's `ctx.apply` did already).
- **TypeScript: `libpandocFilter(filter, options?)`**, a panir filter (or a
  list) as a filter function of libpandoc's JavaScript host
  (`convertWithFilters`), `runFilter`'s twin; a function of the instance
  returning the filters reaches the instance.
- **Attributes by key**, as Lua's `el.attributes.k`: Python
  `attributes["k"]` (`get`, `pop`, `in`), TypeScript `getAttribute`,
  `setAttribute`, `removeAttribute`, `hasAttribute`, Rust `Attr::get`,
  `set`, `remove`, Julia `el.attr["k"]` (`get`, `pop!`).
- **Metadata as Lua reads it**: Lua's truth (`truthy` in TypeScript, Rust
  and Julia; Python's nodes under `bool()`), `lookup` (a nested option),
  `items` (one value or a list; Rust `into_items` too), `given` (TypeScript,
  Rust), `inlines_of` and `blocks_of` (TypeScript `inlinesOf`,
  `blocksOf`), metadata from plain values in every language (Rust `From`
  and `FromIterator` for `MetaValue`).
- `to_roman_numeral` (TypeScript `toRomanNumeral`), as
  `pandoc.utils.to_roman_numeral`; `lua_number` (TypeScript `luaNumber`),
  as Lua's `tonumber` reads text.
- `check_unique` (TypeScript `checkUnique`): a node in two places, for
  tests and debugging.
- A list of filters, as a Lua file returning several: Python
  `main([f, g])`, `run(doc, [f, g])`; Julia `walk!([f, g], doc)`,
  `run_filter([f, g])`; TypeScript `runFilter([f, g])`,
  `applyFilter(doc, [f, g])`.
- Rust: a filter function can fail (`ctx.fail`, `ctx.ok`), and `apply`
  returns the error with its path; `ctx.apply` (a fragment's walk from a
  function), `ctx.meta()`, `ctx.in_list()`, `Filter::meta_value`, `tag()`,
  `Block::inlines()`, `Attr::new`, `Target::new`, `Block::raw`,
  `Inline::raw`; `Visit`, `VisitMut`'s read-only twin.
- The walks of Rust and Julia stop at `MAX_DEPTH` (256) nodes in one
  another with an error (a runaway top-down filter overflowed the stack in
  Rust and did not end in Julia).
- Each README's "Coming from Lua": what a port of a Lua filter trips over.

### Changed, breaking

- Python: to a type checker `Meta` is the map a filter's metadata function
  is given and `doc.meta` is (`NodeDict[MetaValue, ...]`), which converts
  plain values put into it, no longer `dict[str, MetaValue]`: in a function
  annotated `meta: Meta`, `meta["draft"] = True` checks, as it always ran
  (found by panir-filters' ports, whose type check had run on no file). A
  plain dict annotated `Meta` no longer checks: annotate it `dict[str,
  MetaValue]`, or give it to `Pandoc(meta=...)` as it is. Nothing changes
  when running.
- Rust: `Conversion` has the public fields `writer_options` and `sandbox`:
  a struct literal without `..Conversion::default()` doesn't compile
  (make it with `Conversion::from_context`, `from_parts`, or with
  `..Default::default()`).
- Rust: `apply` and `apply_with` return `Result<(), FilterError>`; the
  `Filter` methods `inlines`, `blocks`, `meta` and `pandoc` return
  `Option<()>`; a filter's order is a value (`fn order(&self) -> Order`);
  an error in a fragment walked with `ctx.apply` is the inner function's,
  not wrapped; `truthy` and `items` take a value or an option, no longer
  coerced to `Option<&MetaValue>`.
- Python: `bool(MetaBool(False))` is `False` (every other metadata value
  true, as Lua's); `[]` returned for a metadata value in a map is an error
  (it deleted the key); `to_python` gives `MetaInlines` and `MetaBlocks` as
  lists of them (TypeScript's `toPlain` too); constructors in Lua's order
  where it differs (`Cite`, `Citation`, `Table`, `TableBody`, `Cell`, whose
  content is one argument); `MetaList([a, b])` is an error (it was a list
  in a list): `MetaList(a, b)`; a host running a script in process gets
  every filter of its `main([f, g])`, and runs them all.
- TypeScript: `Meta` and a `MetaMap`'s content are
  `Record<string, MetaValue | undefined>` (a missing field is typed as
  missing); `ctx.parent` is typed by the function.
- Julia: `Cell(content, alignment, row_span, col_span; attr)` in Lua's
  order (the content a vector); `walk!` deeper than 256 nodes is an
  `ASTDepthError`.
- Supported: Python 3.11 and later, Rust 1.97 and later (edition 2024),
  Julia 1.10 and later with JSON.jl 1.11, Node.js 16.11 and later.
