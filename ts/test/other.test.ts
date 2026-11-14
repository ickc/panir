// Functions for nodes that are neither inlines nor blocks (MetaValue, Cell,
// Row, Attr, Caption, Citation), which pandoc's Lua has none of: in each
// order, each is called where it is, typewise after the blocks and before
// the metadata, and replaces, splices and deletes. And a test for each
// mutation the review's mutation testing found surviving.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  applyFilter, ASTDecodeError, ASTTypeError, Attr, Caption, Cell, check, Cite, Citation, fromJSON, Math, MetaBool,
  MetaInlines, MetaList, MetaString, Pandoc, Para, Quoted, RawInline, Row, serialize, Str, stringify, Table, TableBody,
  TableHead, toJSON, toPlain, walk,
} from "../src/index.ts";
import type { Filter, Quoted as QuotedNode, Table as TableNode } from "../src/index.ts";

const doc = () => Pandoc([
  Para(Cite("c", [Citation("k1"), Citation("k2")])),
  Table(Caption("cap"), [], TableHead([Row([Cell("h1"), Cell("h2")])]), [TableBody([Row([Cell("b1")])])]),
], { meta: { list: MetaList(["x", "y"]), s: "z" } });

const ORDERS = ["typewise", "bottomup", "topdown"] as const;

const CALLS: Record<(typeof ORDERS)[number], string[]> = {
  typewise: [
    "Table blocks[1]",
    "MetaValue meta.list.content[0]", "MetaValue meta.list.content[1]", "MetaValue meta.list", "MetaValue meta.s",
    "Citation blocks[0].content[0].citations[0]", "Citation blocks[0].content[0].citations[1]",
    "Caption blocks[1].caption",
    "Cell blocks[1].head.rows[0].cells[0]", "Cell blocks[1].head.rows[0].cells[1]", "Row blocks[1].head.rows[0]",
    "Cell blocks[1].bodies[0].body[0].cells[0]", "Row blocks[1].bodies[0].body[0]",
    "Meta meta", "Pandoc the document",
  ],
  bottomup: [
    "MetaValue meta.list.content[0]", "MetaValue meta.list.content[1]", "MetaValue meta.list", "MetaValue meta.s",
    "Meta meta",
    "Citation blocks[0].content[0].citations[0]", "Citation blocks[0].content[0].citations[1]",
    "Caption blocks[1].caption",
    "Cell blocks[1].head.rows[0].cells[0]", "Cell blocks[1].head.rows[0].cells[1]", "Row blocks[1].head.rows[0]",
    "Cell blocks[1].bodies[0].body[0].cells[0]", "Row blocks[1].bodies[0].body[0]",
    "Table blocks[1]", "Pandoc the document",
  ],
  topdown: [
    "Pandoc the document", "Meta meta",
    "MetaValue meta.list", "MetaValue meta.list.content[0]", "MetaValue meta.list.content[1]", "MetaValue meta.s",
    "Citation blocks[0].content[0].citations[0]", "Citation blocks[0].content[0].citations[1]",
    "Table blocks[1]", "Caption blocks[1].caption",
    "Row blocks[1].head.rows[0]", "Cell blocks[1].head.rows[0].cells[0]", "Cell blocks[1].head.rows[0].cells[1]",
    "Row blocks[1].bodies[0].body[0]", "Cell blocks[1].bodies[0].body[0].cells[0]",
  ],
};

for (const traverse of ORDERS) {
  test(`functions for other nodes are called where they are, ${traverse}`, () => {
    const seen: string[] = [];
    const attrs: string[] = [];
    const note = (name: string) => (_x: unknown, ctx: { where: string }) => { seen.push(`${name} ${ctx.where}`); };
    applyFilter(doc(), {
      traverse, MetaValue: note("MetaValue"), Cell: note("Cell"), Row: note("Row"), Caption: note("Caption"),
      Citation: note("Citation"), Table: note("Table"), Meta: note("Meta"), Pandoc: note("Pandoc"),
      Attr: (_a, ctx) => { attrs.push(ctx.where); },
    } as Filter);
    assert.deepEqual(seen, CALLS[traverse]);
    // an Attr in each node that has one: the table's, its head's, rows' and cells', its body's, its foot's
    assert.deepEqual([...attrs].sort(), [
      "blocks[1].attr", "blocks[1].bodies[0].attr", "blocks[1].bodies[0].body[0].attr",
      "blocks[1].bodies[0].body[0].cells[0].attr", "blocks[1].foot.attr", "blocks[1].head.attr",
      "blocks[1].head.rows[0].attr", "blocks[1].head.rows[0].cells[0].attr", "blocks[1].head.rows[0].cells[1].attr",
    ]);
  });

  test(`functions for other nodes replace, splice and delete, ${traverse}`, () => {
    const out = applyFilter(doc(), {
      traverse,
      // in lists: deleted, doubled, replaced by one and by several
      Cell: (c) => (stringify(c) === "h2" ? [] : stringify(c) === "b1" ? [c, c] : undefined),
      Row: (r) => (stringify(r).startsWith("h") ? Row([...r.cells, Cell("added")], { identifier: "r" }) : undefined),
      Citation: (c) => (c.id === "k1" ? [Citation("a"), Citation("b")] : undefined),
      MetaValue: (m) => (m.t === "MetaString" && m.text === "x" ? [] : m.t === "MetaString" && m.text === "z" ? MetaBool(true) : undefined),
      // alone
      Caption: () => Caption("new caption"),
      Attr: (a) => (a.identifier === "" ? Attr("id") : undefined),
    } as Filter);
    check(out);
    const table = out.blocks[1] as TableNode;
    // (top-down, the new row's cells are walked: h2 is deleted from it)
    assert.deepEqual(table.head.rows[0].cells.map(stringify), ["h1", "added"]);
    assert.equal(table.head.rows[0].attr.identifier, "r");
    assert.deepEqual(table.bodies[0].body[0].cells.map(stringify), ["b1", "b1"]);
    assert.notEqual(table.bodies[0].body[0].cells[0], table.bodies[0].body[0].cells[1]);
    assert.deepEqual(table.caption, Caption("new caption"));
    assert.equal(table.attr.identifier, "id");
    const cite = (out.blocks[0] as { content: { citations: { id: string }[] }[] }).content[0];
    assert.deepEqual(cite.citations.map((c) => c.id), ["a", "b", "k2"]);
    assert.deepEqual(out.meta["list"], MetaList(["y"]));
    assert.deepEqual(out.meta["s"], MetaBool(true));
  });
}

