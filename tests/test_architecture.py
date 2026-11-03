"""Layer rules from the design, enforced by reading imports and source text."""

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "src"

# The only files allowed to hold the plain key. Anywhere else, one failing assertion prints it.
KEY_READERS = (
    "src/arde/adapters/llm/anthropic_client.py",
    "src/arde/adapters/tracer.py",
    "tests/conftest.py",
)
KEY_VARIABLE = "ANTHROPIC_API_KEY"
ENVIRONMENT_READS = ("get", "getenv", "pop", "setdefault")

# ARDE knows nothing about any repo it documents. Whatever it learns about one comes from the
# profile, the code it reads or the wiki, so these words from the first repo it was tried on may
# not appear in the product: the Python, the Go helper and the prompts.
REPO_AGNOSTIC_FOLDERS = ("src/arde", "tools/goinspect")
TARGET_REPO_WORDS = ("catalog", "envOr", "writeError", "fxRates")
# Test data may name anything. The built helper is a binary the build leaves in the folder.
NOT_PRODUCT_FOLDERS = ("testdata", "__pycache__")
BUILT_HELPER = Path("tools/goinspect/goinspect")

DOMAIN_MAY_NOT_IMPORT = ("arde.adapters", "arde.application", "arde.cli")
APPLICATION_MAY_NOT_IMPORT = ("arde.adapters", "arde.cli")
CHECKS_MAY_NOT_IMPORT = ("arde.adapters", "arde.cli")

GO_ADAPTER = Path("arde") / "adapters" / "language" / "go"
GO_COMMANDS = ("build", "vet", "test", "list")
# Prompts live in these, so a Go idiom in one reaches the application by way of a model.
SCANNED_SUFFIXES = (".py", ".md")
# \b treats "_" as part of a word, so it would miss run_goinspect and swag_version. These
# patterns match inside identifiers. swag may not be followed by a lowercase letter, and SWAG not
# by an uppercase one, which keeps Swagger and SWAGGER legal.
GO_SPECIFIC_PATTERNS = {
    "goinspect": re.compile(r"goinspect", re.IGNORECASE),
    "gofmt": re.compile(r"gofmt", re.IGNORECASE),
    "swag": re.compile(r"[Ss]wag(?![a-z])|SWAG(?![A-Z])"),
    "go.mod": re.compile(r"go\.mod"),
    "omitempty": re.compile(r"omitempty", re.IGNORECASE),
    "oneOf": re.compile(r"oneOf"),
    **{
        f"go {command}": re.compile(rf"(?<![a-z])go[\W_]+{command}(?![a-z])", re.IGNORECASE)
        for command in GO_COMMANDS
    },
}


def test_the_scanned_package_exists() -> None:
    assert (SOURCE_ROOT / "arde").is_dir()


def test_domain_imports_no_adapters() -> None:
    assert import_violations(SOURCE_ROOT, "domain", forbidden=DOMAIN_MAY_NOT_IMPORT) == []


def test_application_imports_no_adapters() -> None:
    assert import_violations(SOURCE_ROOT, "application", forbidden=APPLICATION_MAY_NOT_IMPORT) == []


def test_checks_import_no_adapters() -> None:
    assert import_violations(SOURCE_ROOT, "checks", forbidden=CHECKS_MAY_NOT_IMPORT) == []


def test_go_specifics_stay_in_go_adapter() -> None:
    assert go_specific_leaks(SOURCE_ROOT) == []


def test_the_plain_key_is_read_only_by_the_adapter_and_conftest() -> None:
    assert key_reads(REPO_ROOT) == []


def test_product_code_names_no_target_repo() -> None:
    assert target_repo_leaks(REPO_ROOT) == []


# The checks below prove each rule fires on a violation, so a green run above means something.


@pytest.mark.parametrize(
    "source",
    [
        "plain = secret.get_secret_value()",
        'plain = os.environ["ANTHROPIC_API_KEY"]',
        'plain = os.environ.get("ANTHROPIC_API_KEY")',
        'plain = os.environ.pop("ANTHROPIC_API_KEY", "")',
        'plain = os.getenv("ANTHROPIC_API_KEY")',
    ],
)
@pytest.mark.parametrize("file", ["tests/test_new_eval.py", "src/arde/application/pipeline.py"])
def test_reading_the_plain_key_is_reported(tmp_path: Path, file: str, source: str) -> None:
    write(tmp_path, file, text=f"{source}\n")

    assert key_reads(tmp_path) == [f"{file}: reads the API key"]


