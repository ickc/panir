# panir (Rust)

pandoc's document AST, generated from pandoc-types: types that serde
encodes as pandoc's JSON, filters run as pandoc runs Lua filters, and
`VisitMut` and `Visit` traits for any other walk (changing, or reading).

```rust,no_run
use panir::{Ctx, Filter, Inline};

struct Upper;

impl Filter for Upper {
    fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
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
`inlines` and `blocks` (every list of them), `meta`, `pandoc`. Its
`order()`, as Lua's `traverse`, is one of the three orders filter
frameworks use, `Order::Typewise` unless the filter says another (a value:
a filter can choose it when it runs, and be a `dyn Filter`):

- `Typewise` (as pandoc's Lua filters by default, and Haskell's `walk`): one
  walk per kind, each bottom-up: every inline, then every list of inlines,
  then every block, then every list of blocks, then `meta`, then `pandoc`.
- `Topdown` (Lua's other order, and pandocfilters'): `pandoc`, `meta`, then
  a node before its children, which are walked in its replacement too,
  unless the method calls `ctx.skip_children()` (Lua's `return el,
  false`; in the other orders the children came first, and it does
  nothing).
- `Bottomup` (panflute's): one walk, each node after its children.

```rust
use panir::{Ctx, Filter, Inline, Order};

struct NoNotes;

impl Filter for NoNotes {
    fn order(&self) -> Order {
        Order::Topdown
    }
    fn inline(&mut self, x: &mut Inline, ctx: &mut Ctx) -> Option<Vec<Inline>> {
        if let Inline::Note(_) = x {
            ctx.skip_children(); // what is in a note is left as it is
        }
        None
    }
}
```

`apply` also takes a fragment, as Lua's `el:walk{...}`: a `Block`, an
`Inline`, a `Vec<Block>` or a `Vec<Inline>`, changed in place. As in Lua,
the functions apply to what is in a node, not to the node itself; a list's
function also to the list itself; `meta` and `pandoc` only to a document.

A function fails with `ctx.fail(error)` (an error of any type, or a
string), which returns `None`: the walk stops when the function returns,
and `apply` returns a `FilterError` with the error, the method that
failed, the node's constructor and its path (in the other languages' form:
`blocks[2].content[0]`), shown as they show theirs. `panir::filter` prints
it and exits with status 1, as pandoc expects of a JSON filter.

```rust
use panir::{Block, Ctx, Filter, Inline, Pandoc};

struct NoEmph;

impl Filter for NoEmph {
    fn inline(&mut self, x: &mut Inline, ctx: &mut Ctx) -> Option<Vec<Inline>> {
        match x {
            Inline::Emph(_) => ctx.fail("no emphasis, please"),
            _ => None,
        }
    }
}

