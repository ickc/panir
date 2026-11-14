// The shared filter corpus: each corpus/filters/NAME.lua, written here as a
// TypeScript filter, must make of its input what pandoc's Lua filter made.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import {
  applyFilter, Div, Emph, fromJSON, Header, HorizontalRule, MetaBool, MetaInlines, MetaList, MetaString,
  Pandoc as makePandoc, Para, Str, Strong, toJSON,
} from "../src/index.ts";
import type { AnyFilter, Block, Filter, Pandoc, WalkedContext } from "../src/index.ts";

const FORMAT = "json";

type Walked = WalkedContext;
/** A filter that isn't top-down: its functions take a `WalkedContext`. */
type WalkedFilter = Exclude<Filter, { traverse: "topdown" }>;

/** Functions noting each call (as the Lua scenarios do), and the notes. */
function noting(): [WalkedFilter, string[]] {
  const seen: string[] = [];
  const note = (name: string) => (el: object) => {
    seen.push("text" in el ? `${name}:${String(el.text)}` : name);
  };
  const filter: WalkedFilter = {
    Str: note("Str"), Emph: note("Emph"), Strong: note("Strong"), Note: note("Note"), Link: note("Link"),
    Code: note("Code"), Para: note("Para"), Header: note("Header"), Div: note("Div"),
    Table: note("Table"), Figure: note("Figure"), BlockQuote: note("BlockQuote"),
    Inlines: (xs) => { seen.push(`Inlines${xs.length}`); },
    Blocks: (xs) => { seen.push(`Blocks${xs.length}`); },
    Meta: () => { seen.push("Meta"); },
    Pandoc: () => { seen.push("Pandoc"); },
  };
  return [filter, seen];
}

/** A last filter, adding a paragraph listing the notes. */
const listing = (seen: string[]): Filter => ({
  Pandoc: (doc) => { doc.blocks.push(Para(seen.join(" "))); },
});

