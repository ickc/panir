// The types: what TypeScript accepts and rejects. The `@ts-expect-error`
// lines must be errors (tsc -p tsconfig.test.json fails on an unused one),
// and the rest must compile. At run time, constructors throw, and
// serialize checks what filters made.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  applyFilter, ASTTypeError, Emph, fromPlain, given, Header, lookup, MetaBlocks, MetaBool, MetaInlines, MetaList,
  MetaMap, MetaString, Pandoc as makePandoc, Para, parse, serialize, Str, toPlain, truthy, walk,
} from "../src/index.ts";
import type { Block, Filter, Inline, MetaValue, Pandoc, PlainValue, Row } from "../src/index.ts";
import type { runFilter } from "../src/node.ts";

const doc = (): Pandoc => parse(serialize({
  "pandoc-api-version": [1, 23, 1], meta: {}, blocks: [Header(1, [Str("a")]), Para([Emph([Str("b")])])],
} as Pandoc));

test("constructors take their fields' types", () => {
  // @ts-expect-error: a level is a number
  assert.throws(() => Header("1", [Str("a")]), ASTTypeError);
  // @ts-expect-error: a paragraph holds inlines
  assert.throws(() => Para([Para([])]), ASTTypeError);
  // @ts-expect-error: a Str's text is a string
  assert.throws(() => Str(1), ASTTypeError);
});

test("fields are typed", () => {
  const h = Header(1, [Str("a")]);
  // @ts-expect-error: a level is a number
  h.level = "2";
  // @ts-expect-error: no such field
  assert.equal(h.text, undefined);
  // @ts-expect-error: inlines only
  h.content.push(Para([]));
});

test("switch on t narrows", () => {
  const texts: string[] = [];
  const all = (xs: Inline[]) => xs.forEach((x) => {
    switch (x.t) {
      case "Str": texts.push(x.text); break;          // a Str has text
      case "Emph": all(x.content); break;             // an Emph has content
      // @ts-expect-error: not an inline
      case "Para": break;
    }
  });
  all([Str("a"), Emph([Str("b")])]);
  assert.deepEqual(texts, ["a", "b"]);
});

test("filter functions are typed by node", () => {
  const f: Filter = {
    Str: (s) => Str(s.text.toUpperCase()),  // s is a Str
    Header: (h) => { h.level += 1; },       // h is a Header
    // @ts-expect-error: a Str has no level
    Code: (c) => { c.level = 1; },
  };
  applyFilter(doc(), f);
  const g: Filter = {
    // @ts-expect-error: an inline's function returns inlines, not blocks
    Str: () => Para([]),
  };
  // at run time, serialize checks what filters made
  assert.throws(() => serialize(applyFilter(doc(), g)), ASTTypeError);
  // @ts-expect-error: only the three orders
  const h: Filter = { traverse: "sideways" };
  assert.throws(() => applyFilter(doc(), h), /traverse/);
  const k: Filter = {
    // @ts-expect-error: a list of inlines' function returns inlines
    Inlines: (xs) => [Para(xs)],
    Blocks: (xs: Block[]) => xs.slice(0, 1),
  };
  assert.throws(() => serialize(applyFilter(doc(), k)), ASTTypeError);
});

test("runFilter's functions have a document; a fragment's may not", () => {
  // (only type-checked: runFilter reads stdin)
  const checked = (run: typeof runFilter) => [
    run({ Str: (_s, ctx) => { const title: MetaValue | undefined = ctx.doc.meta.title; return title && Str("t"); } }),
    run({ traverse: "topdown", Para: (_p, ctx) => { ctx.skipChildren(); ctx.doc.blocks.length; } }),
    // a filter made elsewhere still runs
    run(anyOrder),
  ];
  assert.equal(typeof checked, "function");
  const anyOrder: Filter = {
    // @ts-expect-error: the filter may be applied to a fragment
    Str: (_s, ctx) => { ctx.doc.meta; },
  };
  assert.throws(() => applyFilter(Para([Str("x")]), anyOrder), TypeError);
});

