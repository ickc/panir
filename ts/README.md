# panir (TypeScript)

pandoc's document AST for TypeScript and JavaScript, in the browser or in
Node.js (16.11 or later), generated from pandoc-types: types, pandoc's
JSON, checks with useful errors, and filters that run as pandoc's Lua
filters do.

```ts
import { applyFilter, parse, serialize } from "panir";

const doc = parse(json); // pandoc's JSON, checked
applyFilter(doc, {
  Header: (h) => { h.level += 1; },
  Str: (s) => (s.text === "TODO" ? "DONE" : undefined),
});
serialize(doc);
```

As a pandoc filter (`pandoc --filter upper.js`; pandoc runs `.js` filters
with `node`, so the filter's `package.json` should say `"type": "module"`):

```js
#!/usr/bin/env node
// A pandoc filter: upper-case all text outside code.
//
//     pandoc --filter ./upper.js input.md

import { Str } from "panir";
import { runFilter } from "panir/node";

await runFilter({
  Str: (s) => Str(s.text.toUpperCase()),
});
```

## The AST

Plain objects with named fields, discriminated by `t`, so `switch (x.t)`
narrows them, and `structuredClone` or `postMessage` carries them to a
worker. Field names are pandoc-types', in camelCase (most are pandoc's Lua
API's: "Coming from Lua" lists those that are not); products have no `t`
(`Attr`, `Cell`, `Citation`, ...); enum-like types are string unions
(`"InlineMath"`).

```ts
const h = Header(1, "Intro", { identifier: "intro", attributes: { lang: "en" } });
assert.deepEqual(h, {
  t: "Header", level: 1,
  attr: { identifier: "intro", classes: [], attributes: [["lang", "en"]] },
  content: [{ t: "Str", text: "Intro" }],
});
```

Constructors take their arguments in the order of pandoc's Lua
constructors: the required fields, then the content, then options, by
name (attributes as `identifier`, `classes`, `attributes`, or `attr`
whole; a link's target as `url` and `title`, or `target`):
`Link("pandoc", { url: "https://pandoc.org" })`, `Cell("a", { colSpan: 2
})`. Where Lua takes every argument in place, so do they:
`Cite(content, citations)`, `Citation(id, mode, prefix, suffix)`,
`Table(caption, colSpecs, head, bodies, foot)`, `TableBody(body, head,
rowHeadColumns)`.

Attributes are `[key, value]` pairs, in order, a key perhaps in two.
By key, as Lua's `el.attributes[key]`: `getAttribute(attr, key)` is the
value of the first pair with the key (or `undefined`), `setAttribute`
gives that pair the value, in its place, or adds a pair at the end,
`removeAttribute` removes that pair and returns its value, and
`hasAttribute` asks. (Classes are an array: `attr.classes.includes("c")`.)

```ts
import { removeAttribute, setAttribute } from "panir";

const cell = Div("x", { attributes: { colspan: "2", lang: "en" } });
assert.equal(removeAttribute(cell.attr, "colspan"), "2");
setAttribute(cell.attr, "lang", "fr");
assert.deepEqual(cell.attr.attributes, [["lang", "fr"]]);
```

`Math`, `Image` and `Attr` are pandoc's names, and also JavaScript's
(`Math.max`, `new Image()`): imported by name they hide the global ones
in that module. Import the module whole, or rename them:

```ts
import * as pandoc from "panir";
import { Image as ImageNode } from "panir";

const formula = pandoc.Math("InlineMath", "e^{i\\pi}");
const figure = ImageNode("a cat", { url: "cat.png" });
```

What converts, converts as in pandoc's Lua:

- where inlines go, a string is its words and spaces (`Para("hello
  world")`), and one inline a list of it; in a list, a string is a `Str`;
- where blocks go, a string is `Plain` text, and one block (or inline) a
  list of it; in a list, a string is `Plain` text, and an inline, or a list
  of inlines and strings (a `Str` each), a `Plain` of them
  (`Div(Str("x"))` is `Div([Plain([Str("x")])])`);
- where metadata goes (`Pandoc(blocks, { meta })`, `MetaMap`, `MetaList`),
  a string is a `MetaString`, a boolean a `MetaBool`, a number a
  `MetaString` of it, an inline or a list of inlines `MetaInlines`, a
  block or a list of blocks `MetaBlocks`, another list a `MetaList`, an
  object a `MetaMap` (`fromPlain` converts the same way).

So, as in Lua, a lone string is split into words, and a string among
others is one `Str`: `Para("a b")` is three inlines, `Para(["a b", "c"])`
two. `inlines("...")` and `blocks("...")` build such lists. To parse
markup, use libpandoc.

A constructor copies the arrays it is given: a node has its own, which the
caller may go on changing or give to another node; read-only arrays are
taken too.

## Checked where you make the mistake

Constructors check their arguments as far as the nodes in them (each
node's `t`: what is in a node was checked when it was made, and
`serialize` checks everything), and say where:

```ts
assert.throws(() => Para([Str("a"), Div("x") as never]), {
  name: "ASTTypeError",
  message: "Para.content[1]: expected Inline, got Div (a Block)",
});
```

Reading JSON says where it is wrong, and so does writing it: a hole in an
array, half of a surrogate pair (`"😀".slice(0, 1)`, on Node.js 20 or
later) and anything else pandoc could not read are errors there, with the
same path:

```ts
assert.throws(() => parse('{"pandoc-api-version":[1,23,1,2],"meta":{},"blocks":[{"t":"Para","c":[{"t":"Str","c":42}]}]}'), {
  name: "ASTDecodeError",
  message: "blocks[0].content[0].text: expected a string, got 42",
});
const broken = Pandoc(Para("a"));
(broken.blocks[0] as Para).content.push(Para("b") as never);
assert.throws(() => serialize(broken), {
  name: "ASTTypeError",
  message: "blocks[0].content[1]: expected Inline, got Para (a Block)",
});
```

Each is an `ASTError`, with `where` and `path` (`["blocks", 0, "content",
1]`), `expected` and `got`: `ASTDecodeError` for JSON, `ASTTypeError` (a
`TypeError` too) for a wrong value wherever it is found (an argument, an
option, what a filter function returns, what `serialize` and `check`
find), and `ASTDepthError` (a `RangeError` too) for a document deeper than
the call stack lets panir go.

The types say the same: TypeScript reports `Para([Div("x")])`,
`h.level = "2"`, a link without a url, `attr` given with `identifier`, a
function for `Str` returning a block, one for `Attr` returning a list;
and it accepts `Para("hello world")`, `Pandoc([], { meta: { draft: true }
})` and what a filter function may return. (The examples here are checked
with `tsc` and run by the tests.)

Two things are JavaScript's:

- An integer beyond 2^53 (a list that starts at 9007199254740993) is refused
  when read: `JSON.parse` has rounded it already.
- The metadata, and a `MetaMap`'s content, are objects without a prototype,
  so that any key is only a key: `doc.meta.constructor` is `undefined`
  unless the document has that key, and `__proto__` is one like another.
  (`structuredClone` gives them a prototype back; they still serialize.
  `copy` copies a node or a document as it is.)

## Filters

A filter is an object of functions by constructor (`Str`, `Header`), type
(`Inline`, `Block`, `MetaValue`) or product (`Cell`, `Pandoc`); a node gets
its constructor's, or else its type's. A function returns nothing to keep
the node (changed in place or not), a node to replace it, or an array to
splice in its place (`[]` deletes it); a metadata value in a map (a field
of `Meta`, or of a `MetaMap`) is replaced by one value (`[]` there is an
error: a key is deleted by the function of `Meta` or of the `MetaMap`). A
function under a capitalised name no node has (`Strr`) is an error. A
function is called as a method of its filter (`this`).

What a function returns converts as pandoc's Lua converts it: for an
inline, a string is its words (`return "two words"`), and a list's
strings a `Str` each; for a block, a string is `Plain` text, an inline a
`Plain` of it, and a list's inlines, or lists of inlines, a `Plain` each.
Anything else is an `ASTTypeError` that says where the value was to go,
and which function returned it, for which node:

```ts
const wrong: Filter = { Str: () => 42 as never };
assert.throws(() => applyFilter(Pandoc(Para("a")), wrong), {
  name: "ASTTypeError",
  message: "blocks[0].content[0]: expected Inline, a list of them, or nothing, got 42\n" +
           "  returned by filter function Str, for the Str at blocks[0].content[0]",
});
```

An error thrown in a function gets the same note (`in filter function
...`).

As in Lua, what one call returns are values: `[el, el]` puts two nodes in
the document, the second a copy. Not across calls: a node returned by two
calls is in two places (Lua's `local br = pandoc.RawBlock(...)`, returned
for every marker, is a copy each time; here it is one node). Everywhere
but within one return a node belongs in one place: to put it in a second,
copy it (`copy(br)`), or whatever changes one later changes both. That is
the filter's author's to get right, as anything a program does; for tests
and debugging, `checkUnique(doc)` throws an error naming a node it finds
in two places, and both (by identity: equal nodes are fine; an array in
two places by what is in it).

```ts
import { checkUnique, copy, HorizontalRule, RawBlock } from "panir";

const br = RawBlock("html", "<hr/>"); // made once
const breaks: Filter = { HorizontalRule: () => copy(br) }; // a copy for each rule
checkUnique(applyFilter(Pandoc([HorizontalRule(), HorizontalRule()]), breaks)); // (without the copy, it throws)
```

As in Lua, `Inlines` and `Blocks` get every list of them, and `Meta` the
metadata; each returns a replacement, or nothing (converted as Lua does: a
string, or one node, is a list of it; plain values are metadata). What a
list function returns, unless the array itself, becomes a new array in the
old one's place (its nodes may hold the old one:
`[{ t: "Emph", content: xs }]`).

The second argument is the `Context`, made only for a function declared
with more than one parameter: `parent`, the `container` the node is in and
its `index` there, `next`/`prev`, `path` and `where`, the `doc`,
`ancestors`, `format`, and `conversion`. They are right after earlier
nodes of the list were deleted or spliced: a node replaced by none or
several is spliced in place, so a list is as it is now however it is
reached. Its types follow the function's: `ctx.next` of an inline is an
`Inline`; `ctx.parent` of a metadata value is a `MetaList`, a `MetaMap`
or the `Pandoc`, the nodes that can hold one, each with a `t` to test
(`ctx.parent?.t === "MetaList"`; a product's, as the `Pandoc`'s, is
`undefined`); `ctx.doc` is a `Pandoc` in a filter written in
`runFilter`'s call (which runs on a document), elsewhere perhaps
`undefined` (a fragment has none).

```ts
const shout: Filter = {
  Str: (s, ctx) => {
    if (ctx.format === "html" && ctx.next?.t !== "Space") return Strong(s.text.toUpperCase());
  },
};
```

`ctx.conversion` is the pandoc run the filter is part of (`format`, and
when known `inputFormat`, `outputFormat`, `readerOptions`, `options`), as
panir's Python `Conversion`. `runFilter` reads it from pandoc's arguments
and environment (`$PANDOC_READER_OPTIONS`, and
`$PANDOC_INPUT_FORMAT`/`$PANDOC_OUTPUT_FORMAT` where set, as libpandoc
does); `applyFilter(doc, filter, conversion)` takes one, or a format's
name.

`traverse` sets the order, one of the three filter frameworks use:

- `"typewise"` (the default, as pandoc's Lua filters and Haskell's `walk`):
  one walk per kind, each bottom-up: every inline, then every list of
  inlines, then every block, then every list of blocks, then the other
  nodes (`MetaValue`, `Cell`...), then `Meta`, then `Pandoc`. So every
  inline is done before any block's function runs.
- `"topdown"` (Lua's other order, and pandocfilters'): `Pandoc`, `Meta`,
  then from the root down, a list before its elements and a node before
  its children, which are walked in the node's replacement too, unless the
  function calls `ctx.skipChildren()` (Lua's `return el, false`). The
  order in which elements start, as a reader meets them: for counters and
  nesting. What a function returns is not given to a function again, nor,
  if a list, are its elements; their children are walked (as in pandoc's
  Lua and pandocfilters: an emphasis replaced by its content keeps an
  emphasis that was directly in it). What a list function returns is the
  list whose elements are walked.
- `"bottomup"` (panflute's): one walk, each node after its children, a list
  after its elements, `Meta` after the metadata, `Pandoc` last. The
  fastest.

Bottom-up and typewise, a function sees what is below it already filtered,
and what it returns isn't walked again. Only top-down functions get
`skipChildren`: a `Filter<"topdown">` (or one written in place with
`traverse: "topdown"`); otherwise it is a type error, and an error when
called, as the children came first. A `Filter` alone is one in any order,
as when `traverse` is chosen at run time. Filters whose functions don't
depend on each other's calls make the same document typewise and
bottom-up.

```ts
const skipNotes: Filter<"topdown"> = {
  traverse: "topdown",
  Note: (_note, ctx) => { ctx.skipChildren(); },
};
const order = Math.random() < 0.5 ? "typewise" : "bottomup";
applyFilter(doc, { traverse: order, Code: (c) => Str(c.text) });
```

They are checked against pandoc's Lua filters: each scenario in
[`corpus/filters/`](https://github.com/ickc/panir/tree/main/corpus/filters)
is a Lua filter, and its TypeScript version must make exactly the same
document. An array of filters runs them one after the other, as a Lua
filter file returning a list: `applyFilter(doc, [first, second])`.

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
  the body. Here a node in a second place is a copy you make (`copy(x)`),
  or one node in two places.
- `el.t`, `el.tag`: `el.t`.
- **Field names** are pandoc's Lua API's (`content`, `text`, `attr`,
  `level`, `citations`, `listAttributes`, ...), but for pandoc-types'
  names here, in camelCase: `quoteType` (Lua's `quotetype`), `mathType`
  (`mathtype`), a table's `colSpecs` (`colspecs`, pairs in Lua, `ColSpec`s
  of `alignment` and `width` here), `rowHeadColumns` (`row_head_columns`),
  a cell's `rowSpan` and `colSpan` (`row_span`, `col_span`), a citation's
  `noteNum` (`note_num`), a caption's `content` (`long`), a cell's
  `content` (`contents`), an image's `content` (`caption`); and a link's
  or image's `target` is `{ url, title }` (in Lua `target`, an image's
  `src`, is the URL, and `title` the element's). An element's attributes,
  target and list attributes are fields of their own here: Lua's
  `el.identifier` is `el.attr.identifier`, `link.title`
  `link.target.title`, `ol.start` `ol.listAttributes.start`.
- `el.attributes.k`, `= v`, `= nil`: `getAttribute(el.attr, "k")`,
  `setAttribute(el.attr, "k", v)`, `removeAttribute(el.attr, "k")`; the
  first pair with the key, as in Lua. A Lua table as attributes (`{id =
  "x", class = "a b"}`) gives the identifier and classes; here
  `attributes: { class: ... }` is a pair named `class`: pass `identifier`
  and `classes`.
- `error(...)`: throw; the error gets a note naming the function and the
  place.
- `return el, false` (top-down): `ctx.skipChildren()`.
- In pandoc's Lua (top-down), `return el, false` from the `Pandoc` or
  `Meta` function skips nothing: the metadata and the blocks are walked
  all the same (checked with pandoc 3.14). Here `ctx.skipChildren()` there
  skips them, as for any node.
- A Lua file returning a list of filters (`return {configure, quotes}`):
  `runFilter([configure, quotes])` (or `applyFilter(doc, [...])`), each a
  whole walk, in turn.
- Arrays count from 0, Lua's lists from 1 (`el.content[1]`).
- The metadata a Lua filter keeps from its `Meta` function, for the
  functions after: `ctx.doc.meta`.
- `FORMAT`: `ctx.format`, the output format pandoc passed; `undefined`
  where none is known (`applyFilter` without one; `runFilter` run by hand,
  without pandoc's argument), where Lua's `FORMAT` is always a string:
  `ctx.format ?? ""`.
- Lua's string patterns (`s:match`, `gsub`, `find`) are not regular
  expressions: `%` escapes (`%.`), `-` is a lazy `*`, `%a` is a letter
  (ASCII), `%s` ASCII whitespace, and there is no `|`; a port translates
  each, or escapes a literal string (`s.replace(/[\\^$.*+?()[\]{}|/]/g,
  "\\$&")`).
- In `gsub`'s replacement too, `%` is special: `%1` is a capture, `%%` a
  percent sign, and any other `%` an error (checked with pandoc 3.14), so
  a replacement taken from the document (a size of `5%`) fails there.
  JavaScript's `replace` has escapes of its own in a replacement string
  (`$&`, `$1`, `$$`): `s.replace(re, () => text)` puts `text` in as it is.
- Lua's strings are bytes: `#s`, `s:len()`, `s:sub(i, j)` and `s:byte(i)`
  count bytes of UTF-8 (`#"é"` is 2; `s:sub(1, 1)` of it is half a
  character, which pandoc reads back as U+FFFD), and `upper` and `lower`
  change ASCII letters only; `utf8.len(s)` counts characters. A string's
  `length` and `slice` count UTF-16 code units (`"😀".length` is 2), `[...s]`
  is its characters; where an original depends on bytes,
  `new TextEncoder().encode(s)` has them (`.length`, `.subarray(i - 1, j)`,
  and `new TextDecoder().decode(...)`, which makes U+FFFD of half a
  character, as pandoc does).
- `PANDOC_READER_OPTIONS`: `ctx.conversion.readerOptions`, keyed as
  pandoc's JSON (`"track-changes"`, `"tab-stop"`, where Lua's fields are
  `track_changes`, `tab_stop`, and pandoc 2's `trackChanges` is gone),
  with the same values (`"all-changes"`); `undefined` where pandoc passed
  none.
- pandoc's Lua has no function for a metadata value (its values are
  plain Lua values, reached through the `Meta` function or `doc.meta`);
  here, as in panflute, `MetaValue` (or `MetaList`, ...) is called on each
  one.

#### Metadata

pandoc's Lua gives a filter the metadata as plain Lua values; here each
value is a node, a `MetaValue`, holding what Lua gives in a field
(checked with pandoc 3.14):

| in the document | in Lua (`pandoc.utils.type`) | here |
|---|---|---|
| `x: some text`; also `x: ""`, `x: 1`, `x: no` | `Inlines` | `MetaInlines`: `content`, `Inline[]` |
| `x: \|` and indented lines (blocks) | `Blocks` | `MetaBlocks`: `content`, `Block[]` |
| `-M x=text` on the command line; YAML's null, `x:` | `string` | `MetaString`: `text` |
| `x: true`, `x: false`; `-M x=true` | `boolean` | `MetaBool`: `value` |
| `x: [a, b]` | `List` | `MetaList`: `content`, `MetaValue[]` |
| `x: {k: v}` | `table` | `MetaMap`: `content`, an object of values |
| no field | `nil` | `undefined` (`meta.x`) |

`pandoc.utils.type(v) == "Inlines"` is `v.t === "MetaInlines"`, which
narrows `v` (a `switch (v.t)` too).

A field may be missing, and the type says so: `meta.x` (as a `MetaMap`'s
`content.x`) is a `MetaValue | undefined`, to test (`meta.x?.t`), or read
with `given`, `lookup` or `items`, which take one. A field is removed with
`delete meta.x`; a value `undefined` is an error where the document is
checked (`serialize`, constructors).

- **Lua's truth**: `if meta.x then` (and `meta.x or d`) is false for a
  missing field and for `false` only; an empty string, list or map is
  true, as is the text `no`. A node is an object, always true in
  JavaScript, `MetaBool(false)` too: `if (meta.x)` is true for `x: false`.
  `truthy(meta.x)` is Lua's. `given(v)` is the value if `truthy`, else
  `undefined`: Lua's `meta.x or d` is `given(meta.x) ?? d`, and `const v =
  given(lookup(meta, "a", "b")); if (v) ...` narrows `v` to a `MetaValue`
  (`truthy`, which says only true or false, does not: a type guard's false
  branch would say `undefined`, and `MetaBool(false)` is false too).
- **A nested option**, Lua's `((meta.a or {}).b or {}).c`: `lookup(meta,
  "a", "b", "c")`, `undefined` where a key is missing or a value on the
  way is not a map (where Lua would fail, on `true`, or find a method, on
  a string or a list). It starts from the metadata or a `MetaMap`, and
  reads a map's own keys only.
- **One value or a list of them** (a filter's `ensure_list`, the shapes
  `header-includes` takes): `items(meta.x)`, a `MetaList`'s items, nothing
  for a missing value, any other value alone; a new array, of the
  document's values.
- **A value as inlines or blocks**, as Lua converts it where they go
  (`pandoc.Inlines(meta.x)` or `pandoc.Span(meta.x)`; `pandoc.Blocks(...)`
  or `pandoc.Div(...)`): `inlinesOf(v)`, `blocksOf(v)`, new arrays
  (copies), or an `ASTTypeError` where Lua fails (checked with pandoc
  3.14):

  | value | `inlinesOf(v)` | `blocksOf(v)` |
  |---|---|---|
  | `MetaInlines` | its inlines | a `Plain` of each inline |
  | `MetaString` | its words | a `Plain` of its words |
  | `MetaBlocks` | fails | its blocks |
  | `MetaMap` | none | none |
  | `MetaList` | a `Str` of each item's text, unsplit, if each is a `MetaString`; else fails | a `Plain` of each item's `inlinesOf` (failing where it does) |
  | `MetaBool` | fails | fails |

  Each of a value's items as one block (Lua's `pandoc.MetaBlocks{block,
  meta.x}`, as filters write `header-includes`) is
  `blocksOf(MetaList(items(v)))`.
- **Writing metadata** (Lua's `meta.x = v`): the metadata is a plain
  object, whose fields don't convert what is put there: `fromPlain(v)`
  converts a plain value as Lua does (a string is a `MetaString`, a
  boolean a `MetaBool`, a number a `MetaString` of it, an inline or a list
  of inlines `MetaInlines`, a block or a list of blocks `MetaBlocks`,
  another list a `MetaList`, an object a `MetaMap`), as the constructors
  do. `toPlain(v)` reads metadata as Lua gives it: strings, booleans,
  arrays, objects, and inlines and blocks as arrays of them (copies, as
  Lua's are values), typed as a `PlainValue` (of the kind given: an object
  of them for the metadata or a `MetaMap`; otherwise narrowed by `typeof`
  and `Array.isArray`), which `fromPlain` takes; `stringify(v)` its text.
- **Lua's tables are references** until pandoc converts the metadata back:
  a table put in two places is two values after (scholarly-metadata merges
  one institute into another). Here a node in two places is one node: copy
  it (`copy`), as any node.
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
  Here the test is `v.t === "MetaList"` (pandoc 3's
  `pandoc.utils.type(v) == "List"`); a port of what the original does now
  takes the other branch too.

```ts
import {
  blocksOf, Conversion, fromPlain, given, inlinesOf, items, lookup, Plain, stringify, toPlain, truthy,
} from "panir";

const meta = Pandoc([], { meta: { draft: false, title: "", toc: { depth: "2" }, tags: "a" } }).meta;
assert.deepEqual([truthy(meta.draft), truthy(meta.title), truthy(meta.missing)], [false, true, false]);
assert.deepEqual(given(meta.draft) ?? MetaString("final"), MetaString("final"));
const depth = given(lookup(meta, "toc", "depth"));
assert.equal(depth ? stringify(depth) : "3", "2");
assert.deepEqual([lookup(meta, "toc", "depth"), lookup(meta, "title", "depth")], [MetaString("2"), undefined]);
assert.deepEqual([items(meta.tags), items(meta.missing)], [[MetaString("a")], []]);
meta.name = fromPlain([Str("Ada"), Space(), Str("L.")]);
assert.deepEqual(meta.name, MetaInlines([Str("Ada"), Space(), Str("L.")]));
assert.deepEqual(toPlain(meta.toc!), { depth: "2" });
assert.equal(toPlain(meta).tags, "a");
assert.deepEqual(inlinesOf(meta.tags!), [Str("a")]);
assert.deepEqual(blocksOf(meta.name), [Plain([Str("Ada")]), Plain([Space()]), Plain([Str("L.")])]);
const options = new Conversion({ readerOptions: { "track-changes": "all-changes" } }).readerOptions;
assert.equal(options?.["track-changes"], "all-changes");
assert.equal("width: SIZE".replace("SIZE", () => "$&5%"), "width: $&5%");
const bytes = new TextEncoder().encode("«»");
assert.deepEqual(["«»".length, bytes.length, new TextDecoder().decode(bytes.subarray(0, 1))], [2, 4, "\ufffd"]);
```

### Fragments

A filter also applies to any node, or a list of inlines or of blocks, as
Lua's `el:walk{...}` does: `applyFilter(el, filter)`. As in Lua, the
functions apply to what is in a node but not to the node itself, a list's
function also to the list itself, and `Meta` and `Pandoc` only to a
document. Unlike Lua, the fragment is changed in place, and returned:

```ts
const loud: Filter = { Str: (s) => s.text.toUpperCase() };
assert.deepEqual(applyFilter(Emph(["a", Str("b")]), loud), Emph([Str("A"), Str("B")]));
```

A context's `path` is then from the fragment (`ctx.where` names it, as
`Emph.content[0]`, or `inlines[0]` in a list), and its `doc` is
`undefined`. An empty list is neither: no function is called.

`walk(target, action)` is the walk underneath: `action(node, ctx, type)`
on every node, the target itself included (or each in a list), `type`
being its type's name in the schema (`"Inline"`, `"Cell"`); it returns
what a filter function may.

## Deep documents

Reading, writing, walking, copying, comparing and `stringify` recurse, a
few calls for each level of the document, and work on documents nested
at least 256 levels deep (blocks and inlines in each other; the Rust
crate's `MAX_DEPTH`) at Node.js's default stack: 300 nested divs is a 9
kB web page. How much deeper depends on the engine; measured, at the
least over Node.js 16.11 to 24 (nested divs / tables in tables' cells),
about:

| | levels |
|---|---|
| `parse`, `fromJSON` | 1,800 / 650 |
| `serialize` (where `JSON.stringify` stops) | 1,000 / 300 |
| `toJSON`, `check`, filters, `walk`, `copy`, `equal`, `stringify` | 1,600 / 400 |

Beyond, reading is an `ASTDecodeError`, and anything else an
`ASTDepthError` (an `ASTError` and a `RangeError`): never the engine's bare
`RangeError`. (Node.js's own `structuredClone` stops at about 1,150 / 290,
and `isDeepStrictEqual` at 750 / 210; `equal` compares as panir sees the
AST, whatever the maps' prototypes.)

## Also

`fromJSON`/`toJSON` for any type of the schema (`fromJSON(j, "Inline")`),
`check`, `stringify` (as pandoc's `pandoc.utils.stringify`, metadata and
all), `toPlain`/`fromPlain` for metadata, `copy`, `equal`.

Status: a prototype.
