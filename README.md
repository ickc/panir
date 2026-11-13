# panir

pandoc's intermediate representation (pan + IR): the document AST every
pandoc reader produces and every writer consumes, for other languages, generated from
[pandoc-types](https://github.com/jgm/pandoc-types) rather than written by
hand: types, pandoc's JSON encoding, checks with useful errors, and filters.

It needs neither pandoc nor [libpandoc](https://github.com/ickc/libpandoc):
a filter written with it runs under `pandoc --filter`, and in-process with
libpandoc's bindings, unchanged.

| language | status | package |
|---|---|---|
| Python | working | [`python/`](python/): `panir` on PyPI (not yet published) |
| Rust | prototype | [`rust/`](rust/): crate `panir` |
| TypeScript | prototype | [`ts/`](ts/): npm `panir`, browser and Node.js |
| Julia | prototype | [`julia/`](julia/): `Panir.jl` |

Each follows its language's idioms, from the same schema:

| | nodes | wrong value | filter |
|---|---|---|---|
| Python | a class per constructor, checked fields and lists | `ASTTypeError` where it happens | `@f.on(Header)` functions |
| Rust | enums, structs with named fields | doesn't compile | a `Filter` trait (Lua's functions); `VisitMut` |
| TypeScript | plain objects, discriminated by `t` | `ASTTypeError` from constructors and `serialize` | `{ Header(h) {...} }`, as Lua filters |
| Julia | a struct per constructor, abstract type per sum | Julia's own `MethodError` | a method per node type |

Every one of them runs as `pandoc --filter`, and runs filters as pandoc runs
Lua filters, in the three orders filter frameworks use: typewise (Lua's
default), top-down (Lua's option, pandocfilters), bottom-up (panflute).

## How it stays in sync with pandoc

```
pandoc-types ──(haskell/, Template Haskell)──> schema/reified.json
             ──(QuickCheck, pandoc-types' own)─> corpus/arbitrary.jsonl
schema/reified.json ──(tools/derive.py)──> schema/pandoc-ast.json, corpus/invalid.jsonl
schema/pandoc-ast.json ──(tools/gen_<language>.py)──> each language's declarations
```

- **`schema/reified.json`**: pandoc-types' declarations, read at compile time
  from pandoc-types itself. An unknown kind of type stops the build.
- **`schema/pandoc-ast.json`**: what every binding generates from. It adds
  what the declarations don't say, decided once for all languages: field
  names (pandoc's Lua API's where it has them), how each type is encoded in
  JSON, defaults, which field constructors take as varargs. `tools/derive.py`
  documents the rules, and fails rather than guesses when pandoc-types changes
  beyond them.
- **The corpus** is the contract. `arbitrary.jsonl` holds random documents
  that pandoc-types' own aeson instances encoded, covering every
  constructor; every binding must decode and re-encode each one unchanged.
  `invalid.jsonl` holds documents each binding must reject, each with the
  path of the offending value (in field names, and in the JSON), which the
  binding must report. `pandoc.jsonl`
  is pandoc's own output for `corpus/*.md`.
- **Filters are checked against pandoc's Lua filters.** Each
  `corpus/filters/NAME.lua` is a scenario (replacing, splicing, typewise
  and topdown order, list functions, skipping children...), and
  `filters.jsonl` holds what pandoc made of a document with it
  (`scripts/filters-corpus.sh`). Each binding writes the scenario as its
  own filter, which must make exactly the same document. Scenarios marked
  stateless must also make it bottom-up, an order pandoc's Lua lacks. So
  far: TypeScript, Python, Julia and Rust.
- **Fuzzing against pandoc** ([`fuzz/`](fuzz/)): random documents, with the
  strings and numbers a binding is likely to get wrong, and random filters,
  through every language. pandoc must read each language's output as the
  document pandoc itself made, of the document alone or with the same
  filter run by its Lua. CI runs a few thousand; `fuzz/run.py --time` runs
  for as long as asked.

The contract, for every language: it reads everything pandoc writes, and
pandoc reads what it writes back to the same AST. The bytes may differ (a
number's format, the order of keys), and JSON pandoc would not write may be
refused or accepted. What a language can't hold it refuses, with the path:
in JavaScript, an integer beyond 2^53.
- **Each language** generates declarations only (classes, fields, encodings)
  and has a small hand-written, generic runtime that reads them. Nothing in a
  runtime names a pandoc type, except helpers like `stringify`.

When pandoc-types changes: bump `pins.env`, run `scripts/generate.sh`, and
read the diff.

## Layout

- `haskell/`: the tool that writes `reified.json` and `arbitrary.jsonl`
  (depends on pandoc-types only; builds in about a minute)
- `schema/`, `corpus/`: generated, committed; CI checks they are current
- `tools/`: `derive.py`, and a generator per language
- `fuzz/`: the fuzzer, its oracle (a Lua filter) and a driver per language
- `python/`, `rust/`, `ts/`, `julia/`: the packages

## License

BSD-3-Clause, like pandoc-types, which the schema is derived from. The
generated code depends on neither pandoc nor libpandoc (which are GPL).
