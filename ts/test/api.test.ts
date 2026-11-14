// The API as pandoc's Lua has it: constructors' order and conversions, what
// a filter function may return, errors, fragments; and the types saying
// the same (the `@ts-expect-error` lines must be errors: tsc -p
// tsconfig.test.json fails on an unused one).

import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  applyFilter, ASTDecodeError, ASTDepthError, ASTError, ASTTypeError, Attr, BulletList, Caption, Cell, check, Cite,
  Citation, Code, CodeBlock, Div, Emph, fromJSON, Header, LineBlock, Link, MetaBool, MetaInlines, MetaList,
  MetaString, Note, Pandoc, Para, parse, Plain, Row, Space, Str, Strong, Table, TableBody, TableHead,
  toJSON, walk,
} from "../src/index.ts";
import type {
  ASTNode, Block, Context, Filter, Inline, JSONValue, Meta, MetaValue, Traverse, WalkedContext,
} from "../src/index.ts";
import { pandoc } from "./pandoc.ts";

const words = (...ws: string[]): Inline[] => ws.flatMap((w, i) => (i ? [Space(), Str(w)] : [Str(w)]));

// -- constructors ---------------------------------------------------------------

test("constructors convert as pandoc's Lua", () => {
  // (each checked with `pandoc lua`: pandoc 3.14)
  assert.deepEqual(Para(Str("a")).content, [Str("a")]);
  assert.deepEqual(Div(Str("a")).content, [Plain([Str("a")])]);
  assert.deepEqual(Div([[Str("a"), "b c"]]).content, [Plain([Str("a"), Str("b c")])]);
  assert.deepEqual(BulletList([Para("a"), "b c", Str("s")]).content,
                   [[Para("a")], [Plain(words("b", "c"))], [Plain([Str("s")])]]);
  assert.deepEqual(BulletList(Para("a")).content, [[Para("a")]]);
  assert.deepEqual(BulletList("x").content, [[Plain([Str("x")])]]);
  assert.deepEqual(LineBlock(["a b", Str("c")]).content, [words("a", "b"), [Str("c")]]);
  assert.deepEqual(Header(1, "x", { identifier: "id" }).attr, Attr("id"));
  for (const bad of [42, true, Para("p")]) {
    assert.throws(() => Para(bad as never), ASTTypeError);
  }
});

test("constructors take their arguments in the order of Lua's", () => {
  // pandoc.Cite(content, citations)
  const cite = Cite("a b", [Citation("k", "NormalCitation")]);
  assert.deepEqual(cite.content, words("a", "b"));
  assert.equal(cite.citations[0].id, "k");
  // pandoc.Citation(id, mode, prefix, suffix, note_num, hash)
  assert.deepEqual(Citation("k", "AuthorInText", "p", "s", 3, 4),
                   { id: "k", prefix: [Str("p")], suffix: [Str("s")], mode: "AuthorInText", noteNum: 3, hash: 4 });
  // pandoc.TableBody(body, head, row_head_columns, attr)
  const body = TableBody([Row(), Row()], [Row()], 2, { identifier: "b" });
  assert.deepEqual([body.body.length, body.head.length, body.rowHeadColumns, body.attr.identifier], [2, 1, 2, "b"]);
  // pandoc.Table(caption, colspecs, head, bodies, foot, attr)
  const table = Table(Caption("c"), [], TableHead([Row([Cell("h")])]), [body], undefined, { identifier: "t" });
  assert.deepEqual([table.caption.content, table.head.rows.length, table.bodies.length, table.attr.identifier],
                   [[Plain([Str("c")])], 1, 1, "t"]);
});

test("constructors take read-only arrays, and copy them", () => {
  const xs: readonly Inline[] = [Str("a")];
  const classes: readonly string[] = ["c"];
  const pairs: readonly (readonly [string, string])[] = [["k", "v"]];
  const para = Para(xs);
  assert.notEqual(para.content, xs);
  assert.deepEqual(Div(xs, { classes, attributes: pairs }).attr, Attr("", ["c"], [["k", "v"]]));
  const rows: readonly Row[] = [Row()];
  assert.equal(TableHead(rows).rows.length, 1);
});

