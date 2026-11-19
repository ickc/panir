#!/usr/bin/env python3
"""Regenerate corpus/yaml-blocks.jsonl: how a code block's text splits into
a YAML header and data (each language's ``yaml_block``), with pandoc's own
verdict on the same text read as a markdown document.

    python3 scripts/yaml-blocks-corpus.py   # needs pandoc (pins.env's) on PATH

The split takes a YAML metadata block, by pandoc's rule, only where it
starts the text. Each case below gives the text, the header the split
gives (``None``: none) and the data, and what pandoc makes of the text
(``"pandoc"``): ``"block"`` if it reads a metadata block starting the
text, ``"none"`` if not, ``"error"`` if it fails. Where the two differ,
the case's ``"note"`` says why. This script asks pandoc, and fails if
pandoc's verdict is not the one written here:

- a header: pandoc reads the header alone (``---``, the header, ``---``)
  as metadata and nothing else, and reads the text as the data's blocks,
  with the header's metadata;
- no header: the would-be header's key, always ``opt`` (and no other
  key's), is not in the text's metadata.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "corpus" / "yaml-blocks.jsonl"

NOT_A_MAP = (
    "the YAML is not a map: pandoc reads no metadata block, which the split can't "
    "tell without reading YAML; yaml_filter sees it in what the reader returns "
    "(blocks, no metadata) and takes the whole text as data"
)

# name, text, header, data, pandoc's verdict, note
CASES: list[tuple[str, str, str | None, str, str, str]] = [
    ("block", "---\nopt: 1\n---\nx\n", "opt: 1\n", "x\n", "block", ""),
    ("dots", "---\nopt: 1\n...\nx\n", "opt: 1\n", "x\n", "block", ""),
    ("blanks after the lines", "--- \t\nopt: 1\n... \t\nx\n", "opt: 1\n", "x\n", "block", ""),
    ("empty", "---\n---\nx\n", "", "x\n", "block", ""),
    ("empty, dots", "---\n...\nx\n", "", "x\n", "block", ""),
    ("empty, the whole text", "---\n---", "", "", "block", ""),
    ("closed at the end", "---\nopt: 1\n---", "opt: 1\n", "", "block", ""),
    ("dots at the end", "---\nopt: 1\n...", "opt: 1\n", "", "block", ""),
    ("data without a newline", "---\nopt: 1\n---\nx", "opt: 1\n", "x", "block", ""),
    ("a blank line before the data", "---\nopt: 1\n---\n\nx\n", "opt: 1\n", "\nx\n", "block", ""),
    (
        "a blank line inside",
        "---\nopt: 1\n\nb: 2\n---\nx\n",
        "opt: 1\n\nb: 2\n",
        "x\n",
        "block",
        "",
    ),
    ("blanks inside", "---\nopt: 1\n \t\n---\nx\n", "opt: 1\n \t\n", "x\n", "block", ""),
    ("a tab before a key", "---\n\topt: 1\n---\nx\n", "\topt: 1\n", "x\n", "block", ""),
    (
        "dashes in a literal",
        "---\nopt: |\n  a\n  ---\n---\nx\n",
        "opt: |\n  a\n  ---\n",
        "x\n",
        "block",
        "",
    ),
    ("a comment only", "---\n# a comment\n---\nx\n", "# a comment\n", "x\n", "block", ""),
    ("the first closing line", "---\nopt: 1\n...\n...\nx\n", "opt: 1\n", "...\nx\n", "block", ""),
    (
        "a header after it is data",
        "---\nopt: 1\n---\n---\nlater: 2\n---\n",
        "opt: 1\n",
        "---\nlater: 2\n---\n",
        "block",
        "",
    ),
    (
        "a later block is data",
        "---\nopt: 1\n---\nx\n\n---\nlater: 2\n---\n",
        "opt: 1\n",
        "x\n\n---\nlater: 2\n---\n",
        "block",
        "pandoc reads the later block as metadata too; the split never does",
    ),
    (
        "four hyphens open nothing",
        "----\nopt: 1\n---\nx\n",
        None,
        "----\nopt: 1\n---\nx\n",
        "none",
        "",
    ),
    (
        "four hyphens close nothing",
        "---\nopt: 1\n----\nx\n",
        None,
        "---\nopt: 1\n----\nx\n",
        "none",
        "",
    ),
    (
        "four dots close nothing",
        "---\nopt: 1\n....\nx\n",
        None,
        "---\nopt: 1\n....\nx\n",
        "none",
        "",
    ),
    (
        "an indented opening line",
        " ---\nopt: 1\n---\nx\n",
        None,
        " ---\nopt: 1\n---\nx\n",
        "none",
        "",
    ),
    (
        "an indented closing line",
        "---\nopt: 1\n ---\nx\n",
        None,
        "---\nopt: 1\n ---\nx\n",
        "none",
        "",
    ),
    ("indented dots", "---\nopt: 1\n ...\nx\n", None, "---\nopt: 1\n ...\nx\n", "none", ""),
    (
        "a blank line after the opening",
        "---\n\nopt: 1\n---\nx\n",
        None,
        "---\n\nopt: 1\n---\nx\n",
        "none",
        "",
    ),
    (
        "blanks after the opening",
        "---\n \t\nopt: 1\n---\nx\n",
        None,
        "---\n \t\nopt: 1\n---\nx\n",
        "none",
        "",
    ),
    ("no closing line", "---\nopt: 1\nx\n", None, "---\nopt: 1\nx\n", "none", ""),
    ("the opening line only", "---", None, "---", "none", ""),
    ("the opening line and a newline", "---\n", None, "---\n", "none", ""),
    (
        "text after the opening",
        "---x\nopt: 1\n---\nx\n",
        None,
        "---x\nopt: 1\n---\nx\n",
        "none",
        "",
    ),
    (
        "a comment after the opening",
        "--- # c\nopt: 1\n---\nx\n",
        None,
        "--- # c\nopt: 1\n---\nx\n",
        "none",
        "",
    ),
    (
        "text after the closing",
        "---\nopt: 1\n---x\nx\n",
        None,
        "---\nopt: 1\n---x\nx\n",
        "none",
        "",
    ),
    (
        "a no-break space after the opening",
        "--- \nopt: 1\n---\nx\n",
        None,
        "--- \nopt: 1\n---\nx\n",
        "none",
        "",
    ),
    (
        "a vertical tab after the opening",
        "---\v\nopt: 1\n---\nx\n",
        None,
        "---\v\nopt: 1\n---\nx\n",
        "none",
        "",
    ),
    (
        "a no-break space after the closing",
        "---\nopt: 1\n--- \nx\n",
        None,
        "---\nopt: 1\n--- \nx\n",
        "none",
        "",
    ),
    ("data first", "a,b\n---\nopt: 1\n---\n", None, "a,b\n---\nopt: 1\n---\n", "none", ""),
    (
        "a blank line first",
        "\n---\nopt: 1\n---\nx\n",
        None,
        "\n---\nopt: 1\n---\nx\n",
        "block",
        "pandoc reads a metadata block after a blank line anywhere; the split takes only "
        "one that starts the text",
    ),
    ("a scalar", "---\nopt\n---\nx\n", "opt\n", "x\n", "none", NOT_A_MAP),
    ("a list", "---\n- opt\n---\nx\n", "- opt\n", "x\n", "none", NOT_A_MAP),
    ("an empty string", "---\n''\n---\nx\n", "''\n", "x\n", "none", NOT_A_MAP),
    (
        "YAML pandoc can't read",
        "---\nopt: [1,\n---\nx\n",
        "opt: [1,\n",
        "x\n",
        "error",
        "pandoc fails on the document; the reader yaml_filter is given fails too",
    ),
    (
        "four hyphens inside",
        "---\nopt: 1\n----\n---\nx\n",
        "opt: 1\n----\n",
        "x\n",
        "error",
        "pandoc fails on the document; the reader yaml_filter is given fails too",
    ),
]


def pandoc(text: str) -> dict | None:
    """pandoc's document for markdown text, None if it fails."""
    p = subprocess.run(
        ["pandoc", "-f", "markdown", "-t", "json"], input=text.encode(), capture_output=True
    )
    return json.loads(p.stdout) if p.returncode == 0 else None


def verdict(text: str, header: str | None, data: str) -> str:
    doc = pandoc(text)
    if doc is None:
        return "error"
    if header is None:
        return "block" if "opt" in doc["meta"] else "none"
    alone = pandoc(f"---\n{header}---\n")
    rest = pandoc(data)
    if alone is None or rest is None or alone["blocks"]:
        return "none"
    same_meta = all(doc["meta"].get(k) == v for k, v in alone["meta"].items())
    return "block" if same_meta and doc["blocks"] == rest["blocks"] else "none"


def main() -> None:
    lines = []
    wrong = []
    for name, text, header, data, expected, note in CASES:
        assert data == text if header is None else text.endswith(data), name
        got = verdict(text, header, data)
        if got != expected:
            wrong.append(f"{name}: pandoc's verdict is {got!r}, not {expected!r}")
        case = {"name": name, "text": text, "header": header, "data": data, "pandoc": got}
        if note:
            case["note"] = note
        lines.append(json.dumps(case, ensure_ascii=False))
    if wrong:
        sys.exit("\n".join(wrong))
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
