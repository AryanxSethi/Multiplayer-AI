"""Every third-party import has to be in a requirements file.

This is here because of a specific way that went wrong. ``pydantic`` is imported
by ``network_a2a/envelope.py`` and two test modules, and it was listed in
``environment.yml`` and in no requirements file. So the suite passed on every
machine set up from the conda environment and failed on import the moment it ran
anywhere else -- which is what CI is, the first place it ran without one.

Nothing in the test suite noticed, because nobody had ever run the suite from the
requirements alone before. This is that check.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Packages this repository provides itself. Anything imported from these
# resolves without being installed.
LOCAL = {"desktop_app", "network_a2a", "src", "tests", "scripts"}

# Import name -> distribution name, for the cases where they differ. Every one
# currently imported matches its distribution exactly, so this is empty on
# purpose: a mismatch is a one-line addition here rather than a guard that
# cannot be satisfied without disabling it.
DISTRIBUTION_ALIASES: dict[str, str] = {}

SCANNED = ("tests", "desktop_app", "network_a2a", "src", "scripts")


def _normalise(name: str) -> str:
    """Compare PySide6, py_side6 and pyside6 as one name."""
    return re.sub(r"[-_.]+", "", name).lower()


def _installed_from_requirements() -> set[str]:
    """Every distribution the requirements files name, includes followed."""
    found: set[str] = set()
    seen: set[Path] = set()

    def read(path: Path) -> None:
        path = path.resolve()
        if path in seen or not path.is_file():
            return
        seen.add(path)
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            # "-r other.txt" or "--requirement other.txt"
            include = re.fullmatch(r"-{1,2}r(?:equirement)?\s+(\S+)", line)
            if include:
                read(path.parent / include.group(1))
                continue
            found.add(_normalise(re.split(r"[<>=!~\[]", line)[0]))

    for name in ("requirements-network.txt", "requirements-desktop.txt",
                 "requirements-dev.txt"):
        read(ROOT / name)
    return found


def _third_party_imports() -> dict[str, list[str]]:
    """Every top-level third-party module imported, with where it came from."""
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    imports: dict[str, list[str]] = {}
    for folder in SCANNED:
        for path in sorted((ROOT / folder).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
                continue
            names: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names |= {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom):
                    # level 0 is absolute; a relative import never needs installing
                    if node.level == 0 and node.module:
                        names.add(node.module.split(".")[0])
            for name in names:
                if name in stdlib or name in LOCAL:
                    continue
                imports.setdefault(name, []).append(
                    str(path.relative_to(ROOT)).replace("\\", "/"))
    return imports


def test_every_third_party_import_is_in_a_requirements_file() -> None:
    """The suite must be installable from the requirements alone.

    Installing ``requirements-desktop.txt`` and running it has to work, because
    that is what CI does and what a reader following the setup instructions
    would do.
    """
    declared = _installed_from_requirements()
    missing: dict[str, list[str]] = {}
    for name, sources in sorted(_third_party_imports().items()):
        wanted = _normalise(DISTRIBUTION_ALIASES.get(name, name))
        if wanted in declared:
            continue
        missing[name] = sorted(set(sources))

    assert not missing, (
        "imported but in no requirements file, so the suite cannot be installed "
        "and run from them:\n"
        + "\n".join(f"  {name}: {', '.join(where)}" for name, where in missing.items())
    )