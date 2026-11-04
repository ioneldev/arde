"""A patch of whole-file changes that git apply turns back into the same bytes.

Each test applies the patch with git itself, with line-ending conversion off, so the test holds
whatever the machine's git settings are.
"""

import subprocess
from pathlib import Path

from arde.adapters.unified_patch import FileChange, unified_patch


def applied(patch: bytes, tree: Path) -> None:
    subprocess.run(
        ["git", "-c", "core.autocrlf=false", "apply", "-"],
        cwd=tree,
        input=patch,
        capture_output=True,
        check=True,
    )


def a_tree(root: Path, files: dict[str, bytes]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return root


def test_a_changed_line_is_applied(tmp_path: Path) -> None:
    tree = a_tree(tmp_path, {"main.go": b"a\nb\nc\n"})

    applied(unified_patch([FileChange("main.go", before=b"a\nb\nc\n", after=b"a\nB\nc\n")]), tree)

    assert (tree / "main.go").read_bytes() == b"a\nB\nc\n"


def test_a_new_file_is_created_with_its_folders(tmp_path: Path) -> None:
    change = FileChange("docs/api.md", before=None, after=b"# API\n\nthings\n")

    applied(unified_patch([change]), tmp_path)

    assert (tmp_path / "docs" / "api.md").read_bytes() == b"# API\n\nthings\n"


def test_an_empty_new_file_is_created(tmp_path: Path) -> None:
    applied(unified_patch([FileChange("empty.txt", before=None, after=b"")]), tmp_path)

    assert (tmp_path / "empty.txt").read_bytes() == b""


def test_a_removed_file_is_deleted(tmp_path: Path) -> None:
    tree = a_tree(tmp_path, {"old.md": b"gone\n"})

    applied(unified_patch([FileChange("old.md", before=b"gone\n", after=None)]), tree)

    assert not (tree / "old.md").exists()


def test_crlf_line_endings_are_kept(tmp_path: Path) -> None:
    before, after = b"a\r\nb\r\n", b"a\r\n// added\r\nb\r\n"
    tree = a_tree(tmp_path, {"main.go": before})

    applied(unified_patch([FileChange("main.go", before=before, after=after)]), tree)

    assert (tree / "main.go").read_bytes() == after


# Lines are split at LF alone: a CR elsewhere in a line is part of the line.
def test_a_carriage_return_inside_a_line_is_kept(tmp_path: Path) -> None:
    before, after = b"a\rb\n", b"a\rb\nc\n"
    tree = a_tree(tmp_path, {"f.txt": before})

    applied(unified_patch([FileChange("f.txt", before=before, after=after)]), tree)

    assert (tree / "f.txt").read_bytes() == after


def test_a_last_line_with_no_newline_is_kept_on_both_sides(tmp_path: Path) -> None:
    tree = a_tree(tmp_path, {"a.txt": b"one\ntwo", "b.txt": b"one"})
    changes = [
        FileChange("a.txt", before=b"one\ntwo", after=b"one\nthree"),
        FileChange("b.txt", before=b"one", after=b"one\ntwo\n"),
    ]

    applied(unified_patch(changes), tree)

    assert (tree / "a.txt").read_bytes() == b"one\nthree"
    assert (tree / "b.txt").read_bytes() == b"one\ntwo\n"


def test_bytes_that_are_not_utf8_are_kept(tmp_path: Path) -> None:
    before, after = b"caf\xe9\n", b"caf\xe9\nbar\n"
    tree = a_tree(tmp_path, {"latin1.txt": before})

    applied(unified_patch([FileChange("latin1.txt", before=before, after=after)]), tree)

    assert (tree / "latin1.txt").read_bytes() == after


def test_a_name_with_a_space_or_a_quote_is_applied(tmp_path: Path) -> None:
    tree = a_tree(tmp_path, {"my notes.md": b"x\n", 'say "hi".md': b"x\n"})
    changes = [
        FileChange("my notes.md", before=b"x\n", after=b"y\n"),
        FileChange('say "hi".md', before=b"x\n", after=b"z\n"),
    ]

    applied(unified_patch(changes), tree)

    assert (tree / "my notes.md").read_bytes() == b"y\n"
    assert (tree / 'say "hi".md').read_bytes() == b"z\n"


def test_an_unchanged_file_adds_nothing() -> None:
    assert unified_patch([FileChange("same.txt", before=b"x\n", after=b"x\n")]) == b""


def test_several_files_make_one_patch_in_the_order_given(tmp_path: Path) -> None:
    tree = a_tree(tmp_path, {"a.txt": b"1\n", "b.txt": b"2\n"})
    changes = [
        FileChange("b.txt", before=b"2\n", after=b"two\n"),
        FileChange("a.txt", before=b"1\n", after=b"one\n"),
        FileChange("c/new.txt", before=None, after=b"3\n"),
    ]

    patch = unified_patch(changes)
    applied(patch, tree)

    assert patch.index(b"b/b.txt") < patch.index(b"b/a.txt") < patch.index(b"b/c/new.txt")
    assert [(tree / n).read_bytes() for n in ("a.txt", "b.txt", "c/new.txt")] == [
        b"one\n",
        b"two\n",
        b"3\n",
    ]
