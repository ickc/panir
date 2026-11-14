/** Helpers: the text of nodes, metadata to and from plain values, copying and comparing. */

import {
  ASTTypeError, deep, describe, isNodeOf, ownerOf, productOf, resolve, setKey, tables, textInlines, toMetaValue,
} from "./core.ts";
import type { TypeExpr } from "./core.ts";
import type { Block, Inline, Meta, MetaValue, MetaValueInit } from "./generated.ts";

/**
 * A string's words and spaces, as pandoc's Lua `pandoc.Inlines`: `Str` for
 * each word, `Space` between, `SoftBreak` for a newline. Constructors
 * convert a string this way themselves (`Para("hello world")`); this is
 * for building lists.
 */
export function inlines(text: string): Inline[] {
  return textInlines(text) as Inline[];
}

/** A string as blocks, as pandoc's Lua `pandoc.Blocks`: one `Plain` of its
 * words and spaces. (To parse markup, use libpandoc.) */
export function blocks(text: string): Block[] {
  return [{ t: "Plain", content: inlines(text) }];
}

const QUOTES: Record<string, [string, string]> = {
  SingleQuote: ["‘", "’"],
  DoubleQuote: ["“", "”"],
};

/** Whether an object is metadata (a map of `MetaValue`s), not a node or a document. */
function isMetaMap(obj: Record<string, unknown>): boolean {
  for (const k of Object.keys(obj)) if (ownerOf(obj[k]) !== "MetaValue") return false;
  return true;
}

/** The keys of a map in the order of pandoc's Map: by code point. */
export function sortedKeys(obj: object): string[] {
  return Object.keys(obj).sort(byCodePoint);
}

function byCodePoint(a: string, b: string): number {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const x = a.charCodeAt(i);
    const y = b.charCodeAt(i);
    if (x === y) continue;
    // UTF-16 puts the surrogates, which make the code points above U+FFFF,
    // below U+E000..U+FFFF
    const sx = x >= 0xd800 && x <= 0xdfff;
    const sy = y >= 0xd800 && y <= 0xdfff;
    return sx === sy ? x - y : sx ? 1 : -1;
  }
  return a.length - b.length;
}

/**
 * The text of a node, a list of nodes, metadata or a document, without
 * markup, exactly as pandoc's Lua `pandoc.utils.stringify`: spaces and
 * breaks become " ", quotes curly quotes, notes and citations' data are
 * dropped, code and math keep their text. Metadata: a map's values in the
 * order of their keys, a `MetaBool` `"true"` or `"false"`; but a whole
 * document is the text of its inlines only (in the metadata too: not its
 * `MetaString` and `MetaBool` values).
 */
export function stringify(value: unknown): string {
  const v = value as { t?: unknown; blocks?: unknown; meta?: unknown } | null;
  const doc = typeof v === "object" && v !== null && typeof v.t !== "string" && Array.isArray(v.blocks)
    && typeof v.meta === "object" && v.meta !== null;
  const inlinesOnly = doc; // (pandoc's Shared.stringify, which stringify is of a document)
  // a loop, not recursion: any depth. `todo` holds what is left, the next
  // last; a string in it is text to add
  const out: string[] = [];
  const todo: unknown[] = [value];
  while (todo.length) {
    const x = todo.pop();
    // (plain values are found only where given: a node's fields that hold
    // no nodes, as an Attr's, are left out)
    if (typeof x === "string" || typeof x === "number") {
      out.push(String(x));
      continue;
    }
    if (typeof x === "boolean") {
      out.push(x ? "true" : "false");
      continue;
    }
    if (Array.isArray(x)) {
      later(todo, x);
      continue;
    }
    if (typeof x !== "object" || x === null) continue;
    const node = x as Record<string, unknown>;
    const t = node.t;
    if (typeof t !== "string") {
      // metadata, or another map (as Lua takes a table: metadata); a
      // product, or a document: its fields, in order
      const product = isMetaMap(node) ? undefined : productOf(node);
      later(todo, product ? fields(node, product) : sortedKeys(node).map((k) => node[k]));
      continue;
    }
    switch (t) {
      case "Str":
      case "Code":
      case "Math":
        out.push(node.text as string);
        continue;
      case "Space":
      case "SoftBreak":
      case "LineBreak":
        out.push(" ");
        continue;
      case "RawInline":
        if (node.format === "html" && String(node.text).startsWith("<br")) out.push(" ");
        continue;
      case "Note":
        continue;
      case "Cite":
        todo.push(node.content);
        continue;
      case "Quoted": {
        const [l, r] = QUOTES[node.quoteType as string] ?? ["", ""];
        later(todo, [l, node.content, r]);
        continue;
      }
      case "MetaString":
        if (!inlinesOnly) out.push(node.text as string);
        continue;
      case "MetaBool":
        if (!inlinesOnly) out.push(node.value ? "true" : "false");
        continue;
    }
    later(todo, fields(node, t));
  }
  return out.join("");
}

/** Push `xs` to be done next, in their order. */
function later(todo: unknown[], xs: unknown[]): void {
  for (let i = xs.length - 1; i >= 0; i--) todo.push(xs[i]);
}

/**
 * The values of a node's fields that hold nodes, in pandoc's order (`name`:
 * its constructor or product); a map's values in the order of its keys.
 */
