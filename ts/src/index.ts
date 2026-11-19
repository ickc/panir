/**
 * pandoc's document AST, generated from pandoc-types.
 *
 * Nodes are plain objects with named fields, discriminated by `t`:
 * `{ t: "Header", level: 1, attr: {...}, content: [...] }`. `parse` and
 * `serialize` read and write pandoc's JSON; filters are objects of
 * functions by node type, as in pandoc's Lua filters.
 */

import { Conversion } from "./conversion.ts";
import { ASTDecodeError, ASTDepthError, fromJSON, isStackOverflow, toJSON } from "./core.ts";
import type { Pandoc } from "./generated.ts";
import { applyFilter } from "./walk.ts";
import type { AnyFilter, Filter, FilterFunctions, Traverse } from "./walk.ts";

export * from "./generated.ts";
export {
  ASTDecodeError, ASTDepthError, ASTError, ASTTypeError, check, formatPath, fromJSON, toJSON,
} from "./core.ts";
export type { JSONValue, Path } from "./core.ts";
export { applyFilter, checkUnique, Context, walk } from "./walk.ts";
export type {
  AnyFilter, Filter, FilterFunctions, ParentNode, Traverse, Walkable, WalkAction, WalkedContext,
} from "./walk.ts";
export { Conversion } from "./conversion.ts";
export type { ConversionInit } from "./conversion.ts";
export {
  blocks, copy, equal, fromPlain, getAttribute, hasAttribute, inlines, removeAttribute, setAttribute, stringify, toPlain,
  given, items, lookup, truthy, inlinesOf, blocksOf, toRomanNumeral, luaNumber,
} from "./util.ts";
export type { PlainValue } from "./util.ts";
export { Options, toData, yamlBlock, yamlFilter } from "./options.ts";
export type { DataValue } from "./options.ts";

/** A document from pandoc's JSON text, checked (`ASTDecodeError`; a `SyntaxError` if it isn't JSON). */
export function parse(text: string): Pandoc {
  let json: unknown;
  try {
    json = JSON.parse(text);
  } catch (e) {
    // (the engine's JSON.parse recurses too)
    if (isStackOverflow(e)) throw new ASTDecodeError([], "JSON nested less deeply", "JSON deeper than the engine's call stack lets panir go");
    throw e;
  }
  return fromJSON(json);
}

/** A document as pandoc's JSON text, checked (`ASTTypeError`). */
export function serialize(doc: Pandoc): string {
  const json = toJSON(doc);
  try {
    return JSON.stringify(json);
  } catch (e) {
    // (the engine's JSON.stringify recurses too)
    if (isStackOverflow(e)) throw new ASTDepthError("serialize");
    throw e;
  }
}

/**
 * A filter function of libpandoc's JavaScript host (libpandoc.wasm's
 * `convertWithFilters(options, input, [fn])`, `{ raw }` or not): it takes
 * the document as pandoc's JSON (parsed, or its text), libpandoc's context
 * (likewise), and the instance it runs in, and returns the new document in
 * the form it was given.
 */
export type LibpandocFilter<P = unknown> = (doc: unknown, context: unknown, pandoc: P) => unknown;

/** A filter written in place, run on a document: its context's `doc` is a `Pandoc`. */
type OnDocument<T extends Traverse> = FilterFunctions<T, Pandoc> & { traverse?: T };

/**
 * A panir filter (or filters, in turn) as a filter function of libpandoc's
 * JavaScript host: `runFilter`'s twin, for a conversion in process. Its
 * functions get the conversion libpandoc's context describes
 * (`Conversion.fromContext`), with `options` if given (the conversion's,
 * which the context doesn't carry: what `convertWithFilters` was given).
 * To reach the instance, to read fragments as the conversion reads
 * (`pandoc.read(text, ctx.conversion)`), give a function of it that returns
 * the filters, a list:
 *
 *     const pandoc = await load(module);  // libpandoc's wasm/node.mjs
 *     const cells = (p: typeof pandoc) => [{ CodeBlock: (c, ctx) => fromJSON(p.read(c.text, ctx.conversion)).blocks }];
 *     pandoc.convertWithFilters(options, input, [libpandocFilter(cells, options)]);
 *
 * panir doesn't depend on libpandoc: `pandoc` is whatever the host passes.
 */
export function libpandocFilter<T extends Traverse = "typewise">(
  filter: OnDocument<T>, options?: Record<string, unknown>): LibpandocFilter;
export function libpandocFilter(filters: readonly AnyFilter[], options?: Record<string, unknown>): LibpandocFilter;
export function libpandocFilter(filters: readonly Filter[], options?: Record<string, unknown>): LibpandocFilter;
export function libpandocFilter<P>(
  make: (pandoc: P) => readonly AnyFilter[], options?: Record<string, unknown>): LibpandocFilter<P>;
export function libpandocFilter(filter: object, options?: Record<string, unknown>): LibpandocFilter {
  return (doc, context, pandoc) => {
    const f: object = typeof filter === "function" ? (filter as (pandoc: unknown) => object)(pandoc) : filter;
    const filters = (Array.isArray(f) ? f : [f]) as readonly Filter[];
    const ctx = typeof context === "string" ? JSON.parse(context) as unknown : context;
    const conversion = Conversion.fromContext(
      ctx !== null && typeof ctx === "object" ? ctx as Record<string, unknown> : {}, options);
    if (typeof doc === "string") return serialize(applyFilter(parse(doc), filters, conversion));
    return toJSON(applyFilter(fromJSON(doc), filters, conversion));
  };
}