@pytest.mark.parametrize("file", KEY_READERS)
def test_a_listed_file_may_read_the_plain_key(tmp_path: Path, file: str) -> None:
    write(tmp_path, file, text="plain = secret.get_secret_value()\n")

    assert key_reads(tmp_path) == []


@pytest.mark.parametrize(
    "source",
    [
        'next_action = "Set ANTHROPIC_API_KEY to a valid key"',
        'assert "ANTHROPIC_API_KEY" in next_action',
        'monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")',
    ],
)
def test_naming_the_variable_without_reading_it_is_accepted(tmp_path: Path, source: str) -> None:
    write(tmp_path, "tests/test_new.py", text=f"{source}\n")

    assert key_reads(tmp_path) == []


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
    write(tmp_path, "arde/domain/bad.py", text=source)

    violations = import_violations(tmp_path, "domain", forbidden=DOMAIN_MAY_NOT_IMPORT)

    assert violations == ["arde/domain/bad.py: imports arde.adapters"]


def test_a_relative_import_is_resolved_from_a_nested_package(tmp_path: Path) -> None:
    write(tmp_path, "arde/domain/nested/bad.py", text="from ...adapters import llm")

    violations = import_violations(tmp_path, "domain", forbidden=DOMAIN_MAY_NOT_IMPORT)

    assert violations == ["arde/domain/nested/bad.py: imports arde.adapters"]


def test_domain_importing_application_is_reported(tmp_path: Path) -> None:
    write(tmp_path, "arde/domain/bad.py", text="from arde.application import pipeline")

    violations = import_violations(tmp_path, "domain", forbidden=DOMAIN_MAY_NOT_IMPORT)

    assert violations == ["arde/domain/bad.py: imports arde.application"]


def test_application_importing_an_adapter_is_reported(tmp_path: Path) -> None:
    write(tmp_path, "arde/application/bad.py", text="from arde.adapters.workspace import Workspace")

    violations = import_violations(tmp_path, "application", forbidden=APPLICATION_MAY_NOT_IMPORT)

    assert violations == ["arde/application/bad.py: imports arde.adapters"]


def test_a_check_importing_an_adapter_is_reported(tmp_path: Path) -> None:
    write(
        tmp_path, "arde/checks/spec_valid.py", text="from arde.adapters.tracer import JsonlTracer"
    )

    violations = import_violations(tmp_path, "checks", forbidden=CHECKS_MAY_NOT_IMPORT)

    assert violations == ["arde/checks/spec_valid.py: imports arde.adapters"]


@pytest.mark.parametrize(
    ("layer", "forbidden"),
    [
        ("domain", DOMAIN_MAY_NOT_IMPORT),
        ("application", APPLICATION_MAY_NOT_IMPORT),
        ("checks", CHECKS_MAY_NOT_IMPORT),
    ],
)
def test_no_inner_layer_may_import_the_cli(
    tmp_path: Path, layer: str, forbidden: tuple[str, ...]
) -> None:
    write(tmp_path, f"arde/{layer}/bad.py", text="from arde.cli import main")

    violations = import_violations(tmp_path, layer, forbidden=forbidden)

    assert violations == [f"arde/{layer}/bad.py: imports arde.cli"]


def test_allowed_imports_are_accepted(tmp_path: Path) -> None:
    write(
        tmp_path,
        "arde/domain/ok.py",
        text="import pydantic\nfrom arde.domain.base import Frozen\nfrom . import base\n",
    )
    write(
        tmp_path,
        "arde/application/ok.py",
        text="from arde.application import ports\nfrom arde.domain import facts\n",
    )

    assert import_violations(tmp_path, "domain", forbidden=DOMAIN_MAY_NOT_IMPORT) == []
    assert import_violations(tmp_path, "application", forbidden=APPLICATION_MAY_NOT_IMPORT) == []


def test_a_layer_with_no_folder_or_no_files_has_no_violations(tmp_path: Path) -> None:
    (tmp_path / "arde").mkdir()
    assert import_violations(tmp_path, "domain", forbidden=DOMAIN_MAY_NOT_IMPORT) == []

    (tmp_path / "arde" / "domain").mkdir()
    assert import_violations(tmp_path, "domain", forbidden=DOMAIN_MAY_NOT_IMPORT) == []


