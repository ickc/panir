/**
 * Walking a document, and filters: functions by node type, run as pandoc
 * runs Lua filters.
 *
 *     const upper: Filter = {
 *       Str: (s) => { s.text = s.text.toUpperCase(); },
 *     };
 *     applyFilter(doc, upper);
 *
 * Generic over the schema, like `core.ts`.
 */

import {
  ASTTypeError, deep, describe, formatPath, isNodeOf, isStackOverflow, nameOf, productOf, returnedNodes,
  resolve, tables, toMeta,
} from "./core.ts";
import type { Path, TypeExpr } from "./core.ts";
import { Conversion } from "./conversion.ts";
import { copy, sortedKeys } from "./util.ts";
import { SCHEMA } from "./schema.ts";
import type {
  ASTNode, Block, Blocks, FilterTypes, Inline, Inlines, Meta, MetaValueInit, Pandoc,
} from "./generated.ts";

/** Whether a context's function asked to skip the node's children. */
let skipped: (ctx: Context) => boolean;

/**
 * Where a node is: its parent, its place in it, the document, the
 * conversion. `S` is the type of the nodes beside it in a list (`next`,
 * `prev`, `container`): `Inline` for an inline's function.
 */
export class Context<S = unknown> {
  /** That field's name (the field of `parent` that holds this node). */
  field: string | undefined;
  /** This node's index in `container` (or its key in the map). */
  index: number | string | undefined;
  /** The pandoc conversion the filter runs in. */
  conversion: Conversion;
  /** Its output format's name (what pandoc passes a JSON filter), e.g. "html5". */
  format: string | undefined;
  // the walk's frames down to this node, and where each was then: the
  // walk goes on and changes them, and a context may be kept
  #frames: Frame[];
  #indexes: (number | string | undefined)[];
  #topDown: boolean;
  #skip = false;
  #parent: unknown;
  #container: unknown[] | Record<string, unknown> | undefined;
  #doc: unknown;
  #root: string;

  static {
    skipped = (ctx) => ctx.#skip;
  }

  constructor(frames: Frame[], doc: unknown, conversion: Conversion, topDown = false, root = "") {
    const n = frames.length;
    const copy = new Array<Frame>(n);
    const indexes = new Array<number | string | undefined>(n);
    for (let i = 0; i < n; i++) {
      copy[i] = frames[i];
      indexes[i] = frames[i].index;
    }
    this.#frames = copy;
    this.#indexes = indexes;
    this.#topDown = topDown;
    this.#root = root;
    const last = n ? frames[n - 1] : undefined;
    this.#parent = last?.parent;
    this.field = last?.field;
    this.#container = last?.container;
    this.index = last?.index;
    this.#doc = doc;
    this.conversion = conversion;
    this.format = conversion.format;
  }

  /** The node whose field holds this one (undefined for a fragment's list's nodes, and the document). */
  get parent(): ASTNode | undefined {
    return this.#parent as ASTNode | undefined;
  }

  /** The list holding this node, if in one (or the map). */
  get container(): S[] | Record<string, S> | undefined {
    return this.#container as S[] | Record<string, S> | undefined;
  }

  /** The document (undefined when the filter applies to a fragment). */
  get doc(): Pandoc | undefined {
    return this.#doc as Pandoc | undefined;
  }

  /** From the document (or the fragment), e.g. `["blocks", 3, "content", 1]`. */
  get path(): Path {
    const out: Path = [];
    this.#frames.forEach((f, i) => {
      if (f.field !== undefined) out.push(f.field);
      for (const k of f.outer) out.push(k);
      const index = this.#indexes[i];
      if (index !== undefined) out.push(index);
    });
    return out;
  }

  /** The path as text, e.g. `blocks[3].content[1]`; in a fragment, from it: `Para.content[1]`, `inlines[0]`. */
  get where(): string {
    return whereOf(this.#root, this.path);
  }

  /** The nodes above this one, the document (or the fragment) first. */
  get ancestors(): ASTNode[] {
    const out: unknown[] = [];
    for (const f of this.#frames) {
      if (f.parent !== undefined && out[out.length - 1] !== f.parent) out.push(f.parent);
    }
    return out as ASTNode[];
  }

  #sibling(step: number): unknown {
    const container = this.#container;
    const index = this.index;
    if (!Array.isArray(container) || typeof index !== "number" || index + step < 0) return undefined;
    return container[index + step];
  }

  /** The node after this one in its list. */
  get next(): S | undefined {
    return this.#sibling(1) as S | undefined;
  }

  /** The node before this one in its list. */
  get prev(): S | undefined {
    return this.#sibling(-1) as S | undefined;
  }

  /**
   * Don't walk this node's children, or its replacement's (Lua's
   * `return el, false`). Only when walking top-down: bottom-up, they have
   * been walked already.
   */
  skipChildren(): void {
    if (!this.#topDown) throw new Error("skipChildren() needs traverse: \"topdown\" (otherwise the children were walked first)");
    this.#skip = true;
  }
}

