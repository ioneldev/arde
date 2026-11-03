"""Getting the code into ARDE's own workspace, without changing the user's repo.

Every repo here is made under tmp_path. A URL is a file:// URL to a bare repo, so no test needs the
network. ARDE's git reads HOME, so each test gets a HOME of its own with no settings in it, because
a developer's own core.autocrlf would otherwise decide what a clone holds.
"""

import hashlib
import os
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from arde.adapters.language.go.adapter import GoAdapter
from arde.adapters.repo_links import LinkLeavesRepoError
from arde.adapters.source import (
    SourceRequest,
    copy_tree,
    ensure_git_eol_config,
    repo_id_from_origin,
    resolve_source,
    without_credentials,
)
from arde.domain.errors import SourceError
from arde.domain.report import ExitCode

FAKE_SECRET = "FAKE_SECRET_VALUE_FOR_THE_TEST"

IDENTITY = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "user-home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    return folder


@pytest.fixture
def arde_home(tmp_path: Path) -> Path:
    return tmp_path / "arde-home"


@pytest.fixture
def secret(tmp_path: Path) -> Path:
    file = tmp_path / "private" / "secret_file"
    file.parent.mkdir()
    file.write_text(f"{FAKE_SECRET}\n", encoding="utf-8")
    return file


def git(*arguments: str, cwd: Path) -> str:
    environment = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "GIT_CONFIG_NOSYSTEM": "1",
        **IDENTITY,
    }
    return subprocess.run(
        ("git", *arguments), cwd=cwd, env=environment, check=True, capture_output=True, text=True
    ).stdout.strip()


