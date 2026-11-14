#!/usr/bin/env python3
"""Generate python/src/panir/_types.py from schema/pandoc-ast.json.

The output only declares: one class per constructor (a subclass of its sum
type's class) or product, fields as annotations, the JSON encoding as class
attributes, and a constructor. What classes do is in ``_core.py``, which
reads these declarations.

Type checkers see more than the run time reads: each field as a descriptor
(``_Field``) read as its value's type and set from what converts to it (a
string where inlines go, an enum's name, ...), and ``Filter.on``'s
signatures, per class of node.

    python3 tools/gen_python.py
"""

from __future__ import annotations

import json
import keyword
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "python/src/panir/_types.py"

PRIM = {"string": "str", "int": "int", "double": "float", "bool": "bool"}

# pandoc's Lua constructors whose positional arguments are in an order of
# their own, not pandoc-types' (as the other generators' LUA_ORDER):
# `pandoc.Cite(content, citations)`, `pandoc.TableBody(body, head,
# row_head_columns, attr)`, ... A flattened field (attr) stays keyword-only;
# a variadic field is one argument, as in Lua: `pandoc.Cell(blocks, align,
# rowspan, colspan, attr)`.
LUA_ORDER = {
    "Cite": ["content", "citations"],
    "Table": ["caption", "col_specs", "head", "bodies", "foot"],
    "TableBody": ["body", "head", "row_head_columns"],
    "Citation": ["id", "mode", "prefix", "suffix", "note_num", "hash"],
    "Cell": ["content", "alignment", "row_span", "col_span"],
}


def variadic(c: dict) -> str | None:
    """The field the constructor takes as *args: none in Lua's order."""
    return None if c["name"] in LUA_ORDER else c.get("variadic")


def py_literal(value) -> str:
    """JSON as a Python literal."""
    return repr(json.loads(json.dumps(value)))


