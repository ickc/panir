/**
 * The generic part of the AST: everything that isn't a declaration.
 *
 * `generated.ts` declares the types and holds the schema (`SCHEMA`); this
 * module interprets the schema, the same way for every type:
 *
 * - decodes and encodes pandoc's JSON, saying where it is wrong
 *   (`ASTDecodeError`);
 * - builds nodes from constructor arguments (`make`), filling defaults,
 *   and checks values put into them (`ASTTypeError`, `check`).
 *
 * Nothing here knows a pandoc type by name.
 */

import { SCHEMA } from "./schema.ts";
import type { Constructor, Field, TypeDecl, TypeExpr } from "./schema.ts";
import type { Pandoc, Types } from "./generated.ts";

export type { Constructor, Field, Schema, TypeDecl, TypeExpr } from "./schema.ts";

// -- errors -------------------------------------------------------------------

/** Where a value is: field names and indices (or keys), from the document or the value given. */
export type Path = (string | number)[];

/** JSON, as `JSON.parse` gives it and `JSON.stringify` takes it. */
export type JSONValue = null | boolean | number | string | JSONValue[] | { [key: string]: JSONValue };

export function formatPath(path: Path): string {
  let out = "";
  for (const p of path) {
    if (typeof p === "number") out += `[${p}]`;
    else if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(p) || !out) out += out ? `.${p}` : p;
    else out += `[${JSON.stringify(p)}]`;
  }
  return out;
}

function message(where: string, expected: string, got: string): string {
  return where ? `${where}: expected ${expected}, got ${got}` : `expected ${expected}, got ${got}`;
}

/**
 * A value that doesn't fit pandoc's AST: `where` it is (`blocks[0].content[1]`,
 * or for a node being made, `Para.content[1]`), its `path` (`["blocks", 0,
 * "content", 1]`), what was `expected`, what it `got`. Each error panir
 * throws for a value is one (`instanceof ASTError`): `ASTDecodeError`,
 * `ASTTypeError` (a `TypeError` too) and `ASTDepthError` (a `RangeError`).
 */
export class ASTError extends Error {
  where: string;
  expected: string;
  got: string;
  path: Path;
  constructor(where: string, expected: string, got: string, path: Path = []) {
    super(message(where, expected, got));
    this.where = where;
    this.expected = expected;
    this.got = got;
    this.path = path;
  }

  // ASTTypeError and ASTDepthError are a TypeError and a RangeError: an
  // ASTError by this test, not by their prototype
  static override [Symbol.hasInstance](x: unknown): x is ASTError {
    const own = Function.prototype[Symbol.hasInstance];
    if (this !== ASTError) return own.call(this, x);
    return own.call(ASTError, x) || own.call(ASTTypeError, x) || own.call(ASTDepthError, x);
  }
}

/** A value of the wrong type: given to a constructor, returned by a filter function, or found by `check` and `toJSON`. */
export class ASTTypeError extends TypeError {
  override name = "ASTTypeError";
  where: string;
  expected: string;
  got: string;
  path: Path;
  constructor(where: string, expected: string, got: string, path: Path = []) {
    super(message(where, expected, got));
    this.where = where;
    this.expected = expected;
    this.got = got;
    this.path = path;
  }
}

/** JSON that isn't a pandoc document (or node) of this API version. */
export class ASTDecodeError extends ASTError {
  override name = "ASTDecodeError";
  constructor(path: Path, expected: string, got: string) {
    super(formatPath(path), expected, got, path);
  }
}

/**
 * A document nested deeper than the engine's call stack lets panir go
 * (writing, walking, copying, comparing it; reading it is an
 * `ASTDecodeError`): `where` says what panir was doing.
 */
export class ASTDepthError extends RangeError {
  override name = "ASTDepthError";
  where: string;
  expected: string;
  got: string;
  path: Path;
  constructor(where: string) {
    const expected = "a document nested less deeply";
    const got = "one deeper than the engine's call stack lets panir go";
    super(message(where, expected, got));
    this.where = where;
    this.expected = expected;
    this.got = got;
    this.path = [];
  }
}

/** Whether `e` is the engine's own error for a call stack overflowed. */
export function isStackOverflow(e: unknown): boolean {
  return e instanceof RangeError && !(e instanceof ASTDepthError) && /call stack/i.test(e.message);
}

/** `f()`, but a call stack overflowed in it is an `ASTDepthError` saying `where`. */
export function deep<T>(where: string, f: () => T): T {
  try {
    return f();
  } catch (e) {
    if (isStackOverflow(e)) throw new ASTDepthError(where);
    throw e;
  }
}

/** Thrown while decoding; the path is collected on the way up. */
class Bad {
  expected: string;
  got: string;
  path: Path = [];
  constructor(expected: string, got: string) {
    this.expected = expected;
    this.got = got;
  }
}

function article(word: string): string {
  return /^[AEIOU]/.test(word) ? "an" : "a";
}

