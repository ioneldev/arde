"""What discovery learns about how to build, run and read one repo."""

from typing import Literal

from arde.domain.base import Frozen, MainPackage, RepoRelativePath


class ExtractionPattern(Frozen):
    kind: Literal["route", "param", "error", "env"]
    call: str
    args: dict[str, int]


class DiscoveredProfile(Frozen):
    """What the discovery agent may set: its submit schema, and what profile.yaml holds."""

    schema_version: Literal[1] = 1
    main_package: MainPackage
    main_file: RepoRelativePath
    port_env: str
    patterns: tuple[ExtractionPattern, ...]
    reported_test_commands: tuple[str, ...] = ()


class RepoProfile(DiscoveredProfile):
    """A discovered profile plus what only the owner may add. Only merge_override builds one."""

    # A separate type, so no schema the model is given can even name this. A command discovery
    # could set would let text from the repo choose what the tool runs.
    extra_test_commands: tuple[str, ...] = ()