class Gen:
    def __init__(self, schema: dict) -> None:
        self.schema = schema
        self.types = {t["name"]: t for t in schema["types"]}
        # a sum type's class, where a constructor has the type's name
        self.cls_name = {}
        for t in schema["types"]:
            name = t["name"]
            clash = t["kind"] == "sum" and any(c["name"] == name for c in t["constructors"])
            self.cls_name[name] = name + "Base" if clash else name
        # the pandoc types found as the elements of a list somewhere: a
        # filter function for them may return a list (to splice)
        self.listed: set[str] = set()
        for t in schema["types"]:
            for c in t.get("constructors", [t] if t["kind"] == "product" else []):
                for f in c["fields"]:
                    self.scan_lists(f["type"])

    def scan_lists(self, ty: dict) -> None:
        r = self.resolve(ty)
        if "list" in r:
            inner = self.resolve(r["list"])
            if "ref" in inner:
                self.listed.add(inner["ref"])
            self.scan_lists(r["list"])
        for k in ("maybe",):
            if k in r:
                self.scan_lists(r[k])
        if "map" in r:
            self.scan_lists(r["map"][1])
        if "tuple" in r:
            for t in r["tuple"]:
                self.scan_lists(t)

    # -- types -------------------------------------------------------------

    def field_type(self, ty: dict) -> str:
        """The type of a field's value, as the run time reads it (``_core``)."""
        if "prim" in ty:
            return PRIM[ty["prim"]]
        if "ref" in ty:
            return self.cls_name[ty["ref"]]
        if "list" in ty:
            return f"list[{self.field_type(ty['list'])}]"
        if "maybe" in ty:
            return f"{self.field_type(ty['maybe'])} | None"
        if "map" in ty:
            return f"dict[{self.field_type(ty['map'][0])}, {self.field_type(ty['map'][1])}]"
        if "tuple" in ty:
            return f"tuple[{', '.join(self.field_type(t) for t in ty['tuple'])}]"
        raise ValueError(ty)

    def resolve(self, ty: dict) -> dict:
        while "ref" in ty and self.types[ty["ref"]]["kind"] == "alias":
            ty = self.types[ty["ref"]]["type"]
        return ty

    @staticmethod
    def is_pairs(ty: dict) -> bool:
        """A list of (str, str): attributes."""
        return (
            "tuple" in ty
            and len(ty["tuple"]) == 2
            and all(t.get("prim") == "string" for t in ty["tuple"])
        )

    def get_type(self, ty: dict) -> str:
        """What reading a field gives, for type checkers: its lists and maps
        are ``NodeList``s and ``NodeDict``s, attributes ``Attributes``."""
        r = self.resolve(ty)
        if "list" in r:
            if self.is_pairs(self.resolve(r["list"])):
                return "Attributes"
            return f"NodeList[{self.get_type(r['list'])}, {self.in_one(r['list'])}]"
        if "map" in r:
            return f"NodeDict[{self.get_type(r['map'][1])}, {self.in_one(r['map'][1])}]"
        if "maybe" in r:
            return f"{self.get_type(r['maybe'])} | None"
        if "tuple" in r:
            return f"tuple[{', '.join(self.get_type(t) for t in r['tuple'])}]"
        return self.field_type(r)

    def in_one(self, ty: dict) -> str:
        """What converts to one value of a type (a list's element, a field
        that isn't a list), as ``_core`` converts it."""
        r = self.resolve(ty)
        if "list" in r:
            return self.in_list(r)
        if "map" in r:
            return f"Mapping[str, {self.in_one(r['map'][1])}]"
        if "maybe" in r:
            return f"{self.in_one(r['maybe'])} | None"
        if "tuple" in r:
            return f"tuple[{', '.join(self.in_one(t) for t in r['tuple'])}]"
        if "ref" in r:
            t = self.types[r["ref"]]
            if t["kind"] == "enum":
                names = ", ".join(json.dumps(v) for v in t["values"])
                return f"{t['name']} | Literal[{names}]"
            if t["kind"] == "sum" and t["name"] in CONVERTED:
                # (metadata by an alias: its type is recursive)
                return "_MetaValueIn" if t["name"] == "MetaValue" else CONVERTED[t["name"]]
            if t["kind"] == "product" and t["encoding"] == "array":
                fields = ", ".join(self.in_one(f["type"]) for f in t["fields"])
                return f"{t['name']} | tuple[{fields}]"
        return self.field_type(r)

    def in_list(self, r: dict) -> str:
        """What converts to a list: any iterable of what converts to its
        elements; for inlines and blocks also a string or one element, as
        in pandoc's Lua; for attributes a mapping."""
        item = self.resolve(r["list"])
        if self.is_pairs(item):
            return "Iterable[tuple[str, str]] | Mapping[str, str]"
        if item.get("ref") == "Inline":
            return f"Iterable[{CONVERTED['Inline']}] | Inline | str"
        if item.get("ref") == "Block":
            return f"Iterable[{CONVERTED['Block']}] | Block | Inline | str"
        return f"Iterable[{self.in_one(item)}]"

    def default_literal(self, ty: dict, value) -> str:
        r = self.resolve(ty)
        if value is None or "map" in r:
            return "None"
        if "list" in r:
            if value:
                sys.exit(f"non-empty default list {value} isn't supported")
            return "()"
        if "ref" in r:
            t = self.types[r["ref"]]
            if t["kind"] == "enum":
                return f"{t['name']}.{value['t']}"
            return "None"  # a product: its own default
        return repr(value)

    @staticmethod
    def in_aliases() -> str:
        return (
            "\n# What converts to metadata (a MetaValue, or a plain Python value: see\n"
            "# from_python), for constructors' parameters and fields' setters.\n"
            f"_MetaValueIn: TypeAlias = {CONVERTED['MetaValue']!r}\n"
        )

    # -- declarations --------------------------------------------------------

    def generate(self) -> str:
        api = self.schema["pandoc-api-version"]
        out = [HEADER.format(api=".".join(map(str, api)))]
        out.append(f"PANDOC_API_VERSION = {tuple(api)!r}\n\n\n")
        names: list[str] = []
        types = self.schema["types"]
        for t in types:
            if t["kind"] == "enum":
                out.append(self.enum(t))
                names.append(t["name"])
        for t in types:
            if t["kind"] == "sum":
                base = self.cls_name[t["name"]]
                out.append(
                    f"class {base}(Node):\n"
                    f'    """Any of pandoc\'s ``{t["name"]}`` constructors."""\n\n'
                    f"    __slots__ = ()\n"
                    f'    _kind = "sum"\n\n\n'
                )
                names.append(base)
                for c in t["constructors"]:
                    out.append(self.cls(c, base, None, t["name"]))
                    names.append(c["name"])
            elif t["kind"] == "product":
                out.append(self.cls(t, "Node", t["encoding"], None))
                names.append(t["name"])
        out.append("# pandoc-types' newtypes and type synonyms\n")
        for t in types:
            if t["kind"] == "alias":
                out.append(f"{t['name']}: TypeAlias = {self.field_type(t['type'])}\n")
                names.append(t["name"])
        out.append(self.in_aliases())
        out.append(self.typed_on())
        out.append("\n__all__ = [\n")
        out.extend(f"    {n!r},\n" for n in ["PANDOC_API_VERSION", *names])
        out.append("]\n\n")
        out.append("finalize(globals(), PANDOC_API_VERSION)\n")
        return "".join(out)

    def result(self, name: str) -> str:
        """What a filter function for a node of pandoc type ``name`` may
        return: None, a node of the type (or what converts to one, for
        inlines and blocks, as in pandoc's Lua), or, where the type is in a
        list, a list of them."""
        if name in RESULTS:
            return RESULTS[name]
        cls = self.cls_name[name]
        return f"{cls} | Sequence[{cls}] | None" if name in self.listed else f"{cls} | None"

    def typed_on(self) -> str:
        """``Filter.on``'s signatures for type checkers: per class, what a
        function for it takes and returns; the function keeps its type."""
        o = [
            "\n# How Filter.on types a filter's functions, for type checkers only: a\n"
            "# function for a class of node takes it (and optionally a Context) and\n"
            "# returns None, a node of its type (or what converts to one), or, where\n"
            "# the type is in a list, a list of them. It keeps its own type.\n"
            "if TYPE_CHECKING:\n"
            "    from collections.abc import Callable\n"
            "    from typing import Any, Concatenate, ParamSpec, TypeVar, overload\n\n"
            "    from ._walk import Context\n\n"
            '    _F = TypeVar("_F", bound=Callable[..., Any])\n'
            '    _P = ParamSpec("_P")\n'
        ]
        leaves: list[tuple[str, str]] = []  # (class, pandoc type)
        bases: list[tuple[str, str]] = []
        for t in self.schema["types"]:
            if t["kind"] == "sum":
                leaves += [(c["name"], t["name"]) for c in t["constructors"]]
                bases.append((self.cls_name[t["name"]], t["name"]))
            elif t["kind"] == "product":
                leaves.append((t["name"], t["name"]))
        for name in dict.fromkeys(ty for _, ty in leaves):
            o.append(f"    _{name}Result: TypeAlias = {self.result(name)!r}\n")
        for cls, ty in leaves + bases:
            r = f"_{ty}Result"
            o.append(
                f'    _F{cls} = TypeVar("_F{cls}", '
                f'bound="Callable[[{cls}], {r}] | Callable[[{cls}, Context], {r}]")\n'
            )
        for cls, ty in bases:
            o.append(f'    _N{cls} = TypeVar("_N{cls}", bound={cls})\n')
            o.append(f'    _R{cls} = TypeVar("_R{cls}", bound="_{ty}Result")\n')
        o.append("\n")
        for cls, _ in leaves + bases:
            o.append(
                "    @overload\n"
                f"    def _typed_on(self: Any, type: type[{cls}], /) -> "
                f"Callable[[_F{cls}], _F{cls}]: ...\n\n"
            )
        for cls, _ in bases:
            n, r = f"_N{cls}", f"_R{cls}"
            fn = f"Callable[Concatenate[{n}, _P], {r}]"
            o.append(
                "    @overload\n"
                f"    def _typed_on(\n"
                f"        self: Any, type: type[{n}], /, *types: type[{n}]\n"
                f"    ) -> Callable[[{fn}], {fn}]: ...\n\n"
            )
        o.append(
            "    @overload\n"
            "    def _typed_on(self: Any, *types: type[Node]) -> Callable[[_F], _F]: ...\n\n"
            "    def _typed_on(self: Any, *types: Any) -> Any: ...\n"
        )
        return "".join(o)

    @staticmethod
    def enum(t: dict) -> str:
        lines = [f"class {t['name']}(Enum):", f'    """pandoc\'s ``{t["name"]}``."""', ""]
        lines += [f"    {v} = {v!r}" for v in t["values"]]
        return "\n".join(lines) + "\n\n\n"

    @staticmethod
    def pyname(n: str) -> str:
        return n + "_" if keyword.iskeyword(n) else n

    def cls(self, c: dict, base: str, product_encoding: str | None, sum_name: str | None) -> str:
        name = c["name"]
        fields = c["fields"]
        fnames = [self.pyname(f["name"]) for f in fields]
        doc = f"pandoc's ``{name}``, a {sum_name}." if sum_name else f"pandoc's ``{name}``."
        lines = [f"class {name}({base}):", f'    """{doc}"""', ""]
        slots = "(" + "".join(f"{n!r}, " for n in fnames).rstrip(" ") + ")"
        # (type checkers see the fields as descriptors, below, which slots
        # would conflict with)
        lines.append("    if not TYPE_CHECKING:")
        lines.append(f"        __slots__ = {slots}")
        init = self.init(c, fnames) if fields else []
        positional = self.positional if fields else []
        # in the constructor's order: its positional parameters, the
        # variadic one, then the others in field order
        match_args = [*positional]
        if variadic(c):
            match_args.append(self.pyname(c["variadic"]))
        match_args += [n for n in fnames if n not in match_args]
        lines.append(f"    __match_args__ = {tuple(match_args)!r}")
        if product_encoding:
            lines.append('    _kind = "product"')
            lines.append(f"    _encoding = {product_encoding!r}")
        else:
            lines.append(f"    _encoding = {c['encoding']!r}")
        keys = {self.pyname(f["name"]): f["key"] for f in fields if "key" in f}
        if keys:
            lines.append(f"    _keys = {keys!r}")
        if variadic(c):
            lines.append(f"    _variadic = {self.pyname(c['variadic'])!r}")
        flatten = tuple(self.pyname(f["name"]) for f in fields if f.get("flatten"))
        if flatten:
            lines.append(f"    _flatten = {flatten!r}")
            # a flattened product's fields, read and set through the node
            # (``el.identifier``, ``el.attributes``), as in pandoc's Lua
            parts = {}
            for f in fields:
                if f.get("flatten"):
                    for g in self.types[f["type"]["ref"]]["fields"]:
                        parts[self.pyname(g["name"])] = self.pyname(f["name"])
            lines.append(f"    _parts = {parts!r}")
        defaults = {self.pyname(f["name"]): f["default"] for f in fields if "default" in f}
        if defaults:
            lines.append(f"    _defaults = {py_literal(defaults)}")
        if positional:
            lines.append(f"    _positional = {tuple(positional)!r}")
        if fields:
            lines.append("")
            lines.append("    if TYPE_CHECKING:")
            for n, f in zip(fnames, fields):
                field = f"_Field[{self.get_type(f['type'])}, {self.set_type(f['type'])}]"
                lines.append(f"        {n}: {field} = _Field()")
            for f in fields:
                if f.get("flatten"):
                    for g in self.types[f["type"]["ref"]]["fields"]:
                        field = f"_Field[{self.get_type(g['type'])}, {self.set_type(g['type'])}]"
                        lines.append(f"        {self.pyname(g['name'])}: {field} = _Field()")
            lines.append("    else:")
            for n, f in zip(fnames, fields):
                lines.append(f"        {n}: {self.field_type(f['type'])}")
            lines.append("")
            lines += init
        return "\n".join(lines) + "\n\n\n"

    def set_type(self, ty: dict) -> str:
        """What a field may be set to."""
        r = self.resolve(ty)
        return self.in_list(r) if "list" in r else self.in_one(r)

    def init(self, c: dict, fnames: list[str]) -> list[str]:
        """``__init__`` and ``_positional``, in pandoc-types' field order, or
        pandoc's Lua's (``LUA_ORDER``).

        Fields are positional, then the variadic field (if any, and not in
        Lua's order) as *args,
        then keyword-only: a field with a default before *args, and any field
        after it, and a required field after one with a default. Flattened
        products are keyword-only, whole or by their fields' names.
        E.g. ``Header(level, *content, attr=, identifier=, ...)``.
        """
        fields = c["fields"]
        star_field = variadic(c)
        values = list(fnames)  # (the fields' values in field order)
        params_order = list(zip(fnames, fields))
        if c["name"] in LUA_ORDER:
            rank = {n: k for k, n in enumerate(LUA_ORDER[c["name"]])}
            assert set(rank) <= set(fnames), c["name"]
            params_order.sort(key=lambda nf: rank.get(nf[0], len(rank)))  # (stable)
        self.positional: list[str] = []
        params: list[str] = []
        star: str | None = None
        kwonly: list[str] = []
        flats: list[str] = []
        positional = True
        after_default = False
        for n, f in params_order:
            ptype = self.set_type(f["type"])
            if f["name"] == star_field:
                star = f"*{n}: {self.in_one(self.resolve(f['type'])['list'])}"
                positional = False
                continue
            if f.get("flatten"):
                prod = self.types[f["type"]["ref"]]
                parts = []
                kwonly.append(f"{n}: {ptype} | None = None")
                for g in prod["fields"]:
                    gn = self.pyname(g["name"])
                    gtype = self.set_type(g["type"])
                    if "default" in g:
                        kwonly.append(
                            f"{gn}: {gtype} = {self.default_literal(g['type'], g['default'])}"
                        )
                    else:
                        kwonly.append(f"{gn}: {gtype} | None = None")
                    parts.append(gn)
                flats.append(
                    f"{n} = _flat({prod['name']}, {c['name']!r}, {n!r}, {n}, "
                    f"({', '.join(parts)}{',' if len(parts) == 1 else ''}))"
                )
                continue
            has_default = "default" in f
            if has_default:
                lit = self.default_literal(f["type"], f["default"])
                if lit == "None" and not ptype.endswith("| None"):
                    ptype += " | None"
                decl = f"{n}: {ptype} = {lit}"
            else:
                decl = f"{n}: {ptype}"
            # before *args, only required fields; and Python allows no
            # required parameter after one with a default
            if has_default and star_field or not has_default and after_default:
                positional = False
            if positional:
                params.append(decl)
                self.positional.append(n)
                after_default = after_default or has_default
            else:
                kwonly.append(decl)
        out = []
        sig = ["self", *params]
        if star:
            sig.append(star)
        elif kwonly:
            sig.append("*")
        sig += kwonly
        head = f"    def __init__({', '.join(sig)}) -> None:"
        if len(head) <= 88:
            out.append(head)
        else:
            out.append("    def __init__(")
            out += [f"        {s}," for s in sig]
            out.append("    ) -> None:")
        out += [f"        {line}" for line in flats]
        tup = f"({', '.join(values)}{',' if len(values) == 1 else ''})"
        out.append(f"        _init(self, {tup})")
        return out