/** A value, briefly, for an error message. */
export function describe(v: unknown): string {
  if (v === null) return "null";
  if (v === undefined) return "undefined";
  if (typeof v === "function") return `a function${v.name ? ` (${v.name})` : ""}`;
  if (typeof v === "bigint") return `${v}n`;
  if (typeof v === "object" && !Array.isArray(v) && typeof (v as { t?: unknown }).t === "string") {
    const t = (v as { t: string }).t;
    const owner = tables().tagOwner.get(t);
    if (owner) return `${t} (${article(owner)} ${owner})`;
    return `"t": ${JSON.stringify(t)} (no such constructor)`;
  }
  let text: string;
  try {
    text = JSON.stringify(v) ?? String(v);
  } catch {
    text = String(v);
  }
  return text.length > 60 ? `${text.slice(0, 57)}...` : text;
}

// -- tables built from the schema -------------------------------------------

interface Tables {
  types: Map<string, TypeDecl>;
  /** constructor name -> its sum type */
  tagOwner: Map<string, string>;
  /** sum type -> its constructors, by name */
  constructors: Map<string, Map<string, Constructor>>;
  /** constructor or product name -> its declaration */
  nodes: Map<string, { fields: Field[]; tag: boolean; encoding: string; owner: string }>;
}

let cache: Tables | undefined;

export function tables(): Tables {
  if (cache) return cache;
  const types = new Map<string, TypeDecl>();
  const tagOwner = new Map<string, string>();
  const nodes: Tables["nodes"] = new Map();
  const constructors: Tables["constructors"] = new Map();
  for (const t of SCHEMA.types) {
    types.set(t.name, t);
    if (t.kind === "sum") {
      constructors.set(t.name, new Map(t.constructors.map((c) => [c.name, c])));
      for (const c of t.constructors) {
        tagOwner.set(c.name, t.name);
        nodes.set(c.name, { fields: c.fields, tag: true, encoding: c.encoding, owner: t.name });
      }
    } else if (t.kind === "product") {
      nodes.set(t.name, { fields: t.fields, tag: false, encoding: t.encoding, owner: t.name });
    }
  }
  cache = { types, tagOwner, nodes, constructors };
  return cache;
}

/** The declaration of a type, by name. */
function declaration(name: string): TypeDecl {
  const t = tables().types.get(name);
  if (t) return t;
  const owner = tables().tagOwner.get(name);
  throw new TypeError(owner ? `${name} is a constructor of the type ${owner}: give ${JSON.stringify(owner)}`
                            : `no pandoc type is called ${JSON.stringify(name)}`);
}

export function resolve(ty: TypeExpr): TypeExpr {
  while ("ref" in ty) {
    const t = declaration(ty.ref);
    if (t.kind !== "alias") break;
    ty = t.type;
  }
  return ty;
}

function typeName(ty: TypeExpr): string {
  ty = resolve(ty);
  if ("prim" in ty) {
    return { string: "a string", int: "an integer", double: "a number", bool: "a boolean" }[ty.prim];
  }
  if ("list" in ty) return `a list of ${typeName(ty.list)}`;
  if ("maybe" in ty) return `${typeName(ty.maybe)} or null`;
  if ("map" in ty) return `an object of ${typeName(ty.map[1])}`;
  if ("tuple" in ty) return `a list of ${ty.tuple.length}`;
  const t = tables().types.get(ty.ref)!;
  return t.kind === "sum" ? t.name : `${article(t.name)} ${t.name}`;
}

/** A plain object: `{}` or one without a prototype (not an array, a Map, a class's). */
function isObject(v: unknown): v is Record<string, unknown> {
  if (typeof v !== "object" || v === null) return false;
  const proto = Object.getPrototypeOf(v);
  return proto === Object.prototype || proto === null;
}

/**
 * A map of the AST (the metadata): an object without a prototype, so that
 * any key is only a key (`__proto__`, `constructor`, `toString`).
 */
function emptyMap(): Record<string, unknown> {
  return Object.create(null) as Record<string, unknown>;
}



const SURROGATE = " (half of a surrogate pair: pandoc reads UTF-8, which has none)";

/** Whether a string has no lone surrogate (true where the engine can't tell). */
function isWellFormed(s: string): boolean {
  return (s as { isWellFormed?(): boolean }).isWellFormed?.() !== false;
}

// What pandoc (aeson) writes for a Double that isn't finite, and reads.
const POSITIVE_INFINITY = "+inf";
const NEGATIVE_INFINITY = "-inf";

function bad(prim: string, v: unknown, hint = ""): never {
  throw new Bad(typeName({ prim } as TypeExpr), describe(v) + hint);
}

/** A string, integer or boolean, which JSON holds as it is. */
function checkPrim(prim: string, v: unknown): unknown {
  switch (prim) {
    case "string":
      if (typeof v !== "string") break;
      if (!isWellFormed(v)) bad(prim, v, SURROGATE);
      return v;
    case "int":
      if (typeof v !== "number" || !Number.isInteger(v)) break;
      // beyond 2^53 JSON.parse has already rounded the number pandoc wrote
      if (!Number.isSafeInteger(v)) bad(prim, v, " (beyond the integers JavaScript's numbers hold exactly)");
      return v;
    case "bool":
      if (typeof v === "boolean") return v;
      break;
  }
  return bad(prim, v);
}

