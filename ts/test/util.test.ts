// stringify, as pandoc's Lua pandoc.utils.stringify; metadata to and from
// plain values; copy and equal.

import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import {
  ASTTypeError, Attr, Citation, Code, copy, Div, Emph, equal, fromJSON, fromPlain, LineBreak, ListAttributes,
  MetaBlocks, MetaBool, MetaInlines, MetaList, MetaMap, MetaString, Note, Pandoc, Para, parse, Plain, Quoted, RawInline,
  serialize, Str, stringify, toPlain,
} from "../src/index.ts";
import type { Pandoc as Doc } from "../src/index.ts";
import { pandoc } from "./pandoc.ts";

// for each document: stringify of the whole, of its metadata, of each
// metadata value, of each block
const LUA = `
for line in io.lines() do
  local doc = pandoc.read(line, "json")
  local out = {whole = pandoc.utils.stringify(doc), metamap = pandoc.utils.stringify(doc.meta),
    meta = {}, blocks = {}}
  for k, v in pairs(doc.meta) do out.meta[k] = pandoc.utils.stringify(v) end
  for i, b in ipairs(doc.blocks) do out.blocks[i] = pandoc.utils.stringify(b) end
  print(pandoc.json.encode(out))
end
`;

/** Metadata of every kind, its keys not in order (as pandoc's JSON has them), and in maps. */
const everyKindOfMetadata = (): Doc => Pandoc(Para(["body", LineBreak(), Quoted("DoubleQuote", Str("q"))]), {
  meta: {
    z: MetaString("zs"),
    b: MetaBool(true),
    m: MetaMap({ y: MetaString("my"), a: MetaInlines(Str("ma")), f: MetaBool(false), l: MetaList([MetaString("l1"), MetaBlocks(Para("l2"))]) }),
    i: MetaInlines([Str("in"), Note(Para("note")), RawInline("html", "<br/>")]),
    k: MetaBlocks(Para("bl")),
  },
});

function ours(doc: Doc) {
  return {
    whole: stringify(doc),
    metamap: stringify(doc.meta),
    meta: Object.fromEntries(Object.entries(doc.meta).map(([k, v]) => [k, stringify(v)])),
    blocks: doc.blocks.map((b) => stringify(b)),
  };
}

test("stringify, metadata of every kind", () => {
  // as pandoc 3.14 (and against pandoc itself, below): a map in the order
  // of its keys; a whole document is its inlines' text only (no
  // MetaString, no MetaBool), but metadata stringified has its strings
  // and booleans too
  assert.deepEqual(ours(everyKindOfMetadata()), {
    whole: "in blmal2body “q”",
    metamap: "truein blmafalsel1l2myzs",
    meta: { z: "zs", b: "true", m: "mafalsel1l2my", i: "in ", k: "bl" },
    blocks: ["body “q”"],
  });
  // maps with a key t, or blocks, are maps
  const tricky = Pandoc([], { meta: { t: MetaString("T"), blocks: MetaInlines("B"), meta: MetaString("M") } });
  assert.equal(stringify(tricky.meta), "BMT");
  assert.equal(stringify(tricky), "B");
  // plain values, as Lua's: given whole, or in lists and maps
  assert.equal(stringify(["x", Str("y"), 1, true]), "xy1true");
  assert.equal(stringify({ b: "v", a: Str("w") }), "wv");
  // what has no inlines has no text; a citation's prefix and suffix are text
  assert.equal(stringify(Attr("i", ["c"], { k: "v" })), "");
  assert.equal(stringify(ListAttributes(3)), "");
  assert.equal(stringify(Citation("k", "NormalCitation", "p", "s")), "ps");
  assert.equal(stringify(Div(Code("c"), { identifier: "id", classes: ["x"] })), "c");
});

