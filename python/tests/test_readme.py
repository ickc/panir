"""The README's examples run as shown, and type-check.

Each ```python block runs, in order, in one namespace; in a ``>>>`` block,
each statement's output is what follows it (an error as ``Name: message``).
For pyright, the blocks are one module, which must have errors only where
a statement's output is an ``ASTTypeError``: in it, or in the statements
since the one before with an output (pyright may see it earlier: a filter
function's wrong return at its decorator), and one in each such span.
"""

import contextlib
import io
import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
SRC = ROOT / "src"

# (the installed wheel's tests are run without the README beside them)
pytestmark = pytest.mark.skipif(not README.exists(), reason="needs python/README.md")


def blocks() -> list[str]:
    return re.findall(r"```python\n(.*?)```", README.read_text(), re.S)


def statements(block: str) -> list[tuple[str, list[str]]]:
    """A block's statements, and what each shows (none outside ``>>>``)."""
    if not block.startswith(">>>"):
        return [(block, [])]
    out: list[tuple[str, list[str]]] = []
    for line in block.splitlines():
        if line.startswith(">>> "):
            out.append((line[4:] + "\n", []))
        elif line.startswith("... "):
            out[-1] = (out[-1][0] + line[4:] + "\n", out[-1][1])
        elif line:
            out[-1][1].append(line)
    return out


def runnable(code: str) -> bool:
    return "import libpandoc" not in code  # (not a dependency)


def test_readme_examples_run():
    namespace: dict = {"__name__": "readme"}
    for block in blocks():
        for code, shown in statements(block):
            if not runnable(code):
                continue
            if shown and ":" in shown[0] and shown[0].split(":")[0].endswith("Error"):
                name, message = shown[0].split(": ", 1)
                with pytest.raises(Exception) as e:
                    exec(compile(code, "<README>", "single"), namespace)
                assert (type(e.value).__name__, str(e.value)) == (name, message), code
                continue
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                exec(compile(code, "<README>", "single" if shown else "exec"), namespace)
            assert out.getvalue().splitlines() == shown, code


@pytest.mark.skipif(shutil.which("pyright") is None, reason="needs pyright")
def test_readme_examples_type_check(tmp_path):
    lines: list[str] = ["from panir import *  # noqa"]
    spans: list[range] = []  # (0-based lines)
    for block in blocks():
        start = len(lines)
        for code, shown in statements(block):
            lines += textwrap.dedent(code).splitlines()
            if shown:
                if shown[0].startswith("ASTTypeError:"):
                    spans.append(range(start, len(lines)))
                start = len(lines)
    (tmp_path / "readme.py").write_text("\n".join(lines) + "\n")
    (tmp_path / "pyrightconfig.json").write_text(
        json.dumps(
            {
                "extraPaths": [str(SRC)],
                "pythonVersion": "3.10",
                "reportMissingImports": False,
                "reportWildcardImportFromLibrary": False,
            }
        )
    )
    out = subprocess.run(
        ["pyright", "--outputjson", "-p", str(tmp_path / "pyrightconfig.json")],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    ).stdout
    errors = [
        (d["range"]["start"]["line"], d["message"].splitlines()[0])
        for d in json.loads(out)["generalDiagnostics"]
        if d["severity"] == "error"
    ]
    for line, message in errors:
        assert any(line in span for span in spans), (line, lines[line], message)
    for span in spans:
        assert any(line in span for line, _ in errors), ("no error", lines[span.start])