/** A path as text: from the document, or from a fragment called `root`. */
function whereOf(root: string, path: Path): string {
  const text = formatPath(path);
  if (!root) return text || "the document";
  if (!text || text.startsWith("[")) return root + text;
  return `${root}.${text}`;
}

/** What to add to the description of a promise returned. */
function promised(r: unknown): string {
  return typeof (r as { then?: unknown } | null)?.then === "function" ? " (a promise: filter functions can't be async)" : "";
}

interface Frame {
  parent: unknown;
  field: string | undefined;
  outer: (string | number)[];
  container: unknown[] | Record<string, unknown> | undefined;
  index: number | string | undefined;
}

/** The orders a filter can walk in. */
export type Traverse = "typewise" | "topdown" | "bottomup";

/** The context a function gets when it can't skip children (not top-down: the children came first). */
export type WalkedContext<S = unknown> = Omit<Context<S>, "skipChildren">;

/** A function's context: top-down, one that can skip children. */
type ContextFor<S, T extends Traverse> = [T] extends ["topdown"] ? Context<S> : WalkedContext<S>;

/** What a function may return: nothing to keep the node, or what converts to its replacement. */
type Returned<R> = R | void | undefined | null;

/**
 * A function of a filter. (Its parameters are checked both ways, as a
 * method's: a top-down filter, whose functions take a `Context`, is a
 * `Filter` too.)
 */
type Fn<N, C, R> = { fn(node: N, ctx: C): Returned<R> }["fn"];

/** A filter's functions, for the order `T`. */
export type FilterFunctions<T extends Traverse = Traverse> = {
  [K in keyof FilterTypes]?: Fn<FilterTypes[K]["node"], ContextFor<FilterTypes[K]["sibling"], T>, FilterTypes[K]["returns"]>;
} & {
  Inlines?: Fn<Inline[], ContextFor<never, T>, Inlines>;
  Blocks?: Fn<Block[], ContextFor<never, T>, Blocks>;
  Meta?: Fn<Meta, ContextFor<never, T>, { readonly [key: string]: MetaValueInit }>;
};

/**
 * Functions by constructor (`Str`, `Header`), sum type (`Inline`, `Block`,
 * `MetaValue`) or product (`Attr`, `Cell`, `Pandoc`); a node gets the one
 * for its constructor, or else its type's. As in pandoc's Lua filters, also
 * `Inlines` and `Blocks`, on every list of them, and `Meta`, on the
 * document's metadata; each returns a replacement, or nothing to keep it.
 *
 * `traverse` is the order:
 * - `"typewise"` (the default, as in Lua filters and Haskell's `walk`): one
 *   walk per kind, each bottom-up: every `Inline`, then every list of
 *   inlines (`Inlines`), then every `Block`, then `Blocks`; then every other
 *   node (`MetaValue`, `Cell`, `Attr`..., which Lua has no functions for),
 *   then `Meta`, then `Pandoc`.
 * - `"topdown"` (as Lua's, and pandocfilters): `Pandoc`, `Meta`, then
 *   depth-first from the root, a list before its elements and a node before
 *   its children, which are walked in its replacement too;
 *   `ctx.skipChildren()` skips what is below.
 * - `"bottomup"` (as panflute): one walk, each node after its children, a
 *   list after its elements, `Meta` after the metadata, `Pandoc` last.
 *
 * The type follows `traverse`: only a `Filter<"topdown">` (whose
 * `traverse` is `"topdown"`) has functions that get `skipChildren`;
 * `Filter` alone is one in any order, as when it is chosen at run time.
 */
export type Filter<T extends Traverse = Traverse> =
  FilterFunctions<T> & ([T] extends ["topdown"] ? { traverse: "topdown" } : { traverse?: T });

/**
 * A filter written out in one of the three orders, as in a list of them
 * written in place (`applyFilter(doc, [{ traverse: "topdown", ... }, ...])`):
 * each one's functions get the context of its `traverse`.
 */
export type AnyFilter = Filter<"topdown"> | Filter<"typewise" | "bottomup">;

/** What a filter applies to: a document, a node, or a list of inlines or of blocks (a fragment). */
export type Walkable = Pandoc | ASTNode | Inline[] | Block[];

/**
 * `items`, each node once: one that is there twice is copied where it comes
 * again. As in pandoc's Lua filters, whose results are values: the nodes of
 * `return [el, el]` are two, changed separately from then on. (One object in
 * two places of a document would be changed in both by whatever changes it
 * later.)
 */
