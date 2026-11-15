// stringify, as pandoc's Lua pandoc.utils.stringify; metadata to and from
// plain values; copy and equal.

import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import {
  ASTTypeError, Attr, blocksOf, Citation, Code, copy, Div, Emph, equal, fromJSON, fromPlain, getAttribute, given,
  hasAttribute, inlinesOf, items, LineBreak, ListAttributes, lookup, removeAttribute, setAttribute,
  MetaBlocks, MetaBool, MetaInlines, MetaList, MetaMap, MetaString, Note, Pandoc, Para, parse, Plain, Quoted, RawInline,
  serialize, Space, Str, stringify, toPlain, truthy,
} from "../src/index.ts";
import type { Pandoc as Doc, MetaString as MetaStringNode } from "../src/index.ts";
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
  const plain = { draft: true, n: "3", tags: ["a", [Str("b")]], title: [Str("T")], body: [Para("p")], m: { k: false } };
  assert.deepEqual(toPlain(meta), plain);
  // and back (as Lua: the inlines and blocks lists of them)
  assert.deepEqual(fromPlain(plain), meta);
  // a MetaBlocks and a MetaInlines become lists of them (copies), as Lua's Blocks and Inlines
  assert.deepEqual(toPlain(MetaBlocks([Para("a b"), Para("c")])), [Para("a b"), Para("c")]);
  assert.deepEqual(toPlain(MetaInlines([Emph("x"), Str("y")])), [Emph("x"), Str("y")]);
  assert.deepEqual(toPlain(parse(serialize(everyKindOfMetadata())).meta), {
    z: "zs", b: true, m: { y: "my", a: [Str("ma")], f: false, l: ["l1", [Para("l2")]] },
    i: [Str("in"), Note([Para("note")]), RawInline("html", "<br/>")], k: [Para("bl")],
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

test("Lua's truth of metadata values: false only for a missing one and MetaBool(false)", () => {
  for (const v of [MetaBool(true), MetaString(""), MetaString("false"), MetaInlines([]), MetaBlocks([]), MetaList([]),
    MetaMap({})]) {
    assert.equal(truthy(v), true, JSON.stringify(v));
  }
  assert.equal(truthy(MetaBool(false)), false);
  assert.equal(truthy(undefined), false);
  assert.equal(truthy(null), false);
  assert.equal(truthy(Pandoc([], { meta: { off: false } }).meta.off), false);
});

test("given: the value if Lua's truth says so, else undefined (Lua's meta.x or d)", () => {
  const meta = Pandoc([], { meta: { off: false, on: true, empty: "", text: "x" } }).meta;
  assert.equal(given(meta.off), undefined);
  assert.equal(given(meta.missing), undefined);
  assert.equal(given(null), undefined);
  for (const k of ["on", "empty", "text"]) assert.equal(given(meta[k]), meta[k]); // the document's own
  assert.equal(stringify(given(meta.off) ?? MetaString("d")), "d");
  assert.equal(stringify(given(meta.empty) ?? MetaString("d")), "");
  // narrowed, with no `!`: a MetaValue where one is needed
  const v = given(meta.text);
  const text: string = v ? stringify(v) : "none";
  assert.equal(text, "x");
  // a narrower type stays as it was
  const s = MetaString("y");
  const same: MetaStringNode | undefined = given(s);
  assert.equal(same, s);
});

test("a nested option, through maps: undefined where a key is missing or a value on the way is not a map", () => {
  const meta = Pandoc([], { meta: { toc: { depth: "2", style: { name: "x" } }, flag: true, l: ["a"] } }).meta;
  assert.deepEqual(lookup(meta, "toc", "depth"), MetaString("2"));
  assert.deepEqual(lookup(meta, "toc", "style", "name"), MetaString("x"));
  assert.deepEqual(lookup(meta.toc, "depth"), MetaString("2")); // from a MetaMap
  assert.equal(lookup(meta, "toc"), meta.toc);
  for (const [key, ...keys] of [["toc", "missing"], ["missing", "depth"], ["flag", "x"], ["l", "a"], ["toc", "depth", "x"],
    ["toc", "constructor"], ["toc", "__proto__"]]) {
    assert.equal(lookup(meta, key!, ...keys), undefined, [key, ...keys].join("."));
  }
  assert.equal(lookup(undefined, "x"), undefined);
  // a field named t is a field
  assert.deepEqual(lookup(Pandoc([], { meta: { t: { u: "v" } } }).meta, "t", "u"), MetaString("v"));
});

test("one value or a list of them, as a list", () => {
  const meta = Pandoc([], { meta: { one: "a", l: ["a", "b"], e: [], off: false } }).meta;
  assert.deepEqual(items(meta.one), [MetaString("a")]);
  assert.deepEqual(items(meta.l), [MetaString("a"), MetaString("b")]);
  assert.deepEqual(items(meta.e), []);
  assert.deepEqual(items(meta.off), [MetaBool(false)]);
  assert.deepEqual(items(meta.missing), []);
  const listed = items(meta.l);
  listed.pop(); // a new array: the document's is as it was
  assert.equal((meta.l as { content: unknown[] }).content.length, 2);
});

test("a metadata value as inlines or blocks, as pandoc's Lua converts it", () => {
  const words = [Str("a"), Space(), Str("b")];
  const emph = MetaInlines([Str("x"), Space(), Emph("y")]);
  assert.deepEqual(inlinesOf(MetaString("a b")), words);
  assert.deepEqual(blocksOf(MetaString("a b")), [Plain(words)]);
  assert.deepEqual(blocksOf(MetaString("")), [Plain([])]);
  assert.deepEqual(inlinesOf(emph), emph.content);
  assert.deepEqual(blocksOf(emph), [Plain([Str("x")]), Plain([Space()]), Plain([Emph("y")])]);
  assert.deepEqual(blocksOf(MetaBlocks([Para("p")])), [Para("p")]);
  assert.deepEqual([inlinesOf(MetaMap({ k: "v" })), blocksOf(MetaMap({ k: "v" }))], [[], []]);
  const texts = MetaList([MetaString("a b"), MetaString("c")]);
  assert.deepEqual(inlinesOf(texts), [Str("a b"), Str("c")]); // a Str each, unsplit
  assert.deepEqual(blocksOf(texts), [Plain(words), Plain([Str("c")])]);
  const mixed = MetaList([MetaInlines([Emph("x")]), MetaMap({}), MetaList([]), MetaList([MetaString("")])]);
  assert.deepEqual(blocksOf(mixed), [Plain([Emph("x")]), Plain([]), Plain([]), Plain([Str("")])]);
  const got = inlinesOf(emph); // copies: Lua's values are values
  assert.ok(got[2] !== emph.content[2]);
  const fails: [(v: never) => unknown, unknown, string][] = [
    [inlinesOf, MetaBlocks([Para("p")]), "expected inlines (MetaInlines, MetaString, MetaMap, or a MetaList of MetaStrings), got MetaBlocks"],
    [inlinesOf, MetaBool(true), "got MetaBool"],
    [inlinesOf, mixed, "MetaList.content[0]: expected an inline (a MetaString), got MetaInlines"],
    [blocksOf, MetaBool(false), "expected blocks (MetaBlocks, MetaInlines, MetaString, MetaMap, or a MetaList), got MetaBool"],
    [blocksOf, MetaList([MetaString("a"), MetaBlocks([])]), "MetaList.content[1]: expected a block"],
    [blocksOf, MetaList([MetaList([emph])]), "MetaList.content[0].content[0]: expected an inline"],
    [inlinesOf, undefined, "got undefined"],
  ];
  for (const [f, v, message] of fails) {
    assert.throws(() => f(v as never), (e: Error) => e instanceof ASTTypeError && e.message.includes(message), message);
  }
});

test("attributes by key, as pandoc's Lua", () => {
  const attr = Attr("id", ["x"], [["k", "v"], ["k", "w"], ["z", "1"]]);
  assert.equal(getAttribute(attr, "k"), "v"); // the first pair with the key
  assert.equal(getAttribute(attr, "missing"), undefined);
  assert.ok(hasAttribute(attr, "z") && !hasAttribute(attr, "missing"));
  setAttribute(attr, "k", "new"); // the first pair's value, in its place
  assert.deepEqual(attr.attributes, [["k", "new"], ["k", "w"], ["z", "1"]]);
  assert.equal(removeAttribute(attr, "k"), "new"); // the first pair only
  assert.deepEqual(attr.attributes, [["k", "w"], ["z", "1"]]);
  setAttribute(attr, "q", "2"); // a new key: at the end
  assert.deepEqual(attr.attributes, [["k", "w"], ["z", "1"], ["q", "2"]]);
  assert.equal(removeAttribute(attr, "missing"), undefined);
  // still pairs: the document's JSON is the same
  assert.deepEqual(Div([], { attr }).attr, Attr("id", ["x"], [["k", "w"], ["z", "1"], ["q", "2"]]));
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
