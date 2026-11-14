/**
 * pandoc's document AST, generated from pandoc-types.
 *
 * Nodes are plain objects with named fields, discriminated by `t`:
 * `{ t: "Header", level: 1, attr: {...}, content: [...] }`. `parse` and
 * `serialize` read and write pandoc's JSON; filters are objects of
 * functions by node type, as in pandoc's Lua filters.
 */

import { ASTDecodeError, ASTDepthError, fromJSON, isStackOverflow, toJSON } from "./core.ts";
import type { Pandoc } from "./generated.ts";

export * from "./generated.ts";
export {
  ASTDecodeError, ASTDepthError, ASTError, ASTTypeError, check, formatPath, fromJSON, toJSON,
} from "./core.ts";
export type { JSONValue, Path } from "./core.ts";
export { applyFilter, Context, walk } from "./walk.ts";
export type {
  AnyFilter, Filter, FilterFunctions, Traverse, Walkable, WalkAction, WalkedContext,
} from "./walk.ts";
export { Conversion } from "./conversion.ts";
export type { ConversionInit } from "./conversion.ts";
export { blocks, copy, equal, fromPlain, inlines, stringify, toPlain } from "./util.ts";

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
