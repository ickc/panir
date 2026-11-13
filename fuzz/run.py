#!/usr/bin/env python3
"""Fuzz every panir language against pandoc (see fuzz/README.md).

    python3 fuzz/run.py --count 2000              # documents and filters
    python3 fuzz/run.py --time 3600 --jobs 28     # for an hour
    python3 fuzz/run.py --replay fuzz/failures/python-filter-12-7.json

A task is a random document, as pandoc wrote it, and maybe a random filter
spec. pandoc is the oracle: a language's output must read back, by pandoc, as
the document pandoc itself made (of the document alone, or with the spec run
by its Lua filters, fuzz/filter.lua). Failures are shrunk and written to
fuzz/failures/.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen  # noqa: E402

ROOT = gen.ROOT
FUZZ = ROOT / "fuzz"
PANDOC = os.environ.get("PANDOC", "pandoc")
ORACLE_TIMEOUT = 20
# the oracle's output beyond this isn't worth a language's time (a filter
# that doubles nodes at every level)
MAX_EXPECTED = 2_000_000


# What a language can't hold, and says so: counted, not a failure.
LIMITS = {
    # a JavaScript number holds integers exactly only up to 2^53
    "ts": ["beyond the integers JavaScript's numbers hold exactly"],
}


def language_commands() -> dict[str, list[str]]:
    node = os.environ.get("NODE", "node")
    return {
        "python": [os.environ.get("PYTHON", sys.executable), str(FUZZ / "driver.py")],
        "ts": [node, str(FUZZ / "driver.ts")],
        "rust": [str(FUZZ / "rust/target/release/panir-fuzz")],
        "julia": [
            os.environ.get("JULIA", "julia"),
            "--startup-file=no",
            f"--project={ROOT / 'julia'}",
            str(FUZZ / "driver.jl"),
        ],
    }


def pandoc(doc: str, spec: str | None = None) -> tuple[bool, str]:
    """pandoc's own writing of a document, after the spec's Lua filters if any."""
    cmd = [PANDOC, "-f", "json", "-t", "json"]
    env = None
    tmp = None
    try:
        if spec is not None:
            tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
            tmp.write(spec)
            tmp.close()
            cmd += ["-L", str(FUZZ / "filter.lua")]
            env = {**os.environ, "PANIR_FUZZ_SPEC": tmp.name}
        try:
            p = subprocess.run(
                cmd, input=doc.encode(), capture_output=True, timeout=ORACLE_TIMEOUT, env=env
            )
        except subprocess.TimeoutExpired:
            return False, "timeout"
        if p.returncode != 0:
            return False, p.stderr.decode(errors="replace").strip()
        return True, p.stdout.decode().rstrip("\n")
    finally:
        if tmp is not None:
            os.unlink(tmp.name)


def run_language(cmd: list[str], tasks: list[tuple[str | None, str]], timeout: float) -> list[str]:
    """A driver's answers; a task it died or hung on answers ``!!``."""
    data = "".join(f"{spec or 'null'}\n{doc}\n" for spec, doc in tasks).encode()
    try:
        p = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout)
        out = p.stdout.decode(errors="replace").split("\n")
        if out and out[-1] == "":
            out.pop()
        died = (
            ""
            if p.returncode == 0
            else f"exit {p.returncode}: {p.stderr.decode(errors='replace')[-2000:]}"
        )
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode(errors="replace").split("\n")[:-1]
        died = f"timeout after {timeout} s"
    if len(out) == len(tasks) and not died:
        return out
    if len(tasks) == 1:
        return ["!!" + json.dumps(died or "no output")]
    # the driver died: the answers before it stand, the task it died on is
    # found alone, the rest run again
    done = out[: len(tasks)]
    k = len(done)
    if k >= len(tasks):
        return done
    return (
        done
        + run_language(cmd, tasks[k : k + 1], timeout)
        + (run_language(cmd, tasks[k + 1 :], timeout) if k + 1 < len(tasks) else [])
    )


