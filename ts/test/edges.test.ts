// The edges: what a review and fuzzing against pandoc found (fuzz/).

import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  applyFilter, ASTDecodeError, ASTTypeError, check, ColSpec, ColWidth, Div, Emph, fromJSON, fromPlain,
  Header, MetaInlines, Pandoc, Para, parse, serialize, Space, Str, Table, toJSON,
} from "../src/index.ts";
import type { Filter, Inline } from "../src/index.ts";

const VERSION = `"pandoc-api-version":[1,23,1,2]`;
const table = (width: string) =>
  `{${VERSION},"meta":{},"blocks":[{"t":"Table","c":[["",[],[]],[null,[]],` +
  `[[{"t":"AlignDefault"},{"t":"ColWidth","c":${width}}]],[["",[],[]],[]],[],[["",[],[]],[]]]}]}`;
const header = (level: string) =>
  `{${VERSION},"meta":{},"blocks":[{"t":"Header","c":[${level},["",[],[]],[]]}]}`;
const exclaim: Filter = { Str: (s) => { s.text += "!"; } };

// -- JSON -----------------------------------------------------------------------

test("a hole in a list is not an element", () => {
  const doc = Pandoc([Para([Str("a"), Space(), Str("b")])]);
  delete (doc.blocks[0] as { content: Inline[] }).content[1];
  assert.throws(() => check(doc), (e: unknown) => {
    assert.ok(e instanceof ASTTypeError);
    assert.equal(e.where, "Pandoc.blocks[0].content[1]");
    return true;
  });
  assert.throws(() => serialize(doc), ASTTypeError);
  const sparse = Pandoc();
  sparse.blocks[2] = Para("x");
  assert.throws(() => serialize(sparse), /Pandoc\.blocks\[0\]: expected Block, got undefined/);
});

test("any key is a key of the metadata", () => {
  const text = `{${VERSION},"meta":{"__proto__":{"t":"MetaBool","c":true},"constructor":{"t":"MetaBool","c":false},` +
    `"toString":{"t":"MetaString","c":"s"}},"blocks":[]}`;
  const doc = parse(text);
  assert.deepEqual(Object.keys(doc.meta), ["__proto__", "constructor", "toString"]);
  assert.deepEqual(doc.meta["__proto__"], { t: "MetaBool", value: true });
  assert.equal(serialize(doc), text);
  // and no key is there that wasn't given
  const empty = parse(`{${VERSION},"meta":{},"blocks":[]}`);
  assert.equal(empty.meta["constructor"], undefined);
  assert.equal(empty.meta["toString"], undefined);
  empty.meta["__proto__"] = { t: "MetaBool", value: true };
  assert.match(serialize(empty), /"meta":\{"__proto__":\{"t":"MetaBool","c":true\}\}/);
  // a constructor's map too
  const made = Pandoc([], { meta: JSON.parse(`{"__proto__":{"t":"MetaBool","value":true}}`) });
  assert.deepEqual(Object.keys(made.meta), ["__proto__"]);
  assert.match(serialize(made), /"__proto__"/);
  assert.deepEqual(Object.keys((fromPlain(JSON.parse(`{"__proto__":"x"}`)) as { content: object }).content), ["__proto__"]);
});

test("only a plain object is a map or a node", () => {
  const doc = Pandoc();
  (doc as { meta: unknown }).meta = new Map([["a", { t: "MetaBool", value: true }]]);
  assert.throws(() => serialize(doc), /Pandoc\.meta: expected an object/);
  assert.throws(() => Div([], { attributes: new Map([["k", "v"]]) as never }), ASTTypeError);
  assert.deepEqual(fromPlain({ t: "x", n: 1 }), {
    t: "MetaMap",
    content: Object.assign(Object.create(null), { t: { t: "MetaString", text: "x" }, n: { t: "MetaString", text: "1" } }),
  });
});