function distinct(items: readonly unknown[]): readonly unknown[] {
  if (items.length < 2) return items;
  const seen = new Set<unknown>();
  let out: unknown[] | undefined;
  for (let k = 0; k < items.length; k++) {
    const x = items[k];
    if (typeof x !== "object" || x === null) continue;
    if (seen.has(x)) (out ??= [...items])[k] = copy(x);
    else seen.add(x);
  }
  return out ?? items;
}

/** Replace `count` elements of `list` at `start` by `items`, in place. */
function replace(list: unknown[], start: number, count: number, items: readonly unknown[]): void {
  if (items.length < 1024) {
    list.splice(start, count, ...items);
    return;
  }
  // (too many arguments for a call: engines stop at about a hundred thousand)
  const tail = list.splice(start + count);
  list.length = start;
  for (const x of items) list.push(x);
  for (const x of tail) list.push(x);
}

/**
 * `walk`'s function: called on every node with its context and the name of
 * its type in the schema (`"Inline"`, `"Cell"`, `"Pandoc"`); returns what
 * a filter function may.
 */
export type WalkAction = (node: ASTNode, ctx: Context, type: string) => unknown;
type Call = (x: unknown, ctx: Context) => unknown;

/** A field of a node that can hold nodes; `many`: the type of its items, if a list of nodes. */
interface Kid {
  name: string;
  type: TypeExpr;
  many: string | undefined;
}

let kidsCache: Map<string, Kid[]> | undefined;
const NONE: Kid[] = [];
const TOP: (string | number)[] = [];

/** By constructor or product name: where a walk goes in such a node. */
function kidsTable(): Map<string, Kid[]> {
  if (kidsCache) return kidsCache;
  const isNode = (name: string) => {
    const kind = tables().types.get(name)!.kind;
    return kind === "sum" || kind === "product";
  };
  const holds = (ty: TypeExpr): boolean => {
    const r = resolve(ty);
    if ("prim" in r) return false;
    if ("ref" in r) return isNode(r.ref);
    if ("list" in r) return holds(r.list);
    if ("maybe" in r) return holds(r.maybe);
    if ("map" in r) return holds(r.map[1]);
    return r.tuple.some(holds);
  };
  kidsCache = new Map();
  for (const [name, node] of tables().nodes) {
    const kids: Kid[] = [];
    for (const f of node.fields) {
      if (!holds(f.type)) continue;
      const r = resolve(f.type);
      const item = "list" in r ? resolve(r.list) : undefined;
      kids.push({ name: f.name, type: f.type, many: item && "ref" in item && isNode(item.ref) ? item.ref : undefined });
    }
    kidsCache.set(name, kids.length ? kids : NONE);
  }
  return kidsCache;
}

/** A filter's functions by name, and which types' nodes and lists this walk calls them for. */
interface Calls {
  /** By constructor, type or product name; and `Inlines`, `Blocks`, `Meta`. */
  fns: Map<string, Call>;
  /** The filter itself: `this`, in its functions. */
  self: unknown;
  nodes: (type: string) => boolean;
  lists: (type: string) => boolean;
  /** For `walk`: one function for every node, given the node's type too. */
  every?: WalkAction;
}

/** Errors already noted with their function and place. */
const noted = new WeakSet<Error>();

/** Note on an error which function, and where (once). */
function note(e: unknown, text: string): void {
  if (!(e instanceof Error) || noted.has(e)) return;
  noted.add(e);
  try {
    e.message += `\n  ${text}`;
  } catch {
    // an error whose message can't be changed (a DOMException, a frozen one)
  }
}

function nameOfFunction(fn: Call): string {
  return fn.name || "(anonymous)";
}

class Walker {
  frames: Frame[] = [];
  calls: Calls;
  topDown: boolean;
  doc: unknown;
  conversion: Conversion;
  /** What the walk starts from, for a context's `where`: "" for a document, else the fragment's name. */
  root: string;
  /** Whether the last function called asked to skip what is below. */
  skipped = false;
  #kids = kidsTable();
  /** Each sum's constructors, by name. */
  #sums = tables().constructors;

  constructor(calls: Calls, topDown: boolean, doc: unknown, conversion: Conversion, root = "") {
    this.calls = calls;
    this.topDown = topDown;
    this.doc = doc;
    this.conversion = conversion;
    this.root = root;
  }

