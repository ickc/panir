#!/usr/bin/env python3
"""Generate julia/src/generated.jl from schema/pandoc-ast.json.

Declarations only; ``core.jl`` does the rest by reflection. Types:

- a sum type is an abstract type (``abstract type Inline <: Node end``), and
  each constructor a mutable struct subtyping it (``Str <: Inline``);
- a product is a mutable struct (``Attr``, ``Citation``, ``Pandoc``);
- an enum-like type is an ``@enum``;
- an alias is a ``const``.

Julia checks field types itself: ``push!(para.content, Para())`` fails. Each
struct gets the positional constructor of all its fields and, where it
differs, a convenience one, as in Python: required fields, then the
variadic field as varargs, then keywords (defaults; flattened products by
their fields' names): ``Header(1, Str("Hi"); identifier = "hi")``. The
variadic field may also be one vector: ``Header(1, [Str("Hi")]; ...)``.

    python3 tools/gen_julia.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "julia/src/generated.jl"

PRIM = {"string": "String", "int": "Int", "double": "Float64", "bool": "Bool"}
KEYWORDS = {
    "end",
    "function",
    "type",
    "module",
    "begin",
    "let",
    "local",
    "global",
    "const",
    "struct",
    "abstract",
    "primitive",
    "import",
    "using",
    "export",
}


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
        return json.dumps(value)

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
        o.append(
            "const _ENUMS = ("
            + ", ".join(t["name"] for t in types if t["kind"] == "enum")
            + ",)\n\n"
        )
        exports = []
        for t in types:
            if t["kind"] == "enum":
                exports += [t["name"], *t["values"]]
            elif t["kind"] == "sum":
                exports += [self.jl_name[t["name"]], *(c["name"] for c in t["constructors"])]
            else:
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
        if fields:
            # the positional constructor; lists, maps and products typed, so
            # that it doesn't overlap the convenience constructor's varargs
            # (OrderedList(list_attributes, items) and OrderedList(item, item))
            params = []
            for f in fields:
                r = self.resolve(f["type"])
                if "list" in r:
                    # a list of lists (or of pairs) only from one of them,
                    # so that BulletList([Plain("a")]) is one item
                    item = self.resolve(r["list"])
                    of = "AbstractVector" if "list" in item else "Tuple" if "tuple" in item else ""
                    ann = f"::AbstractVector{{<:{of}}}" if of else "::AbstractVector"
                elif "map" in r:
                    ann = "::AbstractDict"
                elif "ref" in r and self.types[r["ref"]]["kind"] == "product":
                    ann = f"::{self.jl(r)}"
                else:
                    ann = ""
                params.append(f"{ident(f['name'])}{ann}")
            names = ", ".join(ident(f["name"]) for f in fields)
            lines.append(f"    {name}({', '.join(params)}) = new({names})")
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
        conv = self.convenience(c)
        if conv:
            lines.append(conv)
        return "\n".join(lines) + "\n\n"

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
                # inlines and blocks: also strings, as pandoc's Lua converts them
                if "list" in item:
                    inner = self.resolve(item["list"])
                    if inner.get("ref") in ("Inline", "Block"):
                        star_t = "Union{AbstractVector, AbstractString}"
                    else:
                        star_t = "AbstractVector"
                elif item.get("ref") in ("Inline", "Block"):
                    star_t = f"Union{{{item['ref']}, AbstractString}}"
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
                prod = self.types[f["type"]["ref"]]
                parts = []
                kws.append(f"{n}::Union{{Nothing, {prod['name']}}} = nothing")
                for g in prod["fields"]:
                    gn = ident(g["name"])
                    # nothing: not given (its default is _default's, in _flat)
                    kws.append(f"{gn} = nothing")
                    parts.append(gn)
                flats.append(
                    f"_flat({prod['name']}, {n}, :{name}, :{f['name']}; {', '.join(parts)})"
                )
                args.append(f"__{f['name']}")
            elif "default" in f:
                kws.append(f"{n} = {self.default(f['type'], f['default'])}")
                args.append(n)
            else:
                positional.append(n)
                args.append(n)
        if star is None and not kws:
            return None  # the positional constructor is all there is
        body = []
        for f, fl in zip([f for f in fields if f.get("flatten")], flats):
            body.append(f"    __{f['name']} = {fl}")
        body.append(f"    {name}({', '.join(args)})")

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


# Names a module that uses Panir would lose if Panir exported them too: two
# modules' exports of one name leave it undefined. They are Panir.Meta and
# Panir.Period.
NOT_EXPORTED = {
    "Meta",  # Base.Meta (Meta.parse)
    "Period",  # Dates.Period
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
