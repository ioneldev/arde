"""What discovery learns about how to build, run and read one repo."""

from typing import Literal

from arde.domain.base import Frozen


class ExtractionPattern(Frozen):
    kind: Literal["route", "param", "error", "env"]
    call: str
    args: dict[str, int]


class RepoProfile(Frozen):
    main_package: str
    main_file: str
    port_env: str
    patterns: tuple[ExtractionPattern, ...]
    reported_test_commands: tuple[str, ...] = ()
    # Only the override file may fill this. A command that discovery could set would let text
    # from the repo choose what the tool runs.
    extra_test_commands: tuple[str, ...] = ()