@pytest.mark.parametrize(
    ("source", "name"),
    [
        ('COMMAND = "goinspect ./..."', "goinspect"),
        ('COMMAND = "gofmt -l"', "gofmt"),
        ('COMMAND = "swag init"', "swag"),
        ('COMMAND = "go build ./..."', "go build"),
        ("swag_version = 1", "swag"),
        ("def run_swag():\n    pass", "swag"),
        ("class SwagRunner:\n    pass", "swag"),
        ("SWAG_VERSION = 1", "swag"),
        ('"""Swag annotations."""', "swag"),
        ("def run_goinspect():\n    pass", "goinspect"),
        ("class GoinspectRunner:\n    pass", "goinspect"),
        ('GOINSPECT_BIN = "x"', "goinspect"),
        ("def gofmt_check():\n    pass", "gofmt"),
        ("subprocess.run(['go', 'build'])", "go build"),
        ('subprocess.run(["go","build","./..."])', "go build"),
        ('COMMAND = "go  build"', "go build"),
        ("def go_build_cmd():\n    pass", "go build"),
        ('COMMAND = "go vet ./..."', "go vet"),
        ('COMMAND = "go test ./..."', "go test"),
        ('COMMAND = "go list -json"', "go list"),
        ("subprocess.run(['go', 'vet'])", "go vet"),
        ('subprocess.run(["go","test","./..."])', "go test"),
        ("def run_go_list():\n    pass", "go list"),
    ],
)
def test_go_specific_shapes_outside_the_go_adapter_are_reported(
    tmp_path: Path, source: str, name: str
) -> None:
    write(tmp_path, "arde/application/pipeline.py", text=f"{source}\n")

    assert go_specific_leaks(tmp_path) == [f"arde/application/pipeline.py: mentions {name}"]


def test_go_specific_shapes_inside_the_go_adapter_are_accepted(tmp_path: Path) -> None:
    source = "swag_version = 1\nclass GoinspectRunner: ...\ndef gofmt_check(): ...\n"
    write(tmp_path, str(GO_ADAPTER / "runner.py"), text=source)

    assert go_specific_leaks(tmp_path) == []


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("A repo with a go.mod file.", "go.mod"),
        ("a field marked omitempty", "omitempty"),
        ("may_be_omitempty = True", "omitempty"),
        ('the tag `oneOf:"a b"`', "oneOf"),
    ],
)
@pytest.mark.parametrize(
    "file",
    [
        "arde/application/prompts/discovery.md",
        "arde/application/ports.py",
        "arde/domain/inventory.py",
        "arde/adapters/repo_links.py",
    ],
)
def test_words_of_the_language_outside_the_go_adapter_are_reported(
    tmp_path: Path, file: str, text: str, name: str
) -> None:
    write(tmp_path, file, text=f"{text}\n")

    assert go_specific_leaks(tmp_path) == [f"{file}: mentions {name}"]


def test_a_prompt_is_scanned_for_the_commands_too(tmp_path: Path) -> None:
    write(tmp_path, "arde/application/prompts/explore.md", text="Run go test ./... first.\n")

    assert go_specific_leaks(tmp_path) == ["arde/application/prompts/explore.md: mentions go test"]


def test_a_file_of_another_kind_is_not_scanned(tmp_path: Path) -> None:
    write(tmp_path, "arde/application/data.txt", text="go.mod\n")

    assert go_specific_leaks(tmp_path) == []


@pytest.mark.parametrize("file", ["guidance.md", "guidance.py"])
def test_the_language_may_name_itself_inside_the_go_adapter(tmp_path: Path, file: str) -> None:
    write(tmp_path, str(GO_ADAPTER / file), text="go.mod omitempty oneOf go test\n")

    assert go_specific_leaks(tmp_path) == []


@pytest.mark.parametrize(
    "source",
    [
        'SPEC_FILES = ["swagger.json", "swagger.yaml"]',
        "class SwaggerSpec:\n    pass",
        "SWAGGER_FILE = 'swagger.json'",
        "def validate_swagger():\n    pass",
        '"""A good build ran in Argo build mode, and we go to build later."""',
        '"""We go to test later, and the cargo list and mongo vet tools stay legal."""',
        '"""A latest build, a shortlist and a go-to tester are not commands."""',
    ],
)
def test_names_that_only_contain_the_letters_are_not_reported(tmp_path: Path, source: str) -> None:
    write(tmp_path, "arde/checks/spec_valid.py", text=f"{source}\n")

    assert go_specific_leaks(tmp_path) == []