function decodePrim(prim: string, j: unknown): unknown {
  if (prim !== "double") return checkPrim(prim, j);
  if (typeof j === "number") return j;
  if (j === null) return NaN;
  if (j === POSITIVE_INFINITY) return Infinity;
  if (j === NEGATIVE_INFINITY) return -Infinity;
  return bad(prim, j);
}

function encodePrim(prim: string, v: unknown): unknown {
  if (prim !== "double") return checkPrim(prim, v);
  if (typeof v !== "number") return bad(prim, v);
  if (Number.isFinite(v)) return v;
  return Number.isNaN(v) ? null : v > 0 ? POSITIVE_INFINITY : NEGATIVE_INFINITY;
}

// -- decoding and encoding --------------------------------------------------------
//
// Each type's reader and writer is made once from the schema (`reader`,
// `writer`), as a function that holds its parts' readers: going through the
// schema again for every value (which type, which alias, which
// constructor) was more than half of reading a document.

type Codec = (x: unknown) => unknown;

/** Makes a type's reader or writer; by name, one per type, made when first used. */
class Codecs {
  #byName = new Map<string, Codec>();
  #make: (ty: TypeExpr, codecs: Codecs) => Codec;

  constructor(make: (ty: TypeExpr, codecs: Codecs) => Codec) {
    this.#make = make;
  }

  /** The codec of a type expression. */
  of(ty: TypeExpr): Codec {
    if (!("ref" in ty)) return this.#make(ty, this);
    // a named type may hold itself: found when first called
    const name = ty.ref;
    declaration(name);
    let found = this.#byName.get(name);
    return (x) => {
      if (found === undefined) {
        found = this.#byName.get(name);
        if (found === undefined) {
          found = this.#make({ ref: name }, this);
          this.#byName.set(name, found);
        }
      }
      return found(x);
    };
  }
}

/** A function on each of a list's elements, saying at which index an error is. */
function eachOf(item: Codec): (xs: unknown[]) => unknown[] {
  return (xs) => {
    const out = new Array<unknown>(xs.length);
    let i = 0;
    try {
      // by index: a hole in the array is an element too (undefined)
      for (; i < xs.length; i++) out[i] = item(xs[i]);
    } catch (e) {
      if (e instanceof Bad) e.path.push(i);
      throw e;
    }
    return out;
  };
}

/** A function on each of a map's values, into a new map (`fresh`), saying at which key an error is. */
function eachValue(value: Codec, fresh: () => Record<string, unknown>): (obj: Record<string, unknown>) => unknown {
  return (obj) => {
    const out = fresh();
    let key = "";
    try {
      for (key of Object.keys(obj)) {
        if (!isWellFormed(key)) throw new Bad("a key of well-formed text", describe(key) + SURROGATE);
        const x = value(obj[key]);
        // (assigning to __proto__ sets the prototype of an object that has one)
        if (key === "__proto__") Object.defineProperty(out, key, { value: x, writable: true, enumerable: true, configurable: true });
        else out[key] = x;
      }
    } catch (e) {
      if (e instanceof Bad) e.path.push(key);
      throw e;
    }
    return out;
  };
}

/** The readers or writers of a list of types, applied to the elements of a list of that length. */
function tupleOf(items: Codec[], name: () => string): Codec {
  const n = items.length;
  return (xs) => {
    if (!Array.isArray(xs) || xs.length !== n) throw new Bad(name(), describe(xs));
    const out = new Array<unknown>(n);
    let i = 0;
    try {
      for (; i < n; i++) out[i] = items[i](xs[i]);
    } catch (e) {
      if (e instanceof Bad) e.path.push(i);
      throw e;
    }
    return out;
  };
}

/** A node's fields from their JSON (`c`: one value, an array, or an object by key), into `out`. */
function fieldsReader(name: string, fields: Field[], encoding: string, codecs: Codecs): (c: unknown, out: Record<string, unknown>) => void {
  const names = fields.map((f) => f.name);
  const readers = fields.map((f) => codecs.of(f.type));
  const n = fields.length;
  if (encoding === "value") {
    const read = readers[0];
    const field = names[0];
    return (c, out) => {
      try {
        out[field] = read(c);
      } catch (e) {
        if (e instanceof Bad) e.path.push(field);
        throw e;
      }
    };
  }
  if (encoding === "array") {
    return (c, out) => {
      if (!Array.isArray(c) || c.length !== n) {
        throw new Bad(`${name} with ${n} fields`, Array.isArray(c) ? `${name} with ${c.length}` : describe(c));
      }
      let i = 0;
      try {
        for (; i < n; i++) out[names[i]] = readers[i](c[i]);
      } catch (e) {
        if (e instanceof Bad) e.path.push(names[i]);
        throw e;
      }
    };
  }
  const keys = fields.map((f) => f.key);
  return (c, out) => {
    if (!isObject(c)) throw new Bad(`an object (${name})`, describe(c));
    for (const key of keys) {
      if (!Object.hasOwn(c, key)) throw new Bad(`${name} with ${JSON.stringify(key)}`, "one without");
    }
    let i = 0;
    try {
      for (; i < n; i++) out[names[i]] = readers[i](c[keys[i]]);
    } catch (e) {
      if (e instanceof Bad) e.path.push(names[i]);
      throw e;
    }
  };
}