test("the types refuse what the constructors refuse", () => {
  const url = "u";
  const attr = Attr();
  const cases: (() => unknown)[] = [
    // @ts-expect-error: a link needs a url (or a target)
    () => Link("x"),
    // @ts-expect-error: attributes whole or by their fields
    () => Header(1, "x", { identifier: "i", attr }),
    // @ts-expect-error: a target whole or by its fields
    () => Link("x", { url, target: { url, title: "" } }),
    // @ts-expect-error: no such option
    () => Div("x", { id: "i" }),
    // @ts-expect-error: a number isn't inlines
    () => Para(42),
    // @ts-expect-error: a block isn't an inline
    () => Para([Para("p")]),
  ];
  for (const f of cases) assert.throws(f, ASTTypeError);
  assert.equal(Link("x", { url, title: "t" }).target.title, "t");
  assert.equal(Link("x", { target: { url, title: "" } }).target.url, "u");
});

// -- errors -------------------------------------------------------------------------

test("one kind of error for a wrong value, wherever it is found", () => {
  const typeError = (f: () => unknown, where: string, path: (string | number)[]) =>
    assert.throws(f, (e: unknown) => {
      assert.ok(e instanceof ASTTypeError && e instanceof TypeError && e instanceof ASTError, String(e));
      assert.equal(e.name, "ASTTypeError");
      assert.equal(e.where, where);
      assert.deepEqual(e.path, path);
      return true;
    });
  // an argument, an option, a value in a document (as the decoder says where)
  typeError(() => Para([Str("a"), 1 as never]), "Para.content[1]", ["content", 1]);
  typeError(() => Header(1, "x", { identifier: 2 as never }), "Attr.identifier", ["identifier"]);
  typeError(() => Code("x", { bogus: 1 } as never), "Code", ["bogus"]);
  const doc = Pandoc(Para("a"));
  (doc.blocks[0] as { content: unknown[] }).content[0] = 1;
  typeError(() => check(doc), "blocks[0].content[0]", ["blocks", 0, "content", 0]);
  typeError(() => toJSON(doc), "blocks[0].content[0]", ["blocks", 0, "content", 0]);
  // what a filter function returns
  typeError(() => applyFilter(Pandoc(Para("a")), { Str: () => 1 as never }), "blocks[0].content[0]", ["blocks", 0, "content", 0]);
  typeError(() => applyFilter(Pandoc(), { Meta: () => [] as never }), "meta", ["meta"]);
  typeError(() => applyFilter(Pandoc(), { Pandoc: () => Para("x") as never }), "the document", []);
  // reading: ASTDecodeError, an ASTError but not a TypeError
  assert.throws(() => fromJSON({ t: "Str", c: 1 }, "Inline"), (e: unknown) =>
    e instanceof ASTDecodeError && e instanceof ASTError && !(e instanceof TypeError) && e.where === "text");
  // too deep: ASTDepthError, an ASTError and a RangeError
  const depth = new ASTDepthError("walk");
  assert.ok(depth instanceof ASTError && depth instanceof RangeError && !(depth instanceof TypeError));
  assert.ok(!(new TypeError("x") instanceof ASTError));
  assert.ok(!(new RangeError("x") instanceof ASTError));
});

test("a wrong return names the function and where it was called", () => {
  const cases: [Filter | Filter<"topdown">, string][] = [
    [{ Str: function upper() { return Para("x") as never; } },
     "blocks[0].content[0].content[0]: expected Inline, got Para (a Block)\n  returned by filter function upper, for the Str at blocks[0].content[0].content[0]"],
    [{ traverse: "topdown", Emph: function two() { return [Str("x"), Para("y")] as never; } },
     "blocks[0].content[0].content[2]: expected Inline, got Para (a Block)\n  returned by filter function two, for the Emph at blocks[0].content[0].content[1]"],
    [{ Attr: function attrs() { return [] as never; } },
     "blocks[0].attr: expected one Attr (this position holds one), got []\n  returned by filter function attrs, for the Attr at blocks[0].attr"],
    [{ Blocks: function lists() { return [Para("a"), 7] as never; } },
     "blocks[0].content[1]: expected Block, got 7\n  returned by filter function lists, for the Blocks at blocks[0].content"],
    [{ Meta: function meta() { return { k: () => 1 } as never; } },
     "meta.k: expected MetaValue, got a function (k)\n  returned by filter function meta, for the Meta at meta"],
  ];
  for (const [filter, message] of cases) {
    const doc = Pandoc(Div(Para(["a", Emph("b")])));
    assert.throws(() => applyFilter(doc, filter), (e: unknown) => {
      assert.ok(e instanceof ASTTypeError);
      assert.equal(e.message, message);
      return true;
    });
  }
});

// -- what filter functions return -----------------------------------------------