/** Each scenario as TypeScript filters, run one after the other. */
const SCENARIOS: Record<string, () => AnyFilter[]> = {
  upper: () => [{
    Str: (s) => Str(s.text.toUpperCase()),
  }],

  modify: () => [{
    Header: (h) => { h.level += 1; },
    Link: (l) => { l.target.url = `https://example.org/${l.target.url}`; },
    Image: (i) => { i.target.url = `img/${i.target.url}`; i.attr.attributes.push(["loading", "lazy"]); },
    CodeBlock: (c) => { c.attr.classes.push("numbered"); },
  }],

  splice: () => [{
    Note: () => [],
    Emph: () => [],
    Strong: (s) => s.content,
    Div: (d) => d.content,
    HorizontalRule: () => [Para([Str("one")]), Para([Str("two")])],
  }],

  generic: () => [{
    Str: (s) => Str(`${s.text}!`),
    Inline: (i) => {
      if (i.t === "Str") return Str("never");
      if (i.t === "Code") return Str(i.text);
    },
    Block: (b) => {
      if (b.t === "CodeBlock") return Para([Str(b.text)]);
    },
  }],

  // typewise.lua notes fewer kinds: note only those
  typewise: () => {
    const [filter, seen] = noting();
    const { Strong: _, ...rest } = filter;
    return [rest, listing(seen)];
  },

  topdown: () => {
    const [filter, seen] = noting();
    const { Strong: _, ...rest } = filter;
    return [{ ...rest, traverse: "topdown" }, listing(seen)];
  },

  bottomup: () => {
    const [filter, seen] = noting();
    const { Pandoc: _, ...rest } = filter;
    return [{
      ...rest,
      traverse: "bottomup",
      Pandoc: (doc) => {
        seen.push("Pandoc");
        doc.blocks.push(Para(seen.join(" ")));
      },
    }];
  },

  skip: () => [{
    traverse: "topdown",
    Str: (s) => Str(s.text.toUpperCase()),
    Header: (h) => Para(h.content),
    Div: (_d, ctx) => { ctx.skipChildren(); },
    BlockQuote: (q, ctx) => { ctx.skipChildren(); return Div(q.content); },
    Emph: (_e, ctx) => { ctx.skipChildren(); },
  }],

  toplists: () => [{
    traverse: "topdown",
    Inlines: (xs, ctx) => {
      if (xs.length === 1) {
        ctx.skipChildren();
        return;
      }
      return [...xs].reverse();
    },
    Str: (s) => Str(s.text.toUpperCase()),
  }],

  once: () => [{
    Str: (s) => { if (s.text === "Some") return Emph([Str("new")]); },
    Emph: (e) => Strong(e.content),
  }],

  lists: () => [{
    Inlines: (xs) => [...xs.filter((x) => x.t !== "Space"), Str(`<${xs.length}>`)],
    Blocks: (bs) => bs.flatMap((b): Block[] => (b.t === "Header" ? [b, HorizontalRule()] : [b])),
  }],

  meta: () => [{
    Meta: (m, ctx: Walked) => {
      m.draft = MetaBool(true);
      m.format = MetaString(ctx.format ?? "");
      m.tags = MetaList([MetaString("a"), MetaInlines([Str("b")])]);
      delete m.count;
    },
    Pandoc: (doc) => {
      const draft = doc.meta.draft;
      doc.blocks.unshift(Para([Str(draft?.t === "MetaBool" && draft.value ? "draft" : "final")]));
    },
  }],

  newdoc: () => [{
    traverse: "topdown",
    Pandoc: (doc) => {
      const title = doc.meta.title;
      assert.ok(title?.t === "MetaInlines");
      return makePandoc([Header(1, title.content), Para("made anew")], { meta: doc.meta });
    },
    Str: (s) => Str(s.text.toUpperCase()),
  }],

  twice: () => [{
    Code: (c) => [c, c],
    Para: (p) => {
      const code = p.content.find((x) => x.t === "Code");
      if (code) code.text = "changed";
    },
  }],

  metacount: () => {
    let n = 0;
    return [
      { Meta: (m) => { m.added = MetaInlines([Str("new")]); } },
      {
        Str: (s) => Str(`${s.text}${++n}`),
        Meta: (m) => { m.calls = MetaString(String(++n)); },
        Pandoc: (doc) => { doc.blocks.push(Para(`calls: ${++n}`)); },
      },
    ];
  },
};

const lines = readFileSync(new URL("../../corpus/filters.jsonl", import.meta.url), "utf8")
  .split("\n").filter((l) => l)
  .map((l) => JSON.parse(l) as { name: string; input: unknown; output: unknown; also: "bottomup"[] });

test("every scenario in corpus/filters has a TypeScript filter, and every filter a scenario", () => {
  const lua = readdirSync(new URL("../../corpus/filters/", import.meta.url))
    .filter((f) => f.endsWith(".lua")).map((f) => f.slice(0, -4)).sort();
  assert.deepEqual(lines.map((l) => l.name).sort(), lua, "corpus/filters.jsonl is stale: run scripts/filters-corpus.sh");
  assert.deepEqual(Object.keys(SCENARIOS).sort(), lua);
});

for (const { name, input, output, also } of lines) {
  const scenario = SCENARIOS[name];
  const run = (filters: readonly Filter[]) => toJSON(applyFilter(fromJSON(input) as Pandoc, filters, FORMAT));
  test(`as pandoc's Lua: ${name}`, () => {
    assert.ok(scenario, `no TypeScript filter for corpus/filters/${name}.lua`);
    assert.deepEqual(run(scenario()), output);
  });
  for (const traverse of also) {
    test(`as pandoc's Lua: ${name}, ${traverse}`, () => {
      assert.ok(scenario);
      const filters = scenario().map((f): Filter => {
        assert.ok(f.traverse === undefined, `${name} is marked stateless but sets traverse`);
        return { ...(f as WalkedFilter), traverse };
      });
      assert.deepEqual(run(filters), output);
    });
  }
}