  #context(): Context {
    return new Context(this.frames, this.doc, this.conversion, this.topDown, this.root);
  }

  /**
   * `fn` on `x`; sets `skipped`. A context is made only for a function that
   * takes one: one declared with a single parameter is spared it.
   */
  call(fn: Call, what: string, x: unknown): unknown {
    let ctx: Context | undefined;
    try {
      if (fn.length === 1) {
        this.skipped = false;
        return (fn as (this: unknown, x: unknown) => unknown).call(this.calls.self, x);
      }
      ctx = new Context(this.frames, this.doc, this.conversion, this.topDown, this.root);
      const r = fn.call(this.calls.self, x, ctx);
      this.skipped = skipped(ctx);
      return r;
    } catch (e) {
      // an error from it says which function it was, and where
      // (not a stack overflowed: applyFilter makes that an ASTDepthError)
      if (e instanceof Error && !noted.has(e) && !isStackOverflow(e)) {
        note(e, `in filter function ${nameOfFunction(fn)}, on the ${what} at ${this.#context().where}`);
      }
      throw e;
    }
  }

  /**
   * An error in what `fn` returned for the node `what` here (the walk's
   * frames): of a list's node, `k` places after it.
   */
  #wrong(fn: Call, what: string, expected: string, r: unknown, k?: number): ASTTypeError {
    const ctx = this.#context();
    const path = ctx.path;
    if (k !== undefined) {
      const last = path[path.length - 1];
      if (typeof last === "number") path[path.length - 1] = last + k;
      else path.push(k);
    }
    const e = new ASTTypeError(whereOf(this.root, path), expected, describe(r) + promised(r), path);
    note(e, `returned by filter function ${nameOfFunction(fn)}, for the ${what} at ${ctx.where}`);
    return e;
  }

  /** The function for nodes of a type, by constructor (`tag`) or else by type, if this walk calls any. */
  #find(type: string): (tag: string | undefined) => Call | undefined {
    const { fns, every, nodes } = this.calls;
    if (every) {
      const fn: Call = (x, ctx) => every(x as ASTNode, ctx, type);
      Object.defineProperty(fn, "name", { value: every.name });
      return () => fn;
    }
    if (!nodes(type)) return () => undefined;
    const ofType = fns.get(type);
    return (tag) => (tag === undefined ? ofType : fns.get(tag) ?? ofType);
  }

  /** What a function returned for a position that holds one node of `type`: the node there now. */
  #single(r: unknown, node: unknown, type: string, fn: Call, what: string): unknown {
    if (r === undefined || r === null || r === node) return node;
    if (isNodeOf(type, r)) return r;
    const expected = Array.isArray(r) ? `one ${type} (this position holds one)` : nameOf(type);
    throw this.#wrong(fn, what, expected, r);
  }

  /**
   * What a function returned for a node of a list of `type` (not nothing,
   * nor the node): one node to put in its place, or an array of them
   * (none or several), converted as pandoc's Lua converts and checked.
   */
  #replacement(r: unknown, type: string, fn: Call, what: string): unknown {
    // (the usual: one node of a sum, by its tag; an array has none)
    const tags = this.#sums.get(type);
    if (tags !== undefined) {
      if (typeof r === "object" && r !== null && tags.has((r as { t?: string }).t!)) return r;
    } else if (!Array.isArray(r) && isNodeOf(type, r)) {
      return r;
    }
    const items = typeof (r as { then?: unknown })?.then === "function" ? undefined : returnedNodes(type, r);
    if (items === undefined) throw this.#wrong(fn, what, `${nameOf(type)}, a list of them, or nothing`, r);
    for (let k = 0; k < items.length; k++) {
      if (!isNodeOf(type, items[k])) throw this.#wrong(fn, what, nameOf(type), items[k], k);
    }
    const out = distinct(items);
    return out.length === 1 ? out[0] : out;
  }

  /** A node in a position holding one node; returns it or its replacement. */
  one(node: unknown, type: string): unknown {
    const sum = tables().types.get(type)!.kind === "sum";
    const tag = sum ? (node as { t?: string }).t : undefined;
    const fn = this.#find(type)(tag);
    const kidsOf = (x: unknown) => this.#kids.get(sum ? (x as { t: string }).t : type) ?? NONE;
    if (this.topDown) {
      let skip = false;
      if (fn) {
        node = this.#single(this.call(fn, tag ?? type, node), node, type, fn, tag ?? type);
        skip = this.skipped;
      }
      if (!skip) this.children(node, kidsOf(node));
      return node;
    }
    this.children(node, kidsOf(node));
    return fn ? this.#single(this.call(fn, tag ?? type, node), node, type, fn, tag ?? type) : node;
  }

  /**
   * The nodes of a list: each may be replaced, deleted or spliced; then (or
   * first, top-down) the list. Returns the list, or the one that replaces
   * it: what a list function returned (a new list, since the function's
   * nodes may hold the old one, as `Para(xs)` did before constructors
   * copied).
   */
  many(list: unknown[], type: string, parent: unknown, field: string | undefined, outer: (string | number)[]): unknown[] {
    const { topDown, frames } = this;
    const frame: Frame = { parent, field, outer, container: undefined, index: undefined };
    const whole = this.calls.lists(type) ? this.calls.fns.get(`${type}s`) : undefined;
    if (whole && topDown) {
      list = this.#wholeList(list, type, frame, whole);
      if (this.skipped) return list;
    }
    const sum = tables().types.get(type)!.kind === "sum";
    const find = this.#find(type);
    const kids = this.#kids;
    const ofProduct = sum ? NONE : kids.get(type) ?? NONE;
    frame.container = list;
    frames.push(frame);
    try {
      let i = 0;
      while (i < list.length) {
        const node = list[i];
        const tag = sum ? (node as { t?: string } | null | undefined)?.t : undefined;
        const below = sum ? kids.get(tag as string) ?? NONE : ofProduct;
        const fn = find(tag);
        if (!fn) {
          if (below !== NONE) {
            frame.index = i;
            this.children(node, below);
          }
          i++;
          continue;
        }
        frame.index = i;
        if (!topDown && below !== NONE) this.children(node, below);
        const r = this.call(fn, tag ?? type, node);
        const skip = this.skipped;
        if (list[i] !== node) {
          // the function changed the list itself (ctx.container)
          i = list.indexOf(node);
          i = this.#found(i);
          frame.index = i;
        }
        if (r === undefined || r === null || r === node) {
          if (topDown && !skip && below !== NONE) this.children(node, below);
          i++;
          continue;
        }
        const x = this.#replacement(r, type, fn, tag ?? type);
        let n = 1;
        if (Array.isArray(x)) {
          // none or several, in place
          replace(list, i, 1, x);
          n = x.length;
        } else {
          list[i] = x;
        }
        if (topDown && !skip) {
          for (const end = i + n; i < end; i++) {
            frame.index = i;
            const y = list[i];
            const ys = sum ? kids.get((y as { t: string }).t) ?? NONE : ofProduct;
            if (ys !== NONE) this.children(y, ys);
          }
        } else {
          i += n;
        }
      }
    } finally {
      frames.pop();
    }
    if (whole && !topDown) {
      return this.#wholeList(list, type, { ...frame, container: undefined, index: undefined }, whole);
    }
    return list;
  }

  /** Where a function's own node is in the list it changed (ctx.container), or an error. */
  #found(i: number): number {
    if (i < 0) {
      throw new Error(`${this.#context().where}: a filter function took its own node out of the list it is in ` +
                      "(ctx.container); to delete the node, return []");
    }
    return i;
  }

  /** The list function on `list`: the list, or a new one of what it returned. Sets `skipped`. */
  #wholeList(list: unknown[], type: string, frame: Frame, fn: Call): unknown[] {
    this.frames.push(frame);
    try {
      const what = `${type}s`;
      const r = this.call(fn, what, list);
      if (r === undefined || r === null || r === list) return list;
      // (a new list: its nodes may hold the old one)
      const items = typeof (r as { then?: unknown }).then === "function" ? undefined : returnedNodes(type, r);
      if (items === undefined) throw this.#wrong(fn, what, `a list of ${what}`, r);
      for (let k = 0; k < items.length; k++) {
        if (!isNodeOf(type, items[k])) throw this.#wrong(fn, what, nameOf(type), items[k], k);
      }
      const out = distinct(items);
      return out === items ? items : [...out];
    } finally {
      this.frames.pop();
    }
  }

  /** Walk a field's value; returns it, or its replacement. */
  value(v: unknown, ty: TypeExpr, parent: unknown, field: string, outer: (string | number)[]): unknown {
    const r = resolve(ty);
    if ("prim" in r || v === null || v === undefined) return v;
    if ("ref" in r) {
      if (tables().types.get(r.ref)!.kind === "enum") return v;
      this.frames.push({ parent, field, outer, container: undefined, index: undefined });
      try {
        return this.one(v, r.ref);
      } finally {
        this.frames.pop();
      }
    }
    if ("maybe" in r) return this.value(v, r.maybe, parent, field, outer);
    if ("list" in r) {
      const item = resolve(r.list);
      const list = v as unknown[];
      if ("ref" in item && tables().types.get(item.ref)!.kind !== "enum") {
        return this.many(list, item.ref, parent, field, outer);
      } else if (!("prim" in item)) {
        for (let i = 0; i < list.length; i++) {
          const y = this.value(list[i], r.list, parent, field, [...outer, i]);
          if (y !== list[i]) list[i] = y;
        }
      }
      return v;
    }
    if ("map" in r) {
      const obj = v as Record<string, unknown>;
      const item = resolve(r.map[1]);
      const nodes = "ref" in item && tables().types.get(item.ref)!.kind !== "enum";
      for (const k of sortedKeys(obj)) {
        if (!Object.hasOwn(obj, k)) continue; // a function removed it
        let y;
        if (nodes) {
          // a node in a map: the map is its container, the key its index
          this.frames.push({ parent, field, outer, container: obj, index: k });
          try {
            y = this.one(obj[k], (item as { ref: string }).ref);
          } finally {
            this.frames.pop();
          }
        } else {
          y = this.value(obj[k], r.map[1], parent, field, [...outer, k]);
        }
        if (y !== obj[k]) obj[k] = y;
      }
      return v;
    }
    const tuple = v as unknown[];
    r.tuple.forEach((t, i) => {
      const y = this.value(tuple[i], t, parent, field, [...outer, i]);
      if (y !== tuple[i]) tuple[i] = y;
    });
    return v;
  }

  /** Walk what is in `node`: `kids`, its fields that can hold nodes. */
  children(node: unknown, kids: Kid[]): void {
    const obj = node as Record<string, unknown>;
    for (const kid of kids) {
      const v = obj[kid.name];
      const y = kid.many !== undefined && Array.isArray(v)
        ? this.many(v, kid.many, node, kid.name, TOP)
        : this.value(v, kid.type, node, kid.name, TOP);
      if (y !== v) obj[kid.name] = y;
    }
  }

  /** Walk what is in a document (or any product) of type `type`, field by field. */
  fields(node: unknown, type: string, each?: (field: string) => boolean | void): void {
    for (const kid of this.#kids.get(type) ?? NONE) {
      if (each?.(kid.name) === false) continue;
      this.children(node, [kid]);
    }
  }
}

