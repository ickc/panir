// The edges: what a review and fuzzing against pandoc found (fuzz/).

import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  applyFilter, ASTDecodeError, ASTDepthError, ASTTypeError, check, ColSpec, ColWidth, Div, Emph, fromJSON, fromPlain,
  Header, MetaInlines, Pandoc, Para, parse, RawBlock, serialize, Space, Str, stringify, Table, toJSON,
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
    assert.equal(e.where, "blocks[0].content[1]");
    assert.deepEqual(e.path, ["blocks", 0, "content", 1]);
    return true;
  });
  assert.throws(() => serialize(doc), ASTTypeError);
  const sparse = Pandoc();
  sparse.blocks[2] = Para("x");
  assert.throws(() => serialize(sparse), /^ASTTypeError: blocks\[0\]: expected Block, got undefined/);
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
  assert.throws(() => serialize(doc), /^ASTTypeError: meta: expected an object/);
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
    assert.equal(e.where, "blocks[0].content[0].text");
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
  assert.throws(() => serialize(Pandoc([h])), /^ASTTypeError: blocks\[0\]\.level/);
});

test("doubles that are not finite, as pandoc writes them", () => {
  // (a column `width="0*"` in HTML gives null)
  for (const [written, value] of [["null", NaN], [`"+inf"`, Infinity], [`"-inf"`, -Infinity]] as const) {
    const doc = parse(table(written));
    assert.ok(Object.is((doc.blocks[0] as Table).colSpecs[0].width, undefined) === false);
    const width = ((doc.blocks[0] as Table).colSpecs[0].width as ColWidth).width;
    assert.ok(Object.is(width, value), `${written}: ${width}`);
    assert.equal(serialize(doc), table(written));
    assert.equal(serialize(Pandoc([Table(undefined, [ColSpec(undefined, ColWidth(value))])])), table(written));
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
                (e: unknown) => e instanceof ASTDepthError && e instanceof RangeError && /nested/.test(e.message));
});

