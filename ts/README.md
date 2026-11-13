# panir (TypeScript)

pandoc's document AST for TypeScript and JavaScript, in the browser or in
Node.js, generated from pandoc-types: types, pandoc's JSON, checks with
useful errors, and filters in the style of pandoc's Lua filters.

```ts
import { applyFilter, Header, parse, serialize, Str } from "panir";

const doc = parse(json); // pandoc's JSON, checked
applyFilter(doc, {
  Header: (h) => { h.level += 1; },
  Str: (s) => (s.text === "TODO" ? Str("DONE") : undefined),
});
serialize(doc);
```

As a pandoc filter (`pandoc --filter upper.js`; pandoc runs `.js` filters
with `node`, so the filter's `package.json` should say `"type": "module"`):

```js
#!/usr/bin/env node
import { Str } from "panir";
import { runFilter } from "panir/node";

await runFilter({ Str: (s) => Str(s.text.toUpperCase()) });
```

## Nodes

Plain objects with named fields, discriminated by `t`, so `switch (x.t)`
narrows them, and `structuredClone` or `postMessage` carries them to a
worker:

```ts
{ t: "Header", level: 1, attr: { identifier: "hi", classes: [], attributes: [] },
  content: [{ t: "Str", text: "Hi" }] }
```

Field names are pandoc-types', in camelCase, as in pandoc's Lua API.
Products have no `t` (`Attr`, `Cell`, `Citation`, ...); enum-like types are
string unions (`"InlineMath"`).

Constructors take the required fields, then the content, then options:
`Header(1, [Str("Hi")], { identifier: "hi" })`, `Link([Str("x")], { url })`,
`Cell([Para([Str("a")])], { colSpan: 2 })`. They fill in defaults, and check
their arguments as far as the nodes in them (each node's `t`: what is in a
node was checked when it was made, and `serialize` checks everything):

```
ASTTypeError: Para.content[0]: expected Inline, got Para (a Block)
```

A constructor copies the arrays it is given: a node has its own, which the
caller may go on changing or give to another node.

Constructors take strings as pandoc's Lua does: where a list of inlines
goes, a string is its words and spaces (`Para("hello world")`); in such a
list, a `Str` (`Para(["a", Emph("b")])`); where blocks go, `Plain` text.
`inlines("...")` and `blocks("...")` build such lists.

`parse`/`fromJSON` report where JSON is wrong
(`blocks[0].content[1].text: expected a string, got 42`), and
`serialize`/`toJSON` check a document on the way out: a hole in an array,
half of a surrogate pair (`"😀".slice(0, 1)`) and anything else pandoc could
not read are errors there, with the path.

Two things are JavaScript's:

- An integer beyond 2^53 (a list that starts at 9007199254740993) is refused
  when read: `JSON.parse` has rounded it already.
- The metadata, and a `MetaMap`'s content, are objects without a prototype,
  so that any key is only a key: `doc.meta.constructor` is `undefined`
  unless the document has that key, and `__proto__` is one like another.
  (`structuredClone` gives them a prototype back; they still serialize.)

## Filters

Filters are written and run as pandoc's Lua filters, and tested against
them: each scenario in [`corpus/filters/`](../corpus/filters/) is a Lua
filter, and its TypeScript version must make of a document exactly what
pandoc made.

A filter is an object of functions by constructor (`Str`, `Header`), type
(`Inline`, `Block`, `MetaValue`) or product (`Cell`, `Pandoc`); a node gets
its constructor's, or else its type's. A function returns nothing to keep
the node (changed in place or not), a node to replace it, or an array to
splice in its place (`[]` deletes it). As in Lua, what it returns are
values: `[el, el]` puts two nodes in the document, the second a copy;
elsewhere, to put a node in a second place, copy it (`structuredClone`).
A function is called as a method of its filter (`this`). `Inlines` and `Blocks` get every
list of them, and `Meta` the metadata; each returns a replacement, or
nothing. What a list function returns, unless the array itself, becomes a
new array (its nodes may hold the old one: `[{ t: "Emph", content: xs }]`).
A function under a capitalised name no node has (`Strr`) is an error. The
second argument is the `Context`, made only for a function declared with
more than one parameter: `parent`, the `container` and `index` there,
`next`/`prev` (right after earlier nodes of the list were deleted or
spliced), `path`, `doc`, `format`, and `conversion`: the pandoc run
the filter is part of (`format`, and when known `inputFormat`,
`outputFormat`, `readerOptions`, `options`), as panir's Python
`Conversion`. `runFilter` reads it from pandoc's arguments and environment
(`$PANDOC_READER_OPTIONS`, and `$PANDOC_INPUT_FORMAT`/`$PANDOC_OUTPUT_FORMAT`
where set, as libpandoc does); `applyFilter(doc, filter, conversion)` takes
one.

`traverse` sets the order, one of the three filter frameworks use:

- `"typewise"` (the default, as pandoc's Lua filters and Haskell's `walk`):
  one walk per kind, each bottom-up: every `Inline`, then every list of
  inlines, then every `Block`, then every list of blocks; then the other
  nodes (`MetaValue`, `Cell`...), then `Meta`, then `Pandoc`. So every
  inline is done before any block's function runs.
- `"topdown"` (Lua's other order, and pandocfilters'): `Pandoc`, `Meta`,
  then from the root down, a list before its elements and a node before its
  children, which are walked in the node's replacement too, unless the
  function calls `ctx.skipChildren()` (Lua's `return el, false`). The order
  in which elements start, as a reader meets them: for counters and
  nesting.
- `"bottomup"` (panflute's): one walk, each node after its children, a list
  after its elements, `Meta` after the metadata, `Pandoc` last. The fastest.

Bottom-up and typewise, a function sees what is below it already filtered,
and what it returns isn't walked again. Only top-down functions get
`skipChildren`; elsewhere it is a type error, as the children came first.
Filters whose functions don't depend on each other's calls make the same
document typewise and bottom-up. A list reached through a context is as it
is now. A list being walked holds its node and every one after it, however
it is reached; reached otherwise, as through the document a function
closed over, the nodes deleted or spliced in it so far may show only once
its walk is done, but a change made to it that way is made to it as it is.

An array of filters runs them one after the other, as a Lua filter file
returning a list: `applyFilter(doc, [first, second])`.

Also: `walk`, `stringify`, `inlines`/`blocks`, `toPlain`/`fromPlain` for metadata.

Status: a prototype.
