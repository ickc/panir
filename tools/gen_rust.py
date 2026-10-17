#!/usr/bin/env python3
"""Generate rust/src/generated.rs from schema/pandoc-ast.json.

Rust has no run-time reflection, so unlike Python's the generated code here
is more than declarations: it is what a derive would write. Types:

- a sum type is an enum, adjacently tagged (``#[serde(tag = "t", content =
  "c")]``), which is pandoc-types' encoding: a constructor without fields is a
  unit variant (``Inline::Space``), one with one field holds it
  (``Inline::Str(Text)``), one with more holds a struct of the same name
  with named fields, boxed (``Block::Header(Box<Header>)``), encoded as an
  array. Boxed, so that every variant is small: an ``Inline`` or ``Block``
  is 32 bytes, not the size of its biggest struct (152 and 360). A box
  encodes as what it holds, and ``From<Header> for Block`` builds one;
- a product is a struct: encoded as an array (``Attr``), as an object with
  pandoc-types' keys (``Citation``), or as the document (``Pandoc``);
- an enum-like type is an internally tagged enum: ``{"t": "InlineMath"}``;
- an alias is a type alias;
- pandoc-types' ``Text`` is ``Text`` (``text.rs``): a string that keeps up
  to 22 bytes in place, in the 24 bytes a ``String`` takes.

And ``VisitMut``: a method per type, whose default visits the children, to
override for a filter (as syn's ``visit_mut``); and ``Visit``, the same
reading only (``&``, no copy to walk a part of a document; ``walk_*_ref``).

    python3 tools/gen_rust.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "rust/src/generated.rs"

PRIM = {"string": "Text", "int": "i64", "double": "f64", "bool": "bool"}
KEYWORDS = {
    "type",
    "match",
    "ref",
    "mod",
    "move",
    "fn",
    "impl",
    "use",
    "where",
    "self",
    "loop",
    "box",
    "yield",
    "async",
    "await",
    "dyn",
    "abstract",
    "final",
    "override",
}


def snake(name: str) -> str:
    out = ""
    for i, ch in enumerate(name):
        if ch.isupper() and i and not name[i - 1].isupper():
            out += "_"
        out += ch.lower()
    return out


def ident(name: str) -> str:
    return f"r#{name}" if name in KEYWORDS else name


class Gen:
    def __init__(self, schema: dict) -> None:
        self.schema = schema
        self.types = {t["name"]: t for t in schema["types"]}
        self.out: list[str] = []

    def resolve(self, ty: dict) -> dict:
        while "ref" in ty and self.types[ty["ref"]]["kind"] == "alias":
            ty = self.types[ty["ref"]]["type"]
        return ty

    def rust(self, ty: dict) -> str:
        if "prim" in ty:
            return PRIM[ty["prim"]]
        if "ref" in ty:
            return ty["ref"]
        if "list" in ty:
            return f"Vec<{self.rust(ty['list'])}>"
        if "maybe" in ty:
            return f"Option<{self.rust(ty['maybe'])}>"
        if "map" in ty:
            return f"BTreeMap<{self.rust(ty['map'][0])}, {self.rust(ty['map'][1])}>"
        if "tuple" in ty:
            return f"({', '.join(self.rust(t) for t in ty['tuple'])})"
        raise ValueError(ty)

    def default_expr(self, ty: dict, value) -> str:
        r = self.resolve(ty)
        if value is None:
            return "None"
        if "list" in r:
            return "Vec::new()"
        if "map" in r:
            return "BTreeMap::new()"
        if "ref" in r:
            t = self.types[r["ref"]]
            if t["kind"] == "enum":
                return f"{t['name']}::{value['t']}"
            if t["kind"] == "sum":
                return f"{t['name']}::{value['t']}"
            return f"{t['name']}::default()"
        if r.get("prim") == "string":
            return "Text::new()" if value == "" else f"Text::from({json.dumps(value)})"
        return json.dumps(value)

    def has_default(self, t: dict) -> bool:
        return all("default" in f for f in t["fields"])

    # -- declarations -------------------------------------------------------

    def generate(self) -> str:
        api = self.schema["pandoc-api-version"]
        o = self.out
        o.append(HEADER.format(api=".".join(map(str, api))))
        o.append(
            f"/// The pandoc-types API version these types are for.\n"
            f"pub const PANDOC_API_VERSION: [i64; {len(api)}] = {api!r};\n\n"
        )
        for t in self.schema["types"]:
            getattr(self, t["kind"])(t)
        self.visitor(True)
        self.visitor(False)
        return "".join(o)

    def alias(self, t: dict) -> None:
        name, target = t["name"], self.rust(t["type"])
        self.out.append(f"/// pandoc's `{name}`.\npub type {name} = {target};\n\n")

    def enum(self, t: dict) -> None:
        o = self.out
        o.append(f"/// pandoc's `{t['name']}`.\n")
        o.append("#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize)]\n")
        o.append('#[serde(tag = "t")]\n')
        o.append(f"pub enum {t['name']} {{\n")
        o.extend(f"    {v},\n" for v in t["values"])
        o.append("}\n\n")
        name = t["name"]
        tags = ", ".join(json.dumps(v) for v in t["values"])
        arms = "".join(f'                "{v}" => {name}::{v},\n' for v in t["values"])
        o.append(ENUM_DE.format(name=name, tags=tags, arms=arms))

    def sum(self, t: dict) -> None:
        o = self.out
        structs = []
        o.append(f"/// pandoc's `{t['name']}`.\n")
        o.append("#[derive(Debug, Clone, PartialEq, Serialize)]\n")
        o.append('#[serde(tag = "t", content = "c")]\n')
        o.append(f"pub enum {t['name']} {{\n")
        for c in t["constructors"]:
            n = len(c["fields"])
            if n == 0:
                o.append(f"    {c['name']},\n")
            elif n == 1:
                ty = self.rust(c["fields"][0]["type"])
                # pandoc's JSON writes a Double as Haskell shows it
                attr = '#[serde(serialize_with = "crate::aeson_double")] ' if ty == "f64" else ""
                o.append(f"    {c['name']}({attr}{ty}),\n")
            else:
                o.append(f"    {c['name']}(Box<{c['name']}>),\n")
                structs.append(c)
        o.append("}\n\n")
        self.sum_de(t)
        for c in structs:
            o.append(
                f"impl From<{c['name']}> for {t['name']} {{\n"
                f"    fn from(x: {c['name']}) -> Self {{\n"
                f"        {t['name']}::{c['name']}(Box::new(x))\n    }}\n}}\n\n"
            )
        for c in structs:
            if c["name"] in self.types:
                raise SystemExit(f"constructor {c['name']} clashes with a type name")
            self.struct(c, "array", f"pandoc's `{c['name']}`, a `{t['name']}`.")

    def sum_de(self, t: dict) -> None:
        """Deserialize reading "t" then "c" in place, so that errors inside
        "c" keep their path (serde's derive buffers "c" and loses it)."""
        name = t["name"]
        tags = ", ".join(json.dumps(c["name"]) for c in t["constructors"])
        units = [c["name"] for c in t["constructors"] if not c["fields"]]
        stream, early = [], []
        for c in t["constructors"]:
            cn = c["name"]
            if not c["fields"]:
                skip = "map.next_value::<de::IgnoredAny>()?;"
                stream.append(f'{" " * 24}"{cn}" => {{ {skip} {name}::{cn} }}\n')
                early.append(f'                "{cn}" => {name}::{cn},\n')
            else:
                stream.append(f'{" " * 24}"{cn}" => {name}::{cn}(map.next_value()?),\n')
                early.append(f'                "{cn}" => {name}::{cn}(json(c)?),\n')
        unit_arms = "".join(f'                    "{u}" => Some({name}::{u}),\n' for u in units)
        self.out.append(
            SUM_DE.format(
                name=name, tags=tags, stream="".join(stream), early="".join(early), units=unit_arms
            )
        )

    def product(self, t: dict) -> None:
        self.struct(t, t["encoding"], f"pandoc's `{t['name']}`.")

    def struct(self, t: dict, encoding: str, doc: str) -> None:
        o = self.out
        name = t["name"]
        fields = t["fields"]
        derive_serde = encoding in ("object", "root")
        derives = ["Debug", "Clone", "PartialEq"]
        if derive_serde:
            derives += ["Serialize", "Deserialize"]
        o.append(f"/// {doc}\n#[derive({', '.join(derives)})]\n")
        o.append(f"pub struct {name} {{\n")
        if encoding == "root":
            o.append(
                '    #[serde(rename = "pandoc-api-version")]\n    pub api_version: Vec<i64>,\n'
            )
        for f in fields:
            if "key" in f and f["key"] != f["name"]:
                o.append(f'    #[serde(rename = "{f["key"]}")]\n')
            o.append(f"    pub {ident(f['name'])}: {self.rust(f['type'])},\n")
        o.append("}\n\n")
        if self.has_default(t):
            o.append(
                f"impl Default for {name} {{\n    fn default() -> Self {{\n        {name} {{\n"
            )
            if encoding == "root":
                o.append("            api_version: PANDOC_API_VERSION.to_vec(),\n")
            for f in fields:
                value = self.default_expr(f["type"], f["default"])
                o.append(f"            {ident(f['name'])}: {value},\n")
            o.append("        }\n    }\n}\n\n")
        if encoding == "array":
            refs = ", ".join(f"&self.{ident(f['name'])}" for f in fields)
            types = ", ".join(self.rust(f["type"]) for f in fields)
            names = ", ".join(ident(f["name"]) for f in fields)
            o.append(
                f"impl Serialize for {name} {{\n"
                f"    fn serialize<S: Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {{\n"
                f"        ({refs}).serialize(s)\n    }}\n}}\n\n"
            )
            o.append(
                f"impl<'de> Deserialize<'de> for {name} {{\n"
                f"    fn deserialize<D: Deserializer<'de>>(d: D)"
                f" -> Result<Self, D::Error> {{\n"
                f"        let ({names}) = <({types})>::deserialize(d)?;\n"
                f"        Ok({name} {{ {names} }})\n    }}\n}}\n\n"
            )

    # -- the visitor ----------------------------------------------------------

    def node_types(self) -> list[dict]:
        return [t for t in self.schema["types"] if t["kind"] in ("sum", "product")]

    def holds_nodes(self, ty: dict) -> bool:
        ty = self.resolve(ty)
        if "prim" in ty:
            return False
        if "ref" in ty:
            return self.types[ty["ref"]]["kind"] in ("sum", "product")
        if "list" in ty:
            return self.holds_nodes(ty["list"])
        if "maybe" in ty:
            return self.holds_nodes(ty["maybe"])
        if "map" in ty:
            return self.holds_nodes(ty["map"][1])
        if "tuple" in ty:
            return any(self.holds_nodes(t) for t in ty["tuple"])
        return False

    def list_types(self) -> list[str]:
        """Sum types that appear in lists: they get a visit_<type>s method."""
        seen: list[str] = []

        def scan(ty: dict) -> None:
            ty = self.resolve(ty)
            if "list" in ty:
                inner = self.resolve(ty["list"])
                if (
                    "ref" in inner
                    and self.types[inner["ref"]]["kind"] == "sum"
                    and inner["ref"] not in seen
                ):
                    seen.append(inner["ref"])
                scan(ty["list"])
            for k in ("maybe",):
                if k in ty:
                    scan(ty[k])
            if "map" in ty:
                scan(ty["map"][1])
            if "tuple" in ty:
                for x in ty["tuple"]:
                    scan(x)

        for t in self.node_types():
            for c in t["constructors"] if t["kind"] == "sum" else [t]:
                for f in c["fields"]:
                    scan(f["type"])
        return seen

    def visit_expr(self, ty: dict, x: str, depth: int = 0, mut: bool = True) -> str | None:
        """A statement visiting ``x`` (a ``&mut`` place, or ``&``) of type ``ty``."""
        if not self.holds_nodes(ty):
            return None
        ty = self.resolve(ty)
        v = f"x{depth}"
        r, m = ("&mut ", "_mut") if mut else ("&", "")
        if "ref" in ty:
            return f"v.visit_{snake(ty['ref'])}({x});"
        if "list" in ty:
            inner = self.resolve(ty["list"])
            if "ref" in inner and inner["ref"] in self.lists:
                return f"v.visit_{snake(inner['ref'])}s({x});"
            body = self.visit_expr(ty["list"], v, depth + 1, mut)
            return f"for {v} in ({x}).iter{m}() {{ {body} }}"
        if "maybe" in ty:
            body = self.visit_expr(ty["maybe"], v, depth + 1, mut)
            return f"if let Some({v}) = ({x}).as{'_mut' if mut else '_ref'}() {{ {body} }}"
        if "map" in ty:
            body = self.visit_expr(ty["map"][1], v, depth + 1, mut)
            return f"for {v} in ({x}).values{m}() {{ {body} }}"
        if "tuple" in ty:
            parts = [
                self.visit_expr(t, f"{r}({x}).{i}", depth + 1, mut)
                for i, t in enumerate(ty["tuple"])
            ]
            return " ".join(p for p in parts if p)
        return None

    def visitor(self, mut: bool) -> None:
        """``VisitMut`` and its ``walk_*`` (``mut``), or ``Visit`` and its
        ``walk_*_ref``."""
        o = self.out
        self.lists = self.list_types()
        trait, r, suffix = ("VisitMut", "&mut ", "") if mut else ("Visit", "&", "_ref")
        o.append(VISITOR_DOC if mut else VISIT_DOC)
        o.append(f"pub trait {trait} {{\n")
        for t in self.node_types():
            n = snake(t["name"])
            o.append(
                f"    fn visit_{n}(&mut self, x: {r}{t['name']}) {{\n"
                f"        walk_{n}{suffix}(self, x)\n    }}\n"
            )
        for name in self.lists:
            n = snake(name)
            xs = f"&mut Vec<{name}>" if mut else f"&[{name}]"
            doc = "e.g. to splice or remove some" if mut else "e.g. to look at neighbours"
            o.append(
                f"    /// The {name}s of one list, {doc}.\n"
                f"    fn visit_{n}s(&mut self, xs: {xs}) {{\n"
                f"        walk_{n}s{suffix}(self, xs)\n    }}\n"
            )
        o.append("}\n\n")
        for name in self.lists:
            n = snake(name)
            xs = f"&mut Vec<{name}>" if mut else f"&[{name}]"
            it = "iter_mut" if mut else "iter"
            o.append(
                f"/// Visits each {name} of a list.\n"
                f"pub fn walk_{n}s{suffix}<V: {trait} + ?Sized>(v: &mut V, xs: {xs}) {{\n"
                f"    for x in xs.{it}() {{\n        v.visit_{n}(x);\n    }}\n}}\n\n"
            )
        for t in self.node_types():
            n = snake(t["name"])
            o.append(
                f"/// Visits the children of a {t['name']}.\n"
                f"#[allow(unused_variables)]\n"
                f"pub fn walk_{n}{suffix}<V: {trait} + ?Sized>(v: &mut V, x: {r}{t['name']}) {{\n"
            )
            if t["kind"] == "product":
                for f in t["fields"]:
                    s = self.visit_expr(f["type"], f"{r}x.{ident(f['name'])}", mut=mut)
                    if s:
                        o.append(f"    {s}\n")
            else:
                arms = []
                for c in t["constructors"]:
                    fs = c["fields"]
                    if len(fs) == 1:
                        s = self.visit_expr(fs[0]["type"], "y", mut=mut)
                        if s:
                            arms.append(f"        {t['name']}::{c['name']}(y) => {{ {s} }}\n")
                    elif len(fs) > 1:
                        stmts = [
                            self.visit_expr(f["type"], f"{r}y.{ident(f['name'])}", mut=mut)
                            for f in fs
                        ]
                        stmts = [s for s in stmts if s]
                        if stmts:
                            body = " ".join(stmts)
                            arms.append(f"        {t['name']}::{c['name']}(y) => {{ {body} }}\n")
                if arms:
                    o.append("    match x {\n")
                    o.extend(arms)
                    o.append("        _ => {}\n    }\n")
            o.append("}\n\n")


HEADER = """\
// GENERATED by tools/gen_rust.py from schema/pandoc-ast.json
// (pandoc-api-version {api}). Do not edit; regenerate instead.

//! pandoc's AST types, their JSON encoding, `VisitMut` and `Visit`.

#![allow(clippy::all)]

use serde::de;
use serde::{{Deserialize, Deserializer, Serialize, Serializer}};
use std::collections::BTreeMap;

use crate::Text;

"""

ENUM_DE = """\
impl<'de> Deserialize<'de> for {name} {{
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {{
        const TAGS: &[&str] = &[{tags}];
        let t = crate::de_tag(d, "{name}", TAGS)?;
        Ok(match t {{
{arms}            _ => unreachable!(),
        }})
    }}
}}

"""

SUM_DE = """\
impl<'de> Deserialize<'de> for {name} {{
    fn deserialize<D: Deserializer<'de>>(d: D) -> Result<Self, D::Error> {{
        const TAGS: &[&str] = &[{tags}];
        struct V;
        impl<'de> de::Visitor<'de> for V {{
            type Value = {name};
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {{
                f.write_str("a {name}: {{\\"t\\": ..., \\"c\\": ...}}")
            }}
            fn visit_map<A: de::MapAccess<'de>>(self, mut map: A) -> Result<{name}, A::Error> {{
                let mut tag: Option<&'static str> = None;
                let mut value: Option<{name}> = None;
                let mut early: Option<serde_json::Value> = None;
                // keys and tags are matched, not copied
                while let Some(key) = map.next_key::<crate::Key>()? {{
                    if key == crate::Key::T {{
                        let t = crate::Tag::check(map.next_value_seed(crate::Tag(TAGS))?, TAGS)?;
                        value = match early.take() {{
                            Some(c) => Some(from_value(&t, c)?),
                            None => unit(&t),
                        }};
                        tag = Some(t);
                    }} else if key == crate::Key::C {{
                        match tag.as_deref() {{
                            None => early = Some(map.next_value()?),
                            Some(t) => value = Some(match t {{
{stream}                        _ => unreachable!(),
                            }}),
                        }}
                    }} else {{
                        map.next_value::<de::IgnoredAny>()?;
                    }}
                }}
                match (tag, value) {{
                    (_, Some(v)) => Ok(v),
                    (None, _) => Err(de::Error::missing_field("t")),
                    (Some(_), None) => Err(de::Error::missing_field("c")),
                }}
            }}
        }}
        // "c" before "t" (not pandoc's order): decode it once "t" is known
        fn from_value<E: de::Error>(t: &str, c: serde_json::Value) -> Result<{name}, E> {{
            fn json<T: de::DeserializeOwned, E: de::Error>(c: serde_json::Value) -> Result<T, E> {{
                serde_path_to_error::deserialize(c).map_err(|e| {{
                    let p = e.path().to_string();
                    let at = match p.as_str() {{
                        "." => "c".to_string(),
                        p if p.starts_with('[') => format!("c{{p}}"),
                        p => format!("c.{{p}}"),
                    }};
                    E::custom(format!("{{at}}: {{}}", e.inner()))
                }})
            }}
            Ok(match t {{
{early}                _ => unreachable!(),
            }})
        }}
        fn unit(t: &str) -> Option<{name}> {{
            match t {{
{units}                    _ => None,
            }}
        }}
        d.deserialize_map(V)
    }}
}}

"""

VISITOR_DOC = """\
/// A visitor over a document, changing it in place.
///
/// Each method's default visits the node's children (`walk_*`). Override
/// the ones for the nodes to change; call the `walk_*` function in an
/// override to visit the children too (before changing the node: bottom-up;
/// after: top-down).
///
/// ```
/// use panir::{Inline, VisitMut, walk_inline};
///
/// struct Upper;
/// impl VisitMut for Upper {
///     fn visit_inline(&mut self, x: &mut Inline) {
///         walk_inline(self, x);
///         if let Inline::Str(s) = x {
///             *s = s.to_uppercase().into();
///         }
///     }
/// }
/// ```
"""


VISIT_DOC = """\
/// A visitor over a document, reading it: `VisitMut`'s twin, for `&`
/// (nothing to copy to look through a part of a document).
///
/// Each method's default visits the node's children (`walk_*_ref`).
///
/// ```
/// use panir::{Inline, Visit, walk_inline_ref};
///
/// struct Words(usize);
/// impl Visit for Words {
///     fn visit_inline(&mut self, x: &Inline) {
///         if let Inline::Str(_) = x {
///             self.0 += 1;
///         }
///         walk_inline_ref(self, x);
///     }
/// }
/// let mut n = Words(0);
/// n.visit_inline(&Inline::Emph(vec![Inline::Str("a".into())]));
/// assert_eq!(n.0, 1);
/// ```
"""


def main() -> None:
    schema = json.loads((ROOT / "schema/pandoc-ast.json").read_text())
    OUT.write_text(Gen(schema).generate())


if __name__ == "__main__":
    main()
