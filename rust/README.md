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
`inlines` and `blocks` (every list of them), `meta`, `pandoc`; and, as the
other languages and panflute, `meta_value`, on each metadata value (in a
`MetaList`, it deletes or splices as `inline` does; a map's value, a field
of the metadata or of a `MetaMap`, is replaced by one value: to delete a
key, `meta` or the `MetaMap`'s function removes it; `ctx.in_list()` says
which). Its
`order()`, as Lua's `traverse`, is one of the three orders filter
frameworks use, `Order::Typewise` unless the filter says another (a value:
a filter can choose it when it runs, and be a `dyn Filter`):

- `Typewise` (as pandoc's Lua filters by default, and Haskell's `walk`): one
  walk per kind, each bottom-up: every inline, then every list of inlines,
  then every block, then every list of blocks, then every metadata value,
  then `meta`, then `pandoc`.
- `Topdown` (Lua's other order, and pandocfilters'): `pandoc`, `meta`,
  then a node before its children, which are walked in its replacement
  too, unless the method calls `ctx.skip_children()` (Lua's `return el,
  false`; in the other orders the children came first, and it does
  nothing). What a function returns is not given to a function again, nor,
  if a list, are its elements; their children are walked (as in pandoc's
  Lua and pandocfilters: an emphasis replaced by its content keeps an
  emphasis that was directly in it). What a list function returns is the
  list whose elements are walked.
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
From inside a function, `ctx.apply(x, &mut filter)` walks a fragment with
the walk's conversion, as deep as the walk already is, and, if a function
of that filter fails, fails with its error (where it was in the fragment:
`inlines[2]`, as the other languages say) and returns `None`, for `?`:

```rust
use panir::{Ctx, Filter, Inline, Order};

struct Caps;

impl Filter for Caps {
    fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
        if let Inline::Str(s) = x {
            *s = s.to_uppercase().into();
        }
        None
    }
}

/// An emphasis becomes its content, in capitals (pandocfilters' deemph).
struct Deemph;

impl Filter for Deemph {
    fn order(&self) -> Order {
        Order::Topdown
    }
    fn inline(&mut self, x: &mut Inline, ctx: &mut Ctx) -> Option<Vec<Inline>> {
        let Inline::Emph(content) = x else { return None };
        ctx.apply(content, &mut Caps)?;
        Some(std::mem::take(content))
    }
}
```

Every function returns an `Option`, so that `?` reads the same in all of
them (`ctx.apply(...)?`, or `meta.get("x")?` to stop early): `inline`,
`block` and `meta_value` return `None` to keep the node, or nodes in its
place; `inlines`, `blocks`, `meta` and `pandoc` change their value in place,
and return `Some(())` (`None` the same: done). A function fails with
`return ctx.fail(error)` (an error of any type, or a string), in any of
them; `fail` returns `None`: the walk stops when the function returns,
and `apply` returns a `FilterError` with the error, the method that
failed, the node's constructor and its path (in the other languages' form:
`blocks[2].content[0]`), shown as they show theirs. `panir::filter` prints
it and exits with status 1, as pandoc expects of a JSON filter. A helper
returning a `Result` (with `?` inside) gives the walk its error through
`ctx.ok(result)`: the value, or `None` having failed with the error, for
`?` (`let x = ctx.ok(helper(...))?;`), or as the function's last
expression (`ctx.ok(self.configure(meta))`, a `Result<(), _>`).

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

/// A level of 1 to 6, from a header's attribute.
fn level(text: &str) -> Result<i64, String> {
    let n: i64 = text.parse().map_err(|_| format!("level {text}: not a number"))?;
    if (1..=6).contains(&n) { Ok(n) } else { Err(format!("level {n}: not 1 to 6")) }
}

struct Levels;

impl Filter for Levels {
    fn block(&mut self, x: &mut Block, ctx: &mut Ctx) -> Option<Vec<Block>> {
        let Block::Header(h) = x else { return None };
        let Some(text) = h.attr.remove("level") else { return None };
        h.level = ctx.ok(level(&text))?;
        None
    }
}

let mut doc: Pandoc = panir::from_str(
    r#"{"pandoc-api-version":[1,23,1],"meta":{},"blocks":[{"t":"Header","c":[1,["",[],[["level","9"]]],[]]}]}"#,
).unwrap();
let e = panir::apply(&mut doc, &mut Levels, None).unwrap_err();
assert_eq!(e.to_string(), "level 9: not 1 to 6\n  in filter function block, on the Header at blocks[0]");
```