function makeReader(ty: TypeExpr, codecs: Codecs): Codec {
  const r = resolve(ty);
  if ("prim" in r) {
    const prim = r.prim;
    if (prim === "string") return (j) => (typeof j === "string" && isWellFormed(j) ? j : checkPrim(prim, j));
    return (j) => decodePrim(prim, j);
  }
  if ("list" in r) {
    const each = eachOf(codecs.of(r.list));
    return (j) => {
      if (!Array.isArray(j)) throw new Bad("a list", describe(j));
      return each(j);
    };
  }
  if ("maybe" in r) {
    const some = codecs.of(r.maybe);
    return (j) => (j === null ? null : some(j));
  }
  if ("map" in r) {
    const each = eachValue(codecs.of(r.map[1]), emptyMap);
    return (j) => {
      if (!isObject(j)) throw new Bad("an object", describe(j));
      return each(j);
    };
  }
  if ("tuple" in r) {
    const n = r.tuple.length;
    return tupleOf(r.tuple.map((t) => codecs.of(t)), () => `a list of ${n}`);
  }
  const t = declaration(r.ref);
  if (t.kind === "enum") {
    const values = new Set(t.values);
    const expected = `${article(t.name)} ${t.name}`;
    return (j) => {
      if (isObject(j) && typeof j.t === "string" && values.has(j.t)) return j.t;
      throw new Bad(expected, describe(j));
    };
  }
  if (t.kind === "sum") {
    const byTag = new Map<string, ((c: unknown, out: Record<string, unknown>) => void) | null>();
    for (const c of t.constructors) {
      byTag.set(c.name, c.encoding === "none" ? null : fieldsReader(c.name, c.fields, c.encoding, codecs));
    }
    const name = t.name;
    return (j) => {
      if (!isObject(j) || typeof j.t !== "string") throw new Bad(name, describe(j));
      const read = byTag.get(j.t);
      if (read === undefined) throw new Bad(name, describe(j));
      const out: Record<string, unknown> = { t: j.t };
      if (read === null) return out;
      if (!("c" in j)) throw new Bad(`${j.t} with contents`, `${j.t} without`);
      read(j.c, out);
      return out;
    };
  }
  if (t.kind === "product") {
    const read = fieldsReader(t.name, t.fields, t.encoding, codecs);
    if (t.encoding !== "root") {
      return (j) => {
        const out: Record<string, unknown> = {};
        read(j, out);
        return out;
      };
    }
    const want = SCHEMA["pandoc-api-version"];
    const name = t.name;
    return (j) => {
      if (!isObject(j)) throw new Bad(`an object (${name})`, describe(j));
      const v = j["pandoc-api-version"];
      if (!Array.isArray(v) || v[0] !== want[0] || v[1] !== want[1]) {
        const e = new Bad(`pandoc-api-version ${want[0]}.${want[1]}.*`, describe(v));
        e.path.push("pandoc-api-version");
        throw e;
      }
      const out: Record<string, unknown> = {};
      read(j, out);
      return out;
    };
  }
  throw new Error(`unknown type ${r.ref}`);
}

/** A node's fields as JSON: one value, an array, or an object by key. */
function fieldsWriter(fields: Field[], encoding: string, codecs: Codecs): (v: Record<string, unknown>) => unknown {
  const names = fields.map((f) => f.name);
  const writers = fields.map((f) => codecs.of(f.type));
  const keys = fields.map((f) => f.key);
  const n = fields.length;
  return (v) => {
    const values = new Array<unknown>(n);
    let i = 0;
    try {
      for (; i < n; i++) values[i] = writers[i](v[names[i]]);
    } catch (e) {
      if (e instanceof Bad) e.path.push(names[i]);
      throw e;
    }
    if (encoding === "value") return values[0];
    if (encoding === "array") return values;
    const out: Record<string, unknown> = {};
    for (i = 0; i < n; i++) out[keys[i]] = values[i];
    return out;
  };
}

