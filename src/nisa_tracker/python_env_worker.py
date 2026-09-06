"""Restricted Python executor for the AI assistant tool `python_eval`.

Runs read-only calculations / forecasts inside RestrictedPython in a fresh
subprocess so the model cannot hang the web server and cannot touch the machine.

Protocol:
  in:  one JSON object on stdin  {"code": str, "data": dict}
  out: one JSON object on stdout {"ok": bool, "result": str?, "stdout": str?, "error": str?}
"""

import ast
import json
import math
import statistics
import sys

from RestrictedPython import (
    compile_restricted,
    limited_builtins,
    safe_builtins,
    utility_builtins,
)
from RestrictedPython.Eval import default_guarded_getitem, default_guarded_getiter
from RestrictedPython.Guards import (
    guarded_iter_unpack_sequence,
    guarded_unpack_sequence,
    safer_getattr,
)
from RestrictedPython.PrintCollector import PrintCollector

MAX_CODE_CHARS = 8000


class SandboxError(Exception):
    """Rejected code or request."""


def builtins() -> dict:
    allowed = {
        "list": list,
        "dict": dict,
        "sum": sum,
        "min": min,
        "max": max,
        "enumerate": enumerate,
        "reversed": reversed,
        "format": format,
        "any": any,
        "all": all,
    }
    return {**safe_builtins, **limited_builtins, **utility_builtins, **allowed}


def sandbox_globals(data: dict) -> dict:
    globals_dict = {
        "__builtins__": builtins(),
        "_getattr_": safer_getattr,
        "_getitem_": default_guarded_getitem,
        "_getiter_": default_guarded_getiter,
        "_unpack_sequence_": guarded_unpack_sequence,
        "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
        "_print_": PrintCollector,
        "math": math,
        "statistics": statistics,
    }
    globals_dict.update(data)
    return globals_dict


def lint(code: str) -> None:
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise SandboxError(
                "`import` is not allowed; `math` and `statistics` are already available"
            )
        if isinstance(node, ast.While):
            raise SandboxError("`while` loops are not allowed; use a `for` loop")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise SandboxError(f"access to the dunder attribute {node.attr!r} is not allowed")


def run(code: str, data: dict) -> dict:
    if len(code) > MAX_CODE_CHARS:
        raise SandboxError(f"code is too long: {len(code)} > {MAX_CODE_CHARS} characters")
    lint(code)
    bytecode = compile_restricted(code, "<agent-python>", "exec")
    namespace = sandbox_globals(data)
    exec(bytecode, namespace)  # noqa: S102 -- restricted by RestrictedPython
    out = {"ok": True}
    value = namespace.get("result")
    if value is not None:
        out["result"] = json.dumps(value, default=str)
    collector = namespace.get("_print")
    if collector is not None:
        out["stdout"] = collector()
    return out


def main() -> None:
    request = json.load(sys.stdin)
    try:
        out = run(request.get("code") or "", request.get("data") or {})
    except SandboxError as exc:
        out = {"ok": False, "error": str(exc)}
    except SyntaxError as exc:
        out = {"ok": False, "error": f"SyntaxError: {exc}"}
    except Exception as exc:  # noqa: BLE001 -- any sandbox runtime error is reported to the model
        out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()