/** Every name a filter's function can have. */
let namesCache: Set<string> | undefined;

function names(): Set<string> {
  namesCache ??= new Set([...tables().nodes.keys(), ...tables().constructors.keys(), "Inlines", "Blocks", "Meta"]);
  return namesCache;
}

/** A filter's functions, by name. */
function functionsOf(filter: object): Map<string, Call> {
  if (typeof filter !== "object" || filter === null || Array.isArray(filter)) {
    throw new ASTTypeError("", "a filter (an object of functions)", describe(filter));
  }
  const known = names();
  const fns = new Map<string, Call>();
  const obj = filter as Record<string, unknown>;
  for (const name of known) {
    const f = obj[name];
    if (typeof f === "function") fns.set(name, f as Call);
  }
  // a function under another name would never be called: say so
  for (const key of Object.keys(obj)) {
    if (typeof obj[key] === "function" && /^[A-Z]/.test(key) && !known.has(key)) {
      throw new TypeError(`a filter has a function ${key}, which no node of pandoc's is called`);
    }
  }
  return fns;
}

const INLINE = (t: string) => t === "Inline";
const BLOCK = (t: string) => t === "Block";
const LISTS = (t: string) => t === "Inline" || t === "Block";
const OTHER = (t: string) => t !== "Inline" && t !== "Block";
const NOT_ROOT = (t: string) => t !== SCHEMA.root;
const ALL = () => true;
const NO = () => false;