test("a wrong replacement of a node found alone says where", () => {
  const cases: [Filter, string][] = [
    [{ Caption: function cap() { return Cell("x") as never; } },
     "blocks[1].caption: expected a Caption, got {\"attr\":{\"identifier\":\"\",\"classes\":[],\"attributes\":[]},\"a...\n" +
     "  returned by filter function cap, for the Caption at blocks[1].caption"],
    [{ MetaValue: function meta() { return Str("x") as never; } },
     "meta.list.content[0]: expected MetaValue, got Str (an Inline)\n" +
     "  returned by filter function meta, for the MetaString at meta.list.content[0]"],
    [{ Row: function row() { return [Row(), Cell("c")] as never; } },
     "blocks[1].head.rows[1]: expected a Row, got {\"attr\":{\"identifier\":\"\",\"classes\":[],\"attributes\":[]},\"a...\n" +
     "  returned by filter function row, for the Row at blocks[1].head.rows[0]"],
  ];
  for (const [filter, message] of cases) {
    assert.throws(() => applyFilter(doc(), filter), (e: unknown) => {
      assert.ok(e instanceof ASTTypeError);
      assert.equal(e.message, message);
      return true;
    });
  }
});

// -- what mutations survived ---------------------------------------------------------

test("walk's topDown is the order", () => {
  for (const topDown of [false, true]) {
    const order: string[] = [];
    walk(Para(Quoted("DoubleQuote", "q")), (n) => { order.push("t" in n ? n.t : "?"); }, { topDown });
    assert.deepEqual(order, topDown ? ["Para", "Quoted", "Str"] : ["Str", "Quoted", "Para"]);
  }
});

test("check checks, everything", () => {
  check(doc());
  const bad = doc();
  ((bad.blocks[1] as TableNode).bodies[0].body[0].cells[0].content[0] as { content: unknown[] }).content.push(Para("x"));
  assert.throws(() => check(bad), /^ASTTypeError: blocks\[1\]\.bodies\[0\]\.body\[0\]\.cells\[0\]\.content\[0\]\.content\[1\]: expected Inline/);
  assert.throws(() => check(Str("x"), "Block"), ASTTypeError);
  check(Str("x"), "Inline");
});

test("an enum takes only its values", () => {
  assert.throws(() => Math("Inline" as never, "x"), /expected a MathType, got "Inline"/);
  const q = Quoted("DoubleQuote", "q");
  (q as { quoteType: string }).quoteType = "TripleQuote";
  assert.throws(() => toJSON(q, "Inline"), /^ASTTypeError: quoteType: expected a QuoteType, got "TripleQuote"$/);
  assert.throws(() => fromJSON({ t: "Quoted", c: [{ t: "TripleQuote" }, []] }, "Inline"), ASTDecodeError);
  assert.throws(() => fromJSON({ t: "Quoted", c: ["DoubleQuote", []] }, "Inline"), ASTDecodeError);
  assert.equal((fromJSON({ t: "Quoted", c: [{ t: "SingleQuote" }, []] }, "Inline") as QuotedNode).quoteType, "SingleQuote");
});

test("toPlain of each kind, stringify of a raw <br>", () => {
  assert.equal(toPlain(MetaInlines(["a", RawInline("html", "<br>"), "b"])), "a b");
  assert.equal(toPlain(MetaInlines(["a", RawInline("latex", "<br>"), "b"])), "ab");
  assert.equal(toPlain(MetaInlines(["a", RawInline("html", "<b>"), "b"])), "ab");
  assert.equal(toPlain(MetaString("s")), "s");
  assert.equal(toPlain(MetaBool(false)), false);
  assert.deepEqual(serialize(Pandoc([], { meta: { k: toPlain(MetaList(["a"])) as string[] } })),
                   serialize(Pandoc([], { meta: { k: MetaList([MetaString("a")]) } })));
});
