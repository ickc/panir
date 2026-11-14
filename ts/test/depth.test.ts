// Deep documents: everything works to 256 levels and beyond (the Rust
// crate's MAX_DEPTH: 300 nested divs is a 9 kB web page), at Node's default
// stack; deeper, an ASTDecodeError when reading and an ASTDepthError
// elsewhere, never the engine's bare RangeError.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  applyFilter, ASTDecodeError, ASTDepthError, check, copy, equal, fromJSON, parse, serialize, stringify, toJSON,
  toPlain, walk,
} from "../src/index.ts";
import type { Block, Pandoc } from "../src/index.ts";

const ATTR = ["", [], []];

/** pandoc's JSON of `n` divs in each other, or tables in tables' cells, around a paragraph. */
function deepJSON(n: number, kind: "div" | "table"): unknown {
  let b: unknown = { t: "Para", c: [{ t: "Str", c: "x" }] };
  for (let i = 0; i < n; i++) {
    if (kind === "div") {
      b = { t: "Div", c: [ATTR, [b]] };
    } else {
      const cell = [ATTR, { t: "AlignDefault" }, 1, 1, [b]];
      b = { t: "Table", c: [ATTR, [null, []], [[{ t: "AlignDefault" }, { t: "ColWidthDefault" }]], [ATTR, []],
                            [[ATTR, 0, [], [[ATTR, [cell]]]]], [ATTR, []]] };
    }
  }
  return { "pandoc-api-version": [1, 23, 1, 2], meta: { k: { t: "MetaBlocks", c: [b] } }, blocks: [b] };
}

const ORDERS = ["typewise", "topdown", "bottomup"] as const;

for (const [kind, n] of [["div", 300], ["table", 260]] as const) {
  test(`${n} levels of ${kind}s: everything works`, () => {
    const json = deepJSON(n, kind);
    const text = JSON.stringify(json);
    const doc = parse(text);
    assert.equal(serialize(doc), text);
    assert.equal(JSON.stringify(toJSON(fromJSON(json))), text);
    check(doc);
    for (const traverse of ORDERS) {
      let strs = 0;
      applyFilter(doc, { traverse, Str: (s) => { strs++; return s.text.toUpperCase(); }, Blocks: () => undefined });
      assert.equal(strs, 2);
    }
    assert.equal(stringify(doc), "XX");
    assert.deepEqual(toPlain(doc.meta), { k: "X" });
    let nodes = 0;
    walk(doc, () => { nodes++; });
    assert.ok(nodes > 2 * n);
    const other = copy(doc);
    assert.ok(equal(doc, other));
    assert.notEqual(other.blocks[0], doc.blocks[0]);
    // a fragment too
    applyFilter(doc.blocks[0] as Block, { Str: (s) => `${s.text}!` });
    assert.equal(stringify(doc.blocks[0]), "X!");
  });
}

test("deeper than the stack: panir's errors, not the engine's", () => {
  const n = 40_000;
  const json = deepJSON(n, "div");
  // (JSON.stringify can't write it)
  const text = `{"pandoc-api-version":[1,23,1,2],"meta":{},"blocks":[${'{"t":"Div","c":[["",[],[]],['.repeat(n)}` +
    `{"t":"Para","c":[]}${"]]}".repeat(n)}]}`;
  const decode = (e: unknown) => e instanceof ASTDecodeError && /nested less deeply/.test(e.message);
  assert.throws(() => parse(text), decode);
  assert.throws(() => fromJSON(json), decode);
  // a document made otherwise
  const made = (): Pandoc => {
    let b: Block = { t: "Para", content: [{ t: "Str", text: "x" }] };
    for (let i = 0; i < n; i++) b = { t: "Div", attr: { identifier: "", classes: [], attributes: [] }, content: [b] };
    return { meta: Object.create(null), blocks: [b] };
  };
  const doc = made();
  const b = doc.blocks[0];
  const depth = (where: RegExp) => (e: unknown) => e instanceof ASTDepthError && e instanceof RangeError && where.test(e.where);
  assert.throws(() => toJSON(doc), depth(/^toJSON$/));
  assert.throws(() => serialize(doc), depth(/^toJSON$/));
  assert.throws(() => check(doc), depth(/^toJSON$/));
  for (const traverse of ORDERS) {
    assert.throws(() => applyFilter(doc, { traverse, Str: () => undefined }), depth(/^applyFilter$/), traverse);
  }
  assert.throws(() => applyFilter(b, { Str: () => undefined }), depth(/^applyFilter$/));
  // (a function with a context too: what is noted on errors isn't on these)
  assert.throws(() => applyFilter(doc, { Str: (_s, ctx) => { void ctx; } }), depth(/^applyFilter$/));
  assert.throws(() => walk(doc, () => undefined), depth(/^walk$/));
  assert.throws(() => copy(doc), depth(/^copy$/));
  assert.throws(() => equal(doc, made()), depth(/^equal$/));
  assert.throws(() => stringify(doc), depth(/^stringify$/));
});