class Checker:
    def __init__(self, langs: dict[str, list[str]], jobs: int) -> None:
        self.langs, self.pool = langs, ThreadPoolExecutor(jobs)
        self.refused: dict[str, int] = {}

    def verdict(self, lang: str, expected: str, got: str) -> str | None:
        """None if pandoc reads ``got`` as the AST of ``expected``, else why not."""
        if got == expected:
            return None
        if got.startswith("!"):
            if any(limit in got for limit in LIMITS.get(lang, ())):
                self.refused[lang] = self.refused.get(lang, 0) + 1
                return None
            return got
        ok, back = pandoc(got)
        if not ok:
            return f"pandoc rejects it: {back}"
        if back != expected:
            return "pandoc reads another AST"
        return None

    def check(self, lang: str, tasks: list[dict], timeout: float = 600) -> list[str | None]:
        outs = run_language(self.langs[lang], [(t["spec"], t["doc"]) for t in tasks], timeout)
        for t, o in zip(tasks, outs):
            t.setdefault("got", {})[lang] = o
        return list(
            self.pool.map(lambda p: self.verdict(lang, p[0]["expected"], p[1]), zip(tasks, outs))
        )

    def fails(self, lang: str, spec: dict | None, doc: dict) -> bool:
        """Whether a (shrunk) task still fails: for the same kind of reason isn't required."""
        ok, d1 = pandoc(gen.dumps(doc))
        if not ok:
            return False
        s = None if spec is None else gen.dumps(spec)
        ok, expected = pandoc(d1, s) if s is not None else (True, d1)
        if not ok or len(expected) > MAX_EXPECTED:
            return False
        out = run_language(self.langs[lang], [(s, d1)], 60)[0]
        return self.verdict(lang, expected, out) is not None


# -- shrinking ---------------------------------------------------------------


def shrink(
    checker: Checker, lang: str, spec: dict | None, doc: dict, schema: dict, budget: float = 120
):
    """A smaller task that still fails: rules and filters dropped, list
    elements and map entries removed, nodes replaced by their children's."""
    deadline = time.time() + budget
    fails = lambda s, d: time.time() < deadline and checker.fails(lang, s, d)  # noqa: E731

    def shrink_spec(spec, doc):
        changed = True
        while changed and spec is not None:
            changed = False
            cands = []
            if len(spec["filters"]) > 1:
                for i in range(len(spec["filters"])):
                    cands.append(
                        {**spec, "filters": spec["filters"][:i] + spec["filters"][i + 1 :]}
                    )
            for i, f in enumerate(spec["filters"]):
                for name, rule in f["rules"].items():
                    rules = {k: v for k, v in f["rules"].items() if k != name}
                    cands.append(_with_filter(spec, i, {**f, "rules": rules}))
                    if "mod" in rule:
                        rules = {**f["rules"], name: {"do": rule["do"]}}
                        cands.append(_with_filter(spec, i, {**f, "rules": rules}))
                    if rule["do"] != "keep":
                        rules = {**f["rules"], name: {**rule, "do": "keep"}}
                        cands.append(_with_filter(spec, i, {**f, "rules": rules}))
            if spec["log"]:
                cands.append({**spec, "log": False})
            for c in cands:
                if fails(c, doc):
                    spec, changed = c, True
                    break
        return spec

    def shrink_doc(spec, doc):
        changed = True
        while changed:
            changed = False
            for path in list(_containers(doc)):
                box = _get(doc, path)
                keys = list(range(len(box))) if isinstance(box, list) else list(box)
                for k in reversed(keys):
                    cand = json.loads(json.dumps(doc))
                    cbox = _get(cand, path)
                    if isinstance(cbox, list) and not _is_list_of_nodes(cand, path, schema):
                        continue
                    del cbox[k]
                    if fails(spec, cand):
                        doc, changed = cand, True
                        break
                if changed:
                    break
        return doc

    spec = shrink_spec(spec, doc)
    doc = shrink_doc(spec, doc)
    spec = shrink_spec(spec, doc)
    return spec, doc


def _with_filter(spec: dict, i: int, f: dict) -> dict:
    return {**spec, "filters": spec["filters"][:i] + [f] + spec["filters"][i + 1 :]}


