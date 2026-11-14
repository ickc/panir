#!/usr/bin/env python3
"""Generate julia/src/generated.jl from schema/pandoc-ast.json.

Declarations only; ``core.jl`` does the rest by reflection. Types:

- a sum type is an abstract type (``abstract type Inline <: Node end``), and
  each constructor a mutable struct subtyping it (``Str <: Inline``);
- a product is a mutable struct (``Attr``, ``Citation``, ``Pandoc``);
- an enum-like type is an ``@enum``;
- an alias is a ``const``.

Julia checks field types itself: ``push!(para.content, Para())`` fails. Each
struct gets a constructor of all its fields positionally, in pandoc-types'
order, or in Lua's where pandoc's Lua constructors take every field in an
order of their own (``LUA_ORDER``: ``Cite(content, citations)``); and, where
it differs, a convenience one, as in Python: required fields, then the
variadic field as varargs, then keywords (defaults; flattened products by
their fields' names): ``Header(1, Str("Hi"); identifier = "hi")``. The
variadic field may also be one vector: ``Header(1, [Str("Hi")]; ...)``.
Every constructor copies the lists and maps it is given (``_own``, in
core.jl): a node's lists are its own. Reading JSON makes the fields itself
and passes them as they are (``T(_FIELDS, fields...)``, the inner
constructor).

    python3 tools/gen_julia.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "julia/src/generated.jl"

PRIM = {"string": "String", "int": "Int", "double": "Float64", "bool": "Bool"}
# Julia's reserved words, which a field or a keyword can't be named (but as
# var"end")
KEYWORDS = {
    "baremodule",
    "begin",
    "break",
    "catch",
    "const",
    "continue",
    "do",
    "else",
    "elseif",
    "end",
    "export",
    "false",
    "finally",
    "for",
    "function",
    "global",
    "if",
    "import",
    "let",
    "local",
    "macro",
    "module",
    "quote",
    "return",
    "struct",
    "true",
    "try",
    "using",
    "while",
}

# pandoc's Lua constructors that take every field positionally (but a
# flattened one), in an order of their own, and how many of them Lua
# requires: `pandoc.Cite(content, citations)`. (As the TypeScript
# generator's LUA_ORDER.)
LUA_ORDER = {
    "Cite": (["content", "citations"], 2),
    "Table": (["caption", "col_specs", "head", "bodies", "foot"], 5),
    "TableBody": (["body", "head", "row_head_columns"], 1),
    "Citation": (["id", "mode", "prefix", "suffix", "note_num", "hash"], 2),
}


def jl_string(text: str) -> str:
    """A Julia string literal: JSON's escapes are Julia's, but for `$`."""
    return json.dumps(text).replace("$", "\\$")


def ident(n: str) -> str:
    return f'var"{n}"' if n in KEYWORDS else n


class Gen:
    def __init__(self, schema: dict) -> None:
        self.schema = schema
        self.types = {t["name"]: t for t in schema["types"]}
        self.jl_name = {}
        for t in schema["types"]:
            name = t["name"]
            clash = t["kind"] == "sum" and any(c["name"] == name for c in t["constructors"])
            self.jl_name[name] = name + "Base" if clash else name

    def resolve(self, ty: dict) -> dict:
        while "ref" in ty and self.types[ty["ref"]]["kind"] == "alias":
            ty = self.types[ty["ref"]]["type"]
        return ty

    def jl(self, ty: dict) -> str:
        if "prim" in ty:
            return PRIM[ty["prim"]]
        if "ref" in ty:
            return self.jl_name[ty["ref"]]
        if "list" in ty:
            return f"Vector{{{self.jl(ty['list'])}}}"
        if "maybe" in ty:
            return f"Union{{Nothing, {self.jl(ty['maybe'])}}}"
        if "map" in ty:
            return f"Dict{{{self.jl(ty['map'][0])}, {self.jl(ty['map'][1])}}}"
        if "tuple" in ty:
            return f"Tuple{{{', '.join(self.jl(t) for t in ty['tuple'])}}}"
        raise ValueError(ty)

    def default(self, ty: dict, value) -> str:
        r = self.resolve(ty)
        if value is None:
            return "nothing"
        if "list" in r:
            return f"{self.jl(r['list'])}[]"
        if "map" in r:
            return f"{self.jl(r)}()"
        if "ref" in r:
            t = self.types[r["ref"]]
            if t["kind"] == "enum":
                return value["t"]
            if t["kind"] == "sum":
                return f"{value['t']}()"
            return f"{t['name']}()"
        if isinstance(value, str):
            return jl_string(value)
        return json.dumps(value)

    def owned(self, ty: dict, n: str) -> str:
        """A constructor's argument for a field: converted to the field's
        type, a list or a map copied (`_own`, in core.jl)."""
        return f"_own({self.jl(ty)}, {n})"

    def deps(self, t: dict) -> set[str]:
        """Structs a struct's fields name directly (they must come first)."""
        out: set[str] = set()

        def scan(ty: dict) -> None:
            ty = self.resolve(ty)
            if "ref" in ty:
                k = self.types[ty["ref"]]["kind"]
                if k == "product":
                    out.add(ty["ref"])
            for k in ("list", "maybe"):
                if k in ty:
                    scan(ty[k])
            if "map" in ty:
                scan(ty["map"][1])
            if "tuple" in ty:
                for x in ty["tuple"]:
                    scan(x)

        for f in t["fields"]:
            scan(f["type"])
        return out

    # -- declarations ----------------------------------------------------------

    def generate(self) -> str:
        api = self.schema["pandoc-api-version"]
        o = [HEADER.format(api=".".join(map(str, api)))]
        o.append(f"const PANDOC_API_VERSION = {tuple(api)!r}\n\n")
        types = self.schema["types"]
        for t in types:
            if t["kind"] == "enum":
                values = " ".join(t["values"])
                o.append(f'"pandoc\'s `{t["name"]}`."\n@enum {t["name"]} {values}\n\n')
        for t in types:
            if t["kind"] == "sum":
                o.append(
                    f'"Any of pandoc\'s `{t["name"]}` constructors."\n'
                    f"abstract type {self.jl_name[t['name']]} <: Node end\n\n"
                )
        for t in types:
            if t["kind"] == "alias":
                target = self.jl(t["type"])
                o.append(f'"pandoc\'s `{t["name"]}`."\nconst {t["name"]} = {target}\n\n')
        # structs, each after the structs its fields name
        structs: list[tuple[dict, str, str | None, str]] = []  # decl, super, encoding, sum
        for t in types:
            if t["kind"] == "sum":
                for c in t["constructors"]:
                    structs.append((c, self.jl_name[t["name"]], c["encoding"], t["name"]))
            elif t["kind"] == "product":
                structs.append((t, "Node", t["encoding"], None))
        done: set[str] = set()
        pending = structs[:]
        while pending:
            progress = False
            for s in pending[:]:
                if self.deps(s[0]) <= done:
                    o.append(self.struct(*s))
                    done.add(s[0]["name"])
                    pending.remove(s)
                    progress = True
            if not progress:
                raise SystemExit(f"cyclic structs: {[s[0]['name'] for s in pending]}")
        o.append("# how each type is encoded, for core.jl\n")
        for t in types:
            if t["kind"] == "sum":
                cons = ", ".join(c["name"] for c in t["constructors"])
                o.append(f"_constructors(::Type{{{self.jl_name[t['name']]}}}) = ({cons},)\n")
        o.append(
            "const _SUMS = ("
            + ", ".join(self.jl_name[t["name"]] for t in types if t["kind"] == "sum")
            + ",)\n"
        )
        o.append("\n")
        # what a filter needs: the node types and the enums' values; not the
        # enums' types nor the aliases (`Panir.Alignment`, `Panir.Format`)
        exports = []
        for t in types:
            if t["kind"] == "enum":
                exports += t["values"]
            elif t["kind"] == "sum":
                exports += [self.jl_name[t["name"]], *(c["name"] for c in t["constructors"])]
            elif t["kind"] == "product":
                exports.append(t["name"])
        exports = [e for e in exports if e not in NOT_EXPORTED]
        o.append("export PANDOC_API_VERSION, " + ", ".join(exports) + "\n")
        return "".join(o)

    def struct(self, c: dict, sup: str, encoding: str, sum_name: str | None) -> str:
        name, fields = c["name"], c["fields"]
        doc = f"pandoc's `{name}`, a `{sum_name}`." if sum_name else f"pandoc's `{name}`."
        lines = [f'"{doc}"', f"mutable struct {name} <: {sup}"]
        for f in fields:
            lines.append(f"    {ident(f['name'])}::{self.jl(f['type'])}")
        names = ", ".join(ident(f["name"]) for f in fields)
        if fields:
            # the fields as they are (reading JSON, copy), typed so that it
            # overlaps no other constructor; no other inner constructor, so
            # none of Julia's own
            typed = ", ".join(f"{ident(f['name'])}::{self.jl(f['type'])}" for f in fields)
            lines.append(f"    {name}(::_Fields, {typed}) = new({names})")
        lines.append("end")
        lines.append(f"_encoding(::Type{{{name}}}) = :{encoding}")
        if sum_name is None:
            # a product's defaults, for a constructor that takes its fields
            # as keywords (Header(...; identifier = "x")): _flat, in core.jl
            for f in fields:
                if "default" in f:
                    d = self.default(f["type"], f["default"])
                    lines.append(f"_default(::Type{{{name}}}, ::Val{{:{f['name']}}}) = {d}")
        keys = [f.get("key") for f in fields]
        if any(keys):
            lines.append(f"_keys(::Type{{{name}}}) = ({', '.join(json.dumps(k) for k in keys)},)")
        if fields and name not in LUA_ORDER:
            lines.append(self.positional(c))
        if name in LUA_ORDER:
            lines.append(self.lua_order(c))
        conv = self.convenience(c)
        if conv:
            lines.append(conv)
        return "\n".join(lines) + "\n\n"

    def positional(self, c: dict) -> str:
        """Every field positionally, in pandoc-types' order; lists, maps and
        products typed, so that it doesn't overlap the convenience
        constructor's varargs (OrderedList(list_attributes, items) and
        OrderedList(item, item))."""
        name, fields = c["name"], c["fields"]
        params = []
        for f in fields:
            r = self.resolve(f["type"])
            if "list" in r:
                # a list of lists (or of pairs, against varargs of pairs) only
                # from one of them, so that BulletList([Plain("a")]) is one item
                item = self.resolve(r["list"])
                of = "AbstractVector" if "list" in item else ""
                if "tuple" in item:
                    of = "Tuple" if c.get("variadic") else None  # (attributes: a Dict too)
                if of is None:
                    ann = ""
                else:
                    ann = f"::AbstractVector{{<:{of}}}" if of else "::AbstractVector"
            elif "map" in r:
                ann = "::AbstractDict"
            elif "ref" in r and self.types[r["ref"]]["kind"] == "product":
                ann = f"::{self.jl(r)}"
            else:
                ann = ""
            params.append(f"{ident(f['name'])}{ann}")
        args = ", ".join(self.owned(f["type"], ident(f["name"])) for f in fields)
        return f"{name}({', '.join(params)}) = {name}(_FIELDS, {args})"

    def flat_keywords(self, f: dict) -> tuple[list[str], str]:
        """A flattened product's keywords: the product whole, or its fields
        by name; and the call that makes it of them (`_flat`, in core.jl)."""
        n = ident(f["name"])
        prod = self.types[f["type"]["ref"]]
        # nothing: not given (its default is _default's, in _flat)
        kws = [f"{n}::Union{{Nothing, {prod['name']}}} = nothing"]
        parts = [ident(g["name"]) for g in prod["fields"]]
        kws += [f"{p} = nothing" for p in parts]
        return kws, f"_flat({prod['name']}, {n}, :{{owner}}, :{f['name']}; {', '.join(parts)})"

    def lua_order(self, c: dict) -> str:
        """Every field positionally, in the order of pandoc's Lua constructor
        (the flattened one by keywords), and the `show` that writes it so."""
        name, fields = c["name"], c["fields"]
        order, required = LUA_ORDER[name]
        by_name = {f["name"]: f for f in fields}
        flat = [f for f in fields if f["name"] not in order]
        assert all(f.get("flatten") for f in flat), name
        params = []
        for k, n in enumerate(order):
            f = by_name[n]
            d = "" if k < required else f" = {self.default(f['type'], f['default'])}"
            params.append(f"{ident(n)}{d}")
        kws, body = [], []
        for f in flat:
            k, call = self.flat_keywords(f)
            kws += k
            body.append(f"    __{f['name']} = {call.format(owner=name)}")
        args = ", ".join(
            f"__{f['name']}" if f in flat else self.owned(f["type"], ident(f["name"]))
            for f in fields
        )
        sig = ", ".join(params) + ("; " + ", ".join(kws) if kws else "")
        body.append(f"    {name}(_FIELDS, {args})")
        shown = ", ".join(f":{n}" for n in order) + ","
        keywords = ", ".join(f":{f['name']}" for f in flat) + ("," if flat else "")
        return (
            f"function {name}({sig})\n" + "\n".join(body) + "\nend\n"
            f"Base.show(io::IO, x::{name}) = _show_call(io, x, ({shown}), ({keywords}))"
        )

    def convenience(self, c: dict) -> str | None:
        name, fields = c["name"], c["fields"]
        variadic = c.get("variadic")
        positional, star, kws, args, flats = [], None, [], [], []
        vector = None  # the variadic field given as one vector, when that is unambiguous
        for f in fields:
            n = ident(f["name"])
            r = self.resolve(f["type"])
            if f["name"] == variadic:
                item = self.resolve(r["list"])
                # a list of lists: any vectors, converted ([Plain(...)] is a Vector{Plain});
                # inlines and blocks: also what converts to them, as pandoc's Lua
                # converts it (core.jl): strings, an inline where blocks go, and
                # where a list of them goes, one of them
                if "list" in item:
                    inner = self.resolve(item["list"])
                    if inner.get("ref") in ("Inline", "Block"):
                        converts = CONVERTS[inner["ref"]]
                        star_t = f"Union{{AbstractVector, {inner['ref']}, {converts}}}"
                    else:
                        star_t = "AbstractVector"
                elif item.get("ref") in ("Inline", "Block"):
                    star_t = f"Union{{{item['ref']}, {CONVERTS[item['ref']]}}}"
                elif item.get("ref") == "MetaValue":
                    star_t = "Any"  # converted: MetaList("a", true)
                elif "tuple" in item:
                    star_t = "Tuple"  # converted: ([Str("a")], [[Para("b")]])
                else:
                    star_t = self.jl(r["list"])
                star = f"{n}::{star_t}..."
                # not for a list of lists: OrderedList([Plain(...)]) is one item
                if "list" not in item:
                    vector = f"{n}::AbstractVector"
                args.append(f"_variadic({self.jl(r['list'])}, {n})")
            elif f.get("flatten"):
                k, call = self.flat_keywords(f)
                kws += k
                flats.append(call.format(owner=name))
                args.append(f"__{f['name']}")
            elif "default" in f:
                kws.append(f"{n} = {self.default(f['type'], f['default'])}")
                args.append(self.owned(f["type"], n))
            else:
                positional.append(n)
                args.append(self.owned(f["type"], n))
        if star is None and not kws:
            return None  # the positional constructor is all there is
        body = []
        for f, fl in zip([f for f in fields if f.get("flatten")], flats):
            body.append(f"    __{f['name']} = {fl}")
        body.append(f"    {name}(_FIELDS, {', '.join(args)})")

        def method(last: str | None) -> str:
            sig = ", ".join(positional + ([last] if last else []))
            if kws:
                sig += "; " + ", ".join(kws)
            return f"function {name}({sig})\n" + "\n".join(body) + "\nend"

        out = method(star)
        # the variadic field as a vector too (Div(xs; identifier = "x")), as
        # the positional constructor takes it; without keywords, that is the
        # positional constructor itself (Para(xs))
        if vector and kws:
            out += "\n" + method(vector)
        return out


# What converts to an inline, and to a block, besides one (core.jl's convert)
CONVERTS = {"Inline": "AbstractString", "Block": "Inline, AbstractString"}

# Enum values that are common words, which a module that uses Panir would
# lose if Panir exported them too (two modules' exports of one name leave it
# undefined): Panir.Period, Panir.Example, Panir.Decimal.
NOT_EXPORTED = {
    "Period",  # Dates.Period
    "Example",
    "Decimal",  # Decimals.Decimal
}

HEADER = """\
# GENERATED by tools/gen_julia.py from schema/pandoc-ast.json
# (pandoc-api-version {api}). Do not edit; regenerate instead.

"""


def main() -> None:
    schema = json.loads((ROOT / "schema/pandoc-ast.json").read_text())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(Gen(schema).generate())


if __name__ == "__main__":
    main()
