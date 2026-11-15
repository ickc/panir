// The shared filter corpus: each corpus/filters/NAME.lua, written here as a
// TypeScript filter, must make of its input what pandoc's Lua filter made.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import {
  applyFilter, ASTTypeError, blocksOf, checkUnique, copy, Div, Emph, fromJSON, fromPlain, getAttribute, given, Header,
  HorizontalRule, inlinesOf, items, lookup, MetaBool, MetaInlines, MetaList, MetaString, Pandoc as makePandoc, Para,
  RawBlock, RawInline, removeAttribute, setAttribute, Space, Str, stringify, Strong, toJSON, toRomanNumeral, truthy,
  Underline,
} from "../src/index.ts";
import type {
  AnyFilter, Block, Filter, MetaBool as MetaBoolNode, MetaInlines as MetaInlinesNode, MetaList as MetaListNode,
  MetaString as MetaStringNode, MetaValue, Pandoc, Str as StrNode, WalkedContext,
} from "../src/index.ts";

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
      return makePandoc([Header(1, copy(title.content)), Para("made anew")], { meta: doc.meta });
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

  fails: () => {
    let n = 0;
    return [{
      Para: (p) => {
        if (++n === 2) throw new Error("the second paragraph");
        p.content.push(Str("!"));
      },
    }];
  },

  helperfails: () => {
    const size = (config: MetaValue | undefined) => {
      if (config?.t !== "MetaMap") throw new Error(`indent: a map of options, not ${config?.t}`);
      return config.content.size;
    };
    return [{
      Meta: (meta) => {
        const s = size(meta.indent);
        if (s !== undefined) meta.size = s;
      },
    }];
  },

  replaced: () => {
    const seen: string[] = [];
    return [{
      traverse: "topdown",
      Emph: (e) => { seen.push("Emph"); return e.content; },
      Div: (d) => { seen.push("Div"); return d.content; },
      Strong: (s) => {
        const text = stringify(s);
        seen.push(`Strong:${text}`);
        if (text === "other") return Underline([Strong([Str("in")]), Emph([Str("e")])]);
        if (text === "two") return [Strong([Str("s")]), Underline([Strong([Str("u")])])];
        return undefined;
      },
      Underline: () => { seen.push("Underline"); },
      Str: (s) => { seen.push(`Str:${s.text}`); },
      Inlines: (xs) => {
        const first = xs[0];
        if (first?.t !== "Str" || first.text !== "list") return undefined;
        seen.push("Inlines");
        return [Emph([Str("new")]), Strong([Emph([Str("g")])])];
      },
    }, listing(seen)];
  },

  metavalues: () => {
    const seen: string[] = [];
    const functions = {
      MetaInlines: (v: MetaInlinesNode) => {
        const text = stringify(v);
        seen.push(`MetaInlines:${text}`);
        if (text === "drop") return [];
        if (text === "two") return [MetaString("two-a"), MetaString("two-b")];
        return undefined;
      },
      MetaBlocks: () => { seen.push("MetaBlocks"); },
      MetaList: (v: MetaListNode) => { seen.push(`MetaList:${v.content.length}`); },
      MetaMap: () => { seen.push("MetaMap"); },
      MetaString: (v: MetaStringNode) => { seen.push(`MetaString:${v.text}`); },
      MetaBool: (v: MetaBoolNode) => { seen.push("MetaBool"); return MetaString(String(v.value)); },
      Str: (s: StrNode) => { seen.push(`Str:${s.text}`); },
    };
    return [functions, { traverse: "topdown", ...functions }, listing(seen)];
  },

  metadelete: () => [{
    MetaBool: () => [],
  }],

  truthy: () => [{
    Pandoc: (doc) => {
      const keys = ["yes", "no", "text", "empty", "null", "noword", "zero", "list", "map", "block", "missing"];
      const words = keys.map((k) => `${k}=${truthy(doc.meta[k])}`);
      words.push(`or=${keys.map((k) => stringify(given(doc.meta[k]) ?? MetaString("d"))).join(",")}`);
      doc.blocks.push(Para(words.join(" ")));
    },
  }],

  lookup: () => [{
    Pandoc: (doc) => {
      const paths = [
        ["toc"], ["toc", "depth"], ["toc", "style", "name"], ["toc", "missing"], ["missing", "depth"],
        ["flag", "depth"], ["list", "a"], ["text", "map"], ["toc", "depth", "x"], ["toc", "style", "name", "x"],
      ] as const;
      const words = paths.map(([key, ...keys]) => {
        const v = lookup(doc.meta, key, ...keys);
        return `${[key, ...keys].join(".")}=${v === undefined ? "none" : stringify(v)}`;
      });
      for (const [key, first, ...rest] of paths) { // from a value
        if (first === undefined) continue;
        const v = lookup(doc.meta[key], first, ...rest);
        words.push(`${key}:${[first, ...rest].join(".")}=${v === undefined ? "none" : stringify(v)}`);
      }
      doc.blocks.push(Para(words.join(" ")));
    },
  }],

  prepend: () => {
    const mark = RawInline("tex", "\\raw{}");
    return [{
      Meta: (meta) => {
        for (const k of ["one", "list", "map", "off", "missing"]) {
          meta[k] = fromPlain([RawBlock("html", `<${k}>`), ...items(meta[k])]);
        }
      },
      Str: (s) => (s.text === "raw" ? copy(mark) : undefined), // (one node, returned twice: a copy each)
    }];
  },

  items: () => [{
    Pandoc: (doc) => {
      const keys = ["one", "list", "empty", "map", "nested", "off", "inlines", "missing"];
      const words = keys.map((k) => {
        const texts = items(doc.meta[k]).map((x) => stringify(x));
        return `${k}=${texts.length}:${texts.join(",")}`;
      });
      doc.blocks.push(Para(words.join(" ")));
    },
  }],

  chain: () => {
    let mark = "?";
    return [
      { Meta: (m) => { mark = stringify(truthy(m.mark) ? m.mark : "!"); } },
      { Str: (s) => Str(s.text + mark) },
    ];
  },

  inlinesof: () => [{
    Pandoc: (doc) => {
      const keys = [
        "text", "blocks", "on", "map", "emptymap", "null", "empty", "list",
        "nulls", "mixed", "lists", "listblocks", "listbool", "emptylist",
      ];
      const orFails = <T>(f: () => T[], fails: T): T[] => {
        try {
          return f();
        } catch (e) {
          if (e instanceof ASTTypeError) return [fails];
          throw e;
        }
      };
      for (const k of keys) {
        const v = doc.meta[k]!;
        doc.blocks.push(Para([Str(`${k}:`), Space(), ...orFails(() => inlinesOf(v), Str("fails"))]));
        doc.blocks.push(Div(orFails(() => blocksOf(v), Para("fails")), { identifier: k }));
      }
    },
  }],

  roman: () => [{
    Pandoc: (doc) => {
      const numbers = [0, 1, 4, 9, 14, 40, 90, 400, 1994, 2026, 3999, 4000, 1000000, -1];
      doc.blocks.push(Para(numbers.map((n) => `${n}=${toRomanNumeral(n)}`).join(" ")));
    },
  }],

  attributes: () => [{
    Span: (s) => {
      const k = getAttribute(s.attr, "k");
      if (k !== undefined) {
        setAttribute(s.attr, "first", k);
        setAttribute(s.attr, "k", `${k}!`);
      }
      removeAttribute(s.attr, "drop");
      if (s.attr.classes.includes("mark")) s.attr.classes.push("seen");
    },
  }],
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
  // (a scenario pandoc fails on, output null: the filter must fail too)
  const run = (filters: readonly Filter[]) => {
    const doc = fromJSON(input) as Pandoc;
    if (output === null) {
      assert.throws(() => applyFilter(doc, filters, FORMAT));
      return null;
    }
    const out = applyFilter(doc, filters, FORMAT);
    checkUnique(out); // (Lua's elements are values: no node in two places)
    return toJSON(out);
  };
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