def _get(doc, path):
    for seg in path:
        doc = doc[seg]
    return doc


def _containers(doc):
    """Paths of the lists and metadata maps removing from which keeps a document valid."""
    w = _TypedWalk(_SCHEMA)
    yield from w.containers(doc)


def _is_list_of_nodes(doc, path, schema) -> bool:
    return True


class _TypedWalk:
    """The JSON paths of every list (and map) value in a document, by its schema."""

    def __init__(self, schema: dict) -> None:
        self.types = {t["name"]: t for t in schema["types"]}

    def containers(self, doc):
        yield from self.walk(doc["meta"], self.types["Meta"]["type"], ["meta"])
        yield from self.walk(doc["blocks"], {"list": {"ref": "Block"}}, ["blocks"])

    def walk(self, j, ty, path):
        if "prim" in ty:
            return
        if "list" in ty:
            for i, x in enumerate(j):
                yield from self.walk(x, ty["list"], path + [i])
            yield path
        elif "maybe" in ty:
            if j is not None:
                yield from self.walk(j, ty["maybe"], path)
        elif "map" in ty:
            for k, x in j.items():
                yield from self.walk(x, ty["map"][1], path + [k])
            yield path
        elif "tuple" in ty:
            for i, (x, t) in enumerate(zip(j, ty["tuple"])):
                yield from self.walk(x, t, path + [i])
        else:
            t = self.types[ty["ref"]]
            if t["kind"] == "alias":
                yield from self.walk(j, t["type"], path)
            elif t["kind"] == "sum":
                con = next(c for c in t["constructors"] if c["name"] == j["t"])
                if con["encoding"] == "value":
                    yield from self.walk(j["c"], con["fields"][0]["type"], path + ["c"])
                elif con["encoding"] == "array":
                    for i, f in enumerate(con["fields"]):
                        yield from self.walk(j["c"][i], f["type"], path + ["c", i])
            elif t["kind"] == "product":
                if t["encoding"] == "array":
                    for i, f in enumerate(t["fields"]):
                        yield from self.walk(j[i], f["type"], path + [i])
                elif t["encoding"] == "value":
                    yield from self.walk(j, t["fields"][0]["type"], path)
                else:
                    for f in t["fields"]:
                        yield from self.walk(j[f["key"]], f["type"], path + [f["key"]])


_SCHEMA = gen.load_schema()


# -- the loop ------------------------------------------------------------------


