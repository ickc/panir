"""The fuzzer's Python driver (see fuzz/README.md): tasks on stdin, two lines
each (a filter spec or ``null``, then a document as pandoc wrote it); for
each, one line on stdout: the document after the spec's filters, as JSON, or
``!`` and the error.
"""

from __future__ import annotations

import io
import json
import sys

import panir
from panir import Block, BlockQuote, Emph, Filter, Inline, Pandoc, Para, Str

TEXT = {"Str", "Code", "Math", "RawInline", "CodeBlock", "RawBlock"}
UNWRAP = {
    *("Emph", "Underline", "Strong", "Strikeout", "Superscript", "Subscript"),
    *("SmallCaps", "Quoted", "Cite", "Link", "Image", "Span"),
    *("BlockQuote", "Div", "Figure"),
}


def build(spec: dict) -> list[Filter]:
    log: list[str] = []

    def called(rule: dict, entry: str) -> bool:
        log.append(entry)
        return "mod" not in rule or len(log) % rule["mod"] == rule["eq"]

    def element(rule: dict):
        act = rule["do"]

        def fn(el, ctx):
            me = len(log) + 1
            tag = type(el).__name__
            if not called(rule, f"{tag}:{el.text}" if tag in TEXT else tag):
                return None
            inline = isinstance(el, Inline)

            def mark():
                if tag in TEXT:
                    el.text += f"#{me}"

            if act == "keep":
                return None
            if act == "delete":
                return []
            if act == "dup":
                return [el, el]
            if act == "replace":
                return Str("R") if inline else Para(Str("R"))
            if act in ("wrap", "wrapskip"):
                if act == "wrapskip":
                    ctx.skip_children()
                return Emph(el) if inline else BlockQuote(el)
            if act == "unwrap":
                return list(el.content) if tag in UNWRAP else None
            if act == "mark":
                return mark()
            if act == "markret":
                mark()
                return el
            if act == "skip":
                return ctx.skip_children()
            if act == "markskip":
                ctx.skip_children()
                return mark()
            raise ValueError(act)

        return fn

    def lists(rule: dict, name: str):
        act = rule["do"]

        def fn(xs, ctx):
            if not called(rule, f"{name}{len(xs)}"):
                return None
            if act == "keep":
                return None
            if act == "reverse":
                return xs[::-1]
            if act == "dropfirst":
                del xs[:1]
                return None
            if act == "append":
                return [*xs, Str("A") if name == "Inlines" else Para(Str("A"))]
            if act == "empty":
                return []
            if act == "skip":
                return ctx.skip_children()
            raise ValueError(act)

        return fn

    def meta(rule: dict):
        act = rule["do"]

        def fn(m):
            if not called(rule, "Meta"):
                return None
            if act == "add":
                m["fuzz"] = True
            elif act == "empty":
                return {}
            return None

        return fn

    def document(rule: dict):
        act = rule["do"]

        def fn(doc):
            if not called(rule, "Pandoc"):
                return
            if act == "prepend":
                doc.blocks.insert(0, Para(Str("P")))
            elif act == "clear":
                doc.blocks = []

        return fn

    filters = []
    for f in spec["filters"]:
        flt = Filter(traverse=f["traverse"])
        for name, rule in f["rules"].items():
            if name == "Inlines":
                flt.on_inlines(lists(rule, name))
            elif name == "Blocks":
                flt.on_blocks(lists(rule, name))
            elif name == "Meta":
                flt.on_meta(meta(rule))
            elif name == "Pandoc":
                flt.on(Pandoc)(document(rule))
            else:
                flt.on(getattr(panir, name))(element(rule))
        filters.append(flt)
    if spec["log"]:
        last = Filter()

        @last.on(Pandoc)
        def _(doc):
            doc.blocks.append(Para(Str(" ".join(log))))

        filters.append(last)
    return filters


def main() -> None:
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", newline="\n")
    stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n")
    while True:
        spec_line = stdin.readline()
        if not spec_line:
            break
        doc_line = stdin.readline()
        try:
            spec = json.loads(spec_line)
            doc = panir.loads(doc_line)
            if spec is not None:
                doc = panir.run(doc, build(spec))
            out = panir.dumps(doc)
            if "\n" in out:
                raise ValueError("a newline in the JSON written")
        except RecursionError:
            out = "!RecursionError"
        except Exception as e:
            out = "!" + json.dumps(f"{type(e).__name__}: {e}")
        stdout.write(out + "\n")
        stdout.flush()


if __name__ == "__main__":
    assert Block  # the filter corpus's names
    main()