function makeWriter(ty: TypeExpr, codecs: Codecs): Codec {
  const r = resolve(ty);
  if ("prim" in r) {
    const prim = r.prim;
    if (prim === "string") return (v) => (typeof v === "string" && isWellFormed(v) ? v : checkPrim(prim, v));
    return (v) => encodePrim(prim, v);
  }
  if ("list" in r) {
    const each = eachOf(codecs.of(r.list));
    return (v) => {
      if (!Array.isArray(v)) throw new Bad("a list", describe(v));
      return each(v);
    };
  }
  if ("maybe" in r) {
    const some = codecs.of(r.maybe);
    return (v) => (v === null || v === undefined ? null : some(v));
  }
  if ("map" in r) {
    const each = eachValue(codecs.of(r.map[1]), () => ({})); // JSON: an ordinary object
    return (v) => {
      if (!isObject(v)) throw new Bad("an object", describe(v));
      return each(v);
    };
  }
  if ("tuple" in r) {
    return tupleOf(r.tuple.map((t) => codecs.of(t)), () => typeName(r));
  }
  const t = declaration(r.ref);
  if (t.kind === "enum") {
    const asJSON = new Map(t.values.map((v) => [v, { t: v }]));
    const expected = `${article(t.name)} ${t.name}`;
    return (v) => {
      const j = typeof v === "string" ? asJSON.get(v) : undefined;
      if (j === undefined) throw new Bad(expected, describe(v));
      return { t: j.t };
    };
  }
  if (t.kind === "sum") {
    const byTag = new Map<string, ((v: Record<string, unknown>) => unknown) | null>();
    for (const c of t.constructors) {
      byTag.set(c.name, c.encoding === "none" ? null : fieldsWriter(c.fields, c.encoding, codecs));
    }
    const name = t.name;
    return (v) => {
      if (!isObject(v) || typeof v.t !== "string") throw new Bad(name, describe(v));
      const write = byTag.get(v.t);
      if (write === undefined) throw new Bad(name, describe(v));
      return write === null ? { t: v.t } : { t: v.t, c: write(v) };
    };
  }
  if (t.kind === "product") {
    const write = fieldsWriter(t.fields, t.encoding, codecs);
    const expected = `${article(t.name)} ${t.name}`;
    const root = t.encoding === "root";
    return (v) => {
      if (!isObject(v)) throw new Bad(expected, describe(v));
      const body = write(v);
      return root ? { "pandoc-api-version": [...SCHEMA["pandoc-api-version"]], ...(body as object) } : body;
    };
  }
  throw new Error(`unknown type ${r.ref}`);
}

const readers = new Codecs(makeReader);
const writers = new Codecs(makeWriter);
const readerOf = new WeakMap<TypeExpr, Codec>();

/** A value of a type from its JSON. */
function decode(ty: TypeExpr, j: unknown): unknown {
  let read = readerOf.get(ty);
  if (read === undefined) readerOf.set(ty, (read = readers.of(ty)));
  return read(j);
}

const rootReader = new Map<string, Codec>();
const rootWriter = new Map<string, Codec>();

/**
 * A value of a type (default: a document) from pandoc's JSON, checked:
 * `ASTDecodeError` says where it is wrong. JSON deeper than the engine's
 * call stack lets panir go is an `ASTDecodeError` too.
 */
export function fromJSON(j: unknown): Pandoc;
export function fromJSON<K extends keyof Types>(j: unknown, type: K): Types[K];
export function fromJSON(j: unknown, type: string = SCHEMA.root): unknown {
  let read = rootReader.get(type);
  if (read === undefined) rootReader.set(type, (read = readers.of({ ref: type })));
  const reader = read;
  try {
    return reader(j);
  } catch (e) {
    if (e instanceof Bad) throw new ASTDecodeError(e.path.reverse(), e.expected, e.got);
    // the functions here call themselves for each level of a document
    if (isStackOverflow(e)) throw new ASTDecodeError([], "JSON nested less deeply", "JSON deeper than the engine's call stack lets panir go");
    throw e;
  }
}

/**
 * A value (default: a document) as pandoc's JSON; checks it on the way:
 * `ASTTypeError` says where it is wrong, as `fromJSON` would of that JSON.
 */
export function toJSON(v: Pandoc): JSONValue;
export function toJSON<K extends keyof Types>(v: Types[K], type: K): JSONValue;
export function toJSON(v: unknown, type: string = SCHEMA.root): JSONValue {
  let write = rootWriter.get(type);
  if (write === undefined) rootWriter.set(type, (write = writers.of({ ref: type })));
  const writer = write;
  try {
    return writer(v) as JSONValue;
  } catch (e) {
    if (e instanceof Bad) {
      const path = e.path.reverse();
      throw new ASTTypeError(formatPath(path), e.expected, e.got, path);
    }
    if (isStackOverflow(e)) throw new ASTDepthError("toJSON");
    throw e;
  }
}

/** Throw `ASTTypeError`, saying where, unless a value is well-typed (default: a document). */
export function check(v: unknown): asserts v is Pandoc;
export function check<K extends keyof Types>(v: unknown, type: K): asserts v is Types[K];
export function check(v: unknown, type: string = SCHEMA.root): void {
  toJSON(v as never, type as never);
}

// -- conversions, as pandoc's Lua makes them -----------------------------------

/**
 * Words and spaces, as pandoc-types' `text` (and pandoc's Lua) split a
 * string: `Str` for each run of non-spaces, and for each run of spaces
 * `SoftBreak` if it has a newline, else `Space`.
 */