def make_tasks(seed: int, n: int, mode: str, pool: ThreadPoolExecutor) -> tuple[list[dict], int]:
    """Tasks pandoc accepted: document as pandoc wrote it, spec, pandoc's result."""
    raw = []
    for i in range(n):
        rng = random.Random(f"{seed}/{i}")
        g = gen.Gen(
            rng, _SCHEMA, max_depth=rng.choice([3, 4, 5, 6]), nasty=rng.choice([0, 0.1, 0.3])
        )
        doc = g.document()
        filtered = mode == "filter" or (mode == "both" and i % 2 == 1)
        raw.append(
            (i, gen.dumps(doc), gen.dumps(gen.spec(rng, _SCHEMA, doc)) if filtered else None)
        )

    def prepare(item):
        i, doc, spec = item
        ok, d1 = pandoc(doc)
        if not ok:
            return {
                "i": i,
                "oracle_error": f"pandoc rejects a generated document: {d1}",
                "doc": doc,
            }
        if spec is None:
            return {"i": i, "doc": d1, "spec": None, "expected": d1}
        ok, expected = pandoc(d1, spec)
        if not ok:
            return {"i": i, "oracle_error": expected, "doc": d1, "spec": spec}
        if len(expected) > MAX_EXPECTED:
            return {"i": i, "oracle_error": "too big"}
        return {"i": i, "doc": d1, "spec": spec, "expected": expected}

    tasks = list(pool.map(prepare, raw))
    good = [t for t in tasks if "oracle_error" not in t]
    for t in tasks:
        if "oracle_error" in t and t["oracle_error"] not in ("too big", "timeout"):
            print(f"  oracle: seed {seed} task {t['i']}: {t['oracle_error'][:300]}", flush=True)
    return good, len(tasks) - len(good)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--seed", type=int, default=0, help="first batch's seed")
    ap.add_argument("--count", type=int, default=1000, help="tasks to run (ignored with --time)")
    ap.add_argument("--time", type=float, help="run for this many seconds")
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--langs", default="python,ts,rust,julia")
    ap.add_argument("--mode", choices=["data", "filter", "both"], default="both")
    ap.add_argument("--no-shrink", action="store_true")
    ap.add_argument(
        "--max-failures", type=int, default=20, help="stop after this many, per language"
    )
    ap.add_argument("--out", type=Path, default=FUZZ / "failures")
    ap.add_argument("--replay", type=Path, help="run a saved failure again")
    args = ap.parse_args()

    if shutil.which(PANDOC) is None:
        sys.exit(f"no pandoc ({PANDOC}); set $PANDOC")
    version = subprocess.run([PANDOC, "--version"], capture_output=True, text=True).stdout.split(
        "\n"
    )[0]
    cmds = language_commands()
    langs = {name: cmds[name] for name in args.langs.split(",") if name}
    checker = Checker(langs, args.jobs)

    if args.replay:
        case = json.loads(args.replay.read_text())
        spec = None if case["spec"] is None else gen.dumps(case["spec"])
        d1 = gen.dumps(case["doc"])
        ok, d1 = pandoc(d1)
        ok, expected = pandoc(d1, spec) if spec is not None else (True, d1)
        bad = 0
        for lang in langs:
            out = run_language(langs[lang], [(spec, d1)], 60)[0]
            why = checker.verdict(lang, expected, out)
            print(f"{lang}: {'ok' if why is None else why}")
            if why is not None:
                bad += 1
                print(f"  expected {expected[:2000]}\n  got      {out[:2000]}")
        sys.exit(1 if bad else 0)

    print(f"{version}; languages: {', '.join(langs)}; mode {args.mode}", flush=True)
    start = time.time()
    total = skipped = 0
    failures: dict[str, int] = dict.fromkeys(langs, 0)
    seed = args.seed
    while (time.time() - start < args.time) if args.time else (total + skipped < args.count):
        n = args.batch if args.time else min(args.batch, args.count - total - skipped)
        tasks, bad = make_tasks(seed, n, args.mode, checker.pool)
        skipped += bad
        total += len(tasks)
        results = list(
            ThreadPoolExecutor(len(langs)).map(checker.check, langs, [tasks] * len(langs))
        )
        for lang, verdicts in zip(langs, results):
            for t, why in zip(tasks, verdicts):
                if why is None or failures[lang] >= args.max_failures:
                    continue
                failures[lang] += 1
                spec = None if t["spec"] is None else json.loads(t["spec"])
                doc = json.loads(t["doc"])
                kind = "data" if spec is None else "filter"
                print(f"FAIL {lang} {kind} seed {seed} task {t['i']}: {why[:300]}", flush=True)
                if not args.no_shrink:
                    spec, doc = shrink(checker, lang, spec, doc, _SCHEMA)
                args.out.mkdir(parents=True, exist_ok=True)
                path = args.out / f"{lang}-{kind}-{seed}-{t['i']}.json"
                path.write_text(
                    json.dumps(
                        {"lang": lang, "why": why, "spec": spec, "doc": doc},
                        ensure_ascii=False,
                        indent=1,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                print(f"  -> {path}", flush=True)
        seed += 1
        elapsed = time.time() - start
        print(
            f"seed {seed - 1}: {total} tasks ({skipped} skipped), {elapsed:.0f} s, failures: "
            + ", ".join(f"{k} {v}" for k, v in failures.items())
            + "".join(f"; {k} refused {v}" for k, v in checker.refused.items()),
            flush=True,
        )
        if all(v >= args.max_failures for v in failures.values()):
            break
    sys.exit(1 if any(failures.values()) else 0)


if __name__ == "__main__":
    main()
