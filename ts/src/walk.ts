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

import { ASTTypeError, formatPath, resolve, tables } from "./core.ts";
import type { Path, TypeExpr } from "./core.ts";
import { Conversion } from "./conversion.ts";
import { SCHEMA } from "./generated.ts";
import type { Block, Inline, Meta, Nodes, Pandoc, Sums } from "./generated.ts";

/** Whether a context's function asked to skip the node's children. */
let skipped: (ctx: Context) => boolean;

/** Where a node is: its parent, its place in it, the document, the conversion. */
export class Context {
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
  #walker: Walker | undefined;

  static {
    skipped = (ctx) => ctx.#skip;
  }

  constructor(frames: Frame[], doc: unknown, conversion: Conversion, topDown = false, walker?: Walker) {
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
    this.#walker = walker;
    const last = n ? frames[n - 1] : undefined;
    this.#parent = last?.parent;
    this.field = last?.field;
    this.#container = last?.container;
    this.index = last?.index;
    this.#doc = doc;
    this.conversion = conversion;
    this.format = conversion.format;
  }

  // the lists being walked made what they are now before any is looked at
  // (`many` makes a list's result aside: Splice)
  #mend(): void {
    if (this.#walker?.splicing.length) this.#walker.mend();
  }

  /** The node whose field holds this one. */
  get parent(): unknown {
    this.#mend();
    return this.#parent;
  }

  /** The list holding this node, if in one (or the map). */
  get container(): unknown[] | Record<string, unknown> | undefined {
    this.#mend();
    return this.#container;
  }

  get doc(): unknown {
    this.#mend();
    return this.#doc;
  }

  /** From the document, e.g. `["blocks", 3, "content", 1]`. */
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

  /** The path as text, e.g. `blocks[3].content[1]`. */
  get where(): string {
    return formatPath(this.path) || "the document";
  }

  /** The nodes above this one, the document first. */
  get ancestors(): unknown[] {
    this.#mend();
    const out: unknown[] = [];
    for (const f of this.#frames) {
      if (out[out.length - 1] !== f.parent) out.push(f.parent);
    }
    return out;
  }

