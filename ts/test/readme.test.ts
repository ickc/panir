// The README's examples: each ```ts block of README.md is a region of this
// file (between `#region README` and `#endregion`), in order, so that tsc
// checks it and the tests run it; its ```js block is examples/upper.js.
// (In the README, "panir" is "../src/index.ts" here.)

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { CodeBlock, Div, Emph, Header, MetaInlines, MetaString, Pandoc, Para, Space, Str, Strong, toJSON } from "../src/index.ts";
import type { Filter } from "../src/index.ts";

const json = readFileSync(new URL("../../corpus/pandoc.jsonl", import.meta.url), "utf8").split("\n")[0]!;

// #region README
import { applyFilter, parse, serialize } from "../src/index.ts";

const doc = parse(json); // pandoc's JSON, checked
applyFilter(doc, {
  Header: (h) => { h.level += 1; },
  Str: (s) => (s.text === "TODO" ? "DONE" : undefined),
});
serialize(doc);
// #endregion

// #region README
const h = Header(1, "Intro", { identifier: "intro", attributes: { lang: "en" } });
assert.deepEqual(h, {
  t: "Header", level: 1,
  attr: { identifier: "intro", classes: [], attributes: [["lang", "en"]] },
  content: [{ t: "Str", text: "Intro" }],
});
// #endregion

// #region README
import { removeAttribute, setAttribute } from "../src/index.ts";

const cell = Div("x", { attributes: { colspan: "2", lang: "en" } });
assert.equal(removeAttribute(cell.attr, "colspan"), "2");
setAttribute(cell.attr, "lang", "fr");
assert.deepEqual(cell.attr.attributes, [["lang", "fr"]]);
// #endregion

// #region README
import * as pandoc from "../src/index.ts";
import { Image as ImageNode } from "../src/index.ts";

const formula = pandoc.Math("InlineMath", "e^{i\\pi}");
const figure = ImageNode("a cat", { url: "cat.png" });
// #endregion

// #region README
assert.throws(() => Para([Str("a"), Div("x") as never]), {
  name: "ASTTypeError",
  message: "Para.content[1]: expected Inline, got Div (a Block)",
});
// #endregion

// #region README
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
// #endregion

// #region README
const wrong: Filter = { Str: () => 42 as never };
assert.throws(() => applyFilter(Pandoc(Para("a")), wrong), {
  name: "ASTTypeError",
  message: "blocks[0].content[0]: expected Inline, a list of them, or nothing, got 42\n" +
           "  returned by filter function Str, for the Str at blocks[0].content[0]",
});
// #endregion

// #region README
import { checkUnique, copy, HorizontalRule, RawBlock } from "../src/index.ts";

const br = RawBlock("html", "<hr/>"); // made once
const breaks: Filter = { HorizontalRule: () => copy(br) }; // a copy for each rule
checkUnique(applyFilter(Pandoc([HorizontalRule(), HorizontalRule()]), breaks)); // (without the copy, it throws)
// #endregion

// #region README
const shout: Filter = {
  Str: (s, ctx) => {
    if (ctx.format === "html" && ctx.next?.t !== "Space") return Strong(s.text.toUpperCase());
  },
};
// #endregion

// #region README
const skipNotes: Filter<"topdown"> = {
  traverse: "topdown",
  Note: (_note, ctx) => { ctx.skipChildren(); },
};
const order = Math.random() < 0.5 ? "typewise" : "bottomup";
applyFilter(doc, { traverse: order, Code: (c) => Str(c.text) });
// #endregion

// #region README
import {
  blocksOf, Conversion, fromPlain, given, inlinesOf, items, lookup, Plain, stringify, toPlain, toRomanNumeral, truthy,
} from "../src/index.ts";

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
assert.deepEqual([toRomanNumeral(2026), toRomanNumeral(0), toRomanNumeral(4000)], ["MMXXVI", "", "?"]);
const options = new Conversion({ readerOptions: { "track-changes": "all-changes" } }).readerOptions;
assert.equal(options?.["track-changes"], "all-changes");
const writer = new Conversion({ writerOptions: { "number-sections": true } }).writerOptions;
assert.equal(writer?.["number-sections"], true);
assert.equal("width: SIZE".replace("SIZE", () => "$&5%"), "width: $&5%");
const bytes = new TextEncoder().encode("«»");
assert.deepEqual(["«»".length, bytes.length, new TextDecoder().decode(bytes.subarray(0, 1))], [2, 4, "\ufffd"]);
// #endregion

// #region README
const loud: Filter = { Str: (s) => s.text.toUpperCase() };
assert.deepEqual(applyFilter(Emph(["a", Str("b")]), loud), Emph([Str("A"), Str("B")]));
// #endregion

// #region README
import { fromJSON, libpandocFilter } from "../src/index.ts";

/** What the filter uses of libpandoc's instance (its read takes panir's Conversion). */
interface Libpandoc { read(text: string, conversion: object): unknown }

// a code block of class "md" becomes its text, read as the document was
const md = libpandocFilter((pandoc: Libpandoc) => [{
  CodeBlock: (c, ctx) => (c.attr.classes.includes("md") ? fromJSON(pandoc.read(c.text, ctx.conversion)).blocks : undefined),
}]);
// pandoc.convertWithFilters({ from: "markdown", to: "html" }, input, [md]);
// #endregion

const readme = readFileSync(new URL("../README.md", import.meta.url), "utf8");

test("the README's examples are this file's", () => {
  const own = readFileSync(new URL(import.meta.url), "utf8");
  const regions = [...own.matchAll(/\/\/ #region README\n([^]*?)\/\/ #endregion\n/g)]
    .map((m) => m[1]!.replaceAll('"../src/index.ts"', '"panir"'));
  const blocks = [...readme.matchAll(/```ts\n([^]*?)```\n/g)].map((m) => m[1]);
  assert.deepEqual(blocks, regions);
  const js = [...readme.matchAll(/```js\n([^]*?)```\n/g)].map((m) => m[1]);
  assert.deepEqual(js, [readFileSync(new URL("../examples/upper.js", import.meta.url), "utf8")]);
});

test("the README's examples ran", () => {
  // (they ran when this module was loaded: what they made)
  assert.equal(formula.mathType, "InlineMath");
  assert.equal(figure.target.url, "cat.png");
  assert.equal(Math.max(1, 2), 2); // (the global Math, not pandoc's)
  const d = Pandoc(Para(["a", "b"]));
  applyFilter(d, shout, "html");
  assert.deepEqual(d.blocks, [Para([Strong("A"), Strong("B")])]);
  const n = Pandoc(Para(pandoc.Note(Para("x"))));
  applyFilter(n, skipNotes);
  // as libpandoc's host calls it, with an instance reading every text as one Para
  const read: [string, string | undefined][] = [];
  const instance: Libpandoc = {
    read: (text, conversion) => {
      read.push([text, (conversion as Conversion).inputFormat]);
      return toJSON(Pandoc(Para(text)));
    },
  };
  const input = toJSON(Pandoc([CodeBlock("*x*", { classes: ["md"] }), CodeBlock("y")]));
  const out = md(input, { format: "html", "input-format": "markdown+smart" }, instance);
  assert.deepEqual(fromJSON(out).blocks, [Para("*x*"), CodeBlock("y")]);
  assert.deepEqual(read, [["*x*", "markdown+smart"]]);
  assert.deepEqual(applyFilter(Pandoc(Para("todo TODO")), {
    Str: (s) => (s.text === "TODO" ? "DONE" : undefined),
  }).blocks, [Para("todo DONE")]);
});
