"""Layer rules from the design, enforced by reading imports and source text."""

import ast
import re
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"

DOMAIN_MAY_NOT_IMPORT = ("arde.adapters", "arde.application")
APPLICATION_MAY_NOT_IMPORT = ("arde.adapters",)

GO_ADAPTER = Path("arde") / "adapters" / "language" / "go"
# Word boundaries keep "swagger.json" legal: only the tool named swag is Go-specific.
GO_SPECIFIC_WORDS = re.compile(r"\b(goinspect|gofmt|swag|go build)\b")


def test_the_scanned_package_exists() -> None:
    assert (SOURCE_ROOT / "arde").is_dir()


def test_domain_imports_no_adapters() -> None:
    assert import_violations(SOURCE_ROOT, "domain", DOMAIN_MAY_NOT_IMPORT) == []


def test_application_imports_no_adapters() -> None:
    assert import_violations(SOURCE_ROOT, "application", APPLICATION_MAY_NOT_IMPORT) == []


def test_go_specifics_stay_in_go_adapter() -> None:
    assert go_specific_leaks(SOURCE_ROOT) == []


# The checks below prove each rule fires on a violation, so a green run above means something.


@pytest.mark.parametrize(
    "source",
    [
        "import arde.adapters.llm",
        "from arde.adapters.llm import client",
        "from arde import adapters",
        "from ..adapters import llm",
        "from .. import adapters",
        "def late():\n    import arde.adapters.tracer",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import arde.adapters",
    ],
)
def test_domain_importing_an_adapter_is_reported(tmp_path: Path, source: str) -> None:
    write(tmp_path, "arde/domain/bad.py", source)

    violations = import_violations(tmp_path, "domain", DOMAIN_MAY_NOT_IMPORT)

    assert violations == ["arde/domain/bad.py: imports arde.adapters"]


def test_a_relative_import_is_resolved_from_a_nested_package(tmp_path: Path) -> None:
    write(tmp_path, "arde/domain/nested/bad.py", "from ...adapters import llm")

    violations = import_violations(tmp_path, "domain", DOMAIN_MAY_NOT_IMPORT)

    assert violations == ["arde/domain/nested/bad.py: imports arde.adapters"]


def test_domain_importing_application_is_reported(tmp_path: Path) -> None:
    write(tmp_path, "arde/domain/bad.py", "from arde.application import pipeline")

    violations = import_violations(tmp_path, "domain", DOMAIN_MAY_NOT_IMPORT)

    assert violations == ["arde/domain/bad.py: imports arde.application"]


def test_application_importing_an_adapter_is_reported(tmp_path: Path) -> None:
    write(tmp_path, "arde/application/bad.py", "from arde.adapters.workspace import Workspace")

    violations = import_violations(tmp_path, "application", APPLICATION_MAY_NOT_IMPORT)

    assert violations == ["arde/application/bad.py: imports arde.adapters"]


def test_allowed_imports_are_accepted(tmp_path: Path) -> None:
    write(
        tmp_path,
        "arde/domain/ok.py",
        "import pydantic\nfrom arde.domain.base import Frozen\nfrom . import base\n",
    )
    write(
        tmp_path,
        "arde/application/ok.py",
        "from arde.application import ports\nfrom arde.domain import facts\n",
    )

    assert import_violations(tmp_path, "domain", DOMAIN_MAY_NOT_IMPORT) == []
    assert import_violations(tmp_path, "application", APPLICATION_MAY_NOT_IMPORT) == []


def test_a_layer_with_no_folder_or_no_files_has_no_violations(tmp_path: Path) -> None:
    (tmp_path / "arde").mkdir()
    assert import_violations(tmp_path, "domain", DOMAIN_MAY_NOT_IMPORT) == []

    (tmp_path / "arde" / "domain").mkdir()
    assert import_violations(tmp_path, "domain", DOMAIN_MAY_NOT_IMPORT) == []


@pytest.mark.parametrize("word", ["goinspect", "gofmt", "swag", "go build"])
def test_go_specific_words_outside_the_go_adapter_are_reported(tmp_path: Path, word: str) -> None:
    write(tmp_path, "arde/application/pipeline.py", f'COMMAND = "{word} ./..."\n')

    assert go_specific_leaks(tmp_path) == [f"arde/application/pipeline.py: mentions {word}"]


def test_go_specific_words_inside_the_go_adapter_are_accepted(tmp_path: Path) -> None:
    write(
        tmp_path, str(GO_ADAPTER / "swag_runner.py"), 'COMMANDS = ["gofmt", "swag", "go build"]\n'
    )

    assert go_specific_leaks(tmp_path) == []


def test_swagger_is_not_mistaken_for_swag(tmp_path: Path) -> None:
    write(tmp_path, "arde/checks/spec_valid.py", 'SPEC_FILES = ["swagger.json", "swagger.yaml"]\n')

    assert go_specific_leaks(tmp_path) == []


def import_violations(source_root: Path, layer: str, forbidden: tuple[str, ...]) -> list[str]:
    layer_dir = source_root / "arde" / layer
    return [
        f"{label(path, source_root)}: imports {package}"
        for path in sorted(layer_dir.rglob("*.py"))
        for package in forbidden
        if any(is_inside(module, package) for module in imported_modules(path, source_root))
    ]


def imported_modules(path: Path, source_root: Path) -> set[str]:
    package = path.relative_to(source_root).with_suffix("").parts[:-1]
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = absolute_module(node, package)
            modules.add(base)
            # "from arde import adapters" imports the module arde.adapters.
            modules.update(f"{base}.{alias.name}" for alias in node.names)
    return modules


def absolute_module(node: ast.ImportFrom, package: tuple[str, ...]) -> str:
    if node.level == 0:
        return node.module or ""
    anchor = package[: max(len(package) - (node.level - 1), 0)]
    return ".".join([*anchor, *([node.module] if node.module else [])])


def is_inside(module: str, package: str) -> bool:
    return module == package or module.startswith(f"{package}.")


def go_specific_leaks(source_root: Path) -> list[str]:
    return [
        f"{label(path, source_root)}: mentions {word}"
        for path in sorted((source_root / "arde").rglob("*.py"))
        if not path.relative_to(source_root).is_relative_to(GO_ADAPTER)
        for word in sorted(set(GO_SPECIFIC_WORDS.findall(path.read_text(encoding="utf-8"))))
    ]


def label(path: Path, source_root: Path) -> str:
    return path.relative_to(source_root).as_posix()


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
