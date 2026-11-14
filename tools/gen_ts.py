#!/usr/bin/env python3
"""Generate ts/src/generated.ts and ts/src/schema.ts from schema/pandoc-ast.json.

Declarations only, as for Python: the types (for the type checker), the
schema itself (``SCHEMA``, in ``schema.ts``, which the generic runtime in
``core.ts`` interprets), and a constructor function per constructor and
product. (``schema.ts`` imports nothing: ``core.ts`` reads it, and
``generated.ts`` imports ``core.ts``, without a cycle.)

Nodes are plain objects with named fields, discriminated by ``t``:
``{ t: "Header", level: 1, attr: {...}, content: [...] }``. Products have no
``t`` (``Attr``: ``{ identifier, classes, attributes }``); enum-like types are
string unions (``"InlineMath"``). Field names are the schema's, in
camelCase.

Constructors take their arguments in the order of pandoc's Lua
constructors: the required fields, then the variadic field (as an array),
positionally, and the rest in an options object, flattened products by their
fields' names: ``Header(1, [Str("Hi")], { identifier: "hi" })``; where Lua
takes every field positionally in an order of its own (``pandoc.Cite(content,
citations)``), that order (``LUA_ORDER``). The types accept what the runtime
converts, as pandoc's Lua converts it (a string where inlines go), and
nothing it refuses (a link without a url, ``attr`` and ``identifier``
both).

    python3 tools/gen_ts.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "ts/src/generated.ts"
OUT_SCHEMA = ROOT / "ts/src/schema.ts"

PRIM = {"string": "string", "int": "number", "double": "number", "bool": "boolean"}

# pandoc's Lua constructors whose positional arguments are every field (but
# a flattened one), in an order of their own: `pandoc.Cite(content,
# citations)`, `pandoc.TableBody(body, head, row_head_columns, attr)`, ...
LUA_ORDER = {
    "Cite": ["content", "citations"],
    "Table": ["caption", "col_specs", "head", "bodies", "foot"],
    "TableBody": ["body", "head", "row_head_columns"],
    "Citation": ["id", "mode", "prefix", "suffix", "note_num", "hash"],
}


def camel(name: str) -> str:
    """snake_case to camelCase; each later part's first letter upper case,
    the rest as it is (``str.title`` would lower it)."""
    head, *rest = name.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def union(parts: list[str]) -> str:
    return " | ".join(parts)


def paren(t: str) -> str:
    """A type, bracketed if it is a union (for `T[]`)."""
    return f"({t})" if "|" in t or t.startswith("readonly ") else t


class Gen:
    def __init__(self, schema: dict) -> None:
        self.schema = schema
        self.types = {t["name"]: t for t in schema["types"]}
        self.ts_name = {}
        for t in schema["types"]:
            name = t["name"]
            clash = t["kind"] == "sum" and any(c["name"] == name for c in t["constructors"])
            self.ts_name[name] = name + "Base" if clash else name
        # the types whose values are found in lists (a filter function for
        # them may return a list)
        self.in_lists: set[str] = set()
        for t in schema["types"]:
            for c in t.get("constructors", []) + ([t] if t["kind"] == "product" else []):
                for f in c["fields"]:
                    self.note_lists(f["type"])
            if t["kind"] == "alias":
                self.note_lists(t["type"])

    def note_lists(self, ty: dict) -> None:
        if "list" in ty:
            item = self.resolve(ty["list"])
            if "ref" in item:
                self.in_lists.add(item["ref"])
            self.note_lists(ty["list"])
        for key in ("maybe",):
            if key in ty:
                self.note_lists(ty[key])
        if "map" in ty:
            self.note_lists(ty["map"][1])
        if "tuple" in ty:
            for t in ty["tuple"]:
                self.note_lists(t)

    def resolve(self, ty: dict) -> dict:
        while "ref" in ty and self.types[ty["ref"]]["kind"] == "alias":
            ty = self.types[ty["ref"]]["type"]
        return ty

    def ts(self, ty: dict) -> str:
        if "prim" in ty:
            return PRIM[ty["prim"]]
        if "ref" in ty:
            return self.ts_name[ty["ref"]]
        if "list" in ty:
            return f"{paren(self.ts(ty['list']))}[]"
        if "maybe" in ty:
            return f"{self.ts(ty['maybe'])} | null"
        if "map" in ty:
            return f"Record<string, {self.ts(ty['map'][1])}>"
        if "tuple" in ty:
            return f"[{', '.join(self.ts(t) for t in ty['tuple'])}]"
        raise ValueError(ty)

    def param(self, ty: dict) -> str:
        """What a constructor accepts where a value of `ty` goes: what
        converts to it, as pandoc's Lua converts (the types Inlines,
        Blocks, MetaValueInit), attributes also as a record; read-only
        arrays, since constructors copy them."""
        r = self.resolve(ty)
        if "list" in r:
            item = self.resolve(r["list"])
            if item.get("ref") in ("Inline", "Block"):
                return f"{item['ref']}s"
            if "tuple" in item and all(t.get("prim") == "string" for t in item["tuple"]):
                return "Attributes"
            inner = self.param(r["list"])
            # as pandoc's Lua: one value where a list of lists of inlines or
            # blocks goes is a list of the list it converts to
            one = {"Inlines": " | string | Inline", "Blocks": " | string | Block | Inline"}.get(
                inner, ""
            )
            return f"readonly {paren(inner)}[]{one}"
        if "maybe" in r:
            return f"{self.param(r['maybe'])} | null"
        if "map" in r:
            return f"{{ readonly [key: string]: {self.param(r['map'][1])} }}"
        if "tuple" in r:
            return f"readonly [{', '.join(self.param(t) for t in r['tuple'])}]"
        if r.get("ref") == "MetaValue":
            return "MetaValueInit"
        return self.ts(ty)

    def camel_schema(self) -> dict:
        """The schema with camelCase field names (JSON keys unchanged)."""
        out = json.loads(json.dumps(self.schema))
        for t in out["types"]:
            for c in t.get("constructors", []) + ([t] if t["kind"] == "product" else []):
                for f in c.get("fields", []):
                    f["key"] = f.get("key", f["name"])
                    f["name"] = camel(f["name"])
                if "variadic" in c:
                    c["variadic"] = camel(c["variadic"])
        return out

    # -- declarations ---------------------------------------------------------

    def generate_schema(self) -> str:
        api = self.schema["pandoc-api-version"]
        schema = json.dumps(self.camel_schema(), separators=(",", ":"))
        return SCHEMA_HEADER.format(api=".".join(map(str, api))) + (
            "/** The schema the runtime reads: schema/pandoc-ast.json, camelCase. */\n"
            f"export const SCHEMA: Schema = {schema};\n"
        )

    def generate(self) -> str:
        api = self.schema["pandoc-api-version"]
        out = [HEADER.format(api=".".join(map(str, api)))]
        out.append(f"export const PANDOC_API_VERSION = {json.dumps(api)} as const;\n\n")
        constructors: list[str] = []
        for t in self.schema["types"]:
            kind, name = t["kind"], t["name"]
            if kind == "alias":
                out.append(f"export type {name} = {self.ts(t['type'])};\n\n")
            elif kind == "enum":
                values = " | ".join(json.dumps(v) for v in t["values"])
                out.append(f"export type {name} = {values};\n\n")
            elif kind == "sum":
                cons = [c["name"] for c in t["constructors"]]
                alts = "".join(f"\n  | {c}" for c in cons)
                out.append(f"export type {self.ts_name[name]} ={alts};\n\n")
                for c in t["constructors"]:
                    out.append(self.interface(c, tagged=True))
                    constructors.append(c["name"])
            else:
                out.append(self.interface(t, tagged=False))
                constructors.append(name)
        out.append(INPUTS)
        flat = {f["type"]["ref"]: camel(f["name"]) for f in self.flattened()}
        for name, key in sorted(flat.items()):
            out.append(self.flat_options(name, key))
        out.append("/** Every constructor and product, by name. */\n")
        out.append("export interface Nodes {\n")
        out.extend(f"  {n}: {n};\n" for n in constructors)
        out.append("}\n\n")
        out.append(
            "/** Any node: a constructor's or a product's (`Attr`, `Cell`, `Pandoc`...). */\n"
        )
        out.append("export type ASTNode = Nodes[keyof Nodes];\n\n")
        sums = [t["name"] for t in self.schema["types"] if t["kind"] == "sum"]
        out.append("/** Each sum type, by name. */\n")
        out.append("export interface Sums {\n")
        out.extend(f"  {n}: {self.ts_name[n]};\n" for n in sums)
        out.append("}\n\n")
        out.append(
            '/** Every type of the schema, by its name there (`fromJSON(j, "Inline")`). */\n'
        )
        out.append("export interface Types {\n")
        for t in self.schema["types"]:
            out.append(f"  {t['name']}: {self.ts_name[t['name']]};\n")
        out.append("}\n\n")
        out.append(self.filter_types())
        for t in self.schema["types"]:
            if t["kind"] == "sum":
                for c in t["constructors"]:
                    out.append(self.constructor(c, product=False))
            elif t["kind"] == "product":
                out.append(self.constructor(t, product=True))
        return "".join(out)

    def flattened(self) -> list[dict]:
        out = []
        for t in self.schema["types"]:
            for c in t.get("constructors", []) + ([t] if t["kind"] == "product" else []):
                out.extend(f for f in c["fields"] if f.get("flatten"))
        return out

    def flat_options(self, name: str, key: str) -> str:
        """A flattened product's options: whole (as the field `key`), or by
        its fields, not both."""
        fields = self.types[name]["fields"]
        required = any("default" not in g for g in fields)
        never = "; ".join(f"{camel(g['name'])}?: never" for g in fields)
        parts = "; ".join(
            f"{camel(g['name'])}{'' if 'default' not in g else '?'}: {self.param(g['type'])}"
            for g in fields
        )
        whole = f"{key}{'' if required else '?'}: {name}"
        return (
            f"/** {'An' if name[0] in 'AEIOU' else 'A'} {name} given whole (`{key}`), "
            f"or by its fields. */\n"
            f"export type {name}Options =\n"
            f"  | {{ {whole}; {never} }}\n"
            f"  | {{ {key}?: never; {parts} }};\n\n"
        )

    def filter_types(self) -> str:
        """For each name a filter function can have (constructor, sum,
        product): the node it gets, the type of the nodes beside it in a
        list (`never` if it is never in one), and what it may return."""

        def returns(owner: str) -> str:
            if owner == "Inline":
                return "Inlines"
            if owner == "Block":
                return "Blocks"
            ts = self.ts_name[owner]
            return f"{ts} | readonly {ts}[]" if owner in self.in_lists else ts

        def sibling(owner: str) -> str:
            return self.ts_name[owner] if owner in self.in_lists else "never"

        lines = [
            "/**\n"
            " * The functions a filter can have, by name: the node each gets, what is\n"
            " * beside it in a list (`never`: it is never in one), and what it may\n"
            " * return besides nothing (converted as pandoc's Lua converts it).\n"
            " */\n",
            "export interface FilterTypes {\n",
        ]
        for t in self.schema["types"]:
            name = t["name"]
            if t["kind"] == "sum":
                ts = self.ts_name[name]
                lines.append(
                    f"  {name}: {{ node: {ts}; sibling: {sibling(name)}; "
                    f"returns: {returns(name)} }};\n"
                )
                for c in t["constructors"]:
                    if c["name"] == name:
                        continue  # (ColWidth: the type's entry serves)
                    lines.append(
                        f"  {c['name']}: {{ node: {c['name']}; sibling: {sibling(name)}; "
                        f"returns: {returns(name)} }};\n"
                    )
            elif t["kind"] == "product":
                lines.append(
                    f"  {name}: {{ node: {name}; sibling: {sibling(name)}; "
                    f"returns: {returns(name)} }};\n"
                )
        lines.append("}\n\n")
        return "".join(lines)

    def interface(self, c: dict, tagged: bool) -> str:
        lines = [f"export interface {c['name']} {{"]
        if tagged:
            lines.append(f'  t: "{c["name"]}";')
        for f in c["fields"]:
            lines.append(f"  {camel(f['name'])}: {self.ts(f['type'])};")
        return "\n".join(lines) + "\n}\n\n"

    def constructor(self, c: dict, product: bool) -> str:
        name, fields = c["name"], c["fields"]
        variadic = c.get("variadic")
        array_product = product and c.get("encoding") == "array"
        positional: list[dict] = []
        options: list[dict] = []
        if name in LUA_ORDER:
            by_name = {f["name"]: f for f in fields}
            positional = [by_name[n] for n in LUA_ORDER[name]]
            options = [f for f in fields if f not in positional]
            assert all(f.get("flatten") for f in options), name
        else:
            for f in fields:
                if f.get("flatten"):
                    options.append(f)
                elif "default" not in f and f["name"] != variadic:
                    positional.append(f)
            for f in fields:
                if f in positional or f.get("flatten"):
                    continue
                if f["name"] == variadic or (array_product and not variadic) or len(fields) == 1:
                    positional.append(f)
                else:
                    options.append(f)
            # keep pandoc's order among the positional fields
            positional.sort(key=fields.index)
        plain = [f for f in options if not f.get("flatten")]
        flat = [f for f in options if f.get("flatten")]
        # options that must be given (a link's url) make every argument before them needed
        required = any("default" not in f for f in flat)
        params = []
        for f in positional:
            opt = "?" if "default" in f and not required else ""
            params.append(f"{camel(f['name'])}{opt}: {self.param(f['type'])}")
        parts = []
        if plain:
            parts.append(
                "{ "
                + "; ".join(f"{camel(f['name'])}?: {self.param(f['type'])}" for f in plain)
                + " }"
            )
        parts.extend(f"{f['type']['ref']}Options" for f in flat)
        if parts:
            params.append(f"opts: {' & '.join(parts)}{'' if required else ' = {}'}")
        values = ", ".join(camel(f["name"]) for f in positional)
        opts_arg = "opts" if parts else "{}"
        if name in LUA_ORDER:
            lua = ", ".join(LUA_ORDER[name] + (["attr"] if flat else []))
            doc = f"/** pandoc's `{name}`, as Lua's `pandoc.{name}({lua})`. */\n"
        else:
            doc = f"/** pandoc's `{name}`. */\n"
        return (
            f"{doc}export function {name}({', '.join(params)}): {name} {{\n"
            f'  return make<{name}>("{name}", {{ {values} }}, {opts_arg});\n}}\n\n'
        )


HEADER = """\
// GENERATED by tools/gen_ts.py from schema/pandoc-ast.json
// (pandoc-api-version {api}). Do not edit; regenerate instead.

