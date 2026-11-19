// A filter's options from a code block's YAML header and the document's
// metadata: the split (corpus/yaml-blocks.jsonl, pandoc's verdicts), metadata
// as plain data, the options, and yamlFilter.

import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import {
  CodeBlock, Div, Emph, MetaBlocks, MetaBool, MetaInlines, MetaList, MetaMap, MetaString, Options, Pandoc, Para,
  parse, Space, Str, toData, yamlBlock, yamlFilter,
} from "../src/index.ts";
import type { Meta } from "../src/index.ts";
import { pandoc } from "./pandoc.ts";

type Case = { name: string; text: string; header: string | null; data: string };

const cases = readFileSync(new URL("../../corpus/yaml-blocks.jsonl", import.meta.url), "utf8")
  .split("\n").filter((l) => l).map((l) => JSON.parse(l) as Case);

test("yamlBlock as corpus/yaml-blocks.jsonl", () => {
  assert.ok(cases.length > 30);
  for (const c of cases) assert.deepEqual(yamlBlock(c.text), [c.header ?? undefined, c.data], c.name);
});

test("metadata as plain data", () => {
  const meta = Pandoc([], {
    meta: {
      flag: MetaBool(false),
      text: MetaString("10"),
      caption: MetaInlines([Emph("Sales"), Space(), Str("by"), Space(), Str("region")]),
      body: MetaBlocks([Para("a"), Para("b")]),
      list: MetaList([MetaString("1"), MetaBool(true), MetaList()]),
      map: MetaMap({ k: MetaInlines("v"), m: MetaMap({}) }),
    },
  }).meta;
  const want = {
    flag: false, text: "10", caption: "Sales by region", body: "ab", list: ["1", true, []], map: { k: "v", m: {} },
  };
  assert.deepEqual(toData(meta), want);
  assert.deepEqual(toData(MetaMap(meta)), want);
  assert.deepEqual(toData(new Options(meta)), want);
  assert.equal(toData(MetaInlines("a  b")), "a b");
});

test("the options: the document's defaults, the header's values over them", () => {
  const meta = Pandoc([], {
    meta: {
      pantable: { caption: "doc", alignment: "LC", csv: { delimiter: ";", x: "1" } },
      other: { caption: "other's" },
    },
  }).meta;
  const header = Pandoc([], { meta: { caption: [Str("block")], csv: { delimiter: "," } } }).meta;
  // a key's value replaced whole: no deeper merge
  assert.deepEqual(toData(Options.of(meta, "pantable", header)),
                   { caption: "block", alignment: "LC", csv: { delimiter: "," } });
  assert.deepEqual(toData(Options.of(meta, "pantable")), toData(meta["pantable"]!));
  assert.deepEqual(Options.of(meta, "missing", header).values, header);
  // under the name, anything but a map is ignored; a key of Object's is no key
  assert.deepEqual(toData(Options.of(Pandoc([], { meta: { pantable: "x" } }).meta, "pantable")), {});
  assert.deepEqual(toData(Options.of(meta, "toString")), {});
  // the document's own values
  assert.equal(Options.of(meta, "pantable").get("alignment"), (meta["pantable"] as MetaMap).content["alignment"]);
});

test("the options, read", () => {
  const options = new Options(Pandoc([], {
    meta: {
      caption: [Emph("Sales"), Space(), Str("by")], string: "x", blocks: [Para("p")], yes: true,
      width: "0.4", big: "1000", fraction: "1/2", ms: ["1", "2"], csv: { delimiter: ";" },
    },
  }).meta);
  assert.equal(options.get("caption")?.t, "MetaInlines"); // raw: its inlines
  assert.equal(options.get("missing"), undefined);
  assert.equal(options.get("toString"), undefined);
  assert.deepEqual([options.text("caption"), options.text("string"), options.text("blocks")], ["Sales by", "x", "p"]);
  assert.equal(options.boolean("yes"), true);
  assert.deepEqual([options.number("width"), options.number("big"), options.number("fraction")], [0.4, 1000, undefined]);
  assert.deepEqual(options.list("ms"), ["1", "2"]);
  assert.deepEqual(options.map("csv"), { delimiter: ";" });
  // of another shape, or missing: undefined
  for (const read of ["text", "boolean", "number", "list", "map"] as const) {
    assert.equal(options[read]("missing"), undefined);
  }
  assert.equal(options.text("yes"), undefined);
  assert.equal(options.text("ms"), undefined);
  assert.equal(options.boolean("string"), undefined);
  assert.equal(options.number("yes"), undefined);
  assert.equal(options.list("csv"), undefined);
  assert.equal(options.map("ms"), undefined);
});