/** Lua filters, and the same in TypeScript: pandoc 3.14 decides. */
const RETURNS: [string, string, Filter][] = [
  [
    "a b\n\nc d\n\n    i\n\ntext\n\n    j\n",
    `function Str(s) if s.text == "a" then return "x y" elseif s.text == "b" then return {"p q", pandoc.Emph("e")} end end
     function Para(p) if pandoc.utils.stringify(p):match("^c") then return "block text" end end
     function CodeBlock(c)
       if c.text == "i" then return pandoc.Emph("inl")
       elseif c.text == "j" then return {pandoc.Str("s1"), {pandoc.Str("s2"), "s3 s4"}, "s5 s6"} end
     end`,
    {
      Str: (s) => (s.text === "a" ? "x y" : s.text === "b" ? ["p q", Emph("e")] : undefined),
      Para: (p) => (p.content[0]?.t === "Str" && p.content[0].text.startsWith("c") ? "block text" : undefined),
      CodeBlock: (c) => (c.text === "i" ? Emph("inl") : c.text === "j" ? [Str("s1"), [Str("s2"), "s3 s4"], "s5 s6"] : undefined),
    },
  ],
  [
    "---\ntitle: t\n---\n\nw\n",
    `function Inlines(xs) if #xs == 1 then return "one two" end end
     function Blocks(bs) if #bs == 1 then return pandoc.Str("lone") end end
     function Meta(m) return {draft=true, n=3, s="x y", l={"a", "b"}, il={pandoc.Str("z")}, m={k=false}} end`,
    {
      Inlines: (xs) => (xs.length === 1 ? "one two" : undefined),
      Blocks: (bs) => (bs.length === 1 ? Str("lone") : undefined),
      Meta: () => ({ draft: true, n: 3, s: "x y", l: ["a", "b"], il: [Str("z")], m: { k: false } }),
    },
  ],
];

for (const [i, [markdown, lua, filter]] of RETURNS.entries()) {
  test(`what a function returns converts as in pandoc's Lua (${i})`, (t) => {
    const exe = pandoc(t);
    if (!exe) return;
    const dir = execFileSync("mktemp", ["-d"]).toString().trim();
    const script = `${dir}/f.lua`;
    execFileSync("sh", ["-c", `cat > "${script}"`], { input: lua });
    const run = (args: string[]) => execFileSync(exe, ["-f", "markdown", "-t", "json", ...args], { input: markdown }).toString();
    const want = JSON.parse(run(["--lua-filter", script]));
    assert.deepEqual(toJSON(applyFilter(parse(run([])), filter)), want);
  });
}

test("what a function returns converts as in pandoc's Lua (without pandoc)", () => {
  // the same as above, written out
  const doc = applyFilter(Pandoc([Para(["a", " ", "b"].map((x) => (x === " " ? Space() : Str(x))))]), RETURNS[0][2]);
  assert.deepEqual(doc.blocks, [Para([...words("x", "y"), Space(), Str("p q"), Emph("e")])]);
  const blocks = applyFilter(Pandoc([Para("c d"), CodeBlock("i"), CodeBlock("j")]), RETURNS[0][2]).blocks;
  assert.deepEqual(blocks, [
    Plain(words("block", "text")), Plain([Emph("inl")]),
    Plain([Str("s1")]), Plain([Str("s2"), Str("s3 s4")]), Plain(words("s5", "s6")),
  ]);
  const meta = applyFilter(Pandoc(Para("w"), { meta: { title: "t" } }), RETURNS[1][2]);
  assert.deepEqual(meta.blocks, [Plain([Str("lone")])]);
  assert.deepEqual(meta.meta["l"], MetaList([MetaString("a"), MetaString("b")]));
  assert.deepEqual(meta.meta["il"], MetaInlines([Str("z")]));
  assert.equal(Object.getPrototypeOf(meta.meta), null);
});

test("the document and metadata functions' replacements are used, in every order", () => {
  for (const traverse of ["typewise", "bottomup", "topdown"] as const) {
    const out = applyFilter(Pandoc(Para("old"), { meta: { a: "x" } }), {
      traverse,
      Meta: () => ({ b: true }),
      Pandoc: (d) => Pandoc([...d.blocks, Para("new")], { meta: d.meta }),
    });
    assert.deepEqual(out.blocks, [Para("old"), Para("new")], traverse);
    assert.deepEqual(Object.keys(out.meta), ["b"], traverse);
    assert.deepEqual(out.meta["b"], MetaBool(true));
  }
});

// -- the types of filters ---------------------------------------------------------

