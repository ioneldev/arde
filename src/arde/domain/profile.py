"""What discovery learns about how to build, run and read one repo."""

from typing import Literal, Self

from pydantic import model_validator

from arde.domain.base import CodeIdentifier, Frozen, MainFile, MainPackage

# The argument names that code must read for each kind. A pattern without one could match a call
# and give nothing from it, every time, and look like a repo without that fact. A name that is
# neither required nor optional would be ignored, which reads as something it never gave ARDE.
REQUIRED_ARGUMENTS: dict[str, frozenset[str]] = {
    "route": frozenset({"path", "handler"}),
    "param": frozenset({"name"}),
    "env": frozenset({"name"}),
    "error": frozenset({"code", "status"}),
}
# A route pattern may name the method too, for a function that takes it as an argument. Code reads
# it at every call the pattern matches, and a method that is not a constant stops the run.
OPTIONAL_ARGUMENTS: dict[str, frozenset[str]] = {"route": frozenset({"method"})}


class ExtractionPattern(Frozen):
    kind: Literal["route", "param", "error", "env"]
    call: str
    args: dict[str, int]

    @model_validator(mode="after")
    def _name_exactly_the_arguments_the_kind_reads(self) -> Self:
        required = REQUIRED_ARGUMENTS[self.kind]
        read = required | OPTIONAL_ARGUMENTS.get(self.kind, frozenset())
        missing = sorted(required - self.args.keys())
        if missing:
            raise ValueError(
                f"a {self.kind} pattern must give a position for {', '.join(missing)} in args"
            )
        unknown = sorted(self.args.keys() - read)
        if unknown:
            raise ValueError(
                f"a {self.kind} pattern reads only {', '.join(sorted(read))}: remove "
                f"{', '.join(unknown)} from args"
            )
        return self


class DiscoveredProfile(Frozen):
    """What the discovery agent may set: its submit schema, and what profile.yaml holds."""

    schema_version: Literal[1] = 1
    main_package: MainPackage
    main_file: MainFile
    # A model chooses it, and it names an environment variable, so it is checked where it is set.
    port_env: CodeIdentifier
    patterns: tuple[ExtractionPattern, ...]
    reported_test_commands: tuple[str, ...] = ()


class RepoProfile(DiscoveredProfile):
    """A discovered profile plus what only the owner may add. Only merge_override builds one."""

    # A separate type, so no schema the model is given can even name this. A command discovery
    # could set would let text from the repo choose what the tool runs.
    extra_test_commands: tuple[str, ...] = ()