/** What a filter or `walk` applies to: a document, a node of a type (or product), or a list of them. */
type Target =
  | { kind: "doc" }
  | { kind: "node"; type: string; kids: string; label: string }
  | { kind: "list"; type: string; label: string }
  | { kind: "empty" };

function targetOf(x: unknown): Target {
  const fail = (): never => {
    throw new ASTTypeError("", "a document, a node, or a list of inlines or of blocks", describe(x));
  };
  if (Array.isArray(x)) {
    if (!x.length) return { kind: "empty" };
    const type = tables().tagOwner.get((x[0] as { t?: string } | null)?.t as string);
    if (type !== "Inline" && type !== "Block") return fail();
    for (let i = 0; i < x.length; i++) {
      if (!isNodeOf(type, x[i])) {
        throw new ASTTypeError(`${type.toLowerCase()}s[${i}]`, type, describe(x[i]), [i]);
      }
    }
    return { kind: "list", type, label: `${type.toLowerCase()}s` };
  }
  if (typeof x !== "object" || x === null) return fail();
  const t = (x as { t?: unknown }).t;
  if (typeof t === "string") {
    const type = tables().tagOwner.get(t);
    if (type === undefined) return fail();
    return { kind: "node", type, kids: t, label: t };
  }
  const product = productOf(x as Record<string, unknown>);
  if (product === undefined) return fail();
  if (product === SCHEMA.root) return { kind: "doc" };
  return { kind: "node", type: product, kids: product, label: product };
}

/**
 * `list`'s contents replaced by `items` (what its list function made of
 * it), in place. A node made of the list itself (`[Emph(xs)]`, written as
 * an object literal) gets a copy of what the list held instead.
 */
