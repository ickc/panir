"""Random documents and filter specs for the fuzzer (see fuzz/README.md).

Documents come from ``schema/pandoc-ast.json``, so every constructor and
field is reached without this file naming one. Strings, numbers and map keys
are drawn with a bias to the values a binding is likely to get wrong.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

WORDS = ["a", "b", "The", "title", "x", "yz", "Some", "text", "0", "foo-bar", "é"]
NASTY_STRINGS = [
    "",
    " ",
    "  two  spaces ",
    "\n",
    "\t",
    "\r\n",
    "\u0000",
    "a\u0000b",
    "\u0001\u001f",
    "\u007f",
    "\u0080\u009f",
    " ",
    "  ",
    "﻿",
    "�",
    "￾￿",
    "퟿",
    "\U0001f600",
    "\U00010000\U0010ffff",
    "é",
    '"',
    "\\",
    '\\"\\n',
    "/",
    "</script>",
    "<!--",
    "&amp;",
    "#1",
    "__proto__",
    # around the 22 bytes a short string of panir's Rust holds in place
    "a" * 21,
    "a" * 22,
    "a" * 23,
    "a" * 20 + "é",
    "a" * 21 + "é",
    "a" * 19 + "\U0001f600",
    "é" * 11,
    "é" * 12,
    "\U0001f600" * 6,
    "x" * 300,
    "ß",
    "İ",
    "ǆ",
    "ﬁ",
]
NASTY_KEYS = [
    "",
    "__proto__",
    "constructor",
    "prototype",
    "toString",
    "valueOf",
    "hasOwnProperty",
    "length",
    "0",
    "1",
    "-1",
    "a.b",
    "a b",
    "a/b",
    "é",
    "\U0001f600",
    "\u0000",
    "t",
    "c",
    "meta",
    "blocks",
    "pandoc-api-version",
    "Z",
    "z",
    "a" * 40,
]
INTS = [
    0,
    1,
    -1,
    2,
    6,
    7,
    255,
    2**31 - 1,
    2**31,
    -(2**31),
    -(2**31) - 1,
    2**32,
    2**53 - 1,
    2**53,
    2**53 + 1,
    -(2**53) - 1,
    2**63 - 1,
    -(2**63),
    10**18,
]
DOUBLES = [
    0.0,
    1.0,
    0.5,
    0.25,
    0.039,
    0.1,
    0.1 + 0.2,
    1 / 3,
    2 / 3,
    1e-7,
    1e-5,
    1e21,
    1e22,
    1.5e300,
    5e-324,
    2.2250738585072014e-308,
    1.7976931348623157e308,
    123456789012345680000.0,
    float(2**53),
    float(2**53 + 2),
    -0.0,
    -2.5,
    100.0,
    0.3333333333333333,
    4.35,
    9007199254740993.0,
]


class Gen:
    def __init__(self, rng: random.Random, schema: dict, *, max_depth: int = 5, nasty: float = 0.15):
        self.rng, self.max_depth, self.nasty = rng, max_depth, nasty
        self.types = {t["name"]: t for t in schema["types"]}
        self.version = schema["pandoc-api-version"]

    def document(self) -> dict:
        return {
            "pandoc-api-version": self.version,
            "meta": self.value(self.types["Meta"]["type"], 1),
            "blocks": [
                self.value({"ref": "Block"}, 1) for _ in range(self.rng.choice([0, 1, 2, 3, 5, 8]))
            ],
        }

    def string(self) -> str:
        r = self.rng.random()
        if r < self.nasty:
            return self.rng.choice(NASTY_STRINGS)
        if r < self.nasty * 1.5:
            # any Unicode scalar values
            return "".join(self.scalar() for _ in range(self.rng.randrange(1, 30)))
        return self.rng.choice(WORDS)

    def scalar(self) -> str:
        while True:
            c = self.rng.choice(
                [self.rng.randrange(0x80), self.rng.randrange(0x800), self.rng.randrange(0x110000)]
            )
            if not 0xD800 <= c <= 0xDFFF:
                return chr(c)

    def key(self) -> str:
        if self.rng.random() < self.nasty:
            return self.rng.choice(NASTY_KEYS)
        return self.rng.choice(WORDS) + str(self.rng.randrange(4))

    def count(self, depth: int) -> int:
        if depth >= self.max_depth:
            return 0
        if depth <= 2:
            return self.rng.choice([0, 1, 2, 2, 3, 4])
        return self.rng.choice([0, 1, 1, 2, 3])

    def value(self, ty: dict, depth: int):
        rng = self.rng
        if "prim" in ty:
            p = ty["prim"]
            if p == "string":
                return self.string()
            if p == "int":
                return rng.choice(INTS) if rng.random() < self.nasty else rng.randrange(-1, 7)
            if p == "double":
                return rng.choice(DOUBLES) if rng.random() < 0.5 else rng.random()
            if p == "bool":
                return rng.random() < 0.5
            raise ValueError(p)
        if "list" in ty:
            return [self.value(ty["list"], depth) for _ in range(self.count(depth))]
        if "maybe" in ty:
            return None if rng.random() < 0.5 else self.value(ty["maybe"], depth)
        if "map" in ty:
            return {self.key(): self.value(ty["map"][1], depth) for _ in range(self.count(depth))}
        if "tuple" in ty:
            return [self.value(t, depth) for t in ty["tuple"]]
        t = self.types[ty["ref"]]
        kind = t["kind"]
        if kind == "alias":
            return self.value(t["type"], depth)
        if kind == "enum":
            return {"t": rng.choice(t["values"])}
        if kind == "sum":
            con = rng.choice(t["constructors"])
            fields = [self.value(f["type"], depth + 1) for f in con["fields"]]
            if con["encoding"] == "none":
                return {"t": con["name"]}
            return {"t": con["name"], "c": fields[0] if con["encoding"] == "value" else fields}
        fields = [self.value(f["type"], depth + 1) for f in t["fields"]]
        if t["encoding"] == "array":
            return fields
        if t["encoding"] == "value":
            return fields[0]
        return {f["key"]: v for f, v in zip(t["fields"], fields)}


# -- filter specs ----------------------------------------------------------

ELEMENT_ACTIONS = ["keep", "delete", "dup", "replace", "unwrap", "mark", "markret"]
LIST_ACTIONS = ["keep", "reverse", "dropfirst", "append", "empty"]


def tags(doc) -> set[str]:
    """The constructors a document (as JSON) uses."""
    out: set[str] = set()
    todo = [doc]
    while todo:
        j = todo.pop()
        if isinstance(j, dict):
            if isinstance(j.get("t"), str):
                out.add(j["t"])
            todo.extend(j.values())
        elif isinstance(j, list):
            todo.extend(j)
    return out


def spec(rng: random.Random, schema: dict, doc=None) -> dict:
    """A random filter spec; mostly on the nodes ``doc`` has, if given."""
    types = {t["name"]: t for t in schema["types"]}
    inlines = [c["name"] for c in types["Inline"]["constructors"]]
    blocks = [c["name"] for c in types["Block"]["constructors"]]
    present = sorted(tags(doc) & set(inlines + blocks)) if doc is not None else []

    def rule(actions: list[str]) -> dict:
        r: dict = {"do": rng.choice(actions)}
        if rng.random() < 0.5:
            r["mod"] = rng.choice([2, 3, 5])
            r["eq"] = rng.randrange(r["mod"])
        return r

    def one() -> dict:
        traverse = rng.choice(["typewise", "typewise", "topdown"])
        topdown = traverse == "topdown"
        # top-down, a function's result is walked: one that holds the node
        # it replaces would be replaced for ever, unless it skips
        element = ELEMENT_ACTIONS + (["skip", "wrapskip", "markskip"] if topdown else ["wrap"])
        lists = LIST_ACTIONS + (["skip"] if topdown else [])
        rules: dict = {}
        names = ["Inline", "Block"] + inlines + blocks
        for _ in range(rng.choice([0, 1, 1, 2, 2, 3, 4, 6])):
            name = rng.choice(present) if present and rng.random() < 0.7 else rng.choice(names)
            rules[name] = rule(element)
        for name in ("Inlines", "Blocks"):
            if rng.random() < 0.3:
                rules[name] = rule(lists)
        if rng.random() < 0.2:
            rules["Meta"] = rule(["keep", "add", "empty"])
        if rng.random() < 0.2:
            rules["Pandoc"] = rule(["keep", "prepend", "clear"])
        return {"traverse": traverse, "rules": rules}

    return {
        "filters": [one() for _ in range(rng.choice([1, 1, 1, 2]))],
        "log": rng.random() < 0.8,
    }


def load_schema() -> dict:
    return json.loads((ROOT / "schema/pandoc-ast.json").read_text())


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    import sys

    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    schema = load_schema()
    rng = random.Random(seed)
    print(dumps(Gen(rng, schema).document()))
    print(dumps(spec(rng, schema)), file=sys.stderr)