  #sibling(step: number): unknown {
    const container = this.#container;
    const index = this.index;
    if (!Array.isArray(container) || typeof index !== "number" || index + step < 0) return undefined;
    const w = this.#walker;
    for (const sp of w?.splicing ?? NO_SPLICES) {
      if (sp.list !== container) continue;
      if (container.length !== sp.was.length) {
        w!.mend(); // changed from elsewhere
        break;
      }
      return nodeAt(sp, index + step); // the list as it is now, without making it so
    }
    return container[index + step];
  }

  get next(): unknown {
    return this.#sibling(1);
  }

  get prev(): unknown {
    return this.#sibling(-1);
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

interface Frame {
  parent: unknown;
  field: string | undefined;
  outer: (string | number)[];
  container: unknown[] | Record<string, unknown> | undefined;
  index: number | string | undefined;
}

/**
 * The walk of a list once one of its nodes was replaced by none or several
 * (`Walker.#aside`): the list keeps its nodes (but for those replaced by
 * one) while the result is made aside, in `out`; from `at` on, the list's
 * nodes are those not in it yet, the first perhaps being walked. The list as
 * it is now is `out` and then those: it becomes that (`mend`) at the end of
 * its walk, or when anything reaches it through a Context; `out` is then
 * undefined.
 *
 * `was` is what the list holds as the walk left it, and the nodes it went
 * through: something else may have changed it meanwhile (a function, through
 * the document it closed over). `marks`, in pairs: from the k-th of those
 * nodes on, the results of each begin `off` after its own place, from where
 * that changes.
 */
interface Splice {
  list: unknown[];
  out: unknown[] | undefined;
  at: number;
  was: unknown[];
  marks: number[];
}

const NO_SPLICES: Splice[] = [];

/** Make the list what it is now: `out`, then its nodes from `at`. */
function mend(sp: Splice): void {
  const { list, out, at, was } = sp;
  if (out === undefined) return;
  sp.out = undefined;
  let same = list.length === was.length;
  for (let k = 0; same && k < list.length; k++) same = list[k] === was[k];
  if (!same) {
    const merged = mergedList(sp, out);
    list.length = 0;
    for (const x of merged) list.push(x);
    return;
  }
  const m = out.length;
  const tail = list.length - at;
  if (m <= at) {
    for (let k = 0; k < tail; k++) list[m + k] = list[at + k];
    list.length = m + tail;
  } else {
    for (let k = at; k < m; k++) list.push(undefined);
    for (let k = tail - 1; k >= 0; k--) list[m + k] = list[at + k];
  }
  for (let k = 0; k < m; k++) list[k] = out[k];
}

/** The list as changed from elsewhere, each node the walk went through replaced by what it made of it. */
function mergedList(sp: Splice, out: unknown[]): unknown[] {
  const { at, was, marks } = sp;
  const starts: number[] = [];
  let off = 0;
  for (let k = 0, m = 0; k <= at; k++) {
    for (; m < marks.length && marks[m] <= k; m += 2) off = marks[m + 1];
    starts.push(k + off);
  }
  const walked = new Map<unknown, number>();
  for (let k = 0; k < at; k++) walked.set(was[k], k);
  const merged: unknown[] = [];
  for (const x of sp.list) {
    const k = walked.get(x);
    if (k === undefined) merged.push(x);
    else for (let j = starts[k]; j < starts[k + 1]; j++) merged.push(out[j]);
  }
  return merged;
}

/** The node at `p` in the list as it is now (`out` being made). */
function nodeAt(sp: Splice, p: number): unknown {
  const out = sp.out!;
  return p < out.length ? out[p] : sp.list[sp.at + p - out.length];
}

/** What a filter function returns: nothing to keep the node, a node to replace it, a list to splice. */
export type Result<T> = void | undefined | null | T | T[];

/** The context a function gets when it can't skip children (bottom-up, the children came first). */
export type WalkedContext = Omit<Context, "skipChildren">;

type Fn<N, R, C> = (node: N, ctx: C) => Result<R>;
type Straight<T, C> = (value: T, ctx: C) => T | void | undefined | null;

/** A filter's functions, getting contexts of type `C`. */
type Functions<C> = {
  [K in keyof Nodes]?: Fn<Nodes[K], OwnerOf<K>, C>;
} & {
  [K in keyof Sums]?: Fn<Sums[K], Sums[K], C>;
} & {
  Inlines?: Straight<Inline[], C>;
  Blocks?: Straight<Block[], C>;
  Meta?: Straight<Meta, C>;
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
 * Only top-down functions get `skipChildren`: bottom-up, the children came
 * first.
 */
export type Filter =
  | (Functions<WalkedContext> & { traverse?: "typewise" | "bottomup" })
  | (Functions<Context> & { traverse: "topdown" });

type OwnerOf<K extends keyof Nodes> = {
  [S in keyof Sums]: Nodes[K] extends Sums[S] ? Sums[S] : never;
}[keyof Sums] extends infer U ? ([U] extends [never] ? Nodes[K] : U) : never;

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

/** A node (or any value of the AST) copied, with everything in it. */
function copy<T>(v: T): T {
  if (Array.isArray(v)) return v.map(copy) as T;
  if (typeof v !== "object" || v === null) return v;
  const out = Object.create(Object.getPrototypeOf(v)) as Record<string, unknown>;
  for (const k of Object.keys(v)) {
    const x = copy((v as Record<string, unknown>)[k]);
    // (assigning to __proto__ would set the prototype of an object that has one)
    if (k === "__proto__") Object.defineProperty(out, k, { value: x, writable: true, enumerable: true, configurable: true });
    else out[k] = x;
  }
  return out as T;
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

/** The keys of a map in the order pandoc walks its Map: by code point. */
function sortedKeys(obj: Record<string, unknown>): string[] {
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

type Action = (node: unknown, ctx: Context, type: string) => unknown;
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
  every?: Action;
}

class Walker {
  frames: Frame[] = [];
  calls: Calls;
  topDown: boolean;
  doc: unknown;
  conversion: Conversion;
  /** Whether the last function called asked to skip what is below. */
  skipped = false;
  /**
   * The lists being walked whose result is made aside (`#aside`): those of
   * every pass of a filter's walk (a context may be kept from one to the next).
   */
  splicing: Splice[];
  #kids = kidsTable();

  /** Make every list being walked what it is now. */
  mend(): void {
    for (const sp of this.splicing) mend(sp);
    this.splicing.length = 0;
  }

  /** Make the list `sp` is of what it is now; its walk goes on in place. */
  #unsplice(sp: Splice): void {
    if (sp.out === undefined) return;
    mend(sp);
    this.splicing.splice(this.splicing.indexOf(sp), 1);
  }

  constructor(calls: Calls, topDown: boolean, doc: unknown, conversion: Conversion, splicing: Splice[] = []) {
    this.calls = calls;
    this.splicing = splicing;
    this.topDown = topDown;
    this.doc = doc;
    this.conversion = conversion;
  }

  /** Where the walk is, from the document. */
  #where(): string {
    return new Context(this.frames, this.doc, this.conversion).where;
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
      ctx = new Context(this.frames, this.doc, this.conversion, this.topDown, this);
      const r = fn.call(this.calls.self, x, ctx);
      this.skipped = skipped(ctx);
      return r;
    } catch (e) {
      // an error from it says which function it was, and where
      if (e instanceof Error && !noted.has(e)) {
        noted.add(e);
        try {
          e.message += `\n  in filter function ${fn.name || "(anonymous)"}, on the ${what} at ${this.#where()}`;
        } catch {
          // an error whose message can't be changed (a DOMException, a frozen one)
        }
      }
      throw e;
    }
  }

  /** The function for nodes of a type, by constructor (`tag`) or else by type, if this walk calls any. */
  #find(type: string): (tag: string | undefined) => Call | undefined {
    const { fns, every, nodes } = this.calls;
    if (every) {
      const fn: Call = (x, ctx) => every(x, ctx, type);
      return () => fn;
    }
    if (!nodes(type)) return () => undefined;
    const ofType = fns.get(type);
    return (tag) => (tag === undefined ? ofType : fns.get(tag) ?? ofType);
  }

  /** What a function returned for a position that holds one node: the node there now. */
  #single(r: unknown, node: unknown, what: string): unknown {
    if (r === undefined || r === null) return node;
    if (Array.isArray(r)) throw new ASTTypeError(this.#where(), `one ${what} (this position holds one)`, "a list");
    this.#notPromise(r, what);
    return r;
  }

  #notPromise(r: unknown, what: string): void {
    if (typeof (r as { then?: unknown }).then === "function") {
      throw new ASTTypeError(this.#where(), `a ${what}`, "a promise (filter functions can't be async)");
    }
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
        node = this.#single(this.call(fn, tag ?? type, node), node, "node");
        skip = this.skipped;
      }
      if (!skip) this.children(node, kidsOf(node));
      return node;
    }
    this.children(node, kidsOf(node));
    return fn ? this.#single(this.call(fn, tag ?? type, node), node, "node") : node;
  }

  /**
   * The nodes of a list: each may be replaced, deleted or spliced; then (or
   * first, top-down) the list. Returns the list, or the one that replaces
   * it: what a list function returned (a new list, since the function's
   * nodes may hold the old one, as `Para(xs)` did before constructors
   * copied).
   */
  many(list: unknown[], type: string, parent: unknown, field: string, outer: (string | number)[]): unknown[] {
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
        let x: unknown = r;
        if (Array.isArray(r)) {
          const items = distinct(r);
          if (items.length !== 1) {
            // none or several: the rest of the walk makes the list aside
            this.#aside(list, type, frame, i, items, skip, sum, find, ofProduct);
            break;
          }
          x = items[0];
        } else {
          this.#notPromise(r, type);
        }
        list[i] = x;
        if (topDown && !skip) {
          const xs = sum ? kids.get((x as { t: string }).t) ?? NONE : ofProduct;
          if (xs !== NONE) this.children(x, xs);
        }
        i++;
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
      throw new Error(`${this.#where()}: a filter function took its own node out of the list it is in ` +
                      "(ctx.container); to delete the node, return []");
    }
    return i;
  }

  /**
   * The rest of `many`'s walk once `list[i]` was replaced by `items` (none
   * or several nodes): the result is made aside, in `out`.
   *
   * Replacing in place moved all that follows, every time (quadratic).
   * Meanwhile the list keeps the nodes still to walk, whoever looks at it,
   * and it becomes `out` and then those at the end, after an error too.
   * Anything that reaches it through a Context makes it that first (mend:
   * `sp.out` is then undefined), as does a change to it from elsewhere (seen
   * by its length, or the node's place), and the walk goes on in place
   * (`out` undefined), as before. `i` is the node's place in the result; aside, the
   * node is `list[sp.at]`, and `i === out.length` until it is in `out`.
   */
  #aside(list: unknown[], type: string, frame: Frame, i: number, items: readonly unknown[], skip: boolean,
         sum: boolean, find: (tag: string | undefined) => Call | undefined, ofProduct: Kid[]): void {
    const { topDown } = this;
    const kids = this.#kids;
    let out: unknown[] | undefined = list.slice(0, i);
    for (const x of items) out.push(x);
    const sp: Splice = { list, out, at: i + 1, was: list.slice(), marks: [i + 1, out.length - i - 1] };
    this.splicing.push(sp);
    const n0 = list.length; // (as long as nothing else changes it)
    try {
      let n = items.length;
      for (;;) {
        // (here the node's n replacements are in the result, from i)
        if (topDown && !skip) {
          for (const end = i + n; i < end; i++) {
            frame.index = i;
            const x = out === undefined ? list[i] : out[i];
            const xs = sum ? kids.get((x as { t: string }).t) ?? NONE : ofProduct;
            if (xs !== NONE) {
              this.children(x, xs);
              if (out !== undefined && (sp.out === undefined || list.length !== n0)) {
                this.#unsplice(sp);
                out = undefined; // mended: in place
              }
            }
          }
        } else {
          i += n;
        }
        // the next node
        let node: unknown;
        if (out === undefined) {
          if (i >= list.length) break;
          node = list[i];
        } else {
          if (sp.at >= list.length) break;
          node = sp.was[sp.at] = list[sp.at]; // (another may have put it there)
        }
        const tag = sum ? (node as { t?: string } | null | undefined)?.t : undefined;
        const below = sum ? kids.get(tag as string) ?? NONE : ofProduct;
        const fn = find(tag);
        frame.index = i;
        n = 1;
        if (!fn) {
          if (below !== NONE) {
            this.children(node, below);
            if (out !== undefined && (sp.out === undefined || list.length !== n0 || list[sp.at] !== node)) {
              this.#unsplice(sp);
              out = undefined; // mended: in place, the node at i
            }
          }
          if (out !== undefined) {
            out.push(node);
            sp.at++;
          }
          skip = true;
          continue;
        }
        if (!topDown && below !== NONE) this.children(node, below);
        const r = this.call(fn, tag ?? type, node);
        skip = this.skipped;
        if (out !== undefined && (sp.out === undefined || list.length !== n0 || list[sp.at] !== node)) {
          this.#unsplice(sp);
          out = undefined; // mended (below, or by the function): in place
        }
        if (out === undefined && list[i] !== node) {
          // the function changed the list itself (ctx.container)
          i = this.#found(list.indexOf(node));
          frame.index = i;
        }
        if (r === undefined || r === null || r === node) {
          if (out !== undefined) {
            out.push(node);
            sp.at++;
          }
          continue;
        }
        let x: unknown = r;
        if (Array.isArray(r)) {
          items = distinct(r);
          n = items.length;
          x = items[0];
        } else {
          this.#notPromise(r, type);
        }
        if (n === 1) {
          if (out === undefined) {
            list[i] = x;
          } else {
            list[sp.at] = sp.was[sp.at] = x; // in the list too
            sp.at++;
            out.push(x);
          }
        } else if (out === undefined) {
          replace(list, i, 1, items);
        } else {
          for (const y of items) out.push(y);
          sp.at++;
          sp.marks.push(sp.at, out.length - sp.at);
        }
      }
    } finally {
      // (all walked, or an error: the list whole either way)
      this.#unsplice(sp);
    }
  }

  /** The list function on `list`: the list, or a new one of what it returned. Sets `skipped`. */
  #wholeList(list: unknown[], type: string, frame: Frame, fn: Call): unknown[] {
    this.frames.push(frame);
    try {
      const r = this.call(fn, `${type}s`, list);
      if (r === undefined || r === null || r === list) return list;
      if (!Array.isArray(r)) throw new ASTTypeError(this.#where(), `a list of ${type}s`, typeof r);
      return [...distinct(r)];
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

/**
 * Call `action(node, ctx, type)` on `node` and everything in it, bottom-up
 * (or `topDown`). `action` returns nothing to keep the node, a node to
 * replace it, or in a list, a list to splice in its place (`[]` deletes).
 * Returns `node` or its replacement. `type` is the schema type of `node`
 * (default: a document).
 */
export function walk(node: unknown, action: Action,
                     opts: { type?: string; topDown?: boolean; format?: string | Conversion } = {}): unknown {
  const calls: Calls = { fns: new Map(), self: undefined, nodes: () => true, lists: () => false, every: action };
  const w = new Walker(calls, opts.topDown ?? false, node, toConversion(opts.format));
  return w.one(node, opts.type ?? SCHEMA.root);
}

/** Errors already noted with their function and place. */
const noted = new WeakSet<Error>();

let namesCache: Set<string> | undefined;

/** Every name a filter's function can have. */
function names(): Set<string> {
  namesCache ??= new Set([...tables().nodes.keys(), ...tables().constructors.keys(), "Inlines", "Blocks", "Meta"]);
  return namesCache;
}

/** A filter's functions, by name. */
function functionsOf(filter: Filter): Map<string, Call> {
  const known = names();
  const fns = new Map<string, Call>();
  const obj = filter as unknown as Record<string, unknown>;
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
const ALL = () => true;
const NO = () => false;

function applyOne(doc: Pandoc, filter: Filter, conversion: Conversion): Pandoc {
  const root = SCHEMA.root;
  const traverse = filter.traverse ?? "typewise";
  if (traverse !== "typewise" && traverse !== "topdown" && traverse !== "bottomup") {
    throw new Error(`traverse: expected "typewise", "topdown" or "bottomup", got ${JSON.stringify(traverse)}`);
  }
  const fns = functionsOf(filter);
  const topDown = traverse === "topdown";
  const splicing: Splice[] = []; // (every pass's: see Walker.splicing)
  const walker = (d: Pandoc, nodes: (t: string) => boolean, lists: (t: string) => boolean) =>
    new Walker({ fns, self: filter, nodes, lists }, topDown, d, conversion, splicing);
  /** A function on one value (`Meta`, `Pandoc`): its result or the value, and whether it skips. */
  const straight = (name: string, value: unknown, d: Pandoc, frames: Frame[]): [unknown, boolean] => {
    const fn = fns.get(name);
    if (!fn) return [value, false];
    const w = walker(d, NO, NO);
    w.frames = frames;
    const r = w.call(fn, name, value);
    if (r === undefined || r === null) return [value, w.skipped];
    if (Array.isArray(r)) {
      throw new ASTTypeError(formatPath(frames.map((f) => f.field!)) || "the document",
                             `one ${name} (this position holds one)`, "a list");
    }
    return [r, w.skipped];
  };
  const meta = (d: Pandoc): [unknown, boolean] => straight(
    "Meta", d.meta, d, [{ parent: d, field: "meta", outer: [], container: undefined, index: undefined }]);
  if (topDown) {
    const [d, skip] = straight(root, doc, doc, []);
    doc = d as Pandoc; // the function's document, if it returned one, is the one walked
    if (skip) return doc;
    const [m, skipMeta] = meta(doc);
    doc.meta = m as Meta;
    walker(doc, ALL, LISTS).fields(doc, root, (field) => !(skipMeta && field === "meta"));
    return doc;
  }
  if (traverse === "bottomup") {
    const w = walker(doc, ALL, LISTS);
    for (const kid of kidsTable().get(root) ?? NONE) {
      w.children(doc, [kid]);
      if (kid.name === "meta") doc.meta = meta(doc)[0] as Meta;
    }
    return straight(root, doc, doc, [])[0] as Pandoc;
  }
  const passes: [(t: string) => boolean, (t: string) => boolean, boolean][] = [
    [INLINE, NO, has(fns, "Inline")],
    [NO, INLINE, fns.has("Inlines")],
    [BLOCK, NO, has(fns, "Block")],
    [NO, BLOCK, fns.has("Blocks")],
    [OTHER, NO, hasOther(fns)],
  ];
  for (const [nodes, lists, needed] of passes) {
    if (needed) walker(doc, nodes, lists).fields(doc, root);
  }
  doc.meta = meta(doc)[0] as Meta;
  return straight(root, doc, doc, [])[0] as Pandoc;
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
 * returns it (or its replacement). A list of filters is applied one after
 * the other, as a Lua filter file returning a list. `format` is the output
 * format's name, or the whole `Conversion` (then in `ctx.conversion`).
 */
export function applyFilter(doc: Pandoc, filter: Filter, format?: string | Conversion): Pandoc;
export function applyFilter(doc: Pandoc, filters: readonly Filter[], format?: string | Conversion): Pandoc;
export function applyFilter(doc: Pandoc, filter: Filter | readonly Filter[], format?: string | Conversion): Pandoc {
  const conversion = toConversion(format);
  for (const f of isFilterList(filter) ? filter : [filter]) doc = applyOne(doc, f, conversion);
  return doc;
}

function toConversion(format: string | Conversion | undefined): Conversion {
  return format instanceof Conversion ? format : new Conversion({ format });
}

function isFilterList(f: Filter | readonly Filter[]): f is readonly Filter[] {
  return Array.isArray(f);
}
