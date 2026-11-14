import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  applyFilter, ASTTypeError, Attr, blocks, Conversion, BulletList, Cell, Cite, Code, DefinitionList, Div, Emph,
  Header, inlines, LineBlock, Link, Math, Note, OrderedList, Pandoc, Para, Plain, Quoted, SoftBreak,
  Space, Str, Table, fromPlain, parse, serialize, stringify, toPlain,
} from "../src/index.ts";
import type { Filter, Inline } from "../src/index.ts";

test("constructors fill defaults and flatten attributes", () => {
  assert.deepEqual(Header(1, [Str("Hi")], { identifier: "hi", classes: ["x"] }), {
    t: "Header", level: 1,
    attr: { identifier: "hi", classes: ["x"], attributes: [] },
    content: [{ t: "Str", text: "Hi" }],
  });
  assert.deepEqual(Div([], { attributes: { k: "v" } }).attr, Attr("", [], [["k", "v"]]));
  assert.equal(OrderedList([[Plain([Str("a")])]], { start: 3 }).listAttributes.start, 3);
  assert.deepEqual(OrderedList().listAttributes, { start: 1, style: "DefaultStyle", delimiter: "DefaultDelim" });
  assert.equal(Cell([Para()]).rowSpan, 1);
  assert.deepEqual(Table().bodies, []);
  assert.deepEqual(Link([Str("x")], { url: "u" }).target, { url: "u", title: "" });
});

test("strings convert as in pandoc's Lua", () => {
  const hw = [Str("hello"), Space(), Str("world")];
  assert.deepEqual(Para("hello world"), Para(hw));
  assert.deepEqual(Header(1, "a\nb").content, [Str("a"), SoftBreak(), Str("b")]);
  // one inline: a Str, whole
  assert.deepEqual(Para(["hello world", Emph("x")]).content, [Str("hello world"), Emph([Str("x")])]);
  // blocks: Plain text
  assert.deepEqual(Div("hello world").content, [Plain(hw)]);
  assert.deepEqual(Div(["a b", Para("c")]).content, [Plain([Str("a"), Space(), Str("b")]), Para([Str("c")])]);
  assert.deepEqual(Pandoc("x").blocks, [Plain([Str("x")])]);
  // nested
  assert.deepEqual(LineBlock(["a b", [Str("c")]]).content, [[Str("a"), Space(), Str("b")], [Str("c")]]);
  assert.deepEqual(DefinitionList([["term", ["def"]]]).content, [[[Str("term")], [[Plain([Str("def")])]]]]);
  assert.deepEqual(inlines(" a  b\n"), [Space(), Str("a"), Space(), Str("b"), SoftBreak()]);
  assert.deepEqual(blocks("a"), [Plain([Str("a")])]);
  // a node has its own list, not the one given (which may change, or go in another node)
  const given = [Str("x")];
  const para = Para(given);
  assert.notEqual(para.content, given);
  assert.equal(para.content[0], given[0]);
  given.push(Str("y"));
  assert.deepEqual(para.content, [Str("x")]);
  const items = [[Para("a")]];
  assert.notEqual(BulletList(items).content[0], items[0]);
});

test("constructors check their arguments, and say where", () => {
  const cases: [() => unknown, RegExp][] = [
    [() => Para([Para() as unknown as Inline]), /^Para\.content\[0\]: expected Inline, got Para \(a Block\)$/],
    [() => Cite({ citations: "x" as never }), /Cite\.citations: expected a list, got "x" \(a string; give a list of nodes\)/],
    [() => Math("Bogus" as "InlineMath", "x"), /Math\.mathType: expected a MathType, got "Bogus"/],
    [() => BulletList([[Str("x") as never]]), /BulletList\.content\[0\]\[0\]: expected Block, got Str \(an Inline\)/],
    [() => Header(1.5, []), /Header\.level: expected an integer, got 1\.5/],
  ];
  for (const [f, message] of cases) {
    assert.throws(f, (e: unknown) => e instanceof ASTTypeError && message.test(e.message));
  }
  assert.throws(() => Link([Str("x")]), /Link\(\) needs url \(or target\)/);
  assert.throws(() => Code("x", { attr: Attr(), identifier: "y" }), /give either attr or identifier, not both/);
  assert.throws(() => Code("x", { bogus: 1 } as never), /Code\(\) has no option bogus/);
});

