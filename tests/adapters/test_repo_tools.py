"""The read-only tools an agent has on a repo, against real files, links and awkward content.

A path comes from a model, and every byte a tool returns comes from the repo, so neither may lead
out of it or put a control character in front of the model.
"""

import os
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError
from tree_support import a_tree_too_deep_for_one_path

from arde.adapters.repo_tools import (
    GREP_LINE_LIMIT,
    GREP_MATCH_LIMIT,
    LISTED_FILE_LIMIT,
    READ_LIMIT_BYTES,
    clean_repo_text,
    repo_tools,
)
from arde.application.ports import ToolOutcome

FAKE_SECRET = "FAKE_SECRET_VALUE_FOR_THE_TEST"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    folder = tmp_path / "repo"
    folder.mkdir()
    return folder


@pytest.fixture
def secret(tmp_path: Path) -> Path:
    file = tmp_path / "private" / "secret_file"
    file.parent.mkdir()
    file.write_text(f"KEY={FAKE_SECRET}\n", encoding="utf-8")
    return file


def write(repo: Path, relative: str, content: str | bytes) -> Path:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8", newline="")
    return path


def call(repo: Path, name: str, **tool_input: object) -> ToolOutcome:
    tools = {tool.definition.name: tool for tool in repo_tools(repo)}
    return tools[name].handler(tool_input)


def read(repo: Path, path: str) -> ToolOutcome:
    return call(repo, "read_file", path=path)


def list_files(repo: Path, path: str = ".") -> ToolOutcome:
    return call(repo, "list_files", path=path)


def grep(repo: Path, text: str, path: str = ".") -> ToolOutcome:
    return call(repo, "grep", text=text, path=path)


# A tool that opens a FIFO waits for a writer that never comes, so the call runs in a thread.
def finished_within(seconds: float, tool_call: Callable[[], ToolOutcome]) -> ToolOutcome:
    outcomes: list[ToolOutcome] = []
    worker = threading.Thread(target=lambda: outcomes.append(tool_call()), daemon=True)
    worker.start()
    worker.join(seconds)
    assert not worker.is_alive(), "the tool blocked"
    return outcomes[0]


def test_the_repo_tools_are_list_files_read_file_and_grep_in_a_stable_order(repo: Path) -> None:
    names = [tool.definition.name for tool in repo_tools(repo)]

    assert names == ["list_files", "read_file", "grep"]


def test_every_repo_tool_is_strict_with_a_closed_schema(repo: Path) -> None:
    definitions = [tool.definition for tool in repo_tools(repo)]

    assert all(definition.strict for definition in definitions)
    assert all(
        definition.input_schema["additionalProperties"] is False for definition in definitions
    )


def test_an_input_the_schema_does_not_allow_goes_back_to_the_agent_loop(repo: Path) -> None:
    with pytest.raises(ValidationError):
        call(repo, "read_file", path="a.txt", lines="1-5")


# read_file


def test_read_file_returns_numbered_lines(repo: Path) -> None:
    write(repo, "app/main.txt", "first\n\n\tthird\n")

    assert read(repo, "app/main.txt") == ToolOutcome(
        content="     1\tfirst\n     2\t\n     3\t\tthird"
    )


def test_a_last_line_with_no_line_break_is_still_shown(repo: Path) -> None:
    write(repo, "a.txt", "one\ntwo")

    assert read(repo, "a.txt").content == "     1\tone\n     2\ttwo"


def test_lines_are_counted_at_each_line_feed_and_a_crlf_ending_shows_no_carriage_return(
    repo: Path,
) -> None:
    write(repo, "a.txt", "one\r\ntwo\x0cstill two\u2028still two\r\n")

    assert read(repo, "a.txt").content == "     1\tone\n     2\ttwo\\x0cstill two\\u2028still two"


def test_read_rejects_path_escape(repo: Path, secret: Path) -> None:
    outcome = read(repo, "../private/secret_file")

    assert outcome.is_error
    assert FAKE_SECRET not in outcome.content
    assert "out of the repo" in outcome.content


def test_read_rejects_an_absolute_path(repo: Path, secret: Path) -> None:
    outcome = read(repo, str(secret))

    assert outcome.is_error
    assert FAKE_SECRET not in outcome.content


def test_symlink_outside_root_rejected(repo: Path, secret: Path) -> None:
    (repo / "config.txt").symlink_to(secret)

    outcome = read(repo, "config.txt")

    assert outcome.is_error
    assert FAKE_SECRET not in outcome.content


def test_a_file_reached_through_a_folder_link_that_leaves_is_rejected(
    repo: Path, secret: Path
) -> None:
    (repo / "shared").symlink_to(secret.parent)

    outcome = read(repo, "shared/secret_file")

    assert outcome.is_error
    assert FAKE_SECRET not in outcome.content


