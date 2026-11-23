"""Every function in the app must describe itself in the house style (see README, "Comment style")."""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1] / "stjparts"
NEEDED = ("Description:", "Parameters:", "Returns:")


def test_python_functions_have_descriptions():
    missing = []
    for path in ROOT.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node) or ""
                if not all(word in doc for word in NEEDED):
                    missing.append(f"{path.name}:{node.lineno} {node.name}")
    assert not missing, "Add a Description/Parameters/Returns docstring to:\n" + "\n".join(missing)


def test_javascript_functions_have_descriptions():
    missing = []
    for path in (ROOT / "web").glob("*.js"):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"^\s*(?:async\s+)?function\s+(\w+)|^\s*screens\.(\w+)\s*=", text, re.M):
            before = text[: m.start()].rstrip()
            if not before.endswith("*/") or "Description:" not in before[before.rfind("/**"):]:
                missing.append(f"{path.name} {m.group(1) or m.group(2)}")
    assert not missing, "Add a /** Description/Parameters/Returns */ block above:\n" + "\n".join(missing)