test("filter functions' contexts and results are typed", () => {
  const seen: string[] = [];
  const f: Filter = {
    Str: (s, ctx) => {
      const next: Inline | undefined = ctx.next;
      const prev: Inline | undefined = ctx.prev;
      const parent: ASTNode | undefined = ctx.parent;
      const doc: Pandoc | undefined = ctx.doc;
      const ancestors: ASTNode[] = ctx.ancestors;
      // @ts-expect-error: an inline's neighbours are inlines
      const block: Block | undefined = ctx.next;
      seen.push(`${s.text}:${next?.t}:${prev?.t}:${parent && "t" in parent ? parent.t : "-"}:${doc?.blocks.length}:${ancestors.length}:${block}`);
    },
    Cell: (c, ctx) => {
      const next: Cell | undefined = ctx.next;
      seen.push(`cell:${c.content.length}:${next === undefined}`);
    },
    // @ts-expect-error: one Attr goes where an Attr is
    Attr: () => [],
    // @ts-expect-error: an inline's function returns inlines
    Emph: () => Para("x"),
    // @ts-expect-error: the document's returns a document
    Pandoc: (d) => [d],
    // @ts-expect-error: a list of inlines' returns inlines
    Inlines: () => 42,
  };
  const { Attr: _a, Emph: _e, Pandoc: _p, Inlines: _i, ...right } = f;
  applyFilter(Pandoc([Para(["a", "b"]), Table(undefined, [], undefined, [TableBody([Row([Cell("c")])])])]), right);
  assert.deepEqual(seen, ["a:Str:undefined:Para:2:2:[object Object]", "b:undefined:Str:Para:2:2:undefined", "c:undefined:undefined:Plain:2:6:undefined", "cell:1:true"]);
});

test("a traverse chosen at run time keeps the functions' types", () => {
  for (const traverse of ["typewise", "topdown", "bottomup"] as Traverse[]) {
    const f: Filter = { traverse, Str: (s, ctx) => (ctx.index === 0 ? Str(s.text.toUpperCase()) : undefined) };
    assert.deepEqual(applyFilter(Pandoc(Para("a b")), f).blocks, [Para(words("A", "b"))]);
    // and written in place
    const g = applyFilter(Pandoc(Para("a")), { traverse, Str: (s) => `${s.text}!` });
    assert.deepEqual(g.blocks, [Para("a!")]);
  }
  // a list of filters, some written in place
  const top: Filter<"topdown"> = { traverse: "topdown", Emph: (_e, ctx) => { ctx.skipChildren(); } };
  const any: Filter = { traverse: "typewise" as Traverse };
  applyFilter(Pandoc(), [top, any, { traverse: "topdown", Div: (_d, ctx) => ctx.skipChildren() }, { Str: (s) => s }]);
});

test("reading and writing are typed", () => {
  const doc: Pandoc = fromJSON(toJSON(Pandoc(Para("a"))));
  const inline: Inline = fromJSON({ t: "Str", c: "x" }, "Inline");
  const meta: MetaValue = fromJSON({ t: "MetaBool", c: true }, "MetaValue");
  const json: JSONValue = toJSON(Str("x"), "Inline");
  const unknown: unknown = { t: "Str", text: "x" };
  check(unknown, "Inline");
  const checked: Inline = unknown; // (narrowed by check)
  const walked: Para = walk(Para("a"), () => undefined);
  const list: Inline[] = applyFilter([Str("a")], {});
  assert.deepEqual([doc.blocks.length, inline, meta, json, checked, walked.t, list.length],
                   [1, Str("x"), MetaBool(true), { t: "Str", c: "x" }, Str("x"), "Para", 1]);
  type Ctx = WalkedContext<Inline>;
  const ctxNext: Ctx["next"] = undefined;
  const topCtx: Context<Block>["skipChildren"] | undefined = undefined;
  assert.equal(ctxNext ?? topCtx, undefined);
  void (null as unknown as Meta);
});

// -- fragments ----------------------------------------------------------------------

