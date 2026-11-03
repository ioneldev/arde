"""The Go helper behind a typed client. A stand-in program plays the helper, so no Go is needed."""

import json
import os
import stat
from pathlib import Path

import pytest
from known_environment import known_go_environment

from arde.adapters.child_process import (
    ChildCommand,
    ChildOutcome,
    ChildOutputTooLongError,
    ChildTimeoutError,
    OutputLimit,
)
from arde.adapters.child_text import MESSAGE_TAIL_BYTES
from arde.adapters.language.go import goinspect_client
from arde.adapters.language.go.errors import BadPackageError, BadPatternError, GoinspectError
from arde.adapters.language.go.goinspect_client import (
    GOINSPECT_REPLY_LIMIT,
    GOINSPECT_SOURCE,
    CallMatch,
    Declaration,
    GoinspectClient,
    PatternName,
    SourcePlace,
    UnreadSite,
    WrapperCall,
    build_goinspect,
)
from arde.application.ports import ProfileProblem
from arde.domain.errors import ArdeError
from arde.domain.profile import ExtractionPattern
from arde.domain.report import ExitCode

# Prints a canned reply and the exit code it was given, and records how it was called.
STAND_IN = """#!/bin/sh
printf '%s\\n' "$@" > "$0.args"
case "$1" in match|routes|fingerprints) cp "$3" "$0.handed-over" ;; esac
cat "$0.out"
cat "$0.err" >&2
exit "$(cat "$0.code")"
"""


class StandIn:
    def __init__(self, folder: Path) -> None:
        self.binary = folder / "goinspect"
        self.binary.write_text(STAND_IN, encoding="utf-8")
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        self.replies_with()

    def replies_with(self, stdout: str = "{}", *, stderr: str = "", exit_code: int = 0) -> None:
        Path(f"{self.binary}.out").write_text(stdout, encoding="utf-8")
        Path(f"{self.binary}.err").write_text(stderr, encoding="utf-8")
        Path(f"{self.binary}.code").write_text(str(exit_code), encoding="utf-8")

    @property
    def arguments(self) -> list[str]:
        return Path(f"{self.binary}.args").read_text(encoding="utf-8").splitlines()

    @property
    def file_it_was_given(self) -> object:
        return json.loads(Path(f"{self.binary}.handed-over").read_text(encoding="utf-8"))


NOISY_STDERR = "\x1b[2J" + "bad package\n" * 5000 + "the real reason\n"


@pytest.fixture(autouse=True)
def a_known_go_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(goinspect_client, "go_environment", known_go_environment)


@pytest.fixture
def stand_in(tmp_path: Path) -> StandIn:
    folder = tmp_path / "helper"
    folder.mkdir()
    return StandIn(folder)


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    inspected = tmp_path / "inspected"
    inspected.mkdir()
    return inspected.resolve()


def test_compare_names_both_folders_and_reads_the_reply(stand_in: StandIn, folder: Path) -> None:
    stand_in.replies_with('{"changed": ["a.go"], "added": [], "removed": ["b.go"]}')

    reply = GoinspectClient(stand_in.binary).compare(folder, folder)

    assert (reply.changed, reply.added, reply.removed) == (("a.go",), (), ("b.go",))
    assert stand_in.arguments == ["compare", "--old", str(folder), "--new", str(folder)]


def test_declarations_are_read_with_their_fields(stand_in: StandIn, folder: Path) -> None:
    stand_in.replies_with(
        json.dumps(
            {
                "declarations": [
                    {"file": "a.go", "kind": "func", "name": "F", "hash": "h1", "line": 3}
                ],
                "fields": [
                    {
                        "file": "a.go",
                        "package": "shop",
                        "struct": "S",
                        "name": "Id",
                        "line": 7,
                        "tag": 'json:"id"',
                        "json_tag": "id",
                        "go_type": "int",
                        "embedded": False,
                    }
                ],
            }
        )
    )

    reply = GoinspectClient(stand_in.binary).declarations(folder)

    assert [(d.file, d.kind, d.name, d.hash, d.line) for d in reply.declarations] == [
        ("a.go", "func", "F", "h1", 3)
    ]
    assert [(f.struct, f.name, f.tag, f.go_type, f.embedded) for f in reply.fields] == [
        ("S", "Id", 'json:"id"', "int", False)
    ]
    assert stand_in.arguments == ["declarations", str(folder)]