for (const corpus of ["arbitrary.jsonl", "pandoc.jsonl", "made"]) {
  test(`stringify as pandoc's: ${corpus}`, (t) => {
    const exe = pandoc(t);
    if (!exe) return;
    const texts = corpus === "made"
      ? [serialize(everyKindOfMetadata())]
      : readFileSync(new URL(`../../corpus/${corpus}`, import.meta.url), "utf8").split("\n").filter((l) => l);
    const out = execFileSync(exe, ["lua", "-e", LUA], { input: texts.join("\n") + "\n", maxBuffer: 1 << 28 }).toString();
    const want = out.split("\n").filter((l) => l).map((l) => {
      const w = JSON.parse(l) as { blocks: unknown; meta: unknown };
      // (Lua's empty table is written {})
      if (!Array.isArray(w.blocks)) w.blocks = [];
      if (Array.isArray(w.meta)) w.meta = {};
      return w;
    });
    assert.equal(want.length, texts.length);
    texts.forEach((text, i) => assert.deepEqual(ours(parse(text)), want[i], `document ${i}`));
  });
}

test("metadata to and from plain values, as pandoc's Lua converts", () => {
  const meta = fromPlain({ draft: true, n: 3, tags: ["a", Str("b")], title: [Str("T")], body: Para("p"), m: { k: false } });
  assert.deepEqual(meta, MetaMap({
    draft: MetaBool(true), n: MetaString("3"), tags: MetaList([MetaString("a"), MetaInlines([Str("b")])]),
    title: MetaInlines([Str("T")]), body: MetaBlocks([Para("p")]), m: MetaMap({ k: MetaBool(false) }),
  }));
  assert.deepEqual(toPlain(meta), { draft: true, n: "3", tags: ["a", "b"], title: "T", body: "p", m: { k: false } });
  // a MetaBlocks and a MetaInlines become their text
  assert.equal(toPlain(MetaBlocks([Para("a b"), Para("c")])), "a bc");
  assert.equal(toPlain(MetaInlines([Emph("x"), Str("y")])), "xy");
  assert.deepEqual(toPlain(parse(serialize(everyKindOfMetadata())).meta), {
    z: "zs", b: true, m: { y: "my", a: "ma", f: false, l: ["l1", "l2"] }, i: "in ", k: "bl",
  });
  // what doesn't convert
  for (const bad of [null, undefined, () => 1, [1, Symbol("s")]]) {
    assert.throws(() => fromPlain(bad as never), ASTTypeError);
  }
  assert.throws(() => toPlain(Str("x") as never), ASTTypeError);
  // constructors convert metadata the same way
  assert.deepEqual(Pandoc([], { meta: { draft: true, title: Str("T") } }).meta,
                   Object.assign(Object.create(null), { draft: MetaBool(true), title: MetaInlines([Str("T")]) }));
  assert.deepEqual(MetaList(["a", 1]).content, [MetaString("a"), MetaString("1")]);
});

test("copy and equal", () => {
  const doc = parse(serialize(everyKindOfMetadata()));
  const other = copy(doc);
  assert.ok(equal(doc, other));
  assert.notEqual(other.blocks[0], doc.blocks[0]);
  // maps keep having no prototype (structuredClone gives them one: equal anyway)
  assert.equal(Object.getPrototypeOf(other.meta), null);
  assert.ok(equal(doc, structuredClone(doc)));
  // keys in another order are the same map; NaN is NaN
  const nan = fromJSON({ t: "ColWidth", c: null }, "ColWidth");
  assert.ok(equal(nan, copy(nan)));
  assert.ok(equal(MetaMap({ a: "x", b: "y" }), MetaMap({ b: "y", a: "x" })));
  for (const changed of [Para("y"), Para(["x", "y"]), Div("x"), Plain("x")]) {
    assert.ok(!equal(Para(["x"]), changed), JSON.stringify(changed));
  }
  assert.ok(!equal([Str("a")], { 0: Str("a") }));
  assert.ok(!equal(MetaMap({ a: "x" }), MetaMap({ a: "x", b: "y" })));
});