import {{ make }} from "./core.ts";

"""

SCHEMA_HEADER = """\
// GENERATED by tools/gen_ts.py from schema/pandoc-ast.json
// (pandoc-api-version {api}). Do not edit; regenerate instead.

/** A type as the schema writes it. */
export type TypeExpr =
  | {{ prim: "string" | "int" | "double" | "bool" }}
  | {{ ref: string }}
  | {{ list: TypeExpr }}
  | {{ maybe: TypeExpr }}
  | {{ map: [TypeExpr, TypeExpr] }}
  | {{ tuple: TypeExpr[] }};

export interface Field {{
  name: string;
  key: string;
  type: TypeExpr;
  default?: unknown;
  flatten?: boolean;
}}

export interface Constructor {{
  name: string;
  encoding: string;
  fields: Field[];
  variadic?: string;
}}

export type TypeDecl =
  | {{ name: string; kind: "sum"; constructors: Constructor[] }}
  | {{ name: string; kind: "product"; encoding: string; fields: Field[]; variadic?: string }}
  | {{ name: string; kind: "enum"; values: string[] }}
  | {{ name: string; kind: "alias"; type: TypeExpr }};

export interface Schema {{
  "pandoc-api-version": number[];
  root: string;
  types: TypeDecl[];
}}

"""

INPUTS = """\
/**
 * Inlines as constructors take them, and filter functions return them, as
 * pandoc's Lua converts them: a string is its words and spaces, one inline
 * a list of it, and a string in a list a `Str`.
 */
