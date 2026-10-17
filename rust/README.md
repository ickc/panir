# panir (Rust)

pandoc's document AST, generated from pandoc-types: types that serde
encodes as pandoc's JSON, filters run as pandoc runs Lua filters, and
`VisitMut` and `Visit` traits for any other walk (changing, or reading).

```rust
use panir::{Ctx, Filter, Inline, Typewise};

struct Upper;

impl Filter for Upper {
    type Order = Typewise;

    fn inline(&mut self, x: &mut Inline, _: &mut Ctx<Typewise>) -> Option<Vec<Inline>> {
        if let Inline::Str(s) = x {
            *s = s.to_uppercase().into();
        }
        None // keep it, changed in place; Some(vec![...]) splices, Some(vec![]) deletes
    }
}

fn main() {
    // pandoc --filter ./upper
    panir::filter(|doc, format| panir::apply(doc, &mut Upper, format));
}
```

`panir::filter_with` and `panir::apply_with` pass the whole `Conversion`
instead of the format: the formats with extensions and the reader's options
where pandoc (or libpandoc) tells them, as in Python. A function finds it in
`ctx.conversion()`.

## Filters

A `Filter` has pandoc's Lua filter functions, each a method with a default
that does nothing: `inline` and `block` (a `match` on the constructor),
`inlines` and `blocks` (every list of them), `meta`, `pandoc`. `Order` is
one of the three orders filter frameworks use:

- `Typewise` (as pandoc's Lua filters by default, and Haskell's `walk`): one
  walk per kind, each bottom-up: every inline, then every list of inlines,
  then every block, then every list of blocks, then `meta`, then `pandoc`.
- `Topdown` (Lua's other order, and pandocfilters'): `pandoc`, `meta`, then
  a node before its children, which are walked in its replacement too,
  unless the method calls `ctx.skip_children()`, which only a
  `Ctx<Topdown>` has.
- `Bottomup` (panflute's): one walk, each node after its children.

They are checked against pandoc's Lua filters, with the scenarios in
[`corpus/filters/`](../corpus/filters/). For other nodes (`Cell`, `Attr`,
...) or any other walk, `VisitMut`.

## Types and `VisitMut`

- A sum type is an enum: a constructor without fields is a unit variant
  (`Inline::Space`), with one field it holds the value
  (`Inline::Str(Text)`, `Block::Para(Vec<Inline>)`), with more a boxed
  struct of the same name with named fields (`Block::Header(Box<Header>)`,
  built with `Header { level, attr, content }.into()`). Boxed, so that an
  `Inline` or a `Block` is 32 bytes, not the size of the biggest variant
  (152 and 360): less memory and faster walks on big documents. Fields read
  and change through the box as they are (`Inline::Link(l) => l.target.url
  = ...`); the JSON is the same.
- Every string is a `Text`: up to 22 bytes kept in place (no heap
  allocation; 99.7% of the words of a real document), longer ones a heap
  `String`, in the 24 bytes a `String` takes. It reads as a `&str`; make one
  with `.into()` from `&str` or `String` (`*s = s.to_uppercase().into()`);
  `push_str` and `make_mut` change one in place. Against `String`, reading
  and writing JSON is 15–24% faster and a whole document's AST 16% smaller.
- `from_str` reads without tracking where it is, and reads again with
  tracking only to report an error (another 25% faster).
- Products are structs (`Attr { identifier, classes, attributes }`), with
  `Default` where pandoc's Lua constructors have defaults.
- `VisitMut` has a method per type, and `visit_blocks`/`visit_inlines` for
  whole lists (to splice or remove). Each default visits the children
  through the `walk_*` function of the same name. `Visit` is the same,
  reading only (`&`, through `walk_*_ref`): nothing to copy to look
  through part of a document.
- Strings convert as in pandoc's Lua: `inlines("hello world")` is its words
  and spaces, `blocks("...")` `Plain` text, and `"x".into()` is a `Str`
  where one inline goes, `Plain` text where one block goes.
- `from_str`/`to_string` for documents; `from_str` checks the
  `pandoc-api-version` and reports the path of JSON it can't read.

Decoding errors give the exact path in the JSON, plus line and column:

```
meta.author.c[0].c[2].c[2].c: invalid type: integer `42`, expected a string at line 1 column 244
```

(serde's derived adjacently tagged enums lose the path inside `"c"`, so the
sum types' `Deserialize` is generated: it reads `"t"`, then decodes `"c"` in
place.)

Status: a prototype.