The context (`Ctx`) has the conversion, `skip_children`, `fail`, `ok`,
`apply`, `in_list` and the metadata (`ctx.meta()`; while a metadata value is
walked, the other fields, as the value can't be lent while it changes;
`None` in a fragment), and no more: not the node's parent, siblings or
path, which panir's Python and TypeScript contexts have (pandoc's Lua functions have no context). A function has its node as
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
- **Field names** are pandoc's Lua API's (`content`, `text`, `attr`,
  `level`, `citations`, `note_num`, ...), but for pandoc-types' names
  here: `quote_type` (Lua's `quotetype`), `math_type` (`mathtype`),
  `list_attributes` (`listAttributes`), a table's `col_specs` (`colspecs`,
  pairs in Lua, `ColSpec`s of `alignment` and `width` here), a caption's
  `content` (`long`), a cell's `content` (`contents`), an image's
  `content` (`caption`); and a link's or image's `target` is a `Target` of
  `url` and `title` (in Lua `target`, an image's `src`, is the URL, and
  `title` the element's). An element's attributes, target and list
  attributes are fields of their own here: Lua's `el.identifier` is
  `attr.identifier`, `link.title` `target.title`, `ol.start`
  `list_attributes.start`.
- `el.attributes.k`, `= v`, `= nil`: `attr.get("k")`, `attr.set("k", v)`,
  `attr.remove("k")`; the first pair with the key, as in Lua. A Lua table
  as attributes (`{id = "x", class = "a b"}`) gives the identifier and
  classes: `Attr::new("x", &["a", "b"], &[])` (a pair named `class` is an
  attribute named `class`).
- `error(...)`: `return ctx.fail(...)`; in a helper, `Err(...)`, given to
  the walk by `ctx.ok(helper(...))?`; and `el:walk{...}` in a function:
  `ctx.apply(&mut el, &mut filter)?`, in every function (each returns an
  `Option`).
- `return el, false` (top-down): `ctx.skip_children()`.
- In pandoc's Lua (top-down), `return el, false` from the `Pandoc` or
  `Meta` function skips nothing: the metadata and the blocks are walked
  all the same (checked with pandoc 3.14). Here `ctx.skip_children()`
  there skips them, as for any node.
- A Lua file returning a list of filters (`return {configure, quotes}`):
  `apply` after `apply` in `panir::filter`'s function, `?` between; what a
  later one needs of an earlier one (a Lua upvalue) is a field it is given:

  ```rust,no_run
  use panir::{Ctx, Filter, Inline, Meta, stringify};

  #[derive(Default)]
  struct Configure(Option<String>);

  impl Filter for Configure {
      fn meta(&mut self, meta: &mut Meta, _: &mut Ctx) -> Option<()> {
          self.0 = Some(stringify(meta.get("mark")?));
          Some(())
      }
  }

  struct Mark(String);

  impl Filter for Mark {
      fn inline(&mut self, x: &mut Inline, _: &mut Ctx) -> Option<Vec<Inline>> {
          let Inline::Str(s) = x else { return None };
          s.push_str(&self.0);
          None
      }
  }

  fn main() {
      panir::filter(|doc, format| {
          let mut configure = Configure::default();
          panir::apply(doc, &mut configure, format)?;
          let mark = configure.0.unwrap_or_else(|| "!".into());
          panir::apply(doc, &mut Mark(mark), format)
      });
  }
  ```
- Lists count from 0, Lua's from 1 (`el.content[1]`).
- The metadata a Lua filter keeps from its `Meta` function, for the
  functions after: `ctx.meta()`. Inside the metadata, it lacks the field
  being walked (out of the map while it changes): a field that names
  itself, as pandocfilters' metavars allows, looks missing there, where
  the other languages, whose `ctx.doc.meta` is the whole map, walk it
  until the walk is too deep.
- `FORMAT`: `ctx.format()`, the output format pandoc passed; `None` where
  none is known (`apply` with `None`; `panir::filter` run by hand, without
  pandoc's argument), where Lua's `FORMAT` is always a string:
  `ctx.format().unwrap_or_default()`.
- Lua's string patterns (`s:match`, `gsub`, `find`) are not regular
  expressions: `%` escapes (`%.`), `-` is a lazy `*`, `%a` is a letter
  (ASCII), `%s` ASCII whitespace, and there is no `|`; a port translates
  each, or escapes a literal string (`regex::escape`).
- In `gsub`'s replacement too, `%` is special: `%1` is a capture, `%%` a
  percent sign, and any other `%` an error (checked with pandoc 3.14), so
  a replacement taken from the document (a size of `5%`) fails there.
  The `regex` crate's `replace_all(s, text)` expands `$1` and `$name` in a
  replacement: `NoExpand(text)` puts `text` in as it is.
- Lua's strings are bytes: `#s`, `s:len()`, `s:sub(i, j)` and `s:byte(i)`
  count bytes of UTF-8 (`#"é"` is 2; `s:sub(1, 1)` of it is half a
  character, which pandoc reads back as U+FFFD), and `upper` and `lower`
  change ASCII letters only; `utf8.len(s)` counts characters. `s.len()`
  counts bytes too, but `&s[i..j]` panics inside a character, and
  `s.chars().count()` is `utf8.len`; where an original depends on bytes,
  `s.as_bytes()[i - 1..j]`, and `String::from_utf8_lossy`, which makes
  U+FFFD of half a character, as pandoc does.
- `PANDOC_READER_OPTIONS`: `ctx.conversion().reader_options`, a
  `serde_json::Value` keyed as pandoc's JSON (`"track-changes"`,
  `"tab-stop"`, where Lua's fields are `track_changes`, `tab_stop`, and
  pandoc 2's `trackChanges` is gone), with the same values
  (`"all-changes"`); `None` where pandoc passed none.

  ```rust
  let conversion = panir::Conversion::from_parts(Some("latex".into()), |k| {
      (k == "PANDOC_READER_OPTIONS").then(|| r#"{"track-changes": "all-changes"}"#.into())
  });
  let options = conversion.reader_options.as_ref();
  assert_eq!(options.and_then(|o| o["track-changes"].as_str()), Some("all-changes"));
  let s = "«»";
  assert_eq!((s.chars().count(), s.len()), (2, 4));
  assert_eq!(String::from_utf8_lossy(&s.as_bytes()[0..1]), "\u{fffd}");
  ```
- `pandoc.utils.to_roman_numeral(n)`: `to_roman_numeral(n)`, the same (1
  to 3999; `""` for 0, `"?"` for anything else).
- pandoc's Lua has no function for a metadata value (its values are
  plain Lua values, reached through the `Meta` function or `doc.meta`);
  here, as in panflute, `meta_value` is called on each one.

#### Metadata

pandoc's Lua gives a filter the metadata as plain Lua values; here each
value is a node, a `MetaValue`, holding what Lua gives in a field
(checked with pandoc 3.14):

| in the document | in Lua (`pandoc.utils.type`) | here |
|---|---|---|
| `x: some text`; also `x: ""`, `x: 1`, `x: no` | `Inlines` | `MetaValue::MetaInlines(Vec<Inline>)` |
| `x: \|` and indented lines (blocks) | `Blocks` | `MetaValue::MetaBlocks(Vec<Block>)` |
| `-M x=text` on the command line; YAML's null, `x:` | `string` | `MetaValue::MetaString(Text)` |
| `x: true`, `x: false`; `-M x=true` | `boolean` | `MetaValue::MetaBool(bool)` |
| `x: [a, b]` | `List` | `MetaValue::MetaList(Vec<MetaValue>)` |
| `x: {k: v}` | `table` | `MetaValue::MetaMap(Meta)`, a `BTreeMap` |
| no field | `nil` | `None` (`meta.get("x")`) |

`pandoc.utils.type(v)` is a `match` on the variants (`v.tag()` names them:
`"MetaInlines"`).

- **Lua's truth**: `if meta.x then` (and `meta.x or d`) is false for a
  missing field and for `false` only; an empty string, list or map is
  true, as is the text `no`: `truthy(meta.get("x"))`, or `truthy(v)` of a
  value in hand. `given(meta.get("x"))` is the value if `truthy`, else
  `None`: Lua's `meta.x or d` is `given(...).unwrap_or(&d)`, its `meta.x
  and f(meta.x)` `given(...).map(f)`.
- **A nested option**, Lua's `((meta.a or {}).b or {}).c`: `lookup(&meta,
  &["a", "b", "c"])`, `None` where a key is missing or a value on the way
  is not a map (where Lua would fail, on `true`, or find a method, on a
  string or a list). It starts from the metadata (a `Meta`, as a
  `MetaMap`'s content is too), a value (`&meta["a"]`), or one perhaps
  missing: `lookup(&config, &["b"])` of `let config =
  meta.remove("a")`, an `Option`.
- **One value or a list of them** (a filter's `ensure_list`, the shapes
  `header-includes` takes): `items(meta.get("x"))` (or `items(v)`), a
  `MetaList`'s items, nothing for a missing value, any other value alone:
  a slice, borrowed from the document. `into_items(meta.remove("x"))` (or
  of a value) owns them: a `Vec`, moved out.
- **A value as inlines or blocks**, as Lua converts it where they go
  (`pandoc.Inlines(meta.x)` or `pandoc.Span(meta.x)`; `pandoc.Blocks(...)`
  or `pandoc.Div(...)`): `inlines_of(&v)`, `blocks_of(&v)`, new `Vec`s
  (clones), or an `Err` naming the kind where Lua fails (for
  `ctx.ok(...)?`; checked with pandoc 3.14):

  | value | `inlines_of(&v)` | `blocks_of(&v)` |
  |---|---|---|
  | `MetaInlines` | its inlines | a `Plain` of each inline |
  | `MetaString` | its words | a `Plain` of its words |
  | `MetaBlocks` | fails | its blocks |
  | `MetaMap` | none | none |
  | `MetaList` | a `Str` of each item's text, unsplit, if each is a `MetaString`; else fails | a `Plain` of each item's `inlines_of` (failing where it does) |
  | `MetaBool` | fails | fails |

  Each of a value's items as one block (Lua's `pandoc.MetaBlocks{block,
  meta.x}`, as filters write `header-includes`) is
  `blocks_of(&into_items(v).into())`.
- **Writing metadata** (Lua's `meta.x = v`): `meta.insert("x".into(),
  v.into())` converts a plain value as Lua does (below, in "Types");
  reading needs no conversion: a `MetaMap`'s content is a `BTreeMap`, a
  `MetaList`'s a `Vec`, `MetaInlines`' a `Vec<Inline>`.
- **Lua's tables are references** until pandoc converts the metadata back:
  a table put in two places is two values after (scholarly-metadata merges
  one institute into another). Here a value has one owner: a second place
  takes a `.clone()`, or an index into where it is.
- **`return nil` from the `Meta` function** (or no `return`) loses in Lua
  what it changed in the metadata, in place, nested values too, as for
  any function (above; the `Pandoc` function's the same): the metadata
  counts only if returned (checked with pandoc 3.14). Here what a function
  changes stays: an original that changes the metadata, then returns
  nothing in some case (first-line-indent's, unless it sets `indent`) is
  ported by changing it only in the case that returns it.
- **A metadata value's `.t` is `nil`** since pandoc 2.17 (its values are
  plain Lua values; on `true` or `false`, Lua fails): an original testing
  `meta.x.t == "MetaList"` takes its other branch whatever the value.
  Here the test is `matches!(v, MetaValue::MetaList(_))` (pandoc 3's
  `pandoc.utils.type(v) == "List"`); a port of what the original does now
  takes the other branch too.

```rust
use panir::{Block, Inline, MetaValue, Pandoc, blocks_of, given, inlines_of, into_items, items, lookup, stringify, truthy};

let mut doc = Pandoc::new(vec![]);
doc.meta.insert("draft".into(), false.into());
doc.meta.insert("title".into(), "".into());
doc.meta.insert("toc".into(), [("depth", "2")].into_iter().collect());
doc.meta.insert("tags".into(), "a".into());
let meta = &doc.meta;
assert_eq!(
    [truthy(meta.get("draft")), truthy(meta.get("title")), truthy(meta.get("missing"))],
    [false, true, false]
);
assert_eq!(given(meta.get("draft")).map_or("final".into(), stringify), "final");
assert_eq!(lookup(meta, &["toc", "depth"]), Some(&MetaValue::from("2")));
assert_eq!(lookup(meta, &["title", "depth"]), None);
assert_eq!(lookup(&meta["toc"], &["depth"]), Some(&MetaValue::from("2")));
assert_eq!(items(meta.get("tags")), [MetaValue::from("a")]);
assert!(items(meta.get("missing")).is_empty());
assert_eq!(inlines_of(&meta["tags"]), Ok(vec![Inline::Str("a".into())]));
assert_eq!(blocks_of(&meta["tags"]), Ok(vec![Block::Plain(vec![Inline::Str("a".into())])]));
assert!(inlines_of(&meta["draft"]).is_err()); // as Lua: a boolean is not inlines
assert_eq!(panir::to_roman_numeral(2026), "MMXXVI");

// a raw block before what `tags` has, whatever its shape (as filters write `header-includes`)
let mut header = vec![MetaValue::from(panir::Block::raw("html", "<style/>"))];
header.extend(into_items(doc.meta.remove("tags")));
doc.meta.insert("tags".into(), header.into());
assert_eq!(items(doc.meta.get("tags")).len(), 2);
```

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
  "en")])`; `Target::new(url, title)`, a link's or an image's, in the
  order of Lua's `pandoc.Link(content, target, title)`.
- `Block::raw(format, text)` and `Inline::raw(format, text)`, the nodes
  filters make most, in the order of Lua's `pandoc.RawBlock` and
  `pandoc.RawInline`, take a `&str`, a `String` or a `Text`. Other nodes
  are a variant (`Block::Para(inlines)`, `Inline::Str(text)`, or a string
  `.into()`) or a struct with its fields' names (`Span { attr, content
  }.into()`).

  ```rust
  use panir::{Block, Inline, MetaValue, Pandoc};

  let mut doc = Pandoc::new(vec![Block::raw("html", "<hr/>")]);
  let id = "intro";
  doc.blocks.push(Block::Para(vec![Inline::raw("tex", format!("\\label{{{id}}}"))]));
  let css: MetaValue = Block::raw("html", "<style>p { margin: 0 }</style>").into(); // MetaBlocks
  doc.meta.insert("header-includes".into(), css);
  ```
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
- Metadata converts from plain values as in pandoc's Lua (`From`, and
  `collect`): a string is a `MetaString`, a `bool` a `MetaBool`, an inline
  or a `Vec<Inline>` `MetaInlines`, a block or a `Vec<Block>` `MetaBlocks`,
  a `Vec<MetaValue>` (or anything collected) a `MetaList`, a `Meta` (or
  pairs collected) a `MetaMap`: a map of lists of strings is one expression.

  ```rust
  use std::collections::BTreeMap;
  use panir::MetaValue;

  let by_property = BTreeMap::from([("cites", vec!["a", "b"]), ("extends", vec![])]);
  let cites: MetaValue = by_property
      .into_iter()
      .map(|(p, keys)| (p, keys.into_iter().collect::<MetaValue>()))
      .collect();
  let mut doc = panir::Pandoc::new(vec![]);
  doc.meta.insert("cito_cites".into(), cites);
  doc.meta.insert("draft".into(), true.into());
  ```
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
  walks as deep, counting the walks its functions start (`ctx.apply`, or
  `apply` on a fragment) from where they are: deeper, as a top-down
  function whose result holds what it matched makes it (each result
  walked, without end), is a `FilterError` (`function` `None`), with the
  path. A function
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