export function textInlines(s: string): unknown[] {
  const out: unknown[] = [];
  for (const [run] of s.matchAll(/[ \t\n\r]+|[^ \t\n\r]+/g)) {
    if (" \t\n\r".includes(run[0])) out.push({ t: /[\n\r]/.test(run) ? "SoftBreak" : "Space" });
    else out.push({ t: "Str", text: run });
  }
  return out;
}

/** The sum type `x` is a node of, by its tag; undefined if none. */
export function ownerOf(x: unknown): string | undefined {
  if (typeof x !== "object" || x === null) return undefined;
  const t = (x as { t?: unknown }).t;
  return typeof t === "string" ? tables().tagOwner.get(t) : undefined;
}

let productsByFields: Map<string, string> | undefined;

/** The product a node without a tag is, by its fields (TableHead and TableFoot have the same: the first). */
export function productOf(x: object): string | undefined {
  if (!productsByFields) {
    productsByFields = new Map();
    for (const [name, decl] of tables().types) {
      if (decl.kind !== "product") continue;
      const key = decl.fields.map((f) => f.name).sort().join(" ");
      if (!productsByFields.has(key)) productsByFields.set(key, name);
    }
  }
  return productsByFields.get(Object.keys(x).sort().join(" "));
}

/** A Plain of inlines and strings (a Str each, whole). */
function plainOf(xs: readonly unknown[]): unknown {
  return { t: "Plain", content: xs.map((x) => (typeof x === "string" ? { t: "Str", text: x } : x)) };
}

/** Whether every element is an inline or a string. */
function allInlines(xs: readonly unknown[]): boolean {
  for (const x of xs) if (typeof x !== "string" && ownerOf(x) !== "Inline") return false;
  return true;
}

/**
 * A value as one of the sum or product `ref`, converted as pandoc's Lua
 * converts it: where one inline goes, a string is a `Str`; where one block
 * goes, a string is `Plain` text, and an inline, or a list of inlines and
 * strings (a `Str` each), a `Plain` of them; where metadata goes, plain
 * values (`toMetaValue`). Anything else is left for the checks.
 */
function convertOne(ref: string, v: unknown): unknown {
  if (ref === "Inline") return typeof v === "string" ? { t: "Str", text: v } : v;
  if (ref === "Block") {
    if (typeof v === "string") return { t: "Plain", content: textInlines(v) };
    if (ownerOf(v) === "Inline") return { t: "Plain", content: [v] };
    if (Array.isArray(v) && allInlines(v)) return plainOf(v);
    return v;
  }
  if (ref === "MetaValue") return toMetaValue(v);
  return v;
}

/** Whether a list of `item` takes one value as a list of it: lists of inlines and blocks, and lists of those. */
function takesOne(item: TypeExpr): boolean {
  const r = resolve(item);
  if ("ref" in r) return r.ref === "Inline" || r.ref === "Block";
  return "list" in r && takesOne(r.list);
}

/**
 * A value as a list of `item`, converted as pandoc's Lua converts it: each
 * element (`convert`); where inlines go, a string is its words and spaces,
 * where blocks go `Plain` text, and one node (or, for blocks, an inline) a
 * list of it; attributes may be a record. A new array.
 */
function convertList(item: TypeExpr, v: unknown): unknown {
  if (Array.isArray(v)) {
    const out = new Array<unknown>(v.length);
    for (let i = 0; i < v.length; i++) out[i] = convert(item, v[i]);
    return out;
  }
  const r = resolve(item);
  if (typeof v === "string" || (isObject(v) && typeof v.t === "string")) {
    if ("ref" in r) {
      if (r.ref === "Inline") return typeof v === "string" ? textInlines(v) : [v];
      if (r.ref === "Block") return [convertOne("Block", v)];
    } else if (takesOne(r)) {
      return [convertList((r as { list: TypeExpr }).list, v)];
    }
  }
  if ("tuple" in r && isObject(v) && r.tuple.every((t) => "prim" in t && t.prim === "string")) {
    return Object.entries(v); // attributes as a record
  }
  return v;
}

/** A value as one of type `ty`, converted as pandoc's Lua converts it (`convertOne`, `convertList`). */
function convert(ty: TypeExpr, v: unknown): unknown {
  if (v === null || v === undefined) return v;
  const r = resolve(ty);
  if ("prim" in r) return v;
  if ("ref" in r) return convertOne(r.ref, v);
  if ("list" in r) return convertList(r.list, v);
  if ("maybe" in r) return convert(r.maybe, v);
  if ("tuple" in r) {
    if (!Array.isArray(v)) return v;
    return v.map((x, i) => (i < r.tuple.length ? convert(r.tuple[i], x) : x));
  }
  // a map: into one without a prototype
  if (!isObject(v)) return v;
  const out = emptyMap();
  for (const k of Object.keys(v)) setKey(out, k, convert(r.map[1], v[k]));
  return out;
}

/** `obj[key] = x`, `__proto__` too (assigning to it sets the prototype of an object that has one). */
export function setKey(obj: Record<string, unknown>, key: string, x: unknown): void {
  if (key === "__proto__") Object.defineProperty(obj, key, { value: x, writable: true, enumerable: true, configurable: true });
  else obj[key] = x;
}

