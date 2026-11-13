// The fuzzer's TypeScript driver (see fuzz/README.md): tasks on stdin, two
// lines each (a filter spec or `null`, then a document as pandoc wrote it);
// for each, one line on stdout: the document after the spec's filters, as
// JSON, or `!` and the error.

import { readFileSync, writeSync } from "node:fs";
import { applyFilter, BlockQuote, Emph, Para, parse, serialize, Str } from "../ts/src/index.ts";
import type { Block, Context, Filter, Inline, Meta, Pandoc } from "../ts/src/index.ts";

const TEXT = new Set(["Str", "Code", "Math", "RawInline", "CodeBlock", "RawBlock"]);
const UNWRAP = new Set([
  "Emph", "Underline", "Strong", "Strikeout", "Superscript", "Subscript",
  "SmallCaps", "Quoted", "Cite", "Link", "Image", "Span",
  "BlockQuote", "Div", "Figure",
]);
const BLOCKS = new Set([
  "Plain", "Para", "LineBlock", "CodeBlock", "RawBlock", "BlockQuote", "OrderedList",
  "BulletList", "DefinitionList", "Header", "HorizontalRule", "Table", "Figure", "Div", "Block",
]);

interface Rule { do: string; mod?: number; eq?: number }
interface Spec { filters: { traverse: "typewise" | "topdown" | "bottomup"; rules: Record<string, Rule> }[]; log: boolean }
type Fn = (x: any, ctx: Context) => unknown;

function build(spec: Spec): Filter[] {
  const log: string[] = [];
  const called = (rule: Rule, entry: string): boolean => {
    log.push(entry);
    return rule.mod === undefined || log.length % rule.mod === rule.eq;
  };

  const element = (rule: Rule): Fn => (el: Inline | Block, ctx) => {
    const me = log.length + 1;
    const text = TEXT.has(el.t);
    if (!called(rule, text ? `${el.t}:${(el as Str).text}` : el.t)) return undefined;
    const inline = !BLOCKS.has(el.t);
    const wrapped = () => (inline ? Emph([el as Inline]) : BlockQuote([el as Block]));
    const mark = () => { if (text) (el as Str).text += `#${me}`; };
    switch (rule.do) {
      case "keep": return undefined;
      case "delete": return [];
      case "dup": return [el, el];
      case "replace": return inline ? Str("R") : Para([Str("R")]);
      case "wrap": return wrapped();
      case "unwrap": return UNWRAP.has(el.t) ? (el as { content: unknown[] }).content : undefined;
      case "mark": mark(); return undefined;
      case "markret": mark(); return el;
      case "skip": ctx.skipChildren(); return undefined;
      case "wrapskip": ctx.skipChildren(); return wrapped();
      case "markskip": ctx.skipChildren(); mark(); return undefined;
    }
    throw new Error(`unknown action ${rule.do}`);
  };

  const list = (rule: Rule, name: string): Fn => (xs: unknown[], ctx) => {
    if (!called(rule, `${name}${xs.length}`)) return undefined;
    switch (rule.do) {
      case "keep": return undefined;
      case "reverse": return [...xs].reverse();
      case "dropfirst": xs.shift(); return undefined;
      case "append": return [...xs, name === "Inlines" ? Str("A") : Para([Str("A")])];
      case "empty": return [];
      case "skip": ctx.skipChildren(); return undefined;
    }
    throw new Error(`unknown action ${rule.do}`);
  };

  const meta = (rule: Rule): Fn => (m: Meta) => {
    if (!called(rule, "Meta")) return undefined;
    if (rule.do === "add") m.fuzz = { t: "MetaBool", value: true };
    else if (rule.do === "empty") return {};
    return undefined;
  };

  const document = (rule: Rule): Fn => (doc: Pandoc) => {
    if (!called(rule, "Pandoc")) return undefined;
    if (rule.do === "prepend") doc.blocks.unshift(Para([Str("P")]));
    else if (rule.do === "clear") doc.blocks = [];
    return undefined;
  };

  const filters: Filter[] = [];
  for (const f of spec.filters) {
    const filter: Record<string, unknown> = { traverse: f.traverse };
    for (const [name, rule] of Object.entries(f.rules)) {
      filter[name] = name === "Inlines" || name === "Blocks" ? list(rule, name)
        : name === "Meta" ? meta(rule)
        : name === "Pandoc" ? document(rule)
        : element(rule);
    }
    filters.push(filter as Filter);
  }
  if (spec.log) {
    filters.push({ Pandoc: (doc) => { doc.blocks.push(Para([Str(log.join(" "))])); } });
  }
  return filters;
}

const lines = readFileSync(0, "utf8").split("\n");
for (let i = 0; i + 1 < lines.length; i += 2) {
  let out: string;
  try {
    const spec = JSON.parse(lines[i]!) as Spec | null;
    let doc = parse(lines[i + 1]!);
    if (spec !== null) doc = applyFilter(doc, build(spec));
    out = serialize(doc);
  } catch (e) {
    out = "!" + JSON.stringify(e instanceof Error ? `${e.name}: ${e.message}` : String(e));
  }
  writeSync(1, out + "\n");
}
