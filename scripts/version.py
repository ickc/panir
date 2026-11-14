#!/usr/bin/env python3
"""panir's version, one for every language (see RELEASING.md).

    python3 scripts/version.py              # print it; fail if the packages disagree
    python3 scripts/version.py 0.1.0-alpha.1  # set it everywhere

The version is SemVer (``0.1.0``, ``0.1.0-alpha.1``), as npm, crates.io and
Julia write it; Python gets its PEP 440 spelling (``0.1.0a1``).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+))?")
PEP440_PRE = {"alpha": "a", "beta": "b", "rc": "rc"}


def pep440(v: str) -> str:
    m = SEMVER.fullmatch(v)
    if not m:
        sys.exit(f"not a version: {v!r} (expected e.g. 0.1.0 or 0.1.0-alpha.1)")
    major, minor, patch, pre, n = m.groups()
    return f"{major}.{minor}.{patch}" + (f"{PEP440_PRE[pre]}{n}" if pre else "")


# (file, pattern with the version as group 1, whether it holds the PEP 440 spelling)
PLACES = [
    # (python/pyproject.toml reads it from __init__.py)
    ("python/src/panir/__init__.py", r'^__version__ = "([^"]+)"', True),
    ("python/recipe/recipe.yaml", r'^  version: "([^"]+)"', True),
    ("rust/Cargo.toml", r'^version = "([^"]+)"', False),
    ("rust/Cargo.lock", r'name = "panir"\nversion = "([^"]+)"', False),
    ("ts/package.json", r'^  "version": "([^"]+)"', False),
    ("julia/Project.toml", r'^version = "([^"]+)"', False),
]


def found() -> dict[str, str]:
    out = {}
    for path, pattern, _ in PLACES:
        m = re.search(pattern, (ROOT / path).read_text(), re.M)
        if not m:
            sys.exit(f"{path}: no version found")
        out[path] = m.group(1)
    lock = json.loads((ROOT / "ts/package-lock.json").read_text())
    out["ts/package-lock.json"] = lock["version"]
    out["ts/package-lock.json (root package)"] = lock["packages"][""]["version"]
    return out


def check() -> str:
    versions = found()
    semver = versions["rust/Cargo.toml"]
    expected = {path: pep440(semver) if py else semver for path, _, py in PLACES} | {
        k: semver for k in versions if k.startswith("ts/package-lock.json")
    }
    wrong = {k: v for k, v in versions.items() if v != expected[k]}
    if wrong:
        sys.exit(
            "versions disagree:\n"
            + "\n".join(f"  {k}: {v} (expected {expected[k]})" for k, v in wrong.items())
        )
    return semver


def set_version(v: str) -> None:
    py = pep440(v)
    for path, pattern, is_py in PLACES:
        p = ROOT / path
        text = p.read_text()
        m = re.search(pattern, text, re.M)
        assert m is not None, path
        new = py if is_py else v
        p.write_text(text[: m.start(1)] + new + text[m.end(1) :])
    p = ROOT / "ts/package-lock.json"
    lock = json.loads(p.read_text())
    lock["version"] = v
    lock["packages"][""]["version"] = v
    p.write_text(json.dumps(lock, indent=2) + "\n")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        set_version(sys.argv[1])
    print(check())