# The sum types whose values convert from others, as in pandoc's Lua: a
# string is a Str where one inline goes; where one block goes, a string is
# Plain text, an inline or a list of inlines a Plain of them; metadata
# converts from plain Python values (``from_python``).
CONVERTED = {
    "Inline": "Inline | str",
    "Block": "Block | Inline | str | Sequence[Inline | str]",
    "MetaValue": "MetaValue | str | bool | int | float"
    " | Mapping[str, _MetaValueIn] | Sequence[_MetaValueIn]",
}

# What filter functions for inlines and blocks may return (pandoc's Lua
# converts these): one, or a list to splice; strings convert.
RESULTS = {
    "Inline": "Inline | str | Sequence[Inline | str] | None",
    "Block": f"Block | Inline | str | Sequence[{CONVERTED['Block']}] | None",
}

HEADER = '''\
# GENERATED by tools/gen_python.py from schema/pandoc-ast.json
# (pandoc-api-version {api}). Do not edit; regenerate instead.
"""pandoc's AST types: declarations only.

One class per constructor, a subclass of its type's class (``Header`` of
``Block``), and one per product type (``Attr``, ``Citation``, ...). Fields are
in pandoc-types' order. How the classes behave is in ``_core``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Literal, TypeAlias

from ._core import Attributes, Enum, Node, NodeDict, NodeList, finalize
from ._core import Field as _Field
from ._core import flat as _flat
from ._core import init as _init

'''


def main() -> None:
    schema = json.loads((ROOT / "schema/pandoc-ast.json").read_text())
    OUT.write_text(Gen(schema).generate())


if __name__ == "__main__":
    main()