@pytest.mark.parametrize("word", TARGET_REPO_WORDS)
@pytest.mark.parametrize(
    "file",
    [
        "src/arde/application/pipeline.py",
        "src/arde/adapters/language/go/adapter.py",
        "src/arde/prompts/discovery.md",
        "src/arde/config/models.yaml",
        "tools/goinspect/match.go",
        "tools/goinspect/go.mod",
    ],
)
def test_a_product_file_naming_the_target_repo_is_reported(
    tmp_path: Path, file: str, word: str
) -> None:
    write(tmp_path, file, text=f"a note about {word} here\n")

    assert target_repo_leaks(tmp_path) == [f"{file}: mentions {word}"]


def test_the_words_are_matched_whatever_their_case(tmp_path: Path) -> None:
    write(tmp_path, "src/arde/prompts/explore.md", text="Look at the Catalog and WRITEERROR.\n")

    assert target_repo_leaks(tmp_path) == [
        "src/arde/prompts/explore.md: mentions catalog",
        "src/arde/prompts/explore.md: mentions writeError",
    ]


@pytest.mark.parametrize(
    "file",
    [
        "tools/goinspect/match_test.go",
        "tools/goinspect/testdata/shop/go.mod",
        "src/arde/__pycache__/pipeline.cpython-312.pyc",
        "tests/domain/test_models.py",
        "docs/limitations.md",
    ],
)
def test_test_data_and_everything_outside_the_product_may_name_the_target_repo(
    tmp_path: Path, file: str
) -> None:
    write(tmp_path, file, text="catalog envOr writeError fxRates\n")

    assert target_repo_leaks(tmp_path) == []


def test_the_built_helper_is_not_read_as_text(tmp_path: Path) -> None:
    binary = tmp_path / BUILT_HELPER
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"\xcf\xfa\xed\xfe catalog \x00\xff")

    assert target_repo_leaks(tmp_path) == []


def test_a_file_that_is_not_utf8_is_still_scanned(tmp_path: Path) -> None:
    path = tmp_path / "src/arde/prompts/odd.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe fxRates")

    assert target_repo_leaks(tmp_path) == ["src/arde/prompts/odd.md: mentions fxRates"]


def import_violations(source_root: Path, layer: str, *, forbidden: tuple[str, ...]) -> list[str]:
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
    leaks: list[str] = []
    for path in sorted((source_root / "arde").rglob("*")):
        relative = path.relative_to(source_root)
        if path.suffix not in SCANNED_SUFFIXES or relative.is_relative_to(GO_ADAPTER):
            continue
        names = go_specific_names(path.read_text(encoding="utf-8"))
        leaks.extend(f"{label(path, source_root)}: mentions {name}" for name in names)
    return leaks


def go_specific_names(source: str) -> list[str]:
    return [name for name, pattern in GO_SPECIFIC_PATTERNS.items() if pattern.search(source)]


def target_repo_leaks(repo_root: Path) -> list[str]:
    leaks: list[str] = []
    for path in product_files(repo_root):
        text = path.read_bytes().decode("utf-8", errors="replace").lower()
        leaks.extend(
            f"{label(path, repo_root)}: mentions {word}"
            for word in TARGET_REPO_WORDS
            if word.lower() in text
        )
    return leaks


def product_files(repo_root: Path) -> list[Path]:
    files = [
        path
        for folder in REPO_AGNOSTIC_FOLDERS
        for path in (repo_root / folder).rglob("*")
        if path.is_file()
    ]
    return sorted(path for path in files if is_product_file(path.relative_to(repo_root)))


def is_product_file(relative: Path) -> bool:
    return not (
        relative.name.endswith("_test.go")
        or relative == BUILT_HELPER
        or any(part in NOT_PRODUCT_FOLDERS for part in relative.parts)
    )


def key_reads(repo_root: Path) -> list[str]:
    sources = [*(repo_root / "src").rglob("*.py"), *(repo_root / "tests").rglob("*.py")]
    return [
        f"{label(path, repo_root)}: reads the API key"
        for path in sorted(sources)
        if label(path, repo_root) not in KEY_READERS and reads_the_key(path)
    ]


def reads_the_key(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(is_key_read(node) for node in ast.walk(tree))


def is_key_read(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute):
        return node.attr == "get_secret_value"
    if isinstance(node, ast.Subscript):
        return is_key_variable(node.slice)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr in ENVIRONMENT_READS and any(map(is_key_variable, node.args))
    return False


def is_key_variable(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == KEY_VARIABLE


def label(path: Path, source_root: Path) -> str:
    return path.relative_to(source_root).as_posix()


def write(root: Path, relative: str, *, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
