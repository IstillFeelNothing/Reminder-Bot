"""Архітектурні правила, яких не перевіряє mypy.

- domain/ залежить лише від стандартної бібліотеки та самого domain
  (жодних aiogram / aiosqlite).
"""

import ast
import sys
from pathlib import Path

import pytest

DOMAIN_DIR = Path(__file__).resolve().parents[1] / "domain"


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    return modules


@pytest.mark.parametrize(
    "path",
    [pytest.param(p, id=f"01-{p.name}") for p in sorted(DOMAIN_DIR.glob("*.py"))],
)
def test_domain_depends_only_on_stdlib(path: Path) -> None:
    foreign = _imported_top_level_modules(path) - set(sys.stdlib_module_names) - {"domain"}

    assert foreign == set()
