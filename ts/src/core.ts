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

import { SCHEMA } from "./generated.ts";

// -- the schema's shape ---------------------------------------------------------

export type TypeExpr =
  | { prim: "string" | "int" | "double" | "bool" }
  | { ref: string }
  | { list: TypeExpr }
  | { maybe: TypeExpr }
  | { map: [TypeExpr, TypeExpr] }
  | { tuple: TypeExpr[] };

export interface Field {
  name: string;
  key: string;
  type: TypeExpr;
  default?: unknown;
  flatten?: boolean;
}

export interface Constructor {
  name: string;
  encoding: string;
  fields: Field[];
  variadic?: string;
}

export type TypeDecl =
  | { name: string; kind: "sum"; constructors: Constructor[] }
  | { name: string; kind: "product"; encoding: string; fields: Field[]; variadic?: string }
  | { name: string; kind: "enum"; values: string[] }
  | { name: string; kind: "alias"; type: TypeExpr };

export interface Schema {
  "pandoc-api-version": number[];
  root: string;
  types: TypeDecl[];
}

// -- errors -------------------------------------------------------------------

export type Path = (string | number)[];

export function formatPath(path: Path): string {
  let out = "";
  for (const p of path) {
    if (typeof p === "number") out += `[${p}]`;
    else if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(p) || !out) out += out ? `.${p}` : p;
    else out += `[${JSON.stringify(p)}]`;
  }
  return out;
}

/** A value that doesn't fit pandoc's AST: `where` it is, what was expected, what it got. */
export class ASTError extends Error {
  where: string;
  expected: string;
  got: string;
  constructor(where: string, expected: string, got: string) {
    super(where ? `${where}: expected ${expected}, got ${got}` : `expected ${expected}, got ${got}`);
    this.where = where;
    this.expected = expected;
    this.got = got;
  }
}

/** A value given to a constructor, or found by `check`, of the wrong type. */
export class ASTTypeError extends ASTError {
  override name = "ASTTypeError";
}