def test_a_link_to_a_file_inside_the_repo_is_read(repo: Path) -> None:
    write(repo, "real.txt", "inside\n")
    (repo / "alias.txt").symlink_to("real.txt")

    assert read(repo, "alias.txt") == ToolOutcome(content="     1\tinside")


def test_a_repo_reached_through_a_link_is_read_from_its_real_folder(
    repo: Path, tmp_path: Path
) -> None:
    write(repo, "a.txt", "inside\n")
    entrance = tmp_path / "entrance"
    entrance.symlink_to(repo)

    assert call(entrance, "read_file", path="a.txt") == ToolOutcome(content="     1\tinside")


def test_a_link_that_loops_is_a_tool_error_and_not_a_crash(repo: Path) -> None:
    (repo / "a").symlink_to("b")
    (repo / "b").symlink_to("a")

    assert read(repo, "a").is_error


def test_binary_file_returns_tool_error(repo: Path) -> None:
    write(repo, "logo.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

    outcome = read(repo, "logo.png")

    assert outcome.is_error
    assert "binary" in outcome.content


def test_non_utf8_file_is_read_with_replacement(repo: Path) -> None:
    write(repo, "latin1.txt", b"caf\xe9\n")

    assert read(repo, "latin1.txt") == ToolOutcome(content="     1\tcaf\ufffd")


def test_huge_file_truncated_with_notice(repo: Path) -> None:
    line = "x" * 99 + "\n"
    write(repo, "big.txt", line * 3000)

    outcome = read(repo, "big.txt")

    assert READ_LIMIT_BYTES == 200_000
    assert not outcome.is_error
    assert f"  2000\t{line[:-1]}" in outcome.content
    assert "  2001\t" not in outcome.content
    assert outcome.content.endswith(
        "[ARDE showed the first 200000 of the 300000 bytes in this file. "
        "The tools read no further into a file.]"
    )


def test_a_cut_falls_after_the_last_whole_line(repo: Path) -> None:
    write(repo, "big.txt", ("y" * 149 + "\n") * 2000)

    outcome = read(repo, "big.txt")

    assert f"  1333\t{'y' * 149}\n[ARDE showed the first 199950 of" in outcome.content
    assert "  1334\t" not in outcome.content


def test_an_empty_file_says_it_is_empty(repo: Path) -> None:
    write(repo, "empty.txt", "")

    assert read(repo, "empty.txt") == ToolOutcome(content="empty.txt is empty.")


def test_a_missing_file_is_a_tool_error_that_points_at_list_files(repo: Path) -> None:
    outcome = read(repo, "missing.txt")

    assert outcome.is_error
    assert "list_files" in outcome.content


def test_a_folder_is_a_tool_error_that_points_at_list_files(repo: Path) -> None:
    (repo / "pkg").mkdir()

    outcome = read(repo, "pkg")

    assert outcome.is_error
    assert "list_files" in outcome.content


def test_a_fifo_is_refused_without_waiting_for_a_writer(repo: Path) -> None:
    os.mkfifo(repo / "pipe")

    assert finished_within(5, lambda: read(repo, "pipe")).is_error


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every file")
def test_a_file_that_cannot_be_read_is_a_tool_error(repo: Path) -> None:
    locked = write(repo, "locked.txt", "hidden\n")
    locked.chmod(0o000)
    try:
        outcome = read(repo, "locked.txt")
    finally:
        locked.chmod(0o644)

    assert outcome.is_error
    assert "hidden" not in outcome.content


def test_a_path_with_a_nul_character_is_a_tool_error(repo: Path) -> None:
    assert read(repo, "a\x00.txt").is_error


def test_control_characters_in_a_file_are_shown_and_never_passed_on(repo: Path) -> None:
    write(repo, "a.txt", "a\x1b[2Jb\tc\u202ed\n")

    assert read(repo, "a.txt").content == "     1\ta\\x1b[2Jb\tc\\u202ed"


# list_files


def test_list_files_lists_every_file_under_a_folder_from_the_repo_root_sorted(repo: Path) -> None:
    for name in ("b.txt", "a/z.txt", "a/b/c.txt"):
        write(repo, name, "x\n")

    assert list_files(repo) == ToolOutcome(content="a/b/c.txt\na/z.txt\nb.txt")
    assert list_files(repo, "a") == ToolOutcome(content="a/b/c.txt\na/z.txt")


def test_list_files_leaves_out_a_link_that_leads_out_of_the_repo(repo: Path, secret: Path) -> None:
    write(repo, "kept.txt", "x\n")
    (repo / "leak.txt").symlink_to(secret)
    (repo / "shared").symlink_to(secret.parent)

    assert list_files(repo) == ToolOutcome(content="kept.txt")


def test_list_files_keeps_a_link_to_a_file_inside(repo: Path) -> None:
    write(repo, "real.txt", "x\n")
    (repo / "alias.txt").symlink_to("real.txt")

    assert list_files(repo).content == "alias.txt\nreal.txt"


def test_list_files_leaves_out_what_is_not_a_regular_file(repo: Path) -> None:
    write(repo, "kept.txt", "x\n")
    os.mkfifo(repo / "pipe")
    (repo / "broken").symlink_to("not-there")

    assert list_files(repo).content == "kept.txt"


@pytest.mark.parametrize("path", ["..", "../private", "/"])
def test_list_files_refuses_a_folder_outside_the_repo(repo: Path, secret: Path, path: str) -> None:
    outcome = list_files(repo, path)

    assert outcome.is_error
    assert "secret_file" not in outcome.content


def test_list_files_refuses_a_folder_link_that_leaves_the_repo(repo: Path, secret: Path) -> None:
    (repo / "shared").symlink_to(secret.parent)

    outcome = list_files(repo, "shared")

    assert outcome.is_error
    assert "secret_file" not in outcome.content


def test_list_files_caps_the_list_and_says_how_many_files_there_are(repo: Path) -> None:
    for number in range(LISTED_FILE_LIMIT + 5):
        write(repo, f"f{number:05d}.txt", "")

    lines = list_files(repo).content.split("\n")

    assert len(lines) == LISTED_FILE_LIMIT + 1
    assert lines[-1] == (
        f"[ARDE listed the first {LISTED_FILE_LIMIT} of {LISTED_FILE_LIMIT + 5} files. "
        "List a folder further down to see the rest.]"
    )


def test_list_files_shows_each_name_on_one_line_with_its_control_characters_escaped(
    repo: Path,
) -> None:
    write(repo, "a\nb.txt", "")
    write(repo, "c\td\x1b[31m.txt", "")

    assert list_files(repo).content == "a\\x0ab.txt\nc\\x09d\\x1b[31m.txt"


def test_list_files_on_a_file_points_at_read_file(repo: Path) -> None:
    write(repo, "a.txt", "x\n")

    outcome = list_files(repo, "a.txt")

    assert outcome.is_error
    assert "read_file" in outcome.content


def test_list_files_of_a_folder_with_no_file_says_so(repo: Path) -> None:
    (repo / "empty").mkdir()

    assert list_files(repo, "empty") == ToolOutcome(content="There is no file under empty.")


def test_a_tree_too_deep_for_one_path_is_listed_and_searched_without_a_crash(repo: Path) -> None:
    write(repo, "a.txt", "alpha\n")
    a_tree_too_deep_for_one_path(repo)

    assert list_files(repo).content.startswith("a.txt")
    assert grep(repo, "alpha").content.startswith("a.txt:1: alpha")


def test_list_files_of_a_missing_folder_is_a_tool_error(repo: Path) -> None:
    assert list_files(repo, "missing").is_error


# grep


def test_grep_prints_each_matching_line_with_its_file_and_number(repo: Path) -> None:
    write(repo, "a.txt", "alpha\nbeta\nalphabet\n")
    write(repo, "sub/b.txt", "no\nalpha\n")

    assert grep(repo, "alpha") == ToolOutcome(
        content="a.txt:1: alpha\na.txt:3: alphabet\nsub/b.txt:2: alpha"
    )


# The model picks the text and the repo supplies the lines, so a regex could run for as long as
# the two together make it. The text is matched as written instead, and no regex can be invalid.
@pytest.mark.parametrize(
    ("text", "found"),
    [("call(", "a.txt:1: call(x)"), ("[unclosed", "a.txt:3: [unclosed"), ("(a+)+$", "")],
)
def test_grep_matches_regex_characters_as_plain_text(repo: Path, text: str, found: str) -> None:
    write(repo, "a.txt", "call(x)\ncallxx\n[unclosed\naaaaaaaaaaaaaaaaaaaaaaaaaaaa!\n")

    outcome = grep(repo, text)

    assert not outcome.is_error
    assert outcome.content == (found or "No line in . holds that text.")


def test_grep_does_not_treat_a_dot_as_any_character(repo: Path) -> None:
    write(repo, "a.txt", "abc\n")

    assert grep(repo, "a.c").content == "No line in . holds that text."


def test_grep_is_case_sensitive(repo: Path) -> None:
    write(repo, "a.txt", "Alpha\nalpha\n")

    assert grep(repo, "alpha").content == "a.txt:2: alpha"


def test_grep_caps_matches(repo: Path) -> None:
    write(repo, "a.txt", "match\n" * (GREP_MATCH_LIMIT + 20))

    lines = grep(repo, "match").content.split("\n")

    assert len(lines) == GREP_MATCH_LIMIT + 1
    assert lines[GREP_MATCH_LIMIT - 1] == f"a.txt:{GREP_MATCH_LIMIT}: match"
    assert lines[-1] == (
        f"[ARDE showed the first {GREP_MATCH_LIMIT} of {GREP_MATCH_LIMIT + 20} matching lines. "
        "Search for longer text or in a smaller folder to see the rest.]"
    )


def test_grep_cuts_a_long_matching_line(repo: Path) -> None:
    write(repo, "min.txt", "match" + "z" * 10_000 + "\n")

    content = grep(repo, "match").content

    assert content == (
        f"min.txt:1: {('match' + 'z' * 10_000)[:GREP_LINE_LIMIT]} "
        f"[ARDE cut this line at {GREP_LINE_LIMIT} characters]"
    )


def test_grep_searches_one_file_when_given_one(repo: Path) -> None:
    write(repo, "a.txt", "alpha\n")
    write(repo, "b.txt", "alpha\n")

    assert grep(repo, "alpha", "b.txt").content == "b.txt:1: alpha"


def test_grep_passes_over_a_binary_file(repo: Path) -> None:
    write(repo, "blob.bin", b"alpha\x00\x01")
    write(repo, "a.txt", "alpha\n")

    assert grep(repo, "alpha").content == "a.txt:1: alpha"


def test_grep_never_reads_through_a_link_that_leads_out_of_the_repo(
    repo: Path, secret: Path
) -> None:
    (repo / "leak.txt").symlink_to(secret)
    (repo / "shared").symlink_to(secret.parent)

    outcome = grep(repo, "KEY")

    assert FAKE_SECRET not in outcome.content
    assert outcome.content == "No line in . holds that text."


@pytest.mark.parametrize("path", ["../private", "../private/secret_file", "leak.txt"])
def test_grep_refuses_a_path_that_leads_out_of_the_repo(
    repo: Path, secret: Path, path: str
) -> None:
    (repo / "leak.txt").symlink_to(secret)

    outcome = grep(repo, "KEY", path)

    assert outcome.is_error
    assert FAKE_SECRET not in outcome.content


def test_grep_does_not_wait_on_a_fifo(repo: Path) -> None:
    os.mkfifo(repo / "pipe")
    write(repo, "a.txt", "alpha\n")

    assert finished_within(5, lambda: grep(repo, "alpha")).content == "a.txt:1: alpha"


def test_grep_reads_as_much_of_a_file_as_read_file_does_and_says_when_it_cut_one(
    repo: Path,
) -> None:
    write(repo, "big.txt", "x" * 99 + "\n" + "y" * READ_LIMIT_BYTES + "\nneedle\n")

    assert grep(repo, "needle").content == (
        "No line in . holds that text.\n"
        f"[ARDE searched only the first {READ_LIMIT_BYTES} bytes of 1 file here.]"
    )


@pytest.mark.parametrize("text", ["", "two\nlines"])
def test_grep_needs_text_on_one_line(repo: Path, text: str) -> None:
    write(repo, "a.txt", "two\nlines\n")

    assert grep(repo, text).is_error


def test_grep_shows_control_characters_in_a_matching_line_escaped(repo: Path) -> None:
    write(repo, "a.txt", "alpha \x1b]0;OWNED\x07\r\n")

    assert grep(repo, "alpha").content == "a.txt:1: alpha \\x1b]0;OWNED\\x07"


def test_grep_reads_a_file_that_is_not_utf8_with_replacement(repo: Path) -> None:
    write(repo, "latin1.txt", b"caf\xe9 alpha\n")

    assert grep(repo, "alpha").content == "latin1.txt:1: caf\ufffd alpha"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every file")
def test_grep_says_how_many_files_it_could_not_read(repo: Path) -> None:
    locked = write(repo, "locked.txt", "alpha\n")
    write(repo, "a.txt", "alpha\n")
    locked.chmod(0o000)
    try:
        content = grep(repo, "alpha").content
    finally:
        locked.chmod(0o644)

    assert content == "a.txt:1: alpha\n[ARDE could not read 1 file here.]"


# clean_repo_text


def test_clean_repo_text_keeps_line_breaks_and_tabs() -> None:
    assert clean_repo_text("a\n\tb") == "a\n\tb"


@pytest.mark.parametrize(
    ("raw", "shown"),
    [("\x1b[2J", "\\x1b[2J"), ("\r", "\\x0d"), ("\x7f", "\\x7f"), ("\u202e", "\\u202e")],
)
def test_clean_repo_text_writes_out_each_unprintable_character(raw: str, shown: str) -> None:
    assert clean_repo_text(f"a{raw}b") == f"a{shown}b"


# A name that is not UTF-8 reaches Python as a lone surrogate, which no encoder accepts.
def test_clean_repo_text_writes_out_a_lone_surrogate() -> None:
    shown = clean_repo_text("bad\udcff.txt")

    assert shown == "bad\\udcff.txt"
    assert shown.encode("utf-8")
