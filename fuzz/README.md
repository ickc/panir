# Fuzzing against pandoc

Random documents and random filters, through every panir language, with
pandoc deciding what is right.

    PANDOC=/path/to/pandoc python3 fuzz/run.py --count 2000
    python3 fuzz/run.py --time 3600 --jobs 28          # for an hour
    python3 fuzz/run.py --langs python,rust --mode filter
    python3 fuzz/run.py --replay fuzz/failures/ts-data-0-4.json

It needs the pandoc of `pins.env` on the `PATH` (or `$PANDOC`), and for each
language asked for: Python with `PYTHONPATH=python/src`; Node 24 (`$NODE`);
`cargo build --release` in `fuzz/rust`; Julia with `julia/` instantiated.

## What is checked

The contract (the parent project's `DESIGN.md`): panir reads everything
pandoc writes, and pandoc reads what panir writes back to the same AST.
Bytes may differ (a number's format, the order of keys); the AST may not.

- **Data.** A document is generated from `schema/pandoc-ast.json`, so every
  constructor and field is reached without the generator naming one, with
  the strings, numbers and keys a binding is likely to get wrong (control
  characters, astral characters, 64-bit integers, doubles of 17 digits and
  ones that aren't finite, `__proto__` as a metadata key). pandoc reads and writes it: that is a
  document pandoc wrote. Each language reads and writes that; pandoc reads
  the result and writes it again, which must give the same bytes as pandoc's
  first writing.
- **Filters.** A filter spec is a small program: per node type (`Str`,
  `Inline`, `Inlines`, `Meta`, `Pandoc`...) an action (delete, duplicate,
  replace, wrap, unwrap, change in place, skip the children; reverse a
  list, append to it...), maybe only on some calls (`mod`/`eq`, on the
  count of calls so far), in typewise or top-down order, one filter or two
  in a row. Every call is noted, and the notes become the document's last
  paragraph, so the order of calls is part of the result. `filter.lua` runs
  the spec with pandoc's own Lua filter machinery; each `driver.*` runs it
  with its language's filters, written as a user of that language would.
  The documents must be the same.

A failure is shrunk (rules dropped, list elements removed) and written to
`fuzz/failures/`, as `{lang, why, spec, doc}`; `--replay` runs one again in
every language.

What a language can't hold, and says so, is counted (`refused`), not
failed: in JavaScript, an integer beyond 2^53 (`LIMITS` in `run.py`).

## Not checked here

- Bottom-up order: pandoc's Lua has no such walk to compare with. The
  scenarios in `corpus/filters/` cover it.
- JSON pandoc would not write. A binding may accept more or less of it
  than pandoc does.
