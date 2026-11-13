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

/** Contexts whose function asked to skip the node's children. */
const skipping = new WeakSet<Context>();

/** Where a node is: its parent, its place in it, the document, the conversion. */
export class Context {
  /** The node whose field holds this one. */
  parent: unknown;
  /** That field's name. */
  field: string | undefined;
  /** The list holding this node, if in one, and its index there (or key, in a map). */
  container: unknown[] | Record<string, unknown> | undefined;
  index: number | string | undefined;
  doc: unknown;
  /** The pandoc conversion the filter runs in. */
  conversion: Conversion;
  /** Its output format's name (what pandoc passes a JSON filter), e.g. "html5". */
  format: string | undefined;
  #path: Path;
  #ancestors: unknown[];
  #topDown: boolean;

  constructor(frames: Frame[], doc: unknown, conversion: Conversion, topDown = false) {
    this.#topDown = topDown;
    const last = frames[frames.length - 1];
    this.parent = last?.parent;
    this.field = last?.field;
    this.container = last?.container;
    this.index = last?.index;
    this.doc = doc;
    this.conversion = conversion;
    this.format = conversion.format;
    this.#path = frames.flatMap((f) => [
      ...(f.field === undefined ? [] : [f.field]),
      ...f.outer,
      ...(f.index === undefined ? [] : [f.index]),
    ]);
    this.#ancestors = [];
    for (const f of frames) {
      if (this.#ancestors[this.#ancestors.length - 1] !== f.parent) this.#ancestors.push(f.parent);
    }
  }

  /** From the document, e.g. `["blocks", 3, "content", 1]`. */
  get path(): Path {
    return [...this.#path];
  }

  /** The path as text, e.g. `blocks[3].content[1]`. */
  get where(): string {
    return formatPath(this.#path) || "the document";
  }

  /** The nodes above this one, the document first. */
  get ancestors(): unknown[] {
    return [...this.#ancestors];
  }

  #sibling(step: number): unknown {
    if (!Array.isArray(this.container) || typeof this.index !== "number") return undefined;
    return this.container[this.index + step];
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
    skipping.add(this);
  }
}

interface Frame {
  parent: unknown;
  field: string | undefined;
  outer: (string | number)[];
  container: unknown[] | Record<string, unknown> | undefined;
  index: number | string | undefined;
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

/** What a function returned for a position that holds one value: not a list, not a promise. */
function oneValue(r: unknown, what: string, where: string): void {
  if (Array.isArray(r)) throw new ASTTypeError(where, `one ${what} (this position holds one)`, "a list");
  if (typeof (r as { then?: unknown } | null)?.then === "function") {
    throw new ASTTypeError(where, `a ${what}`, "a promise (filter functions can't be async)");
  }
}

type Action = (node: unknown, ctx: Context, type: string) => unknown;
/** As `Action`, making the context only if it needs it (`ctx()`). */
type LazyAction = (node: unknown, ctx: () => Context, type: string) => unknown;
/** Called on a list of nodes of type `type`; returns its replacement, or nothing. */
type ListAction = (list: unknown[], ctx: () => Context, type: string) => unknown;

class Walker {
  frames: Frame[] = [];
  action: LazyAction;
  listAction: ListAction | undefined;
  topDown: boolean;
  doc: unknown;
  conversion: Conversion;

  constructor(action: LazyAction, topDown: boolean, doc: unknown, conversion: Conversion,
              listAction?: ListAction) {
    this.action = action;
    this.listAction = listAction;
    this.topDown = topDown;
    this.doc = doc;
    this.conversion = conversion;
  }

  /** Where the walk is, from the document. */
  #path(): Path {
    return this.frames.flatMap((f) => [
      ...(f.field === undefined ? [] : [f.field]),
      ...f.outer,
      ...(f.index === undefined ? [] : [f.index]),
    ]);
  }

  /** Call `fn` with a context made on demand; returns its result, and whether it skips the children. */
  #call(fn: (ctx: () => Context) => unknown): [unknown, boolean] {
    let ctx: Context | undefined;
    const r = fn(() => (ctx ??= new Context(this.frames, this.doc, this.conversion, this.topDown)));
    return [r, ctx !== undefined && skipping.has(ctx)];
  }

  /** A node in a position holding one node; returns it or its replacement. */
  one(node: unknown, type: string, where: string): unknown {
    if (this.topDown) {
      const [r, skip] = this.#call((ctx) => this.action(node, ctx, type));
      node = single(r, node, where);
      if (!skip) this.children(node, type);
      return node;
    }
    this.children(node, type);
    return single(this.#call((ctx) => this.action(node, ctx, type))[0], node, where);
  }

  /** The nodes of a list: each may be replaced, deleted or spliced; then (or first, top-down) the list. */
  many(list: unknown[], type: string, parent: unknown, field: string, outer: (string | number)[]): void {
    const frame: Frame = { parent, field, outer, container: undefined, index: undefined };
    if (this.topDown && this.#wholeList(list, type, frame)) return;
    frame.container = list;
    frame.index = 0;
    this.frames.push(frame);
    try {
      let i = 0;
      while (i < list.length) {
        frame.index = i;
        if (!this.topDown) this.children(list[i], type);
        const [r, skip] = this.#call((ctx) => this.action(list[i], ctx, type));
        let items: readonly unknown[];
        if (r === undefined || r === null) {
          items = [list[i]];
        } else if (Array.isArray(r)) {
          items = distinct(r);
        } else {
          oneValue(r, type, formatPath(this.#path()));
          items = [r];
        }
        if (items.length !== 1 || items[0] !== list[i]) replace(list, i, 1, items);
        if (this.topDown && !skip) {
          for (let k = 0; k < items.length; k++) {
            frame.index = i + k;
            this.children(list[i + k], type);
          }
        }
        i += items.length;
      }
    } finally {
      this.frames.pop();
    }
    if (!this.topDown) this.#wholeList(list, type, { ...frame, container: undefined, index: undefined });
  }

  /** The list function on `list`, replacing its contents in place; whether it skips the elements. */
  #wholeList(list: unknown[], type: string, frame: Frame): boolean {
    const listAction = this.listAction;
    if (!listAction) return false;
    this.frames.push(frame);
    try {
      const [r, skip] = this.#call((ctx) => listAction(list, ctx, type));
      if (r !== undefined && r !== null && r !== list) {
        if (!Array.isArray(r)) {
          throw new ASTTypeError(formatPath(this.#path()), `a list of ${type}s`, typeof r);
        }
        const items = [...distinct(r)]; // (a copy: `r` may hold `list` itself)
        list.length = 0;
        for (const x of items) list.push(x);
      }
      return skip;
    } finally {
      this.frames.pop();
    }
  }

  /** Walk a field's value; returns it, or its replacement. */
  value(v: unknown, ty: TypeExpr, parent: unknown, field: string, outer: (string | number)[]): unknown {
    const r = resolve(ty);
    if ("prim" in r || v === null || v === undefined) return v;
    if ("ref" in r) {
      const t = tables().types.get(r.ref)!;
      if (t.kind === "enum") return v;
      this.frames.push({ parent, field, outer, container: undefined, index: undefined });
      try {
        return this.one(v, r.ref, formatPath(this.#path()));
      } finally {
        this.frames.pop();
      }
    }
    if ("maybe" in r) return this.value(v, r.maybe, parent, field, outer);
    if ("list" in r) {
      const item = resolve(r.list);
      const list = v as unknown[];
      if ("ref" in item && tables().types.get(item.ref)!.kind !== "enum") {
        this.many(list, item.ref, parent, field, outer);
      } else if (!("prim" in item)) {
        list.forEach((x, i) => {
          const y = this.value(x, r.list, parent, field, [...outer, i]);
          if (y !== x) list[i] = y;
        });
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
            y = this.one(obj[k], (item as { ref: string }).ref, formatPath(this.#path()));
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

  children(node: unknown, type: string): void {
    const decl = tables().types.get(type)!;
    let fields;
    if (decl.kind === "sum") {
      const tag = (node as { t: string }).t;
      fields = tables().nodes.get(tag)?.fields ?? [];
    } else if (decl.kind === "product") {
      fields = decl.fields;
    } else {
      return;
    }
    const obj = node as Record<string, unknown>;
    for (const f of fields) {
      const y = this.value(obj[f.name], f.type, node, f.name, []);
      if (y !== obj[f.name]) obj[f.name] = y;
    }
  }
}

function single(r: unknown, node: unknown, where: string): unknown {
  if (r === undefined || r === null) return node;
  oneValue(r, "node", where);
  return r;
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
  const w = new Walker((n, ctx, t) => action(n, ctx(), t), opts.topDown ?? false, node, toConversion(opts.format));
  const type = opts.type ?? SCHEMA.root;
  return w.one(node, type, type);
}

type Fns = Record<string, ((x: unknown, ctx: Context) => unknown) | undefined>;

/** Errors already noted with their function and place. */
const noted = new WeakSet<Error>();

/**
 * `fn(x, ctx)`, as a method of its filter (`this`); an error from it says
 * which function it was, and where.
 */
function callNoting(fns: Fns, fn: (x: unknown, ctx: Context) => unknown, what: string, x: unknown, ctx: Context): unknown {
  try {
    return fn.call(fns, x, ctx);
  } catch (e) {
    if (e instanceof Error && !noted.has(e)) {
      noted.add(e);
      try {
        e.message += `\n  in filter function ${fn.name || "(anonymous)"}, on the ${what} at ${ctx.where}`;
      } catch {
        // an error whose message can't be changed (a DOMException, a frozen one)
      }
    }
    throw e;
  }
}

/** The function for a node: its constructor's, else its type's; only for types `only` accepts. */
function nodeAction(fns: Fns, only: (type: string) => boolean): LazyAction {
  return (node, ctx, type) => {
    if (!only(type)) return undefined;
    const tag = (node as { t?: unknown }).t;
    const what = typeof tag === "string" ? tag : type;
    const fn = fns[what] ?? fns[type];
    return fn ? callNoting(fns, fn, what, node, ctx()) : undefined;
  };
}

/** The `Inlines` or `Blocks` function, for lists of the types `only` accepts. */
function listAction(fns: Fns, only: (type: string) => boolean): ListAction {
  return (list, ctx, type) => {
    const fn = only(type) ? fns[`${type}s`] : undefined;
    return fn ? callNoting(fns, fn, `${type}s`, list, ctx()) : undefined;
  };
}

/** A function called on one value (`Meta`, `Pandoc`): its result or `value`, and whether it skips. */
function straight(fns: Fns, name: string, value: unknown, frames: Frame[], doc: unknown,
                  conversion: Conversion, topDown: boolean): [unknown, boolean] {
  const fn = fns[name];
  if (!fn) return [value, false];
  const ctx = new Context(frames, doc, conversion, topDown);
  const r = callNoting(fns, fn, name, value, ctx);
  if (r === undefined || r === null) return [value, skipping.has(ctx)];
  oneValue(r, name, ctx.where);
  return [r, skipping.has(ctx)];
}

const INLINE = (t: string) => t === "Inline";
const BLOCK = (t: string) => t === "Block";
const LISTS = (t: string) => t === "Inline" || t === "Block";
const OTHER = (t: string) => t !== "Inline" && t !== "Block";
const NONE = () => false;

function applyOne(doc: Pandoc, filter: Filter, conversion: Conversion): Pandoc {
  const fns = filter as unknown as Fns;
  const root = SCHEMA.root;
  const traverse = filter.traverse ?? "typewise";
  if (traverse !== "typewise" && traverse !== "topdown" && traverse !== "bottomup") {
    throw new Error(`traverse: expected "typewise", "topdown" or "bottomup", got ${JSON.stringify(traverse)}`);
  }
  const topDown = traverse === "topdown";
  const meta = (d: Pandoc): [unknown, boolean] => straight(
    fns, "Meta", d.meta, [{ parent: d, field: "meta", outer: [], container: undefined, index: undefined }],
    d, conversion, topDown);
  const fields = (tables().types.get(root) as { fields: { name: string; type: TypeExpr }[] }).fields;
  const all = (d: Pandoc) => new Walker(nodeAction(fns, () => true), topDown, d, conversion, listAction(fns, LISTS));
  if (topDown) {
    const [d, skip] = straight(fns, root, doc, [], doc, conversion, true);
    doc = d as Pandoc; // the function's document, if it returned one, is the one walked
    if (skip) return doc;
    const [m, skipMeta] = meta(doc);
    doc.meta = m as Meta;
    const w = all(doc);
    const obj = doc as unknown as Record<string, unknown>;
    for (const f of fields) {
      if (!(skipMeta && f.name === "meta")) obj[f.name] = w.value(obj[f.name], f.type, doc, f.name, []);
    }
    return doc;
  }
  const obj = doc as unknown as Record<string, unknown>;
  if (traverse === "bottomup") {
    const w = all(doc);
    for (const f of fields) {
      obj[f.name] = w.value(obj[f.name], f.type, doc, f.name, []);
      if (f.name === "meta") doc.meta = meta(doc)[0] as Meta;
    }
    return straight(fns, root, doc, [], doc, conversion, false)[0] as Pandoc;
  }
  const passes: [LazyAction, ListAction | undefined, boolean][] = [
    [nodeAction(fns, INLINE), undefined, has(fns, "Inline")],
    [nodeAction(fns, NONE), listAction(fns, INLINE), fns.Inlines !== undefined],
    [nodeAction(fns, BLOCK), undefined, has(fns, "Block")],
    [nodeAction(fns, NONE), listAction(fns, BLOCK), fns.Blocks !== undefined],
    [nodeAction(fns, OTHER), undefined, hasOther(fns)],
  ];
  for (const [action, lists, needed] of passes) {
    if (needed) new Walker(action, false, doc, conversion, lists).children(doc, root);
  }
  doc.meta = meta(doc)[0] as Meta;
  return straight(fns, root, doc, [], doc, conversion, false)[0] as Pandoc;
}

/** Whether `fns` has a function for the sum `sum` or one of its constructors. */
function has(fns: Fns, sum: string): boolean {
  const decl = tables().types.get(sum) as { constructors?: { name: string }[] };
  return fns[sum] !== undefined || (decl.constructors ?? []).some((c) => fns[c.name] !== undefined);
}

/** Whether `fns` has a function for a node that isn't an Inline, a Block or the document. */
function hasOther(fns: Fns): boolean {
  for (const [name, decl] of tables().types) {
    if (name === "Inline" || name === "Block" || name === SCHEMA.root) continue;
    if (decl.kind === "sum" ? has(fns, name) : decl.kind === "product" && fns[name] !== undefined) return true;
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
