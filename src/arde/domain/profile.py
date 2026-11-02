"""What discovery learns about how to build, run and read one repo."""

from typing import Literal, Self

from pydantic import model_validator

from arde.domain.base import Frozen, MainFile, MainPackage

# The argument names that code reads for each kind. A pattern without one could match a call and
# give nothing from it, every time, and look like a repo without that fact.
REQUIRED_ARGUMENTS: dict[str, frozenset[str]] = {
    "route": frozenset({"path", "handler"}),
    "param": frozenset({"name"}),
    "env": frozenset({"name"}),
    "error": frozenset({"code", "status"}),
}


class ExtractionPattern(Frozen):
    kind: Literal["route", "param", "error", "env"]
    call: str
    args: dict[str, int]

    @model_validator(mode="after")
    def _name_the_arguments_the_kind_needs(self) -> Self:
        missing = sorted(REQUIRED_ARGUMENTS[self.kind] - self.args.keys())
        if missing:
            raise ValueError(
                f"a {self.kind} pattern must give a position for {', '.join(missing)} in args"
            )
        return self


class DiscoveredProfile(Frozen):
    """What the discovery agent may set: its submit schema, and what profile.yaml holds."""

    schema_version: Literal[1] = 1
    main_package: MainPackage
    main_file: MainFile
    port_env: str
    patterns: tuple[ExtractionPattern, ...]
    reported_test_commands: tuple[str, ...] = ()


class RepoProfile(DiscoveredProfile):
    """A discovered profile plus what only the owner may add. Only merge_override builds one."""

    # A separate type, so no schema the model is given can even name this. A command discovery
    # could set would let text from the repo choose what the tool runs.
    extra_test_commands: tuple[str, ...] = ()