function doc() {
  return Pandoc([
    Header(1, [Str("Title")]),
    Para([Str("a"), Space(), Emph([Str("b")]), Note([Para([Str("n")])])]),
    BulletList([[Plain([Str("x")])], [Plain([Str("y")])]]),
  ]);
}

test("filters modify, replace, splice and delete", () => {
  const f: Filter = {
    Header: (h) => { h.level += 1; },
    Str: (s) => {
      if (s.text === "a") return Str("A");
      if (s.text === "b") return [Str("b1"), Space(), Str("b2")];
      if (s.text === "x") return [];
    },
  };
  const d = applyFilter(doc(), f);
  assert.equal((d.blocks[0] as { level: number }).level, 2);
  assert.equal(stringify(d.blocks[1]), "A b1 b2");
  assert.deepEqual((d.blocks[2] as { content: unknown }).content, [[Plain()], [Plain([Str("y")])]]);
});

test("the constructor's function wins over its type's", () => {
  const seen: string[] = [];
  applyFilter(Pandoc([Para([Str("a"), Space()])]), {
    Inline: (x) => { seen.push(x.t); },
    Str: () => { seen.push("str"); },
  });
  assert.deepEqual(seen, ["str", "Space"]);
});

test("typewise and topdown", () => {
  for (const traverse of ["typewise", "topdown"] as const) {
    const order: string[] = [];
    applyFilter(Pandoc([Para([Emph([Str("x")])])]), {
      traverse,
      Para: () => { order.push("Para"); },
      Emph: () => { order.push("Emph"); },
      Str: () => { order.push("Str"); },
    });
    assert.deepEqual(order, traverse === "topdown" ? ["Para", "Emph", "Str"] : ["Str", "Emph", "Para"]);
  }
});

test("context", () => {
  let found: unknown;
  applyFilter(doc(), {
    Str: (s, ctx) => {
      if (s.text === "b") {
        found = { path: ctx.path, where: ctx.where, parent: (ctx.parent as { t: string }).t, index: ctx.index,
                  format: ctx.format, ancestors: ctx.ancestors.map((a) => (a as { t?: string }).t ?? "Pandoc") };
      }
    },
  }, "html");
  assert.deepEqual(found, {
    path: ["blocks", 1, "content", 2, "content", 0], where: "blocks[1].content[2].content[0]",
    parent: "Emph", index: 0, format: "html", ancestors: ["Pandoc", "Para", "Emph"],
  });
  const paths: string[] = [];
  applyFilter(doc(), { Str: (_s, ctx) => { paths.push(ctx.where); } });
  assert.ok(paths.includes("blocks[2].content[1][0].content[0]"), paths.join(" "));
});

test("the conversion", () => {
  const conversion = new Conversion({ format: "html5", inputFormat: "commonmark_x-smart", readerOptions: { columns: 72 } });
  let seen: Conversion | undefined;
  let format: string | undefined;
  applyFilter(doc(), { Pandoc: (_d, ctx) => { seen = ctx.conversion; format = ctx.format; } }, conversion);
  assert.equal(seen, conversion);
  assert.equal(format, "html5");
  // a format alone is a conversion knowing only that
  applyFilter(doc(), { Pandoc: (_d, ctx) => { seen = ctx.conversion; } }, "latex");
  assert.equal(seen?.format, "latex");
  assert.equal(seen?.inputFormat, undefined);
  const env = Conversion.fromEnvironment(["html5"], {
    PANDOC_READER_OPTIONS: '{"columns":72,"extensions":["smart"]}',
    PANDOC_INPUT_FORMAT: "markdown+smart", PANDOC_OUTPUT_FORMAT: "",
  });
  assert.deepEqual([env.format, env.inputFormat, env.outputFormat, env.readerOptions?.["columns"]],
                   ["html5", "markdown+smart", undefined, 72]);
});