test("a type's name, not a constructor's", () => {
  assert.deepEqual(fromJSON({ t: "Str", c: "x" }, "Inline"), Str("x"));
  // @ts-expect-error: a constructor's name
  assert.throws(() => fromJSON({ t: "Str", c: "x" }, "Str"), /Str is a constructor of the type Inline/);
  // @ts-expect-error: no such type
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

test("a list function's nodes may hold its list", () => {
  // (the list's contents were replaced in place: the list ended up in itself)
  const doc = applyFilter(Pandoc([Para("a b")]), [
    { Inlines: (xs) => [{ t: "Emph", content: xs }] },
    { Blocks: (bs) => [{ t: "Div", attr: { identifier: "", classes: [], attributes: [] }, content: bs }] },
    exclaim,
  ]);
  assert.equal(serialize(doc), serialize(Pandoc([Div([Para([Emph("a! b!")])])])));
  let wrapped = false; // (top-down, the list in the Emph is walked next: once is enough)
  const top = applyFilter(Pandoc([Para("a b")]), {
    traverse: "topdown",
    Inlines: (xs) => {
      if (wrapped) return undefined;
      wrapped = true;
      return [{ t: "Emph", content: xs }];
    },
    Str: exclaim.Str,
  });
  assert.equal(serialize(top), serialize(Pandoc([Para([Emph("a! b!")])])));
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

// A long list whose nodes are deleted, kept, replaced by one and by several
// (once quadratic: each replacement by none or several moved the rest).
const LONG = 3_000;
type StrNode = ReturnType<typeof Str>;

/** By its number: deleted, kept, replaced by one, by several (an Emph among them), returned twice. Top-down, the Emph's Str is walked too. */
const spliceRule = (s: StrNode): Inline | Inline[] | undefined => {
  if (s.text.startsWith("x")) return Str(s.text.toUpperCase());
  if (!/^\d+$/.test(s.text)) return undefined;
  const k = Number(s.text);
  return [[], undefined, Str(`${k}r`), [Str(`${k}a`), Space(), Emph([Str(`x${k}`)])], [s, s]][k % 5];
};

/** What the walk should make of the k-th Str, written plainly. */
const spliced = (k: number, topDown: boolean): Inline[] => [
  [],
  [Str(`${k}`)],
  [Str(`${k}r`)],
  [Str(`${k}a`), Space(), Emph([Str(`${topDown ? "X" : "x"}${k}`)])],
  [Str(`${k}`), Str(`${k}`)],
][k % 5];

const numbered = (n: number) => Array.from({ length: n }, (_, k) => Str(String(k)));

for (const traverse of ["bottomup", "topdown", "typewise"] as const) {
  test(`a long list spliced, ${traverse}`, () => {
    const topDown = traverse === "topdown";
    const original = numbered(LONG);
    const doc = Pandoc([Para(original)]);
    const content = (doc.blocks[0] as { content: Inline[] }).content;
    const filter = { traverse, Str: spliceRule } as Filter;
    const out = (applyFilter(doc, filter).blocks[0] as { content: Inline[] }).content;
    assert.deepEqual(out, Array.from({ length: LONG }, (_, k) => spliced(k, topDown)).flat());
    assert.equal(out, content); // the same list
    assert.equal(out[0], original[1]); // kept: the node itself
    assert.deepEqual(out[5], original[4]); // twice: two nodes
    assert.deepEqual(out[6], original[4]);
    assert.notEqual(out[5], out[6]);
  });
}

for (const traverse of ["bottomup", "topdown"] as const) {
  test(`a long list spliced, with contexts, ${traverse}`, () => {
    // the context is right after earlier nodes were deleted and multiplied:
    // looked at first halfway
    const topDown = traverse === "topdown";
    const original = numbered(LONG);
    const looked = [0, LONG / 2, LONG / 2 + 1, LONG / 2 + 3, LONG / 2 + 4, LONG - 1];
    const seen = new Map<number, [unknown, unknown, unknown, unknown]>();
    const filter = {
      traverse,
      Str: (s: StrNode, ctx: { index: unknown; prev: unknown; next: unknown; container: unknown }) => {
        if (/^\d+$/.test(s.text) && looked.includes(Number(s.text))) {
          seen.set(Number(s.text), [ctx.index, ctx.prev, ctx.next, ctx.container]);
        }
        return spliceRule(s);
      },
    } as Filter;
    const out = (applyFilter(Pandoc([Para(original)]), filter).blocks[0] as { content: Inline[] }).content;
    assert.deepEqual(out, Array.from({ length: LONG }, (_, k) => spliced(k, topDown)).flat());
    for (const k of looked) {
      const [index, prev, next, container] = seen.get(k)!;
      let before = 0;
      for (let j = 0; j < k; j++) before += spliced(j, topDown).length;
      assert.equal(index, before);
      assert.equal(container, out);
      assert.equal(prev, before ? out[before - 1] : undefined);
      assert.equal(next, k + 1 < LONG ? original[k + 1] : undefined);
    }
  });

  test(`a long list seen from below, and after an error, ${traverse}`, () => {
    // a function below the list sees it whole, through its context
    const n = 2_000;
    const lengths: [string, number, number][] = [];
    const halve = (s: StrNode) => (Number(s.text) % 2 === 0 ? [] : [s, Str(s.text)]);
    const filter = {
      traverse,
      Str: (s: StrNode, ctx: { ancestors: unknown[]; doc: unknown }) => {
        if (!s.text.startsWith("in")) return halve(s);
        const para = ctx.ancestors[ctx.ancestors.length - 2] as { content: Inline[] };
        const doc = ctx.doc as { blocks: { content: Inline[] }[] };
        lengths.push([s.text, para.content.length, doc.blocks[0].content.length]);
        return undefined;
      },
    } as Filter;
    const items = Array.from({ length: n }, (_, k) => (k % 100 === 50 ? Emph([Str(`in${k}`)]) : Str(String(k))));
    const doc = applyFilter(Pandoc([Para(items)]), filter);
    // before the k-th: half of those deleted, half doubled (Emph: one)
    const made = (k: number) => {
      let m = 0;
      for (let j = 0; j < k; j++) m += j % 100 === 50 ? 1 : j % 2 === 0 ? 0 : 2;
      return m;
    };
    const expected: [string, number, number][] = [];
    for (let k = 50; k < n; k += 100) expected.push([`in${k}`, made(k) + n - k, made(k) + n - k]);
    assert.deepEqual(lengths, expected);
    assert.equal((doc.blocks[0] as { content: Inline[] }).content.length, made(n));

    // an error halfway: the list as far as it went, then the rest as it was
    const original = numbered(n);
    const failing = Pandoc([Para(original)]);
    const stop = { traverse, Str: (s: StrNode) => { if (s.text === "1001") throw new Error("stop"); return halve(s); } } as Filter;
    assert.throws(() => applyFilter(failing, stop), /stop/);
    const kept = original.slice(0, 1001).filter((_, k) => k % 2).flatMap((s) => [s, s]);
    assert.deepEqual((failing.blocks[0] as { content: Inline[] }).content, [...kept, ...original.slice(1001)]);
  });
}

/** Whether two lists hold the same nodes (not equal ones), in order. */
const same = (xs: readonly unknown[], ys: readonly unknown[]) =>
  xs.length === ys.length && xs.every((x, k) => x === ys[k]);

/** The nodes of `xs` from `node` (itself, not an equal one) on. */
const fromNode = (xs: readonly unknown[], node: unknown) => {
  const k = xs.indexOf(node);
  assert.ok(k >= 0, "the node is in the list");
  return xs.slice(k);
};

for (const traverse of ["bottomup", "topdown", "typewise"] as const) {
  test(`a list being walked seen from a closure, ${traverse}`, () => {
    // A function deletes or doubles blocks; a later one looks at the document
    // it closed over (not ctx): the list is as it is now, the earlier
    // deletions and doublings made
    const original = [
      RawBlock("html", "del"), Para("p1"), RawBlock("html", "dbl"), Para("p3"),
      RawBlock("html", "del"), Para("p5"), Header(1, "h"),
    ];
    const doc = Pandoc([...original]);
    const seen: string[] = [];
    const blocks = {
      traverse,
      RawBlock: (b: ReturnType<typeof RawBlock>) => (b.text === "del" ? [] : [b, RawBlock("html", "dbl2")]),
      Para: (p: ReturnType<typeof Para>) => {
        seen.push(`${stringify(p)}: ${doc.blocks.map((b) => stringify(b) || (b as { text: string }).text).join(" ")}`);
        assert.ok(same(fromNode(doc.blocks, p), fromNode(original, p)));
      },
    } as Filter;
    applyFilter(doc, blocks);
    assert.deepEqual(seen, [
      "p1: p1 dbl p3 del p5 h", "p3: p1 dbl dbl2 p3 del p5 h", "p5: p1 dbl dbl2 p3 p5 h",
    ]);
    assert.deepEqual(doc.blocks, [
      Para("p1"), RawBlock("html", "dbl"), RawBlock("html", "dbl2"), Para("p3"), Para("p5"), Header(1, "h"),
    ]);

    // one level down: a Para's inlines, seen from a function on them
    const para = Para("a b c d e");
    const inlines = [...para.content];
    const doc2 = Pandoc([para]);
    const texts: string[] = [];
    const inner = {
      traverse,
      Space: () => [],
      Str: (s: StrNode) => {
        texts.push(`${s.text}: ${stringify(para.content)}`);
        assert.ok(same(fromNode(para.content, s), fromNode(inlines, s)));
        assert.equal((doc2.blocks[0] as { content: Inline[] }).content, para.content);
        return s.text === "c" ? [s, Str("C")] : undefined;
      },
    } as Filter;
    applyFilter(doc2, inner);
    assert.deepEqual(texts, ["a: a b c d e", "b: ab c d e", "c: abc d e", "d: abcCd e", "e: abcCde"]);
    assert.deepEqual(doc2.blocks, [Para(["a", "b", "c", "C", "d", "e"].map((t) => Str(t)))]);
  });
}

for (const traverse of ["bottomup", "topdown"] as const) {
  test(`neighbours while a long list is spliced, ${traverse}`, () => {
    // ctx.prev and ctx.next, of the node's context and of contexts kept from
    // earlier nodes, while nodes are deleted and doubled: as the list is now
    // (the nodes made so far, then the rest), which ctx.container then shows
    const n = 3_000;
    const original = numbered(n);
    const made: Inline[] = []; // what the walk made of the nodes so far
    type Ctx = { index: unknown; prev: unknown; next: unknown; container: unknown };
    const kept: Ctx[] = [];
    const check = (ctxs: Ctx[], now: Inline[]) => {
      for (const c of ctxs) {
        const i = c.index as number;
        assert.equal(c.prev, now[i - 1]);
        assert.equal(c.next, now[i + 1]);
      }
    };
    const filter = {
      traverse,
      Str: (s: StrNode, ctx: Ctx) => {
        const k = Number(s.text);
        const now = [...made, ...original.slice(k)];
        assert.equal(ctx.index, made.length);
        check([ctx], now);
        if (k % 100 === 0) check(kept, now);
        if (k === n / 2) assert.ok(same(ctx.container as Inline[], now)); // (made what it is now; in place from then on)
        kept.push(ctx);
        const r = k % 3 === 0 ? [] : k % 3 === 1 ? [s, Str(s.text)] : undefined;
        made.push(...(r ?? [s]));
        return r;
      },
    } as Filter;
    const doc = applyFilter(Pandoc([Para(original)]), filter);
    assert.ok(same((doc.blocks[0] as { content: Inline[] }).content, made));
    check(kept, made);
  });
}

for (const traverse of ["bottomup", "topdown", "typewise"] as const) {
  test(`a list being walked changed from a closure, ${traverse}`, () => {
    // after an earlier block was deleted and one doubled, a function changes
    // the document it closed over (not ctx.container): what it changed, it
    // changed in the list as it is
    type Block = ReturnType<typeof Para> | ReturnType<typeof RawBlock> | ReturnType<typeof Header>;
    const text = (b: Block) => stringify(b) || (b as { text: string }).text;
    const find = (blocks: Block[], t: string) => blocks.findIndex((b) => stringify(b) === t);
    const run = (change: (blocks: Block[], at: string) => void) => {
      const doc = Pandoc([
        RawBlock("html", "del"), Para("a"), RawBlock("html", "dbl"), Header(1, "One"), Para("x"), Header(1, "Two"),
      ]);
      const calls: string[] = [];
      applyFilter(doc, {
        traverse,
        RawBlock: (b: ReturnType<typeof RawBlock>) => (b.text === "del" ? [] : [b, RawBlock("html", "dbl2")]),
        Header: (h: ReturnType<typeof Header>) => {
          calls.push(stringify(h));
          change(doc.blocks as Block[], stringify(h));
        },
      } as Filter);
      assert.deepEqual(calls, ["One", "Two"]);
      return (doc.blocks as Block[]).map(text);
    };
    // before it, after it, at the end
    assert.deepEqual(run((blocks, at) => {
      if (at !== "One") return;
      blocks.unshift(Para("TOC"));
      blocks.splice(find(blocks, "a"), 1);
      blocks.splice(find(blocks, "x"), 1);
      blocks.push(Para("end"));
    }), ["TOC", "dbl", "dbl2", "One", "Two", "end"]);
    // a node replaced through the closure
    assert.deepEqual(run((blocks, at) => {
      if (at === "One") blocks[find(blocks, "a")] = Para("A");
    }), ["A", "dbl", "dbl2", "One", "x", "Two"]);
  });
}

test("a context kept from an earlier pass shows the list as it is", () => {
  const kept: { doc: unknown }[] = [];
  const seen: string[][] = [];
  applyFilter(Pandoc([RawBlock("html", "x"), Para("a"), RawBlock("html", "y"), Para("b")]), {
    // typewise: the inlines, then the blocks
    Str: (_s, ctx) => { kept.push(ctx); },
    RawBlock: () => [],
    Para: () => { seen.push((kept[0].doc as { blocks: { t: string }[] }).blocks.map((b) => b.t)); },
  });
  assert.deepEqual(seen, [["Para", "RawBlock", "Para"], ["Para", "Para"]]);
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
  const cases: [() => unknown, RegExp][] = [
    [() => applyFilter(Pandoc(), { Pandoc: ((d: Pandoc) => [d, d]) as never }),
     /^the document: expected one Pandoc \(this position holds one\), got \[.*\n {2}returned by filter function Pandoc, for the Pandoc at the document$/],
    [() => applyFilter(Pandoc([Para("a")]), { Str: (async () => undefined) as never }),
     /^blocks\[0\]\.content\[0\]: expected Inline, a list of them, or nothing, got \{\} \(a promise: filter functions can't be async\)\n/],
    [() => applyFilter(Pandoc([], { meta: { title: MetaInlines("t") } }), { MetaValue: (() => []) as never }),
     /^meta\.title: expected one MetaValue \(this position holds one\), got \[\]\n {2}returned by filter function MetaValue, for the MetaInlines at meta\.title$/],
    [() => applyFilter(Pandoc([Para("a")]), { Inlines: (() => 42) as never }),
     /^blocks\[0\]\.content: expected a list of Inlines, got 42\n {2}returned by filter function Inlines, for the Inlines at blocks\[0\]\.content$/],
  ];
  for (const [f, message] of cases) {
    assert.throws(f, (e: unknown) => e instanceof ASTTypeError && message.test(e.message));
  }
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