let mut doc = Pandoc::new(vec![Block::Para(vec!["a".into(), Inline::Emph(vec![])])]);
let e = panir::apply(&mut doc, &mut NoEmph, None).unwrap_err();
assert_eq!(e.path, "blocks[0].content[1]");
assert_eq!(
    e.to_string(),
    "no emphasis, please\n  in filter function inline, on the Emph at blocks[0].content[1]"
);
```

The context (`Ctx`) has the conversion, `skip_children`, `fail` and the
metadata (`ctx.meta()`, while the blocks are walked: `None` while the
metadata itself is, and in a fragment), and no more: not the node's
parent, siblings or path, which panir's Python and TypeScript contexts
have (pandoc's Lua functions have no context). A function has its node as
`&mut`, and while it does nothing else may reach the node, its parent
included. A walk that needs them is a `VisitMut` that keeps its own (a
stack of what it is in).

They are checked against pandoc's Lua filters, with the scenarios in
[`corpus/filters/`](https://github.com/ickc/panir/tree/main/corpus/filters). For other nodes (`Cell`, `Attr`,
...) or any other walk, `VisitMut`.

### Coming from Lua

What a port of a Lua filter trips over:

- **`return nil` undoes a function's changes in Lua; here they stay.**
  In pandoc's Lua a function gets a copy of its element, and what it
  changes counts only if it returns the element: `return nil` (or no
  `return`) leaves the element as it was, children and lists included
  (checked with pandoc 3.14). Here the node itself is changed, and the
  changes stay whatever the function returns. A Lua filter that changes its
  element, then returns `nil` (pandoc-ext/list-table removes a div's class,
  then declines an empty div) is ported by deciding first, and changing
  after.
- **Lua's elements are values**: `pandoc.Span(meta.title)` puts a copy in
  the body; here, `.clone()` (ownership allows nothing else).
- `el.t`, `el.tag`: `x.tag()`.
- `el.attributes.k`, `= v`, `= nil`: `attr.get("k")`, `attr.set("k", v)`,
  `attr.remove("k")`; the first pair with the key, as in Lua. A Lua table
  as attributes (`{id = "x", class = "a b"}`) gives the identifier and
  classes: `Attr::new("x", &["a", "b"], &[])` (a pair named `class` is an
  attribute named `class`).
- `error(...)`: `return ctx.fail(...)`.
- `return el, false` (top-down): `ctx.skip_children()`.
- Lists count from 0, Lua's from 1 (`el.content[1]`).
- The metadata a Lua filter keeps from its `Meta` function, for the
  functions after: `ctx.meta()`.

## Types and `VisitMut`

- A sum type is an enum: a constructor without fields is a unit variant
  (`Inline::Space`), with one field it holds the value
  (`Inline::Str(Text)`, `Block::Para(Vec<Inline>)`), with more a boxed
  struct of the same name with named fields (`Block::Header(Box<Header>)`,
  built with `Header { level, attr, content }.into()`). Boxed, so that an
  `Inline` or a `Block` is 32 bytes, not the size of the biggest variant
  (152 and 360): less memory and faster walks on big documents. Fields read
  and change through the box as they are (`Inline::Link(l) => l.target.url
  = ...`); the JSON is the same. `x.tag()` is the constructor's name
  (`"Para"`), as Lua's `el.tag` and the JSON's `"t"`; `block.inlines()`
  and `inlines_mut()` the inlines of a `Plain`, `Para` or `Header`, as
  Lua's `block.content` of those.
- Every string is a `Text`: up to 22 bytes kept in place (no heap
  allocation; 99.7% of the words of a real document), longer ones a heap
  `String`, in the 24 bytes a `String` takes. It reads as a `&str`; make one
  with `.into()` from `&str`, `String` or `Cow<str>` (`*s =
  s.to_uppercase().into()`); `push_str`, `extend` and `write!` append to
  one, `edit(|s| ...)` changes it as a `String` (a short one stays in
  the value). Against `String`, reading and writing JSON is 15–24% faster
  and a whole document's AST 16% smaller.
- `from_str` reads without tracking where it is (another 25% faster); the
  path of an error is found from its position in the text, which is gone
  through up to there, not by reading the document again. Lists of inlines
  and of blocks are read to their length, through a stack each kind
  shares, not grown by doubling: a document read takes 5% more memory
  than its clone, where it took 43% more.
- `to_string` writes each type's JSON straight into a string (generated,
  the same text as serde's `Serialize` gives, twice as fast), and
  `to_writer` hands it over block by block.
- Products are structs (`Attr { identifier, classes, attributes }`), with
  `Default` where pandoc's Lua constructors have defaults. Built with
  their fields' names, so in no order to remember (Lua's positional
  constructors have one; `Table { caption, ..Default::default() }` here).
- `Attr::new(identifier, classes, attributes)`, in the order of Lua's
  `pandoc.Attr`, takes `&str`s: `Attr::new("", &["note"], &[("lang",
  "en")])`.
- An `Attr`'s attributes are pairs, in order, a key perhaps in two; by
  key, as Lua's `el.attributes[key]`: `attr.get("k")` is the value of the
  first pair with the key, `attr.set("k", v)` gives that pair the value,
  in its place, or adds a pair at the end, and `attr.remove("k")` removes
  that pair and returns its value; `attr.has_class("c")` asks.
- Every node is `Clone`, `Eq` and `Hash`. A `ColWidth` compares its number
  bit for bit: one that isn't a number (pandoc's `null`) equals itself, and
  `0.0` and `-0.0`, written differently, differ.
- A `Pandoc` is made with `Pandoc::new(blocks)` (then `doc.meta`), and its
  `api_version()` is always one pandoc reads: set when read, and not a
  public field.
- `VisitMut` has a method per type, and `visit_blocks`/`visit_inlines` for
  whole lists (to splice or remove). Each default visits the children
  through the `walk_*` function of the same name. `Visit` is the same,
  reading only (`&`, through `walk_*_ref`): nothing to copy to look
  through part of a document. `doc.visit(&mut v)` walks a document with a
  `Visit`, `doc.visit_mut(&mut v)` with a `VisitMut`.
- Strings convert as in pandoc's Lua: `inlines("hello world")` is its words
  and spaces, `blocks("...")` `Plain` text, and `"x".into()` is a `Str`
  where one inline goes, `Plain` text where one block goes.
- `stringify(&x)` is pandoc's Lua `pandoc.utils.stringify`, exactly (both
  corpora and metadata of every kind are checked against pandoc): the text
  of a document, its `meta`, a block, an inline, a metadata value, a
  citation, a table's parts, or a list of them. A map's values come in the
  order of their keys; a whole document is the text of its inlines only
  (in its metadata too: not its `MetaString` and `MetaBool` values).
- `from_str`/`to_string` (and `from_slice`, `from_reader`, `to_writer`)
  for documents; `from_str` checks the `pandoc-api-version` and reports
  the path of JSON it can't read. It
  reads a document down to `MAX_DEPTH` (256) levels of blocks and inlines in
  each other, and as far as 640 kB of stack allow (a level takes five times
  the stack unoptimized); a deeper one is an error, not a crash. A filter
  walks as deep, counting the walks its functions start (`apply` on a
  fragment) from where they are: deeper, as a top-down function whose
  result holds what it matched makes it (each result walked, without
  end), is a `FilterError` (`function` `None`), with the path. A function
  that calls itself without end is Rust's stack overflow (the process
  aborts): panir can't see it.
- A `ColWidth` that isn't finite is read and written as pandoc does (`null`,
  `"+inf"`, `"-inf"`); a finite one byte for byte.

A decoding error (`panir::Error`) has the exact path in the JSON (`path`,
empty for the document itself), what is wrong (`message`), and the `line`
and `column` in the text; displayed as

```text
meta.author.c[0].c[2].c[2].c: invalid type: integer `42`, expected a string at line 1 column 244
```

The path goes on into contents that came before their tag (`{"c": ...,
"t": ...}`, as JSON with sorted keys has them): the same path as in
pandoc's order. (serde's derived adjacently tagged enums lose the path
inside `"c"`, so the sum types' `Deserialize` is generated: it reads
`"t"`, then decodes `"c"` in place.)

Rust 1.97 or newer: CI runs the tests on 1.97, and the minimum is raised
only when a dependency needs it. Status: a prototype.