test("runFilter tells the filter the conversion, under pandoc", (t) => {
  try { execFileSync("pandoc", ["--version"]); } catch { return t.skip("no pandoc on PATH"); }
  const dir = mkdtempSync(join(tmpdir(), "panir-"));
  writeFileSync(join(dir, "package.json"), '{"type": "module"}');
  const node = new URL("../src/node.ts", import.meta.url).href;
  const index = new URL("../src/index.ts", import.meta.url).href;
  writeFileSync(join(dir, "f.js"), `
import { Para, Str } from ${JSON.stringify(index)};
import { runFilter } from ${JSON.stringify(node)};
await runFilter({ Pandoc: (d, ctx) => {
  d.blocks.push(Para([Str([ctx.format, ctx.conversion.format, ctx.conversion.readerOptions.columns].join(","))]));
  return d;
} });
`);
  const out = execFileSync("pandoc", ["-f", "markdown", "-t", "plain", "--columns=60", "--filter", join(dir, "f.js")],
                           { input: "hi" }).toString();
  assert.equal(out, "hi\n\nplain,plain,60\n");
});

test("errors in filter functions say where", () => {
  assert.throws(() => applyFilter(doc(), { Header: function broken() { throw new Error("boom"); } }),
                /boom\n {2}in filter function broken, on the Header at blocks\[0\]/);
});

test("misused filters say so", () => {
  for (const traverse of ["typewise", "bottomup"] as const) {
    // a type error (only top-down functions can skip), and at run time, for JavaScript
    // @ts-expect-error: skipChildren isn't on a WalkedContext
    assert.throws(() => applyFilter(doc(), { traverse, Str: (_s, ctx) => { ctx.skipChildren(); } }),
                  /skipChildren\(\) needs traverse: "topdown"[^]*on the Str at blocks\[0\]\.content\[0\]/);
  }
  assert.throws(() => applyFilter(doc(), { Inlines: () => Str("x") as never }),
                (e: unknown) => e instanceof ASTTypeError && /a list of Inlines/.test(e.message));
  assert.throws(() => applyFilter(doc(), { traverse: "inside-out" as never }),
                /traverse: expected "typewise", "topdown" or "bottomup"/);
  assert.throws(() => applyFilter(doc(), { Blocks: function lists() { throw new Error("boom"); } }),
                /boom\n {2}in filter function lists, on the Blocks at blocks\[1\]\.content\[3\]\.content/);
});

test("a list of filters runs one after the other", () => {
  const d = applyFilter(doc(), [{ Str: (s) => Str(`${s.text}1`) }, { Str: (s) => Str(`${s.text}2`) }]);
  assert.equal(stringify(d.blocks[0]), "Title12");
});

test("JSON text round trip, and serialize checks", () => {
  const d = doc();
  assert.deepEqual(parse(serialize(d)), d);
  (d.blocks[1] as { content: unknown[] }).content.push(Para());
  assert.throws(() => serialize(d), /Pandoc\.blocks\[1\]\.content\[4\]: expected Inline, got Para \(a Block\)/);
});

test("stringify and metadata", () => {
  const p = Para([Str("a"), Space(), Quoted("DoubleQuote", [Str("q")]), Code("c"), Note([Para([Str("n")])])]);
  assert.equal(stringify(p), "a “q”c");
  const meta = fromPlain({ draft: true, tags: ["a"], n: 3 });
  assert.deepEqual(toPlain(meta), { draft: true, tags: ["a"], n: "3" });
});

test("structuredClone keeps documents intact (for workers)", () => {
  const d = doc();
  const clone = structuredClone(d);
  // (but for the metadata's missing prototype: a clone's maps are ordinary objects)
  assert.deepEqual({ ...clone, meta: null }, { ...d, meta: null });
  assert.equal(serialize(clone), serialize(d));
});