test("half of a surrogate pair is not text pandoc can read", { skip: !("isWellFormed" in String.prototype) }, () => {
  const half = "😀x".slice(0, 1);
  assert.throws(() => Str(half), /^ASTTypeError: Str\.text: expected a string, got .* \(half of a surrogate pair/);
  const s = Str("x");
  const whole = Pandoc([Para([s])]);
  s.text = half;
  assert.throws(() => serialize(whole), (e: unknown) => {
    assert.ok(e instanceof ASTTypeError);
    assert.equal(e.where, "Pandoc.blocks[0].content[0].text");
    assert.match(e.got, /surrogate/);
    return true;
  });
  const doc = Pandoc();
  doc.meta["\ud83d"] = { t: "MetaBool", value: true };
  assert.throws(() => serialize(doc), /surrogate/);
  assert.equal(parse(serialize(Pandoc([Para([Str("😀")])]))).blocks.length, 1);
});

test("an integer JavaScript can't hold exactly is refused, not rounded", () => {
  for (const level of ["9007199254740993", "9223372036854775807", "-9223372036854775808", "1e19"]) {
    assert.throws(() => parse(header(level)), (e: unknown) => {
      assert.ok(e instanceof ASTDecodeError, String(e));
      assert.deepEqual(e.path, ["blocks", 0, "level"]);
      assert.match(e.got, /exactly/);
      return true;
    });
  }
  assert.equal(serialize(parse(header("9007199254740991"))), header("9007199254740991"));
  assert.throws(() => Header(2 ** 53, "x"), ASTTypeError);
  assert.throws(() => Header(1e19, "x"), ASTTypeError);
  assert.throws(() => Header(1.5, "x"), ASTTypeError);
  const h = Header(1, "x");
  h.level = 2 ** 63;
  assert.throws(() => serialize(Pandoc([h])), /Pandoc\.blocks\[0\]\.level/);
});

test("doubles that are not finite, as pandoc writes them", () => {
  // (a column `width="0*"` in HTML gives null)
  for (const [written, value] of [["null", NaN], [`"+inf"`, Infinity], [`"-inf"`, -Infinity]] as const) {
    const doc = parse(table(written));
    assert.ok(Object.is((doc.blocks[0] as Table).colSpecs[0].width, undefined) === false);
    const width = ((doc.blocks[0] as Table).colSpecs[0].width as ColWidth).width;
    assert.ok(Object.is(width, value), `${written}: ${width}`);
    assert.equal(serialize(doc), table(written));
    assert.equal(serialize(Pandoc([Table({ colSpecs: [ColSpec(undefined, ColWidth(value))] })])), table(written));
  }
  assert.equal(((parse(table("1e999")).blocks[0] as Table).colSpecs[0].width as ColWidth).width, Infinity);
  for (const bad of [`"inf"`, "true", `"0.5"`]) assert.throws(() => parse(table(bad)), ASTDecodeError);
});

test("a document too deep for the call stack is an error of the AST", () => {
  let j: unknown = { t: "Str", c: "x" };
  for (let i = 0; i < 20000; i++) j = { t: "Emph", c: [j] };
  const doc = { "pandoc-api-version": [1, 23, 1, 2], meta: {}, blocks: [{ t: "Para", c: [j] }] };
  assert.throws(() => fromJSON(doc), (e: unknown) => e instanceof ASTDecodeError && /nested/.test(e.message));
  let node: Inline = Str("x");
  for (let i = 0; i < 20000; i++) node = { t: "Emph", content: [node] };
  assert.throws(() => toJSON({ meta: {}, blocks: [{ t: "Para", content: [node] }] }),
                (e: unknown) => e instanceof ASTTypeError && /nested/.test(e.message));
});

test("a type's name, not a constructor's", () => {
  assert.deepEqual(fromJSON({ t: "Str", c: "x" }, "Inline"), Str("x"));
  assert.throws(() => fromJSON({ t: "Str", c: "x" }, "Str"), /Str is a constructor of the type Inline/);
  assert.throws(() => toJSON(Str("x"), "Nope"), /no pandoc type is called "Nope"/);
});

// -- filters --------------------------------------------------------------------

test("top-down, the document a Pandoc function returns is the one walked", () => {
  const doc = applyFilter(Pandoc([Para("old")]), {
    traverse: "topdown",
    Pandoc: (d) => Pandoc([Para("new words")], { meta: d.meta }),
    Str: (s) => Str(s.text.toUpperCase()),
  });
  assert.deepEqual(doc.blocks, [Para("NEW WORDS")]);
});

test("a node returned twice is two nodes", () => {
  // as in Lua, where a function's results are values
  let doc = applyFilter(Pandoc([Para("a")]), [{ Para: (p) => [p, p] }, exclaim]);
  assert.deepEqual(doc.blocks, [Para("a!"), Para("a!")]);
  assert.notEqual(doc.blocks[0], doc.blocks[1]);
  doc = applyFilter(Pandoc([Para([Emph("b")])]), { traverse: "topdown", Emph: (e) => [e, e], Str: exclaim.Str });
  assert.deepEqual(doc.blocks, [Para([Emph("b!"), Emph("b!")])]);
  doc = applyFilter(Pandoc([Para("c")]), [{ Inlines: (xs) => (xs.length === 1 ? [...xs, ...xs] : undefined) }, exclaim]);
  assert.deepEqual(doc.blocks, [Para([Str("c!"), Str("c!")])]);
  // a list function may return its own list, changed
  doc = applyFilter(Pandoc([Para("d")]), { Inlines: (xs) => { xs.push(Str("e")); return xs; } });
  assert.deepEqual(doc.blocks, [Para([Str("d"), Str("e")])]);
});

test("metadata is walked in the order of its keys, by code point", () => {
  // as pandoc's Map; a function that counts its calls depends on it
  const seen: string[] = [];
  const doc = Pandoc([], { meta: { zzz: MetaInlines("z"), "10": MetaInlines("ten"), "2": MetaInlines("two") } });
  doc.meta["aaa"] = MetaInlines("a");
  doc.meta["\u{1f600}"] = MetaInlines("astral");
  doc.meta["～"] = MetaInlines("bmp");
  applyFilter(doc, { Str: (s) => { seen.push(s.text); } });
  assert.deepEqual(seen, ["ten", "two", "a", "z", "bmp", "astral"]);
});

test("a function may remove a later key of the map", () => {
  const doc = Pandoc([], { meta: { a: MetaInlines("a"), b: MetaInlines("b") } });
  applyFilter(doc, { MetaValue: (_, ctx) => { delete (ctx.container as Record<string, unknown>)["b"]; } });
  assert.deepEqual(Object.keys(doc.meta), ["a"]);
});

test("lists longer than a call takes arguments", () => {
  const n = 130_000;
  const many = () => Array.from({ length: n }, () => Para("x"));
  assert.equal(applyFilter(Pandoc(many()), { Blocks: (bs) => bs.slice() }).blocks.length, n);
  assert.equal(applyFilter(Pandoc([Div(many())]), { Div: (d) => d.content }).blocks.length, n);
});

test("a function is called as a method of its filter", () => {
  class Counter {
    n = 0;
    Str(_: Str) { this.n++; }
  }
  const counter = new Counter();
  applyFilter(Pandoc([Para("a b c")]), counter);
  assert.equal(counter.n, 3);
});

test("what a function may not return", () => {
  assert.throws(() => applyFilter(Pandoc(), { Pandoc: ((d: Pandoc) => [d, d]) as never }),
                /expected one Pandoc \(this position holds one\), got a list/);
  assert.throws(() => applyFilter(Pandoc([Para("a")]), { Str: (async () => undefined) as never }),
                /blocks\[0\]\.content\[0\]: expected a Inline, got a promise/);
  assert.throws(() => applyFilter(Pandoc([], { meta: { title: MetaInlines("t") } }), { MetaValue: (() => []) as never }),
                /^ASTTypeError: meta\.title: expected one node/);
  assert.throws(() => applyFilter(Pandoc([Para("a")]), { Inlines: (() => "text") as never }),
                /^ASTTypeError: blocks\[0\]\.content: expected a list of Inlines/);
});

test("an error whose message can't be changed is still the error", () => {
  const frozen = Object.freeze(new Error("frozen"));
  assert.throws(() => applyFilter(Pandoc([Para("a")]), { Str: () => { throw frozen; } }), (e) => e === frozen);
  assert.throws(() => applyFilter(Pandoc([Para("a")]), { Str: () => { structuredClone(() => 1); } }),
                (e: unknown) => e instanceof Error && e.name === "DataCloneError");
});

test("runFilter has written its output when it resolves", () => {
  const dir = mkdtempSync(join(tmpdir(), "panir-"));
  const script = join(dir, "exit.ts");
  const node = new URL("../src/node.ts", import.meta.url).href;
  writeFileSync(script, `import { runFilter } from ${JSON.stringify(node)};\nawait runFilter({});\nprocess.exit(0);\n`);
  const input = serialize(Pandoc(Array.from({ length: 40_000 }, () => Para("some words in a paragraph"))));
  assert.ok(input.length > 2_000_000);
  // through a pipe, as pandoc runs a filter
  const out = execFileSync(process.execPath, [script, "html"], { input, maxBuffer: 1 << 26 }).toString();
  assert.equal(out.length, input.length);
});