test("a filter applies to a node: what is in it, not the node", () => {
  const seen: string[] = [];
  const emph = Emph(["a", Strong("b")]);
  const out = applyFilter(emph, {
    Emph: () => { seen.push("Emph"); },
    Strong: (s, ctx) => { seen.push(`Strong at ${ctx.where}, doc ${ctx.doc}, parent ${(ctx.parent as Emph).t}`); },
    Str: (s, ctx) => { seen.push(`${s.text} at ${ctx.where}`); return s.text.toUpperCase(); },
    Inlines: (xs) => { seen.push(`Inlines${xs.length}`); },
    Meta: () => { seen.push("Meta"); },
    Pandoc: () => { seen.push("Pandoc"); },
  });
  assert.equal(out, emph); // in place
  assert.deepEqual(out, Emph(["A", Strong("B")]));
  assert.deepEqual(seen, [
    // (typewise: the inlines, Strong among them, then the lists of them)
    "a at Emph.content[0]", "b at Emph.content[1].content[0]", "Strong at Emph.content[1], doc undefined, parent Emph",
    "Inlines1", "Inlines2",
  ]);
  // each order; and products
  for (const traverse of ["typewise", "bottomup", "topdown"] as const) {
    const cell = Cell([Para("x"), Div("y")]);
    applyFilter(cell, { traverse, Para: () => [], Str: (s) => `${s.text}!` });
    assert.deepEqual(cell.content, [Div("y!")], traverse);
  }
});

test("a filter applies to a list: its list function too", () => {
  for (const traverse of ["typewise", "bottomup", "topdown"] as const) {
    const xs: Inline[] = [Str("a"), Emph("b")];
    const out = applyFilter(xs, { traverse, Inlines: (ys) => [...ys, Str("!")] });
    assert.equal(out, xs);
    // (top-down, the list is walked after its function: its new Str is in it)
    assert.deepEqual(xs, [Str("a"), Emph(["b", "!"]), Str("!")], traverse);
    // a list function's result made of the list itself
    const bs: Block[] = [Para("p")];
    let wrapped = false; // (top-down, the list in the Div is walked next: once is enough)
    applyFilter(bs, {
      traverse,
      Blocks: (ys) => {
        if (wrapped) return undefined;
        wrapped = true;
        return [{ t: "Div", attr: Attr(), content: ys }];
      },
    });
    assert.deepEqual(bs, [Div(Para("p"))], traverse);
  }
  const where: string[] = [];
  applyFilter([Para("a")], { Str: (_s, ctx) => { where.push(ctx.where); } });
  assert.deepEqual(where, ["blocks[0].content[0]"]);
  // an empty list is neither: nothing is called
  assert.deepEqual(applyFilter([], { Inlines: () => [Str("x")] }), []);
  // what isn't a fragment
  assert.throws(() => applyFilter([Str("a"), Para("b")] as never, {}),
                /^ASTTypeError: inlines\[1\]: expected Inline, got Para \(a Block\)$/);
  assert.throws(() => applyFilter(42 as never, {}), /expected a document, a node, or a list of inlines or of blocks, got 42/);
  assert.throws(() => applyFilter([MetaBool(true)] as never, {}), ASTTypeError);
});

test("walk: every node, the target itself too", () => {
  const seen: string[] = [];
  const para = Para(["a", Emph("b")]);
  const out = walk(para, (node, ctx, type) => {
    seen.push(`${type}:${"t" in node ? node.t : "?"}:${ctx.where}`);
    if ("t" in node && node.t === "Str") return node.text.toUpperCase();
    if ("t" in node && node.t === "Para") return Plain(node.content);
  });
  assert.deepEqual(out, Plain(["A", Emph("B")]));
  assert.deepEqual(seen, [
    "Inline:Str:Para.content[0]", "Inline:Str:Para.content[1].content[0]", "Inline:Emph:Para.content[1]", "Block:Para:Para",
  ]);
  // top-down: a node before what is in it
  const order: string[] = [];
  walk(Pandoc(Para(Emph("x"))), (node) => { order.push("t" in node ? node.t : "Pandoc"); }, { topDown: true });
  assert.deepEqual(order, ["Pandoc", "Para", "Emph", "Str"]);
  const bottom: string[] = [];
  walk(Pandoc(Para(Emph("x"))), (node) => { bottom.push("t" in node ? node.t : "Pandoc"); });
  assert.deepEqual(bottom, ["Str", "Emph", "Para", "Pandoc"]);
  // a list: each
  const list = [Str("a"), Str("b")];
  walk(list, (node) => ("t" in node && node.t === "Str" && node.text === "a" ? [] : undefined));
  assert.deepEqual(list, [Str("b")]);
  // products too, with their type's name
  const types = new Set<string>();
  walk(Pandoc(Table(Caption("c"), [], undefined, [TableBody([Row([Cell("x")])])]), { meta: { k: "v" } }),
       (_node, _ctx, type) => { types.add(type); });
  assert.deepEqual([...types].sort(), [
    "Attr", "Block", "Caption", "Cell", "ColWidth", "Inline", "MetaValue", "Pandoc", "Row", "TableBody", "TableFoot",
    "TableHead",
  ].filter((t) => t !== "ColWidth"));
  void Note;
});