/**
 * A value as metadata, as pandoc's Lua converts it: a `MetaValue` as it
 * is, a string a `MetaString`, a boolean a `MetaBool`, a number a
 * `MetaString` of it, an inline or a list of inlines `MetaInlines`, a
 * block or a list of blocks `MetaBlocks`, another list a `MetaList`, an
 * object a `MetaMap`. Anything else as it is (for the checks to refuse).
 */
export function toMetaValue(v: unknown): unknown {
  switch (typeof v) {
    case "string":
      return { t: "MetaString", text: v };
    case "boolean":
      return { t: "MetaBool", value: v };
    case "number":
      return { t: "MetaString", text: String(v) };
  }
  if (Array.isArray(v)) {
    if (v.length && v.every((x) => ownerOf(x) === "Inline")) return { t: "MetaInlines", content: [...v] };
    if (v.length && v.every((x) => ownerOf(x) === "Block")) return { t: "MetaBlocks", content: [...v] };
    return { t: "MetaList", content: v.map(toMetaValue) };
  }
  if (!isObject(v)) return v;
  switch (ownerOf(v)) {
    case "MetaValue":
      return v;
    case "Inline":
      return { t: "MetaInlines", content: [v] };
    case "Block":
      return { t: "MetaBlocks", content: [v] };
  }
  const content = emptyMap();
  for (const k of Object.keys(v)) setKey(content, k, toMetaValue(v[k]));
  return { t: "MetaMap", content };
}

/** Metadata (a map of `MetaValue`s) from a map of what converts to them (`toMetaValue`), in a new map without a prototype. */
export function toMeta(v: unknown): unknown {
  return convert({ map: [{ prim: "string" }, { ref: "MetaValue" }] }, v);
}

// -- checks ---------------------------------------------------------------------

/**
 * Check a value: its own shape, and of the nodes in it only that each is
 * one of the right type (its tag). What is in those was checked when they
 * were made, and all of it is when the document is written. (Checking it
 * all here checked a document's every node once for each node above it.)
 */
function checkArgument(ty: TypeExpr, v: unknown): void {
  const r = resolve(ty);
  if ("prim" in r) {
    encodePrim(r.prim, v);
    return;
  }
  if ("maybe" in r) {
    if (v !== null && v !== undefined) checkArgument(r.maybe, v);
    return;
  }
  // the value itself, then what is in it, each at its place
  let inside: () => void;
  let at: string | number = 0;
  if ("list" in r) {
    if (!Array.isArray(v)) throw new Bad("a list", describe(v));
    inside = () => {
      for (let i = 0; i < v.length; i++) {
        at = i;
        checkArgument(r.list, v[i]);
      }
    };
  } else if ("map" in r) {
    if (!isObject(v)) throw new Bad("an object", describe(v));
    inside = () => {
      for (const key of Object.keys(v)) {
        at = key;
        if (!isWellFormed(key)) throw new Bad("a key of well-formed text", describe(key) + SURROGATE);
        checkArgument(r.map[1], v[key]);
      }
    };
  } else if ("tuple" in r) {
    if (!Array.isArray(v) || v.length !== r.tuple.length) throw new Bad(typeName(r), describe(v));
    inside = () => {
      for (let i = 0; i < v.length; i++) {
        at = i;
        checkArgument(r.tuple[i], v[i]);
      }
    };
  } else {
    const t = declaration(r.ref);
    if (t.kind === "enum") {
      if (typeof v !== "string" || !t.values.includes(v)) throw new Bad(`${article(t.name)} ${t.name}`, describe(v));
      return;
    }
    if (t.kind === "sum") {
      // a node: its tag says whether it is one of these
      if (!isObject(v) || typeof v.t !== "string" || !tables().constructors.get(t.name)!.has(v.t)) {
        throw new Bad(t.name, describe(v));
      }
      return;
    }
    if (t.kind !== "product") return;
    if (!isObject(v)) throw new Bad(`${article(t.name)} ${t.name}`, describe(v));
    inside = () => {
      for (const f of t.fields) {
        at = f.name;
        checkArgument(f.type, v[f.name]);
      }
    };
  }
  try {
    inside();
  } catch (e) {
    if (e instanceof Bad) e.path.push(at);
    throw e;
  }
}

/**
 * Whether `x` is a node of the sum or product `type`, as constructors check
 * their arguments: a sum's node by its tag, a product by its fields (and the
 * tags of the nodes in them).
 */
export function isNodeOf(type: string, x: unknown): boolean {
  const tags = tables().constructors.get(type);
  if (tags) return typeof x === "object" && x !== null && tags.has((x as { t?: string }).t!) && isObject(x);
  try {
    checkArgument({ ref: type }, x);
    // (and no field of another: a Cell isn't a Caption)
    const fields = tables().nodes.get(type)?.fields;
    if (fields) for (const k of Object.keys(x as object)) if (!fields.some((f) => f.name === k)) return false;
    return true;
  } catch (e) {
    if (e instanceof Bad) return false;
    throw e;
  }
}