def write(folder: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


def a_repo(folder: Path, files: dict[str, str]) -> Path:
    folder.mkdir(parents=True)
    git("init", "--quiet", "--initial-branch=main", cwd=folder)
    write(folder, files)
    commit_all(folder)
    return folder


def commit_all(repo: Path) -> None:
    git("add", "--all", cwd=repo)
    git("commit", "--quiet", "--message=change", cwd=repo)


def a_remote(tmp_path: Path) -> tuple[str, Path]:
    """A bare repo as a file:// URL, with a main branch and a feature branch, and its upstream."""
    upstream = a_repo(tmp_path / "upstream", {"main.txt": "line one\nline two\n"})
    git("switch", "--quiet", "--create", "feature", cwd=upstream)
    write(upstream, {"feature.txt": "only on feature\n"})
    commit_all(upstream)
    git("switch", "--quiet", "main", cwd=upstream)
    bare = tmp_path / "remote.git"
    git("clone", "--quiet", "--bare", str(upstream), str(bare), cwd=tmp_path)
    return bare.as_uri(), upstream


def local(repo: Path, branch: str | None = None) -> SourceRequest:
    return SourceRequest(repo=str(repo), branch=branch)


def files_in(folder: Path) -> dict[str, str]:
    return {name: data.decode("utf-8") for name, data in every_byte_in(folder).items()}


# os.walk, because it never follows a link, and a test must see the tree as it is.
def every_byte_in(folder: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for parent, _, names in os.walk(folder):
        for name in names:
            path = Path(parent) / name
            if not path.is_symlink():
                found[path.relative_to(folder).as_posix()] = path.read_bytes()
    return dict(sorted(found.items()))


def copied(source_repo: Path, dest: Path) -> Path:
    copy_tree(resolve_source(local(source_repo), Path("unused-for-a-local-path")), dest)
    return dest


# URL sources


def test_url_clone_uses_autocrlf_false(tmp_path: Path, home: Path, arde_home: Path) -> None:
    url, _ = a_remote(tmp_path)
    (home / ".gitconfig").write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")

    source = resolve_source(SourceRequest(repo=url, branch=None), arde_home)

    assert source.kind == "url"
    assert (source.root / "main.txt").read_bytes() == b"line one\nline two\n"


def test_url_clone_checks_out_named_branch(tmp_path: Path, arde_home: Path) -> None:
    url, upstream = a_remote(tmp_path)

    source = resolve_source(SourceRequest(repo=url, branch="feature"), arde_home)

    assert (source.root / "feature.txt").read_text(encoding="utf-8") == "only on feature\n"
    assert source.revision == git("rev-parse", "feature", cwd=upstream)


def test_url_clone_lives_in_the_workspace_of_its_repo_id(tmp_path: Path, arde_home: Path) -> None:
    url, _ = a_remote(tmp_path)

    source = resolve_source(SourceRequest(repo=url, branch=None), arde_home)

    assert source.root == arde_home / "workspaces" / source.repo_id / "clone"
    assert source.repo_id == repo_id_from_origin(url)
    assert source.origin == url


def test_unknown_branch_raises_with_next_action(tmp_path: Path, arde_home: Path) -> None:
    url, _ = a_remote(tmp_path)

    with pytest.raises(SourceError) as stopped:
        resolve_source(SourceRequest(repo=url, branch="no-such-branch"), arde_home)

    assert stopped.value.exit_code == ExitCode.BAD_USAGE
    assert "no-such-branch" in str(stopped.value)
    assert "--branch" in stopped.value.next_action
    assert list(arde_home.rglob("clone")) == []


def test_a_url_that_cannot_be_cloned_raises_with_next_action(
    tmp_path: Path, arde_home: Path
) -> None:
    url = (tmp_path / "nothing-here.git").as_uri()

    with pytest.raises(SourceError) as stopped:
        resolve_source(SourceRequest(repo=url, branch=None), arde_home)

    assert "nothing-here" in str(stopped.value)
    assert stopped.value.next_action


def test_a_second_clone_of_the_same_url_replaces_the_first(tmp_path: Path, arde_home: Path) -> None:
    url, upstream = a_remote(tmp_path)
    first = resolve_source(SourceRequest(repo=url, branch=None), arde_home)
    write(upstream, {"main.txt": "line one\nline two\nline three\n"})
    commit_all(upstream)
    git("push", "--quiet", str(tmp_path / "remote.git"), "main", cwd=upstream)

    second = resolve_source(SourceRequest(repo=url, branch=None), arde_home)

    assert second.root == first.root
    assert second.revision != first.revision
    assert (second.root / "main.txt").read_text(encoding="utf-8").endswith("line three\n")


# Local sources: what is copied


def test_local_copy_keeps_uncommitted_and_untracked(tmp_path: Path) -> None:
    repo = a_repo(
        tmp_path / "shop", {"main.go": "package main\n", "web/routes.go": "package web\n"}
    )
    write(repo, {"main.go": "package main // edited\n", "notes/todo.txt": "untracked\n"})
    before = every_byte_in(repo)

    dest = copied(repo, tmp_path / "copy")

    assert files_in(dest) == {
        "main.go": "package main // edited\n",
        "notes/todo.txt": "untracked\n",
        "web/routes.go": "package web\n",
    }
    assert every_byte_in(repo) == before


def test_local_copy_excludes_gitignored_env(tmp_path: Path) -> None:
    repo = a_repo(tmp_path / "shop", {".gitignore": ".env\n", "main.go": "package main\n"})
    write(repo, {".env": f"KEY={FAKE_SECRET}\n"})

    dest = copied(repo, tmp_path / "copy")

    assert not (dest / ".env").exists()
    assert (dest / ".gitignore").is_file()


def test_local_copy_skips_a_file_deleted_from_the_working_tree(tmp_path: Path) -> None:
    repo = a_repo(tmp_path / "shop", {"main.go": "package main\n", "old.go": "package main\n"})
    (repo / "old.go").unlink()

    dest = copied(repo, tmp_path / "copy")

    assert files_in(dest) == {"main.go": "package main\n"}


def test_local_copy_holds_no_git_folder(tmp_path: Path) -> None:
    repo = a_repo(tmp_path / "shop", {"main.go": "package main\n"})

    dest = copied(repo, tmp_path / "copy")

    assert not (dest / ".git").exists()


def test_local_copy_keeps_a_files_mode(tmp_path: Path) -> None:
    repo = a_repo(tmp_path / "shop", {"run.sh": "#!/bin/sh\n"})
    (repo / "run.sh").chmod(0o755)

    dest = copied(repo, tmp_path / "copy")

    assert os.access(dest / "run.sh", os.X_OK)


def test_names_with_odd_characters_are_copied_exactly(tmp_path: Path) -> None:
    odd = ("café.txt", "tab\there.txt", 'quo"te.txt', "back\\slash.txt", "new\nline.txt", "a b.txt")
    repo = a_repo(tmp_path / "shop", {name: f"{number}\n" for number, name in enumerate(odd)})

    dest = copied(repo, tmp_path / "copy")

    assert files_in(dest) == {name: f"{number}\n" for number, name in enumerate(odd)}


def test_no_go_mod_detected_by_adapter_not_source(tmp_path: Path) -> None:
    repo = a_repo(tmp_path / "notes", {"README.md": "no module here\n"})

    dest = copied(repo, tmp_path / "copy")

    assert (dest / "README.md").is_file()
    assert not GoAdapter(Path("unused")).detects(dest)


# Local sources: links


def test_local_copy_keeps_links_as_links(tmp_path: Path) -> None:
    repo = a_repo(tmp_path / "shop", {"config/base.yaml": "port: 8080\n"})
    (repo / "config.yaml").symlink_to(Path("config") / "base.yaml")
    commit_all(repo)
    (repo / "settings").symlink_to("config")

    dest = copied(repo, tmp_path / "copy")

    assert (dest / "config.yaml").is_symlink()
    assert os.readlink(dest / "config.yaml") == os.path.join("config", "base.yaml")
    assert (dest / "settings").is_symlink()
    assert os.readlink(dest / "settings") == "config"


def test_link_out_of_the_repo_stops_the_run(tmp_path: Path, secret: Path) -> None:
    repo = a_repo(tmp_path / "shop", {"main.go": "package main\n"})
    (repo / "go.mod").symlink_to(secret)
    dest = tmp_path / "copy"

    with pytest.raises(LinkLeavesRepoError) as stopped:
        copied(repo, dest)

    assert stopped.value.exit_code == ExitCode.BAD_USAGE
    assert "go.mod" in str(stopped.value)
    assert FAKE_SECRET not in f"{stopped.value} {stopped.value.next_action}"
    assert (dest / "go.mod").is_symlink()


# Git quotes a line of packed-refs it cannot parse, so this link put the secret in git's error.
def test_link_inside_dot_git_stops_the_run(tmp_path: Path, secret: Path) -> None:
    repo = a_repo(tmp_path / "shop", {"main.go": "package main\n"})
    git("pack-refs", "--all", cwd=repo)
    (repo / ".git" / "packed-refs").unlink()
    (repo / ".git" / "packed-refs").symlink_to(secret)

    with pytest.raises(LinkLeavesRepoError) as stopped:
        resolve_source(local(repo), Path("unused"))

    assert ".git/packed-refs" in str(stopped.value)
    assert FAKE_SECRET not in f"{stopped.value} {stopped.value.next_action}"


def test_a_hook_linked_to_a_script_in_the_repo_is_fine(tmp_path: Path) -> None:
    repo = a_repo(tmp_path / "shop", {"scripts/pre-commit": "#!/bin/sh\n"})
    (repo / ".git" / "hooks" / "pre-commit").symlink_to(
        Path("..") / ".." / "scripts" / "pre-commit"
    )

    assert resolve_source(local(repo), Path("unused")).kind == "local"


# The index still lists lib/inner.txt after lib became a link. Read through the link, the copy
# would take a file from outside, and written through the copied link, it would land outside.
def test_a_file_behind_a_linked_folder_is_neither_read_nor_written(tmp_path: Path) -> None:
    repo = a_repo(tmp_path / "shop", {"lib/inner.txt": "tracked\n"})
    shutil.rmtree(repo / "lib")
    (repo / "lib").symlink_to(Path("..") / "target")
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "inner.txt").write_text(FAKE_SECRET, encoding="utf-8")
    dest = tmp_path / "deeper" / "copy"
    (tmp_path / "deeper" / "target").mkdir(parents=True)

    with pytest.raises(LinkLeavesRepoError):
        copied(repo, dest)

    assert list((tmp_path / "deeper" / "target").iterdir()) == []
    assert all(FAKE_SECRET.encode() not in text for text in every_byte_in(dest).values())


# A crafted index


def an_index_listing(repo: Path, names: list[str]) -> None:
    """Writes a version 2 index by hand, as git itself refuses to add these names."""
    empty_blob = bytes.fromhex("e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")
    entries = b""
    for name in sorted(names):
        encoded = name.encode("utf-8")
        entry = struct.pack(">10I", 0, 0, 0, 0, 0, 0, 0o100644, 0, 0, 0) + empty_blob
        entry += struct.pack(">H", len(encoded)) + encoded
        entries += entry + b"\0" * (8 - len(entry) % 8)
    body = b"DIRC" + struct.pack(">II", 2, len(names)) + entries
    (repo / ".git" / "index").write_bytes(body + hashlib.sha1(body).digest())


@pytest.mark.parametrize(
    "listed", ["../private/secret_file", "/etc/hosts", ".git/config", ".GIT/config", "a//b"]
)
def test_a_listed_path_that_git_would_refuse_stops_the_copy(
    tmp_path: Path, secret: Path, listed: str
) -> None:
    repo = a_repo(tmp_path / "shop", {"main.go": "package main\n"})
    an_index_listing(repo, ["main.go", listed])
    dest = tmp_path / "copy"

    with pytest.raises(SourceError) as stopped:
        copied(repo, dest)

    assert stopped.value.next_action
    assert not dest.exists()


# A pipe stands where a tracked file was, and reading it would wait for a writer forever.
def test_a_pipe_in_the_tree_stops_the_copy(tmp_path: Path) -> None:
    repo = a_repo(tmp_path / "shop", {"main.go": "package main\n", "feed": "data\n"})
    (repo / "feed").unlink()
    os.mkfifo(repo / "feed")

    with pytest.raises(SourceError) as stopped:
        copied(repo, tmp_path / "copy")

    assert "feed" in str(stopped.value)


# Measured: git ls-files runs the command core.fsmonitor names in the repo's own settings.
def test_git_never_runs_a_command_the_repos_settings_name(tmp_path: Path) -> None:
    repo = a_repo(tmp_path / "shop", {"main.go": "package main\n"})
    marker = tmp_path / "ran"
    git("config", "core.fsmonitor", f"touch '{marker}'; false", cwd=repo)

    copied(repo, tmp_path / "copy")
    ensure_git_eol_config(repo)

    assert not marker.exists()


# Local sources: what is refused


def test_not_a_git_repo_raises_with_next_action(tmp_path: Path) -> None:
    folder = tmp_path / "plain"
    write(folder, {"main.go": "package main\n"})

    with pytest.raises(SourceError) as stopped:
        resolve_source(local(folder), Path("unused"))

    assert stopped.value.exit_code == ExitCode.BAD_USAGE
    assert "git init" in stopped.value.next_action


def test_missing_path_raises_with_next_action(tmp_path: Path) -> None:
    with pytest.raises(SourceError) as stopped:
        resolve_source(local(tmp_path / "nowhere"), Path("unused"))

    assert stopped.value.exit_code == ExitCode.BAD_USAGE
    assert "nowhere" in str(stopped.value)
    assert stopped.value.next_action


def test_a_file_given_as_the_repo_raises_with_next_action(tmp_path: Path) -> None:
    file = tmp_path / "main.go"
    file.write_text("package main\n", encoding="utf-8")

    with pytest.raises(SourceError) as stopped:
        resolve_source(local(file), Path("unused"))

    assert stopped.value.next_action


def test_a_folder_inside_a_repo_but_not_at_its_top_is_refused(tmp_path: Path) -> None:
    repo = a_repo(tmp_path / "shop", {"service/main.go": "package main\n"})

    with pytest.raises(SourceError) as stopped:
        resolve_source(local(repo / "service"), Path("unused"))

    assert str(repo.resolve()) in stopped.value.next_action


def test_a_repo_path_typed_in_another_case_is_still_its_top(tmp_path: Path) -> None:
    a_repo(tmp_path / "shop", {"main.go": "package main\n"})
    if not (tmp_path / "SHOP").exists():
        pytest.skip("this file system tells letter cases apart")

    assert resolve_source(local(tmp_path / "SHOP"), Path("unused")).kind == "local"


def test_a_branch_with_a_local_path_is_refused(tmp_path: Path) -> None:
    repo = a_repo(tmp_path / "shop", {"main.go": "package main\n"})

    with pytest.raises(SourceError) as stopped:
        resolve_source(local(repo, branch="main"), Path("unused"))

    assert "--branch" in stopped.value.next_action


def test_a_repo_with_no_commit_yet_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "fresh"
    folder.mkdir()
    git("init", "--quiet", cwd=folder)
    write(folder, {"main.go": "package main\n"})

    with pytest.raises(SourceError) as stopped:
        resolve_source(local(folder), Path("unused"))

    assert "commit" in stopped.value.next_action


# Local sources: identity


def test_a_local_source_records_its_commit(tmp_path: Path) -> None:
    repo = a_repo(tmp_path / "shop", {"main.go": "package main\n"})

    source = resolve_source(local(repo), Path("unused"))

    assert source.kind == "local"
    assert source.root == repo.resolve()
    assert source.revision == git("rev-parse", "HEAD", cwd=repo)


def test_repo_id_without_remote_hashes_path(tmp_path: Path) -> None:
    first = a_repo(tmp_path / "one" / "shop", {"main.go": "package main\n"})
    second = a_repo(tmp_path / "two" / "shop", {"main.go": "package main\n"})

    first_id = resolve_source(local(first), Path("unused")).repo_id
    second_id = resolve_source(local(second), Path("unused")).repo_id

    assert first_id != second_id
    assert first_id == resolve_source(local(first), Path("unused")).repo_id
    assert first_id.startswith("shop-")
    assert resolve_source(local(first), Path("unused")).origin == str(first.resolve())


def test_a_local_clone_shares_the_workspace_of_its_origin(tmp_path: Path) -> None:
    repo = a_repo(tmp_path / "checkout", {"main.go": "package main\n"})
    git("remote", "add", "origin", "https://user:token@github.com/team/shop.git", cwd=repo)

    source = resolve_source(local(repo), Path("unused"))

    assert source.repo_id == repo_id_from_origin("https://github.com/team/shop")
    assert source.origin == "https://github.com/team/shop.git"


def test_repo_id_from_origin_url_is_stable() -> None:
    spellings = (
        "https://github.com/team/shop",
        "https://github.com/team/shop.git",
        "https://GitHub.com/team/shop/",
        "https://user:token@github.com/team/shop.git",
        "git@github.com:team/shop.git",
        "ssh://git@github.com/team/shop.git",
    )

    assert {repo_id_from_origin(url) for url in spellings} == {"shop-b463c806ea33"}
    assert repo_id_from_origin("https://github.com/fork/shop") != "shop-b463c806ea33"


def test_a_repo_id_is_a_plain_folder_name_whatever_the_url() -> None:
    for url in ("https://example.com/", "https://example.com/team/..", "git@host:a/.hidden"):
        repo_id = repo_id_from_origin(url)

        assert "/" not in repo_id
        assert not repo_id.startswith(".")


def test_the_origin_keeps_no_credentials() -> None:
    assert without_credentials("https://user:token@example.com/team/shop.git") == (
        "https://example.com/team/shop.git"
    )
    assert without_credentials("https://token@example.com:8443/shop") == (
        "https://example.com:8443/shop"
    )
    assert without_credentials("git@example.com:team/shop.git") == "git@example.com:team/shop.git"


# Line endings


def test_crlf_with_lf_index_adds_autocrlf_arg(tmp_path: Path) -> None:
    upstream = a_repo(tmp_path / "upstream", {"a.go": "package a\n", "b.go": "package b\n"})
    checkout = tmp_path / "checkout"
    git("-c", "core.autocrlf=true", "clone", "--quiet", str(upstream), str(checkout), cwd=tmp_path)
    # A container sees the files through a mount, whose stat data differ, so git reads them again.
    for name in ("a.go", "b.go"):
        os.utime(checkout / name, (2_000_000_000, 2_000_000_000))

    assert ensure_git_eol_config(checkout) == ("-c", "core.autocrlf=true")
    # What the design measured: every file looked changed without it, and none with it.
    assert git("status", "--porcelain", cwd=checkout) != ""
    assert git(*ensure_git_eol_config(checkout), "status", "--porcelain", cwd=checkout) == ""


def test_lf_checkout_adds_no_eol_arg(tmp_path: Path) -> None:
    repo = a_repo(tmp_path / "shop", {"a.go": "package a\n"})

    assert ensure_git_eol_config(repo) == ()
