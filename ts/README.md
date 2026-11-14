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
worker. Field names are pandoc-types', in camelCase, as in pandoc's Lua
API; products have no `t` (`Attr`, `Cell`, `Citation`, ...); enum-like
types are string unions (`"InlineMath"`).

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
splice in its place (`[]` deletes it). A function under a capitalised name
no node has (`Strr`) is an error. A function is called as a method of its
filter (`this`).

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

As in Lua, what a function returns are values: `[el, el]` puts two nodes
in the document, the second a copy. Elsewhere a node belongs in one place:
to put it in a second, copy it (`copy(el)`), or whatever changes one later
changes both.

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
nodes of the list were deleted or spliced, and a list reached through a
context is as it is now. (A list being walked holds its node and every one
after it, however it is reached; reached otherwise, as through the
document a function closed over, the nodes deleted or spliced in it so far
may show only once its walk is done, but a change made to it that way is
made to it as it is.) Its types follow the function's: `ctx.next` of an
inline is an `Inline`.

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
  function calls `ctx.skipChildren()` (Lua's `return el, false`). The order
  in which elements start, as a reader meets them: for counters and
  nesting.
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