/** What a value of the sum or product `type` is called in an error: "Inline", "a Row". */
export function nameOf(type: string): string {
  return typeName({ ref: type });
}

/**
 * What a filter function returned for a node of a list of `type`, as the
 * nodes to put there: an inline's or a block's converted as pandoc's Lua
 * converts it (a list element by element; a string where inlines go its
 * words, where blocks go `Plain` text; one node a list of it), in a new
 * array; another node's as it is (Lua has no functions for those: no
 * conversions); undefined if it isn't anything of the sort. The caller
 * checks each node.
 */
export function returnedNodes(type: string, r: unknown): unknown[] | undefined {
  const lua = type === "Inline" || type === "Block";
  if (Array.isArray(r)) return lua ? convertList({ ref: type }, r) as unknown[] : [...r];
  if (typeof r === "string") return lua ? convertList({ ref: type }, r) as unknown[] : undefined;
  if (typeof r !== "object" || r === null) return undefined;
  return [lua ? convertOne(type, r) : r];
}

// -- constructors ---------------------------------------------------------------

/**
 * A constructor's argument for a field of type `ty`, converted and checked
 * (`ASTTypeError` saying where): made once per field. A string, an
 * integer or a boolean is only checked.
 */
function coercer(ty: TypeExpr, name: string, field: string): (v: unknown) => unknown {
  const fail = (e: unknown): never => {
    if (!(e instanceof Bad)) throw e;
    const path = [field, ...e.path.reverse()];
    throw new ASTTypeError(formatPath([name, ...path]), e.expected, e.got, path);
  };
  const r = resolve(ty);
  if ("prim" in r) {
    const prim = r.prim;
    return (v) => {
      if (prim === "string" && typeof v === "string" && isWellFormed(v)) return v;
      try {
        encodePrim(prim, v);
      } catch (e) {
        fail(e);
      }
      return v;
    };
  }
  return (v) => {
    v = convert(ty, v);
    try {
      checkArgument(ty, v);
    } catch (e) {
      fail(e);
    }
    return v;
  };
}

/** A node's fields as its constructor fills them: `flat`, a product whose fields may be given as options. */
interface Maker {
  tag: boolean;
  fields: { f: Field; flat: { name: string; fields: Field[] } | undefined; coerce: (v: unknown) => unknown }[];
  /** The names of its options. */
  known: Set<string>;
}

const makers = new Map<string, Maker>();

function maker(name: string): Maker {
  let m = makers.get(name);
  if (m) return m;
  const node = tables().nodes.get(name)!;
  const known = new Set<string>();
  const fields = node.fields.map((f) => {
    known.add(f.name);
    const coerce = coercer(f.type, name, f.name);
    if (!f.flatten) return { f, flat: undefined, coerce };
    const product = (f.type as { ref: string }).ref;
    const parts = tables().nodes.get(product)!.fields;
    for (const g of parts) known.add(g.name);
    return { f, flat: { name: product, fields: parts }, coerce };
  });
  m = { tag: node.tag, fields, known };
  makers.set(name, m);
  return m;
}

/** Build a node from a generated constructor's arguments; `ASTTypeError` for any that is wrong. */
export function make<T>(name: string, given: Record<string, unknown>,
                        opts: Record<string, unknown> = {}): T {
  const m = maker(name);
  if (typeof opts !== "object" || opts === null || Array.isArray(opts)) {
    throw new ASTTypeError(name, "an object of options", describe(opts), []);
  }
  const out: Record<string, unknown> = m.tag ? { t: name } : {};
  for (const { f, flat, coerce } of m.fields) {
    let v = given[f.name];
    if (v === undefined) v = opts[f.name];
    if (flat) {
      const parts = flat.fields.filter((g) => opts[g.name] !== undefined);
      if (v !== undefined && parts.length) {
        throw new ASTTypeError(name, `either ${f.name} or ${parts.map((g) => g.name).join(", ")}`, "both", [f.name]);
      }
      if (v === undefined) {
        const missing = flat.fields.find((g) => !("default" in g) && opts[g.name] === undefined);
        if (missing) {
          throw new ASTTypeError(`${name}.${missing.name}`, `${typeName(missing.type)} (or ${f.name})`, "undefined", [missing.name]);
        }
        const own: Record<string, unknown> = {};
        for (const g of parts) own[g.name] = opts[g.name];
        v = make<unknown>(flat.name, {}, own);
      }
    }
    if (v === undefined) {
      if (!("default" in f)) throw new ASTTypeError(`${name}.${f.name}`, typeName(f.type), "undefined", [f.name]);
      v = decode(f.type, f.default); // a fresh value each time
    }
    out[f.name] = coerce(v);
  }
  for (const k of Object.keys(opts)) {
    if (!m.known.has(k)) {
      const known = [...m.known].filter((o) => !(o in given));
      throw new ASTTypeError(name, `an option of ${name}'s (${known.join(", ")})`, JSON.stringify(k), [k]);
    }
  }
  return out as T;
}