def test_matches_hand_the_patterns_over_as_a_file(stand_in: StandIn, folder: Path) -> None:
    stand_in.replies_with(
        '{"matches": [{"kind": "env", "callee": "os.Getenv", "file": "m.go", "line": 4,'
        ' "args": {"name": "\\"PORT\\""}, "literals": {"name": "PORT"}}],'
        ' "unmatched_patterns": [{"kind": "param", "call": "^q$"}]}'
    )
    pattern = ExtractionPattern(kind="env", call=r"^os\.Getenv$", args={"name": 0})

    reply = GoinspectClient(stand_in.binary).matches(folder, [pattern])

    assert reply.matches == (
        CallMatch(
            kind="env",
            callee="os.Getenv",
            file="m.go",
            line=4,
            args={"name": '"PORT"'},
            literals={"name": "PORT"},
        ),
    )
    assert reply.unmatched_patterns == (PatternName(kind="param", call="^q$"),)
    assert stand_in.file_it_was_given == [
        {"kind": "env", "call": r"^os\.Getenv$", "args": {"name": 0}}
    ]
    assert stand_in.arguments[:2] == ["match", "--patterns"]
    assert stand_in.arguments[3] == str(folder)


def test_a_match_reply_with_no_literals_object_is_refused(stand_in: StandIn, folder: Path) -> None:
    stand_in.replies_with(
        '{"matches": [{"kind": "env", "callee": "os.Getenv", "file": "m.go", "line": 4,'
        ' "args": {"name": "name"}}], "unmatched_patterns": []}'
    )
    pattern = ExtractionPattern(kind="env", call=r"^os\.Getenv$", args={"name": 0})

    with pytest.raises(GoinspectError, match="literals"):
        GoinspectClient(stand_in.binary).matches(folder, [pattern])


def test_fingerprints_hand_over_the_ids_as_a_file_and_read_the_reply(
    stand_in: StandIn, folder: Path
) -> None:
    stand_in.replies_with(
        '{"fingerprints": {"a": "h"}, "reach": {"a": ["x.a"]}, "unresolved": ["b"],'
        ' "without_body": ["c"]}'
    )
    generic = "(*example.com/x.pair[K, V]).serve"

    reply = GoinspectClient(stand_in.binary).fingerprints(
        folder, ["a", generic], main_package="./cmd/api"
    )

    assert reply.fingerprints == {"a": "h"}
    assert reply.reach == {"a": ("x.a",)}
    assert reply.unresolved == ("b",)
    assert reply.without_body == ("c",)
    assert stand_in.arguments[:2] == ["fingerprints", "--roots"]
    assert stand_in.arguments[3:] == ["--main", "./cmd/api", str(folder)]
    assert stand_in.file_it_was_given == ["a", generic]


def test_no_roots_means_no_call_to_the_helper(tmp_path: Path, folder: Path) -> None:
    reply = GoinspectClient(tmp_path / "not-there").fingerprints(folder, [], main_package=".")

    assert (reply.fingerprints, reply.reach, reply.unresolved) == ({}, {}, ())
    assert reply.without_body == ()


def test_a_failure_carries_the_helpers_message_and_exits_with_one(
    stand_in: StandIn, folder: Path
) -> None:
    stand_in.replies_with("", stderr="goinspect: cannot parse bad.go\n", exit_code=1)

    with pytest.raises(GoinspectError) as failed:
        GoinspectClient(stand_in.binary).declarations(folder)

    assert isinstance(failed.value, ArdeError)
    assert "cannot parse bad.go" in str(failed.value)
    assert failed.value.exit_code == ExitCode.CHECKS_FAILED
    assert failed.value.next_action


@pytest.mark.parametrize(
    ("stdout", "reason"),
    [
        ("", "Invalid JSON"),
        ("not json", "Invalid JSON"),
        ('{"changed": [], "added": []', "Invalid JSON"),
        ('{"changed": [], "added": []}', "removed"),
        ('{"changed": [], "added": [], "removed": [], "extra": 1}', "extra"),
        ('{"changed": "a.go", "added": [], "removed": []}', "changed"),
        ('{"changed": null, "added": [], "removed": []}', "changed"),
    ],
)
def test_a_reply_that_does_not_fit_is_an_error_and_not_a_guess(
    stand_in: StandIn, folder: Path, stdout: str, reason: str
) -> None:
    stand_in.replies_with(stdout)

    with pytest.raises(GoinspectError) as unreadable:
        GoinspectClient(stand_in.binary).compare(folder, folder)

    assert reason in str(unreadable.value)
    assert unreadable.value.exit_code == ExitCode.CHECKS_FAILED
    assert unreadable.value.next_action