function refill(list: unknown[], items: readonly unknown[]): void {
  const old = list.slice();
  const seen = new Set<unknown>();
  const fix = (v: unknown): void => {
    if (typeof v !== "object" || v === null || seen.has(v)) return;
    seen.add(v);
    if (Array.isArray(v)) {
      for (let i = 0; i < v.length; i++) {
        if (v[i] === list) v[i] = old;
        else fix(v[i]);
      }
      return;
    }
    const obj = v as Record<string, unknown>;
    for (const k of Object.keys(obj)) {
      if (obj[k] === list) obj[k] = old;
      else fix(obj[k]);
    }
  };
  const copy = [...items];
  copy.forEach(fix);
  replace(list, 0, list.length, copy);
}

/**
 * Call `action(node, ctx, type)` on `target` and every node in it,
 * bottom-up (or `topDown`); `target` is a document, a node, or a list of
 * inlines or of blocks (then `action` is called on each). `action` returns
 * what a filter function may: nothing to keep the node, its replacement,
 * or in a list, what converts to a list to splice in its place (`[]`
 * deletes). Returns `target` (changed in place) or its replacement.
 */
export function walk<W extends Walkable>(target: W, action: WalkAction,
                                         opts: { topDown?: boolean; format?: string | Conversion } = {}): W {
  if (typeof action !== "function") throw new ASTTypeError("", "a function", describe(action));
  const kind = targetOf(target);
  if (kind.kind === "empty") return target;
  const calls: Calls = { fns: new Map(), self: undefined, nodes: ALL, lists: NO, every: action };
  const doc = kind.kind === "doc" ? target : undefined;
  const label = kind.kind === "doc" ? "" : kind.label;
  const w = new Walker(calls, opts.topDown ?? false, doc, toConversion(opts.format), label);
  return deep("walk", () => {
    if (kind.kind === "list") return w.many(target as unknown[], kind.type, undefined, undefined, TOP) as W;
    return w.one(target, kind.kind === "doc" ? SCHEMA.root : kind.type) as W;
  });
}

function applyOne(target: unknown, filter: Filter, conversion: Conversion): unknown {
  const root = SCHEMA.root;
  const traverse = filter.traverse ?? "typewise";
  if (traverse !== "typewise" && traverse !== "topdown" && traverse !== "bottomup") {
    throw new ASTTypeError("traverse", `"typewise", "topdown" or "bottomup"`, JSON.stringify(traverse), ["traverse"]);
  }
  const fns = functionsOf(filter);
  const kind = targetOf(target);
  if (kind.kind === "empty") return target;
  const topDown = traverse === "topdown";
  const isDoc = kind.kind === "doc";
  const label = isDoc ? "" : kind.label;
  const walker = (d: unknown, nodes: (t: string) => boolean, lists: (t: string) => boolean) =>
    new Walker({ fns, self: filter, nodes, lists }, topDown, d, conversion, label);

  if (!isDoc) {
    // a fragment: as Lua's el:walk{}, what is in it (a list's own function
    // too), not the node itself, nor Meta and Pandoc; in place
    const all = (w: Walker) => {
      if (kind.kind === "list") {
        const list = target as unknown[];
        const out = w.many(list, kind.type, undefined, undefined, TOP);
        if (out !== list) refill(list, out);
      } else {
        w.children(target, kidsTable().get(kind.kids) ?? NONE);
      }
    };
    deep("applyFilter", () => {
      if (traverse !== "typewise") {
        all(walker(undefined, NOT_ROOT, LISTS));
        return;
      }
      for (const [nodes, lists, needed] of passes(fns)) if (needed) all(walker(undefined, nodes, lists));
    });
    return target;
  }

  let doc = target as Pandoc;
  /** A function on one value (`Meta`, `Pandoc`): its result or the value, and whether it skips. */
  const straight = (name: string, value: unknown, d: Pandoc, frames: Frame[]): [unknown, boolean] => {
    const fn = fns.get(name);
    if (!fn) return [value, false];
    const w = walker(d, NO, NO);
    w.frames = frames;
    const r = w.call(fn, name, value);
    if (r === undefined || r === null || r === value) return [value, w.skipped];
    if (name === "Meta") {
      // as Lua: a map of what converts to metadata
      const meta = isPlainMap(r) ? toMeta(r) as Record<string, unknown> : undefined;
      if (meta === undefined) throw wrongStraight(fn, "metadata (an object of MetaValues)", r, ["meta"]);
      for (const k of Object.keys(meta)) {
        if (!isNodeOf("MetaValue", meta[k])) throw wrongStraight(fn, "MetaValue", meta[k], ["meta", k]);
      }
      return [meta, w.skipped];
    }
    if (!isNodeOf(root, r)) {
      throw wrongStraight(fn, Array.isArray(r) ? `one ${root} (this position holds one)` : `a ${root}`, r, []);
    }
    return [r, w.skipped];
  };
  const meta = (d: Pandoc): [unknown, boolean] => straight(
    "Meta", d.meta, d, [{ parent: d, field: "meta", outer: [], container: undefined, index: undefined }]);
  return deep("applyFilter", () => {
    if (topDown) {
      const [d, skip] = straight(root, doc, doc, []);
      doc = d as Pandoc; // the function's document, if it returned one, is the one walked
      if (skip) return doc;
      const [m, skipMeta] = meta(doc);
      doc.meta = m as Meta;
      walker(doc, NOT_ROOT, LISTS).fields(doc, root, (field) => !(skipMeta && field === "meta"));
      return doc;
    }
    if (traverse === "bottomup") {
      const w = walker(doc, NOT_ROOT, LISTS);
      for (const kid of kidsTable().get(root) ?? NONE) {
        w.children(doc, [kid]);
        if (kid.name === "meta") doc.meta = meta(doc)[0] as Meta;
      }
      return straight(root, doc, doc, [])[0] as Pandoc;
    }
    for (const [nodes, lists, needed] of passes(fns)) {
      if (needed) walker(doc, nodes, lists).fields(doc, root);
    }
    doc.meta = meta(doc)[0] as Meta;
    return straight(root, doc, doc, [])[0] as Pandoc;
  });
}

