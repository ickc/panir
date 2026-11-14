// The README's examples: each ```ts block of README.md is a region of this
// file (between `#region README` and `#endregion`), in order, so that tsc
// checks it and the tests run it; its ```js block is examples/upper.js.
// (In the README, "panir" is "../src/index.ts" here.)

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { Div, Emph, Header, Pandoc, Para, Str, Strong } from "../src/index.ts";
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
const loud: Filter = { Str: (s) => s.text.toUpperCase() };
assert.deepEqual(applyFilter(Emph(["a", Str("b")]), loud), Emph([Str("A"), Str("B")]));
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
  assert.deepEqual(applyFilter(Pandoc(Para("todo TODO")), {
    Str: (s) => (s.text === "TODO" ? "DONE" : undefined),
  }).blocks, [Para("todo DONE")]);
});