/** JSON that isn't a pandoc document (or node) of this API version. */
export class ASTDecodeError extends ASTError {
  override name = "ASTDecodeError";
  path: Path;
  constructor(path: Path, expected: string, got: string) {
    super(formatPath(path), expected, got);
    this.path = path;
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

function describe(v: unknown): string {
  if (v === null) return "null";
  if (v === undefined) return "undefined";
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

/** `f` on each element of a list; an error says at which index. */
function mapList(xs: unknown[], f: (x: unknown) => unknown): unknown[] {
  const out = new Array<unknown>(xs.length);
  let i = 0;
  try {
    // by index: a hole in the array is an element too (undefined)
    for (; i < xs.length; i++) out[i] = f(xs[i]);
  } catch (e) {
    if (e instanceof Bad) e.path.push(i);
    throw e;
  }
  return out;
}

/** `f` on each value of a map, into `out`; an error says at which key. */
function mapMap(obj: Record<string, unknown>, f: (x: unknown) => unknown,
                out: Record<string, unknown>): Record<string, unknown> {
  let key = "";
  try {
    for (key of Object.keys(obj)) {
      if (!isWellFormed(key)) throw new Bad("a key of well-formed text", describe(key) + SURROGATE);
      const value = f(obj[key]);
      // (assigning to __proto__ sets the prototype of an object that has one)
      if (key === "__proto__") Object.defineProperty(out, key, { value, writable: true, enumerable: true, configurable: true });
      else out[key] = value;
    }
  } catch (e) {
    if (e instanceof Bad) e.path.push(key);
    throw e;
  }
  return out;
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

// -- decoding -------------------------------------------------------------------

function decode(ty: TypeExpr, j: unknown): unknown {
  ty = resolve(ty);
  if ("prim" in ty) return decodePrim(ty.prim, j);
  if ("list" in ty) {
    if (!Array.isArray(j)) throw new Bad("a list", describe(j));
    const item = ty.list;
    return mapList(j, (x) => decode(item, x));
  }
  if ("maybe" in ty) return j === null ? null : decode(ty.maybe, j);
  if ("map" in ty) {
    if (!isObject(j)) throw new Bad("an object", describe(j));
    const value = ty.map[1];
    return mapMap(j, (x) => decode(value, x), emptyMap());
  }
  if ("tuple" in ty) {
    const items = ty.tuple;
    if (!Array.isArray(j) || j.length !== items.length) {
      throw new Bad(`a list of ${items.length}`, describe(j));
    }
    let i = 0;
    return mapList(j, (x) => decode(items[i++], x));
  }
  const t = declaration(ty.ref);
  if (t.kind === "enum") {
    if (isObject(j) && typeof j.t === "string" && t.values.includes(j.t)) return j.t;
    throw new Bad(`${article(t.name)} ${t.name}`, describe(j));
  }
  if (t.kind === "sum") {
    const c = isObject(j) && typeof j.t === "string" ? tables().constructors.get(t.name)!.get(j.t) : undefined;
    if (!c) throw new Bad(t.name, describe(j));
    const out: Record<string, unknown> = { t: c.name };
    const obj = j as Record<string, unknown>;
    if (c.encoding === "none") return out;
    if (!("c" in obj)) throw new Bad(`${c.name} with contents`, `${c.name} without`);
    decodeFields(c.name, c.fields, c.encoding, obj.c, out);
    return out;
  }
  if (t.kind === "product") {
    const out: Record<string, unknown> = {};
    if (t.encoding === "root") {
      if (!isObject(j)) throw new Bad(`an object (${t.name})`, describe(j));
      const v = j["pandoc-api-version"];
      const want = SCHEMA["pandoc-api-version"];
      if (!Array.isArray(v) || v[0] !== want[0] || v[1] !== want[1]) {
        const e = new Bad(`pandoc-api-version ${want[0]}.${want[1]}.*`, describe(v));
        e.path.push("pandoc-api-version");
        throw e;
      }
    }
    decodeFields(t.name, t.fields, t.encoding, j, out);
    return out;
  }
  throw new Error(`unknown type ${ty.ref}`);
}

function decodeFields(name: string, fields: Field[], encoding: string, c: unknown,
                      out: Record<string, unknown>): void {
  if (encoding === "array") {
    if (!Array.isArray(c) || c.length !== fields.length) {
      const got = Array.isArray(c) ? `${name} with ${c.length}` : describe(c);
      throw new Bad(`${name} with ${fields.length} fields`, got);
    }
  } else if (encoding !== "value") {
    if (!isObject(c)) throw new Bad(`an object (${name})`, describe(c));
    for (const f of fields) {
      if (!Object.hasOwn(c, f.key)) throw new Bad(`${name} with ${JSON.stringify(f.key)}`, "one without");
    }
  }
  let i = 0;
  try {
    for (; i < fields.length; i++) {
      const f = fields[i];
      const x = encoding === "value" ? c : encoding === "array" ? (c as unknown[])[i] : (c as Record<string, unknown>)[f.key];
      out[f.name] = decode(f.type, x);
    }
  } catch (e) {
    if (e instanceof Bad) e.path.push(fields[i].name);
    throw e;
  }
}

/** Run a decoder or encoder; what it throws becomes the error `fail` makes of it. */
function guarded<T>(f: () => T, fail: (e: Bad) => Error): T {
  try {
    return f();
  } catch (e) {
    if (e instanceof Bad) throw fail(e);
    if (e instanceof RangeError && /call stack/i.test(e.message)) {
      // the functions here call themselves for each level of a document
      throw fail(new Bad("a document nested less deeply", "one beyond the engine's call stack"));
    }
    throw e;
  }
}

/** A value of a type (default: a document) from pandoc's JSON, checked. */
export function fromJSON(j: unknown, type: string = SCHEMA.root): unknown {
  return guarded(() => decode({ ref: type }, j),
                 (e) => new ASTDecodeError(e.path.reverse(), e.expected, e.got));
}

// -- encoding and checking ------------------------------------------------------

function encode(ty: TypeExpr, v: unknown): unknown {
  ty = resolve(ty);
  if ("prim" in ty) return encodePrim(ty.prim, v);
  if ("list" in ty) {
    if (!Array.isArray(v)) throw new Bad("a list", describe(v));
    const item = ty.list;
    return mapList(v, (x) => encode(item, x));
  }
  if ("maybe" in ty) return v === null || v === undefined ? null : encode(ty.maybe, v);
  if ("map" in ty) {
    if (!isObject(v)) throw new Bad("an object", describe(v));
    const value = ty.map[1];
    return mapMap(v, (x) => encode(value, x), {}); // JSON: an ordinary object
  }
  if ("tuple" in ty) {
    const items = ty.tuple;
    if (!Array.isArray(v) || v.length !== items.length) throw new Bad(typeName(ty), describe(v));
    let i = 0;
    return mapList(v, (x) => encode(items[i++], x));
  }
  const t = declaration(ty.ref);
  if (t.kind === "enum") {
    if (typeof v === "string" && t.values.includes(v)) return { t: v };
    throw new Bad(`${article(t.name)} ${t.name}`, describe(v));
  }
  if (t.kind === "sum") {
    const c = isObject(v) && typeof v.t === "string" ? tables().constructors.get(t.name)!.get(v.t) : undefined;
    if (!c) throw new Bad(t.name, describe(v));
    if (c.encoding === "none") return { t: c.name };
    return { t: c.name, c: encodeFields(c.fields, c.encoding, v as Record<string, unknown>) };
  }
  if (t.kind === "product") {
    if (!isObject(v)) throw new Bad(`${article(t.name)} ${t.name}`, describe(v));
    const body = encodeFields(t.fields, t.encoding, v);
    if (t.encoding === "root") {
      return { "pandoc-api-version": [...SCHEMA["pandoc-api-version"]], ...(body as object) };
    }
    return body;
  }
  throw new Error(`unknown type ${ty.ref}`);
}

function encodeFields(fields: Field[], encoding: string, v: Record<string, unknown>): unknown {
  const values = new Array<unknown>(fields.length);
  let i = 0;
  try {
    for (; i < fields.length; i++) values[i] = encode(fields[i].type, v[fields[i].name]);
  } catch (e) {
    if (e instanceof Bad) e.path.push(fields[i].name);
    throw e;
  }
  if (encoding === "value") return values[0];
  if (encoding === "array") return values;
  const out: Record<string, unknown> = {};
  fields.forEach((f, k) => { out[f.key] = values[k]; });
  return out;
}

/** A value (default: a document) as pandoc's JSON; checks it on the way. */
export function toJSON(v: unknown, type: string = SCHEMA.root): unknown {
  return guarded(() => encode({ ref: type }, v),
                 (e) => new ASTTypeError(formatPath([type, ...e.path.reverse()]), e.expected, e.got));
}

/** Throw `ASTTypeError` if a value (default: a document) isn't well-typed. */
export function check(v: unknown, type: string = SCHEMA.root): void {
  toJSON(v, type);
}

// -- constructors ---------------------------------------------------------------

// -- strings, as pandoc's Lua converts them ----------------------------------

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

/** The value with its strings converted as pandoc's Lua does: where a list
 * of inlines goes, a string is its words and spaces; where one inline goes,
 * a `Str`; where blocks go, `Plain` text. Anything else is left for
 * `encode` to check. */
function fromStrings(ty: TypeExpr, v: unknown): unknown {
  const r = resolve(ty);
  if (typeof v === "string" && "ref" in r) {
    if (r.ref === "Inline") return { t: "Str", text: v };
    if (r.ref === "Block") return { t: "Plain", content: textInlines(v) };
    return v;
  }
  if ("list" in r) {
    const item = resolve(r.list);
    if (typeof v === "string" && "ref" in item) {
      if (item.ref === "Inline") return textInlines(v);
      if (item.ref === "Block") return [{ t: "Plain", content: textInlines(v) }];
    }
    return Array.isArray(v) ? mapChanged(v, (x) => fromStrings(r.list, x)) : v;
  }
  if ("tuple" in r && Array.isArray(v)) {
    return mapChanged(v, (x, i) => (i < r.tuple.length ? fromStrings(r.tuple[i], x) : x));
  }
  return v;
}

/**
 * xs mapped, into a new list: a node has its own lists, not the caller's
 * (which the caller may change, or put in another node).
 */
function mapChanged(xs: unknown[], f: (x: unknown, i: number) => unknown): unknown[] {
  const out = new Array<unknown>(xs.length);
  for (let i = 0; i < xs.length; i++) out[i] = f(xs[i], i);
  return out;
}

/**
 * Check a constructor's argument: its own shape, and of the nodes in it
 * only that each is one of the right type (its tag). What is in those was
 * checked when they were made, and all of it is when the document is
 * written. (Checking it all here checked a document's every node once for
 * each node above it.)
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

function coerce(ty: TypeExpr, v: unknown, where: [string, string]): unknown {
  const r = resolve(ty);
  // attributes may be given as a record
  if ("list" in r && "tuple" in r.list && isObject(v)) v = Object.entries(v);
  v = fromStrings(ty, v);
  // a map given as an object with a prototype: its keys, into one without
  if ("map" in r && isObject(v) && Object.getPrototypeOf(v) !== null) v = Object.assign(emptyMap(), v);
  try {
    checkArgument(ty, v);
  } catch (e) {
    if (!(e instanceof Bad)) throw e;
    let hint = "";
    if (typeof v === "string" && "list" in r && "ref" in r.list) hint = " (a string; give a list of nodes)";
    throw new ASTTypeError(formatPath([...where, ...e.path.reverse()]), e.expected, e.got + hint);
  }
  return v;
}

/** A node's fields as its constructor fills them: `flat`, a product whose fields may be given as options. */
interface Maker {
  tag: boolean;
  fields: { f: Field; flat: { name: string; fields: Field[] } | undefined }[];
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
    if (!f.flatten) return { f, flat: undefined };
    const product = (f.type as { ref: string }).ref;
    const parts = tables().nodes.get(product)!.fields;
    for (const g of parts) known.add(g.name);
    return { f, flat: { name: product, fields: parts } };
  });
  m = { tag: node.tag, fields, known };
  makers.set(name, m);
  return m;
}

/** Build a node from a generated constructor's arguments. */
export function make<T>(name: string, given: Record<string, unknown>,
                        opts: Record<string, unknown>): T {
  const m = maker(name);
  const out: Record<string, unknown> = m.tag ? { t: name } : {};
  for (const { f, flat } of m.fields) {
    let v = given[f.name];
    if (v === undefined) v = opts[f.name];
    if (flat) {
      const parts = flat.fields.filter((g) => opts[g.name] !== undefined);
      if (v !== undefined && parts.length) {
        throw new TypeError(`${name}(): give either ${f.name} or ${parts.map((g) => g.name).join(", ")}, not both`);
      }
      if (v === undefined) {
        const missing = flat.fields.filter((g) => !("default" in g) && opts[g.name] === undefined);
        if (missing.length) {
          throw new TypeError(`${name}() needs ${missing.map((g) => g.name).join(", ")} (or ${f.name})`);
        }
        const own: Record<string, unknown> = {};
        for (const g of parts) own[g.name] = opts[g.name];
        v = make<unknown>(flat.name, {}, own);
      }
    }
    if (v === undefined) {
      if (!("default" in f)) throw new TypeError(`${name}() needs ${f.name}`);
      v = decode(f.type, f.default); // a fresh value each time
    }
    out[f.name] = coerce(f.type, v, [name, f.name]);
  }
  for (const k of Object.keys(opts)) {
    if (!m.known.has(k)) {
      throw new TypeError(`${name}() has no option ${Object.keys(opts).filter((o) => !m.known.has(o)).join(", ")}`);
    }
  }
  return out as T;
}