/** An error in what a Meta or Pandoc function returned. */
function wrongStraight(fn: Call, expected: string, r: unknown, path: Path): ASTTypeError {
  const e = new ASTTypeError(formatPath(path) || "the document", expected, describe(r) + promised(r), path);
  note(e, `returned by filter function ${nameOfFunction(fn)}, for the ${path[0] === "meta" ? "Meta" : "Pandoc"} at ${
    path[0] === "meta" ? "meta" : "the document"}`);
  return e;
}

/** An object that may be a map (not an array, nor a node). */
function isPlainMap(v: unknown): boolean {
  if (typeof v !== "object" || v === null || Array.isArray(v)) return false;
  const proto = Object.getPrototypeOf(v);
  return (proto === Object.prototype || proto === null) && typeof (v as { t?: unknown }).t !== "string";
}

/** The typewise passes: which nodes, which lists, and whether the filter has a function for them. */
function passes(fns: Map<string, Call>): [(t: string) => boolean, (t: string) => boolean, boolean][] {
  return [
    [INLINE, NO, has(fns, "Inline")],
    [NO, INLINE, fns.has("Inlines")],
    [BLOCK, NO, has(fns, "Block")],
    [NO, BLOCK, fns.has("Blocks")],
    [OTHER, NO, hasOther(fns)],
  ];
}

/** Whether `fns` has a function for the sum `sum` or one of its constructors. */
function has(fns: Map<string, Call>, sum: string): boolean {
  if (fns.has(sum)) return true;
  for (const name of tables().constructors.get(sum)?.keys() ?? []) if (fns.has(name)) return true;
  return false;
}

/** Whether `fns` has a function for a node that isn't an Inline, a Block or the document. */
function hasOther(fns: Map<string, Call>): boolean {
  for (const [name, decl] of tables().types) {
    if (name === "Inline" || name === "Block" || name === SCHEMA.root) continue;
    if (decl.kind === "sum" ? has(fns, name) : decl.kind === "product" && fns.has(name)) return true;
  }
  return false;
}

/**
 * Apply a filter to a document, in place, as pandoc applies a Lua filter;
 * returns it (or the document its `Pandoc` function returned). A list of
 * filters is applied one after the other, as a Lua filter file returning a
 * list. `format` is the output format's name, or the whole `Conversion`
 * (then in `ctx.conversion`).
 *
 * Or to a fragment: a node, or a list of inlines or of blocks, as Lua's
 * `el:walk{...}`: the functions apply to what is in a node, not to the node
 * itself (a list's function to the list itself too), and `Meta` and
 * `Pandoc` only to a document. Unlike Lua, in place: returns the fragment.
 */
export function applyFilter<W extends Walkable, T extends Traverse = "typewise">(
  target: W, filter: FilterFunctions<T> & { traverse?: T }, format?: string | Conversion): W;
export function applyFilter<W extends Walkable>(target: W, filters: readonly AnyFilter[], format?: string | Conversion): W;
export function applyFilter<W extends Walkable>(target: W, filters: readonly Filter[], format?: string | Conversion): W;
export function applyFilter(target: Walkable, filter: object | readonly object[],
                            format?: string | Conversion): Walkable {
  const conversion = toConversion(format);
  let out: unknown = target;
  for (const f of isFilterList(filter) ? filter : [filter as Filter]) out = applyOne(out, f, conversion);
  return out as Walkable;
}

function toConversion(format: string | Conversion | undefined): Conversion {
  return format instanceof Conversion ? format : new Conversion({ format });
}

function isFilterList(f: object | readonly object[]): f is readonly Filter[] {
  return Array.isArray(f);
}