def test_a_helper_that_hangs_is_stopped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, folder: Path
) -> None:
    hanging = tmp_path / "hanging"
    hanging.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    hanging.chmod(hanging.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(goinspect_client, "GOINSPECT_TIMEOUT_S", 0.5)

    with pytest.raises(ChildTimeoutError, match=r"0\.5 s"):
        GoinspectClient(hanging).declarations(folder)


class CountingBuild:
    """Stands in for the build: writes the file it was asked for and counts how often it ran."""

    def __init__(self) -> None:
        self.runs = 0

    def __call__(self, command: ChildCommand) -> ChildOutcome:
        self.runs += 1
        Path(command.argv[command.argv.index("-o") + 1]).write_text("binary", encoding="utf-8")
        return ChildOutcome(exit_code=0, stdout="", stderr="")


def test_the_helper_is_built_once_per_process_for_a_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    build = CountingBuild()
    monkeypatch.setattr(goinspect_client, "run_child", build)
    monkeypatch.setattr(goinspect_client, "_built", set())

    first = build_goinspect(tmp_path / "bin")
    second = build_goinspect(tmp_path / "bin")

    assert first == second == tmp_path / "bin" / "goinspect"
    assert build.runs == 1


def test_a_helper_deleted_after_the_build_is_built_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    build = CountingBuild()
    monkeypatch.setattr(goinspect_client, "run_child", build)
    monkeypatch.setattr(goinspect_client, "_built", set())

    os.makedirs(tmp_path / "bin")
    build_goinspect(tmp_path / "bin").unlink()
    build_goinspect(tmp_path / "bin")

    assert build.runs == 2


def test_a_failed_build_names_the_next_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def failing_build(command: ChildCommand) -> ChildOutcome:
        return ChildOutcome(exit_code=2, stdout="", stderr="main.go:3: oops\n")

    monkeypatch.setattr(goinspect_client, "run_child", failing_build)
    monkeypatch.setattr(goinspect_client, "_built", set())

    with pytest.raises(GoinspectError, match=r"main\.go:3: oops") as failed:
        build_goinspect(tmp_path / "bin")

    assert failed.value.next_action


def test_a_helper_folder_that_cannot_be_made_stops_with_a_next_action_and_no_raw_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(goinspect_client, "_built", set())
    (tmp_path / "a-file").write_text("not a folder", encoding="utf-8")

    with pytest.raises(GoinspectError) as failed:
        build_goinspect(tmp_path / "a-file" / "bin")

    assert isinstance(failed.value, ArdeError)
    assert failed.value.next_action
    assert "a-file" in str(failed.value)


def test_a_failed_build_gives_a_short_clean_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def noisy_build(command: ChildCommand) -> ChildOutcome:
        return ChildOutcome(exit_code=2, stdout="", stderr=NOISY_STDERR)

    monkeypatch.setattr(goinspect_client, "run_child", noisy_build)
    monkeypatch.setattr(goinspect_client, "_built", set())

    with pytest.raises(GoinspectError) as failed:
        build_goinspect(tmp_path / "bin")

    assert "the real reason" in str(failed.value)
    assert "\x1b" not in str(failed.value)
    assert len(str(failed.value)) < 2 * MESSAGE_TAIL_BYTES


@pytest.mark.parametrize(
    "reason",
    ["goinspect: cannot parse web/a.go", "goinspect: /repo has no go.mod; pass the directory"],
)
def test_a_failure_with_a_reason_sends_the_reader_to_that_reason_and_guesses_nothing(
    stand_in: StandIn, folder: Path, reason: str
) -> None:
    stand_in.replies_with("", stderr=reason, exit_code=1)

    with pytest.raises(GoinspectError) as failed:
        GoinspectClient(stand_in.binary).declarations(folder)

    assert reason in str(failed.value)
    assert "what the message names" in failed.value.next_action
    assert "does not parse" not in failed.value.next_action


def test_a_failure_with_no_reason_says_so_and_does_not_point_at_a_message_it_lacks(
    stand_in: StandIn, folder: Path
) -> None:
    stand_in.replies_with("", stderr="   \n", exit_code=1)

    with pytest.raises(GoinspectError) as failed:
        GoinspectClient(stand_in.binary).declarations(folder)

    assert "gave no reason" in str(failed.value)
    assert "what the message names" not in failed.value.next_action


def test_a_helper_killed_by_a_signal_says_so(tmp_path: Path, folder: Path) -> None:
    killed = tmp_path / "killed"
    killed.write_text("#!/bin/sh\nkill -9 $$\n", encoding="utf-8")
    killed.chmod(killed.stat().st_mode | stat.S_IXUSR)

    with pytest.raises(GoinspectError) as failed:
        GoinspectClient(killed).declarations(folder)

    assert "exit -9" in str(failed.value)
    assert "gave no reason" in str(failed.value)
    assert "memory" in failed.value.next_action


NO_PATTERNS: tuple[ExtractionPattern, ...] = ()


def test_a_pattern_goinspect_refuses_is_a_problem_of_the_profile(
    stand_in: StandIn, folder: Path
) -> None:
    refused = 'goinspect: bad patterns: pattern 1 (kind "route"): call does not compile\n'
    stand_in.replies_with("", stderr=refused, exit_code=2)

    with pytest.raises(BadPatternError) as failed:
        GoinspectClient(stand_in.binary).matches(folder, NO_PATTERNS)

    assert isinstance(failed.value, ProfileProblem)
    assert failed.value.exit_code == ExitCode.DISCOVERY_NEEDS_INPUT
    assert 'pattern 1 (kind "route")' in str(failed.value)
    assert "goinspect:" not in str(failed.value)
    assert "pattern" in failed.value.next_action


def test_routes_reports_a_refused_pattern_the_same_way(stand_in: StandIn, folder: Path) -> None:
    stand_in.replies_with("", stderr="goinspect: bad patterns: pattern 2\n", exit_code=2)

    with pytest.raises(BadPatternError):
        GoinspectClient(stand_in.binary).routes(folder, NO_PATTERNS, main_package=".")


@pytest.mark.parametrize("command", ["routes", "fingerprints"])
def test_a_main_package_goinspect_refuses_is_a_problem_of_the_profile(
    stand_in: StandIn, folder: Path, command: str
) -> None:
    refused = "goinspect: bad main_package: ./gone does not load\n"
    stand_in.replies_with("", stderr=refused, exit_code=2)
    client = GoinspectClient(stand_in.binary)

    with pytest.raises(BadPackageError) as failed:
        if command == "routes":
            client.routes(folder, NO_PATTERNS, main_package="./gone")
        else:
            client.fingerprints(folder, ["example.com/x.f"], main_package="./gone")

    assert isinstance(failed.value, ProfileProblem)
    assert failed.value.exit_code == ExitCode.DISCOVERY_NEEDS_INPUT
    assert str(failed.value) == "main_package ./gone does not load"
    assert "main_package" in failed.value.next_action


# The Go runtime exits 2 too. A crash on a repo's file must not reach a model as its own mistake.
@pytest.mark.parametrize(
    "crash",
    [
        "panic: runtime error: index out of range\n\ngoroutine 1 [running]:\n",
        "fatal error: stack overflow\n",
        "",
    ],
    ids=["panic", "fatal error", "silence"],
)
def test_exit_two_without_the_mark_is_a_crash_and_no_problem_of_the_profile(
    stand_in: StandIn, folder: Path, crash: str
) -> None:
    stand_in.replies_with("", stderr=crash, exit_code=2)

    with pytest.raises(GoinspectError) as failed:
        GoinspectClient(stand_in.binary).matches(folder, NO_PATTERNS)

    assert not isinstance(failed.value, ProfileProblem)


def test_the_mark_without_exit_two_is_no_problem_of_the_profile(
    stand_in: StandIn, folder: Path
) -> None:
    stand_in.replies_with("", stderr="goinspect: bad patterns: quoted text\n", exit_code=1)

    with pytest.raises(GoinspectError):
        GoinspectClient(stand_in.binary).matches(folder, NO_PATTERNS)


# The message keeps only the end of a long reason, and the mark is at the start.
def test_the_mark_is_read_from_the_whole_of_a_long_message(stand_in: StandIn, folder: Path) -> None:
    refused = "goinspect: bad main_package: " + "x" * (3 * MESSAGE_TAIL_BYTES) + " end\n"
    stand_in.replies_with("", stderr=refused, exit_code=2)

    with pytest.raises(BadPackageError) as failed:
        GoinspectClient(stand_in.binary).routes(folder, NO_PATTERNS, main_package="./x")

    assert str(failed.value).endswith("end")
    assert len(str(failed.value)) < 2 * MESSAGE_TAIL_BYTES


def test_a_problem_of_the_profile_gives_a_short_clean_message(
    stand_in: StandIn, folder: Path
) -> None:
    stand_in.replies_with("", stderr="goinspect: bad patterns: \x1b[2Jpattern 1\n", exit_code=2)

    with pytest.raises(BadPatternError) as failed:
        GoinspectClient(stand_in.binary).matches(folder, NO_PATTERNS)

    assert "\x1b" not in str(failed.value)


def test_a_failing_helper_gives_a_short_clean_message(stand_in: StandIn, folder: Path) -> None:
    stand_in.replies_with("", stderr=NOISY_STDERR, exit_code=1)

    with pytest.raises(GoinspectError) as failed:
        GoinspectClient(stand_in.binary).declarations(folder)

    assert "the real reason" in str(failed.value)
    assert "\x1b" not in str(failed.value)
    assert len(str(failed.value)) < 2 * MESSAGE_TAIL_BYTES


def test_the_whole_reply_is_kept_up_to_64_megabytes_and_a_longer_one_is_refused() -> None:
    assert OutputLimit(max_bytes=64 * 1024 * 1024, when_longer="fail") == GOINSPECT_REPLY_LIMIT


def test_a_reply_past_the_cap_stops_the_run_and_is_not_cut_into_bad_json(
    monkeypatch: pytest.MonkeyPatch, stand_in: StandIn, folder: Path
) -> None:
    stand_in.replies_with(
        '{"changed": [' + '"a.go", ' * 100 + '"b.go"], "added": [], "removed": []}'
    )
    monkeypatch.setattr(
        goinspect_client, "GOINSPECT_REPLY_LIMIT", OutputLimit(max_bytes=100, when_longer="fail")
    )

    with pytest.raises(ChildOutputTooLongError) as refused:
        GoinspectClient(stand_in.binary).compare(folder, folder)

    assert isinstance(refused.value, ArdeError)
    assert refused.value.next_action


def test_a_relative_helper_path_is_fixed_where_the_client_is_made(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    arde_folder, target_repo = tmp_path / "arde", tmp_path / "repo"
    (arde_folder / "bin").mkdir(parents=True)
    (target_repo / "bin").mkdir(parents=True)
    stand_in = StandIn(arde_folder / "bin")
    stand_in.replies_with('{"declarations": [], "fields": []}')
    planted = target_repo / "bin" / "goinspect"
    planted.write_text(f"#!/bin/sh\ntouch {tmp_path}/planted-helper-ran\n", encoding="utf-8")
    planted.chmod(planted.stat().st_mode | stat.S_IXUSR)
    monkeypatch.chdir(arde_folder)

    reply = GoinspectClient(Path("bin/goinspect")).declarations(target_repo)

    assert reply.declarations == ()
    assert not (tmp_path / "planted-helper-ran").exists()


def test_a_relative_folder_for_the_helper_is_built_where_the_caller_stands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    built_at: list[str] = []

    def recording_build(command: ChildCommand) -> ChildOutcome:
        target = Path(command.argv[command.argv.index("-o") + 1])
        built_at.append(str(target))
        target.write_text("binary", encoding="utf-8")
        return ChildOutcome(exit_code=0, stdout="", stderr="")

    monkeypatch.setattr(goinspect_client, "run_child", recording_build)
    monkeypatch.setattr(goinspect_client, "_built", set())
    monkeypatch.chdir(tmp_path)

    binary = build_goinspect(Path("relative-bin"))

    assert binary == (tmp_path / "relative-bin" / "goinspect").resolve()
    assert built_at == [str(binary)]


class RecordingRun:
    def __init__(self, stdout: str = '{"declarations": [], "fields": []}') -> None:
        self.commands: list[ChildCommand] = []
        self.stdout = stdout

    def __call__(self, command: ChildCommand) -> ChildOutcome:
        self.commands.append(command)
        if "-o" in command.argv:
            Path(command.argv[command.argv.index("-o") + 1]).write_text("binary", encoding="utf-8")
        return ChildOutcome(exit_code=0, stdout=self.stdout, stderr="")


def test_every_call_to_goinspect_runs_with_the_go_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, folder: Path
) -> None:
    recording = RecordingRun()
    monkeypatch.setattr(goinspect_client, "run_child", recording)

    GoinspectClient(tmp_path / "goinspect").declarations(folder)

    [command] = recording.commands
    assert command.env == known_go_environment(folder)


def test_the_build_of_goinspect_runs_with_the_go_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recording = RecordingRun()
    monkeypatch.setattr(goinspect_client, "run_child", recording)
    monkeypatch.setattr(goinspect_client, "_built", set())

    build_goinspect(tmp_path / "bin")

    [command] = recording.commands
    assert command.env == known_go_environment(GOINSPECT_SOURCE)


ROUTES_REPLY = {
    "package_folders": [".", "web"],
    "build_files": ["main.go", "web/router.go"],
    "sites": [
        {
            "kind": "route",
            "callee": "mux.Handle",
            "file": "web/router.go",
            "line": 9,
            "args": {"path": 'prefix + "/x"', "handler": "h.list"},
            "literals": {"path": "/api/x"},
            "handler_id": "(*example.com/depot.desk).list",
            "handler_file": "web/desk.go",
            "handler_line": 30,
        }
    ],
    "stripped_prefixes": [{"file": "web/files.go", "line": 4}],
    "unread_sites": [{"file": "web/router.go", "line": 6, "problem": "builds its path"}],
    "wrapper_calls": [{"kind": "env", "file": "main.go", "line": 7}],
}


def test_routes_hand_over_the_patterns_and_the_main_package_and_read_the_reply(
    stand_in: StandIn, folder: Path
) -> None:
    stand_in.replies_with(json.dumps(ROUTES_REPLY))
    pattern = ExtractionPattern(kind="route", call=r"^app\.get$", args={"path": 0, "handler": 1})

    reply = GoinspectClient(stand_in.binary).routes(folder, [pattern], main_package="./cmd/api")

    assert stand_in.arguments[:2] == ["routes", "--patterns"]
    assert stand_in.arguments[3:] == ["--main", "./cmd/api", str(folder)]
    assert stand_in.file_it_was_given == [pattern.model_dump()]
    assert reply.package_folders == (".", "web")
    assert reply.build_files == ("main.go", "web/router.go")
    [site] = reply.sites
    assert (site.file, site.line, site.literals) == ("web/router.go", 9, {"path": "/api/x"})
    assert site.handler_id == "(*example.com/depot.desk).list"
    assert (site.handler_file, site.handler_line) == ("web/desk.go", 30)
    assert reply.stripped_prefixes == (SourcePlace(file="web/files.go", line=4),)
    assert reply.unread_sites == (
        UnreadSite(file="web/router.go", line=6, problem="builds its path"),
    )
    assert reply.wrapper_calls == (WrapperCall(kind="env", file="main.go", line=7),)


def test_a_site_with_no_root_reads_as_none(stand_in: StandIn, folder: Path) -> None:
    unresolved = {
        **ROUTES_REPLY["sites"][0],  # type: ignore[index]
        "handler_id": None,
        "handler_file": None,
        "handler_line": None,
    }
    stand_in.replies_with(json.dumps({**ROUTES_REPLY, "sites": [unresolved]}))

    reply = GoinspectClient(stand_in.binary).routes(folder, [], main_package=".")

    assert (reply.sites[0].handler_id, reply.sites[0].handler_line) == (None, None)


def test_a_routes_reply_with_no_package_folders_is_refused(stand_in: StandIn, folder: Path) -> None:
    stand_in.replies_with(json.dumps({**ROUTES_REPLY, "package_folders": None}))

    with pytest.raises(GoinspectError, match="package_folders"):
        GoinspectClient(stand_in.binary).routes(folder, [], main_package=".")


def test_a_routes_reply_with_a_problem_arde_does_not_know_is_refused(
    stand_in: StandIn, folder: Path
) -> None:
    unknown = [{"file": "web/router.go", "line": 6, "problem": "something new"}]
    stand_in.replies_with(json.dumps({**ROUTES_REPLY, "unread_sites": unknown}))

    with pytest.raises(GoinspectError, match="unread_sites"):
        GoinspectClient(stand_in.binary).routes(folder, [], main_package=".")


def test_a_declaration_reads_as_the_id_changed_declarations_uses() -> None:
    reply = Declaration(file="web/a.go", kind="func", name="server.quote", hash="h", line=3)

    assert reply.id == "web/a.go::func server.quote"