test("a function's ctx.parent is a node that can hold its node, and has a t to test", () => {
  const seen: string[] = [];
  const meta = { l: MetaList([MetaString("a")]), m: MetaMap({ k: MetaString("b") }), s: MetaString("c") };
  applyFilter(makePandoc([Para([Str("x")])], { meta }), {
    MetaString: (_v, ctx) => {
      // a MetaValue's parent is a MetaList, a MetaMap or the document,
      // which has no t (a product's is undefined)
      if (ctx.parent?.t === "MetaList") seen.push(`in a list of ${ctx.parent.content.length}`);
      else if (ctx.parent?.t === "MetaMap") seen.push(`in a map of ${Object.keys(ctx.parent.content).length}`);
      else if (ctx.parent !== undefined) seen.push(`a field of ${ctx.parent.blocks.length} block`);
      // @ts-expect-error: no Str holds a metadata value
      if (ctx.parent?.t === "Str") seen.push("never");
    },
    Str: (_s, ctx) => {
      const p: ReturnType<typeof Para> | undefined = ctx.parent?.t === "Para" ? ctx.parent : undefined;
      seen.push(`in a ${p?.t}`);
    },
    Cell: (_c, ctx) => {
      const row: Row | undefined = ctx.parent;
      assert.ok(row);
    },
  });
  assert.deepEqual(seen, ["in a Para", "in a list of 1", "in a map of 1", "a field of 1 block"]);
  // a context of its own (in walk) is of any node, with a t to test
  walk(makePandoc([Para([Str("x")])]), (_x, ctx) => { if (ctx.parent?.t === "Para") seen.push("walked"); });
  assert.equal(seen.at(-1), "walked");
});

test("Lua's truth: given narrows to the value; truthy, not a type guard, doesn't", () => {
  const meta = makePandoc([], { meta: { toc: { on: "x", off: false } } }).meta;
  const need = (v: MetaValue) => v.t;
  // (lookup's result is MetaValue | undefined, as is any value perhaps missing)
  const on = given(lookup(meta, "toc", "on"));
  if (on) assert.equal(need(on), "MetaString");
  assert.equal(given(lookup(meta, "toc", "off")) ?? "d", "d");
  const v = lookup(meta, "toc", "on");
  if (truthy(v)) {
    // @ts-expect-error: still MetaValue | undefined (its false branch can't say undefined: MetaBool(false))
    need(v);
  }
  const off = lookup(meta, "toc", "off");
  if (!truthy(off)) assert.equal(off?.t, "MetaBool"); // (a value, though false)
});

test("toPlain's result is typed: by the kind given, else any plain value", () => {
  const text: string = toPlain(MetaString("a"));
  const flag: boolean = toPlain(MetaBool(true));
  const inlines: Inline[] = toPlain(MetaInlines([Str("x")]));
  const blocks: Block[] = toPlain(MetaBlocks([Para("x")]));
  const list: PlainValue[] = toPlain(MetaList(["a"]));
  const meta = makePandoc([], { meta: { toc: { depth: "2" }, draft: false } }).meta;
  const map: { [key: string]: PlainValue } = toPlain(meta);
  assert.deepEqual([text, flag, inlines, blocks, list, map.draft], ["a", true, [Str("x")], [Para("x")], ["a"], false]);
  const v: MetaValue = meta.toc!;
  const plain = toPlain(v); // any of them: narrowed by a test, with no cast
  if (typeof plain === "object" && !Array.isArray(plain)) assert.deepEqual(plain.depth, "2");
  // @ts-expect-error: not a string, unless narrowed
  const wrong: string = toPlain(v);
  assert.ok(wrong);
  // and back: what toPlain gives is what fromPlain and the constructors take
  assert.deepEqual(fromPlain(toPlain(v)), v);
  assert.deepEqual(MetaMap({ again: toPlain(v) }).content.again, v);
});
