"""The interfaces the application needs from the outside world. Adapters implement them."""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from arde.application.models_config import RoleConfig
from arde.domain.base import CodeIdentifier, Frozen
from arde.domain.errors import ArdeError
from arde.domain.inventory import Inventory, Route
from arde.domain.profile import RepoProfile
from arde.domain.report import ExitCode
from arde.domain.usage import Usage


class Tracer(Protocol):
    """Writes the run's record: structured events for the trace, free text for the debug log.

    Event kinds: run_start, step_start, step_end, model_call, model_error, tool_call, correction,
    agent_end, check_result, repair, warning and run_end. The tracer stamps each event, so callers
    pass no time. A field named summary may be any length: the tracer cuts it, after redaction.
    """

    def event(self, kind: str, **fields: object) -> None: ...

    def debug(self, text: str) -> None: ...


# No default for strict: the API refuses some schemas in strict mode, so each tool decides.
class ToolDef(Frozen):
    name: str
    description: str
    input_schema: dict[str, object]
    strict: bool


class ToolCall(Frozen):
    id: str
    name: str
    input: dict[str, object]


class LLMRequest(Frozen):
    config: RoleConfig
    system: str
    messages: tuple[dict[str, object], ...]
    tools: tuple[ToolDef, ...]


ModelStop = Literal[
    "end_turn",
    "tool_use",
    "stop_sequence",
    "max_tokens",
    "refusal",
    "model_context_window_exceeded",
]


class Refusal(Frozen):
    """Why the API refused a turn. Either part may be absent, because the API may not name one."""

    category: str | None
    explanation: str | None


class PaidCall(Frozen):
    """What the API reported for one call. A call that failed afterwards was still paid for."""

    usage: Usage
    latency_s: float
    # Plain text here, because a failed call may have ended on a reason the loop cannot act on.
    stop_reason: str | None
    text: str = ""
    request_id: str | None = None
    refusal: Refusal | None = None


class LLMResponse(PaidCall):
    stop_reason: ModelStop
    tool_calls: tuple[ToolCall, ...]
    # Sent back unchanged on the next turn, because the API checks thinking blocks byte for byte.
    assistant_content: tuple[object, ...]


# Lives with the port, not the adapter, because application code catches it and may not import
# an adapter.
class ProviderError(ArdeError):
    """The model provider failed in a way the run cannot work around.

    paid_call holds what the API had reported before the failure, for the caller to charge. It is
    None only when the API reported nothing that can be charged.
    """

    def __init__(
        self,
        message: str,
        *,
        next_action: str,
        exit_code: ExitCode = ExitCode.CHECKS_FAILED,
        paid_call: PaidCall | None = None,
    ) -> None:
        super().__init__(message, next_action=next_action, exit_code=exit_code)
        self.paid_call = paid_call


class ParseFailed(ProviderError):
    """The reply came back whole but holds no usable value."""

    def __init__(self, message: str, *, next_action: str, response: LLMResponse) -> None:
        super().__init__(message, next_action=next_action, paid_call=response)
        self.usage = response.usage


class LLMClient(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...

    def parse[T: BaseModel](
        self, request: LLMRequest, schema: type[T]
    ) -> tuple[T, LLMResponse]: ...


class ToolOutcome(Frozen):
    content: str
    is_error: bool = False


class Tool(Frozen):
    definition: ToolDef
    handler: Callable[[dict[str, object]], ToolOutcome]


class CheckResult(Frozen):
    """What the language's own checks said about a repo. A failed check is data, not an error."""

    build_ok: bool
    vet_ok: bool
    test_ok: bool
    output: str
    # Every test, benchmark, fuzz test and example the toolchain lists. Zero is a valid answer.
    test_count: int
    # Files the build leaves out, such as a test file behind a build tag or one for another
    # platform, as paths from the repo root.
    ignored_files: tuple[str, ...]


class LanguageAdapter(Protocol):
    """Everything ARDE knows about one language. Each method holds an idiom of that language.

    Methods for rendering annotations, generating the spec and checking agent edits arrive with
    the tasks that implement them, so every method here has an implementation.

    Every method that takes a repo raises an ArdeError, before it reads anything, when a link in
    that repo leads out of it.
    """

    def detects(self, repo: Path) -> bool: ...

    def is_comment_only(self, old_file: Path, new_file: Path) -> bool:
        """True when the files differ only in ordinary comment lines and the tags ARDE adds.

        Those tags are `oneOf` and `binding:"optional"`, and the syntax trees must also be equal.
        An ordinary comment is a whole line that starts with `//` and a space and is no directive.
        Everything else counts as real: a block comment, a trailing comment, a blank line, a
        directive, and any comment edit in a file with C source or a test with an example. A
        file that is missing, unreadable or not source code answers False.
        """
        ...

    def changed_declarations(self, old_repo: Path, new_repo: Path) -> list[str]:
        """Ids, as file::kind name, of the declarations added, removed or changed. Sorted."""
        ...

    def extract_inventory(self, repo: Path, profile: RepoProfile) -> Inventory:
        """Raises an ArdeError when the repo has no route, or a route it cannot read."""
        ...

    def route_fingerprints(self, repo: Path, routes: Sequence[Route]) -> dict[str, str]:
        """A hash per route, keyed "METHOD /path". A route with no hash is left out of the result.

        A route is left out when its handler is not a plain name or cannot be followed, and the
        caller counts a missing route as changed.
        """
        ...

    def run_checks(self, repo: Path, profile: RepoProfile) -> CheckResult: ...

    def build_service(self, repo: Path, profile: RepoProfile) -> Path:
        """Builds the service and returns the binary, in a new folder outside the repo.

        The caller removes that folder, which is the parent of the path returned.
        """
        ...


class ServiceConfig(Frozen):
    """How to start one service and where to look when it will not start."""

    binary: Path
    # A service that reads a file by a relative path must start in its own repo, so the runner
    # never falls back on the folder ARDE happens to run in.
    cwd: Path
    # It comes from a model, so it is checked before it names an environment variable.
    port_env: CodeIdentifier
    log: Path
    timeout_s: float = Field(default=10, gt=0, allow_inf_nan=False)


class ServiceHandle(Frozen):
    base_url: str
    port: int


# These live with the port, not the adapter, because application code catches them and may not
# import an adapter.
class ServiceError(ArdeError):
    def __init__(self, message: str, *, next_action: str) -> None:
        super().__init__(message, next_action=next_action, exit_code=ExitCode.CHECKS_FAILED)


class ServiceStartError(ServiceError):
    """The service did not open its port: it exited, it never listened, or it could not be run."""


class ServiceDiedError(ServiceError):
    """The service opened its port and then exited by itself, while the run was still using it."""


class ServiceStopError(ServiceError):
    """Something of the service is still running after the stop, or still holds its port."""


class ServiceRunner(Protocol):
    def running(self, config: ServiceConfig) -> AbstractContextManager[ServiceHandle]:
        """Starts the service, waits until its port accepts a connection, and hands out its address.

        Raises ServiceStartError before the body runs when the service cannot be started. The
        service is stopped when the body ends, whether it returns, raises or is interrupted.
        A service that exited by itself while the body ran raises ServiceDiedError, and one that
        cannot be stopped raises ServiceStopError. Neither replaces an error already on its way
        out: it is attached to that error as a note.
        """
        ...