/** A reader that reads each header as its metadata from `known` (by its
 *  text), or, if missing there, as pandoc reads a YAML scalar: blocks. */
const stub = (known: Record<string, Meta>, calls: string[][]) => (texts: string[]) => {
  calls.push(texts);
  return texts.map((t) => (Object.hasOwn(known, t) ? Pandoc([], { meta: known[t] }) : Pandoc([Para(t)])));
};

test("yamlFilter replaces the blocks, with one read of every header", () => {
  const doc = Pandoc([
    CodeBlock("---\ncaption: one\n---\na,b\n", { classes: ["table"] }),
    Div([CodeBlock("plain,data", { classes: ["table"] })]),
    CodeBlock("---\ncaption: x\n---\nnot ours", { classes: ["other"] }),
    CodeBlock("---\ncaption\n---\nc,d", { classes: ["csv"] }), // a scalar: no header
    CodeBlock("---\n---\ne,f", { classes: ["table", "x"] }),
  ], { meta: { pantable: { caption: "default", alignment: "LC" } } });
  const calls: string[][] = [];
  const read = stub({ "---\ncaption: one\n---\n": { caption: MetaInlines("one") }, "---\n---\n": {} }, calls);
  const seen: unknown[] = [];
  const out = yamlFilter(doc, ["table", "csv"], "pantable", read, (options, data, block) => {
    seen.push([toData(options), data, block.attr.classes[0]]);
    if (data.startsWith("plain")) return [];
    return [Para(options.text("caption") ?? ""), Para(data)];
  });
  assert.equal(out, doc);
  // one call, every header, in the document's order
  assert.deepEqual(calls, [["---\ncaption: one\n---\n", "---\ncaption\n---\n", "---\n---\n"]]);
  const defaults = { caption: "default", alignment: "LC" };
  assert.deepEqual(seen, [
    [{ caption: "one", alignment: "LC" }, "a,b\n", "table"],
    [defaults, "plain,data", "table"],
    [defaults, "---\ncaption\n---\nc,d", "csv"],
    [defaults, "e,f", "table"],
  ]);
  assert.deepEqual(doc.blocks, [
    Para("one"), Para("a,b\n"), Div([]), CodeBlock("---\ncaption: x\n---\nnot ours", { classes: ["other"] }),
    Para("default"), Para("---\ncaption\n---\nc,d"), Para("default"), Para("e,f"),
  ]);
});

test("yamlFilter without headers reads nothing", () => {
  const calls: string[][] = [];
  const doc = Pandoc([CodeBlock("a,b", { classes: ["table"] }), Para("x")]);
  yamlFilter(doc, "table", "pantable", stub({}, calls), () => undefined);
  assert.deepEqual(calls, []);
  assert.deepEqual(doc.blocks, [CodeBlock("a,b", { classes: ["table"] }), Para("x")]);
});

test("yamlFilter with the pandoc command as the reader", (t) => {
  const exe = pandoc(t);
  if (!exe) return;
  const read = (texts: string[]) =>
    texts.map((text) => parse(execFileSync(exe, ["-f", "markdown", "-t", "json"], { input: text }).toString("utf8")));
  const text = '---\ncaption: "*Sales* by region"\nwidth: 0.4\nn: 010\nmarkdown: true\n---\na,b\n';
  const doc = Pandoc([CodeBlock(text, { classes: ["table"] })], { meta: { pantable: { markdown: false } } });
  const got: [Options, string][] = [];
  yamlFilter(doc, "table", "pantable", read, (options, data) => {
    got.push([options, data]);
  });
  assert.equal(got.length, 1);
  const [options, data] = got[0]!;
  assert.equal(data, "a,b\n");
  assert.deepEqual(options.get("caption"), MetaInlines([Emph("Sales"), Space(), Str("by"), Space(), Str("region")]));
  assert.deepEqual([options.text("caption"), options.number("width"), options.text("n")], ["Sales by region", 0.4, "10"]);
  assert.equal(options.boolean("markdown"), true);
});