function fields(node: Record<string, unknown>, name: string): unknown[] {
  const decl = tables().nodes.get(name);
  const out: unknown[] = [];
  if (decl === undefined) return out;
  for (const f of decl.fields) {
    const v = node[f.name];
    if (typeof v !== "object" || v === null || !holdsNodes(f.type)) continue;
    if (!Array.isArray(v) && "map" in resolve(f.type)) {
      for (const k of sortedKeys(v)) out.push((v as Record<string, unknown>)[k]);
    } else {
      out.push(v);
    }
  }
  return out;
}

/** Whether a value of a type can hold a node of a sum (an inline, say): not an Attr's. */
function holdsNodes(ty: TypeExpr): boolean {
  const r = resolve(ty);
  if ("prim" in r) return false;
  if ("list" in r) return holdsNodes(r.list);
  if ("maybe" in r) return holdsNodes(r.maybe);
  if ("map" in r) return holdsNodes(r.map[1]);
  if ("tuple" in r) return r.tuple.some(holdsNodes);
  const decl = tables().types.get(r.ref)!;
  if (decl.kind === "sum") return true;
  if (decl.kind !== "product") return false;
  let known = holding.get(r.ref);
  if (known === undefined) {
    holding.set(r.ref, false); // (while it is found out: a product in itself)
    known = decl.fields.some((f) => holdsNodes(f.type));
    holding.set(r.ref, known);
  }
  return known;
}

const holding = new Map<string, boolean>();

/** Metadata as plain values: string, boolean, array, object. `MetaInlines` and `MetaBlocks` become their text (`stringify`). */
export function toPlain(value: MetaValue | Meta): unknown {
  return deep("toPlain", () => plain(value));
}

function plain(value: unknown): unknown {
  const v = value as MetaValue;
  switch (ownerOf(v) === "MetaValue" ? v.t : undefined) {
    case "MetaMap":
      return plain((v as { content: unknown }).content);
    case "MetaList":
      return (v as { content: unknown[] }).content.map(plain);
    case "MetaBool":
      return (v as { value: boolean }).value;
    case "MetaString":
      return (v as { text: string }).text;
    case "MetaInlines":
    case "MetaBlocks":
      return stringify((v as { content: unknown }).content);
  }
  if (typeof value === "object" && value !== null && !Array.isArray(value) && isMetaMap(value as Record<string, unknown>)) {
    const out: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(value)) setKey(out, k, plain(x));
    return out;
  }
  throw new ASTTypeError("", "metadata (a MetaValue, or a map of them)", describe(value));
}

/**
 * A value as metadata, as pandoc's Lua converts it (and constructors do,
 * where metadata goes): a string is a `MetaString`, a boolean a `MetaBool`,
 * a number a `MetaString` of it, an inline or a list of inlines
 * `MetaInlines`, a block or a list of blocks `MetaBlocks`, another list a
 * `MetaList`, an object a `MetaMap`; a `MetaValue` is itself. Anything else
 * is an `ASTTypeError`.
 */
export function fromPlain(value: MetaValueInit): MetaValue {
  return deep("fromPlain", () => {
    const v = toMetaValue(value);
    if (!isNodeOf("MetaValue", v)) throw new ASTTypeError("", "what converts to metadata", describe(value));
    // (what is in it, too: a function in a list, say)
    check(v);
    return v as MetaValue;
  });
}

/** Refuse what `toMetaValue` left as it was, inside a `MetaValue` it made. */
function check(v: unknown): void {
  const node = v as { t: string; content?: unknown };
  if (node.t === "MetaList") {
    for (const x of node.content as unknown[]) {
      if (!isNodeOf("MetaValue", x)) throw new ASTTypeError("", "what converts to metadata", describe(x));
      check(x);
    }
  } else if (node.t === "MetaMap") {
    const content = node.content as Record<string, unknown>;
    for (const k of Object.keys(content)) {
      if (!isNodeOf("MetaValue", content[k])) throw new ASTTypeError(k, "what converts to metadata", describe(content[k]), [k]);
      check(content[k]);
    }
  }
}

/**
 * A node, a list of them, metadata or a document copied, with everything in
 * it: a node in a second place should be a copy. Unlike `structuredClone`,
 * maps keep having no prototype.
 */
export function copy<T>(value: T): T {
  return deep("copy", () => copyOf(value));
}

function copyOf<T>(v: T): T {
  if (Array.isArray(v)) return v.map(copyOf) as T;
  if (typeof v !== "object" || v === null) return v;
  const out = Object.create(Object.getPrototypeOf(v)) as Record<string, unknown>;
  for (const k of Object.keys(v)) setKey(out, k, copyOf((v as Record<string, unknown>)[k]));
  return out as T;
}

/**
 * Whether two values of the AST are the same: the same nodes with the same
 * fields, whatever their maps' prototypes or keys' order (a `NaN` width is
 * a `NaN` width).
 */
export function equal(a: unknown, b: unknown): boolean {
  return deep("equal", () => same(a, b));
}

function same(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) {
    return typeof a === "number" && typeof b === "number" && a !== a && b !== b; // NaN
  }
  if (Array.isArray(a)) {
    if (!Array.isArray(b) || a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (!same(a[i], b[i])) return false;
    return true;
  }
  if (Array.isArray(b)) return false;
  const x = a as Record<string, unknown>;
  const y = b as Record<string, unknown>;
  const keys = Object.keys(x);
  if (keys.length !== Object.keys(y).length) return false;
  for (const k of keys) if (!Object.hasOwn(y, k) || !same(x[k], y[k])) return false;
  return true;
}
