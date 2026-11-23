"""Every function in the app must describe itself in the house style (see README, "Comment style")."""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1] / "stjparts"
NEEDED = ("Description:", "Parameters:", "Returns:")


def comment_block_above(lines, line_no):
    """Return the run of # comment lines directly above line `line_no` (1-based)."""
    block = []
    i = line_no - 2
    while i >= 0 and lines[i].strip().startswith("#"):
        block.insert(0, lines[i].strip())
        i -= 1
    return "\n".join(block)


def test_python_functions_have_descriptions_above_them():
    missing = []
    for path in ROOT.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        lines = text.split("\n")
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                top = min([node.lineno] + [d.lineno for d in node.decorator_list])
                block = comment_block_above(lines, top)
                if not all(word in block for word in NEEDED):
                    missing.append(f"{path.name}:{node.lineno} {node.name}")
    assert not missing, "Add a # Description/Parameters/Returns block above:\n" + "\n".join(missing)


def test_javascript_functions_have_descriptions():
    missing = []
    for path in (ROOT / "web").glob("*.js"):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"^\s*(?:async\s+)?function\s+(\w+)|^\s*screens\.(\w+)\s*=", text, re.M):
            before = text[: m.start()].rstrip()
            if not before.endswith("*/") or "Description:" not in before[before.rfind("/**"):]:
                missing.append(f"{path.name} {m.group(1) or m.group(2)}")
    assert not missing, "Add a /** Description/Parameters/Returns */ block above:\n" + "\n".join(missing)
