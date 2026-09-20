"""Every module must survive being imported with evaluated annotations.

Annotations are evaluated at class/function definition time (no
`from __future__ import annotations` in this codebase), so a malformed one is
not a style problem -- it is an ImportError. `GameState.__init__` carried
`game_dict: [str, str or int] | None`, which builds an actual list and then
asks for `list | None`; on Python 3.13 that raises

    TypeError: unsupported operand type(s) for |: 'list' and 'NoneType'

before the module body finishes, and every test that imports the Controller
died during collection rather than in a test.
"""

import ast
import importlib
import os
import unittest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SKIP_DIRS = {
    ".git", ".nodeterm", "__pycache__", "runtime", "Accounts", "data",
    "images", "assets", "Buttons", "tests", "venv", ".venv",
}


def _python_files():
    for dirpath, dirnames, filenames in os.walk(_REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


class AnnotationSyntaxTest(unittest.TestCase):
    @staticmethod
    def _is_display(node) -> bool:
        return isinstance(node, (ast.List, ast.Set, ast.Dict))

    def test_no_annotation_is_a_bare_literal_display(self):
        """`x: [str, int]` builds a list; `| None` on one is an ImportError.

        Only the top level of an annotation is checked, and the operands of a
        `|` union: `Callable[[], BotState]` legitimately contains a list
        display inside its subscript.
        """
        offenders = []
        for path in _python_files():
            with open(path, "r", encoding="utf-8") as handle:
                try:
                    tree = ast.parse(handle.read(), filename=path)
                except SyntaxError:
                    continue
            for node in ast.walk(tree):
                annotation = None
                if isinstance(node, (ast.arg, ast.AnnAssign)):
                    annotation = node.annotation
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    annotation = node.returns
                if annotation is None:
                    continue
                candidates = [annotation]
                while candidates:
                    current = candidates.pop()
                    if self._is_display(current):
                        offenders.append(
                            f"{os.path.relpath(path, _REPO_ROOT)}:{current.lineno}"
                        )
                        continue
                    if isinstance(current, ast.BinOp) and isinstance(current.op, ast.BitOr):
                        candidates.extend((current.left, current.right))
        self.assertEqual(offenders, [], f"literal display used as a type: {offenders}")

    def test_game_state_imports_and_constructs(self):
        module = importlib.import_module("Controller.Utilities.GameState")
        self.assertEqual(module.GameState().game_dict, {})
        self.assertEqual(module.GameState({"a": 1}).game_dict, {"a": 1})


if __name__ == "__main__":
    unittest.main()