export type Inlines = string | Inline | readonly (Inline | string)[];

/**
 * Blocks as constructors take them, and filter functions return them, as
 * pandoc's Lua converts them: a string is `Plain` text, one block a list of
 * it; in a list, a string is `Plain` text, and an inline, or a list of
 * inlines (strings: a `Str` each), a `Plain` of them.
 */
export type Blocks =
  | string
  | Block
  | Inline
  | readonly (Block | Inline | string | readonly (Inline | string)[])[];

/** Attributes as constructors take them: (key, value) pairs, or a record. */
export type Attributes =
  | readonly (readonly [string, string])[]
  | { readonly [key: string]: string };

/**
 * Metadata as constructors take it, as pandoc's Lua converts it: a string
 * is a `MetaString`, a boolean a `MetaBool`, a number a `MetaString` of it,
 * an inline or a list of inlines `MetaInlines`, a block or a list of blocks
 * `MetaBlocks`, another list a `MetaList`, and an object a `MetaMap`.
 */
export type MetaValueInit =
  | MetaValue
  | string
  | boolean
  | number
  | Inline
  | Block
  | readonly MetaValueInit[]
  | { readonly [key: string]: MetaValueInit };

"""


def main() -> None:
    schema = json.loads((ROOT / "schema/pandoc-ast.json").read_text())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    gen = Gen(schema)
    OUT.write_text(gen.generate())
    OUT_SCHEMA.write_text(gen.generate_schema())


if __name__ == "__main__":
    main()
