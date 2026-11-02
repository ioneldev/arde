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


# Lives with the port, not the adapter, for the same reason as ProviderError.
class ProfileProblem(ArdeError):
    """The profile does not fit the repo, and a better profile could. Discovery may correct it.

    Any other ArdeError from an adapter means the repo or the machine is broken, which no value in
    a profile can fix, so it stops the run and never goes back to a model as its own mistake.
    """

    def __init__(self, message: str, *, next_action: str) -> None:
        super().__init__(message, next_action=next_action, exit_code=ExitCode.DISCOVERY_NEEDS_INPUT)


class ToolOutcome(Frozen):
    content: str
    is_error: bool = False


class Tool(Frozen):
    definition: ToolDef
    handler: Callable[[dict[str, object]], ToolOutcome]


class CheckResult(Frozen):
    """What the language's own checks measured about a repo. A failed check is data, not an error.

    test_ok says that no test failed, which also holds when none ran. tests_run says how many did.
    """

    build_ok: bool
    vet_ok: bool
    test_ok: bool
    # Readable text. A test chooses every byte of it, so code decides from the fields around it.
    output: str
    # Top-level tests that ran to a pass or a fail. Zero is a valid answer, and worth a warning.
    tests_run: int
    # Each test and subtest that skipped, as package.name, one line each.
    tests_skipped: tuple[str, ...]
    # Every test, benchmark, fuzz test and example the toolchain lists, which need not have run.
    # None when a test package does not compile, because a count of the rest would read as a total.
    tests_listed: int | None
    # Files the build leaves out, such as a test file behind a build tag or one for another
    # platform, as paths from the repo root. A file that leads out of the repo is not listed.
    ignored_files: tuple[str, ...]
    # Folders from the repo root that hold a module of their own. No check above reached them.
    unchecked_modules: tuple[str, ...]


# Why a route has no hash: its handler is written in place, several functions share its key, no
# function of the service is behind it, or it reaches code with no body to read.
UnhashedReason = Literal["inline", "shared key", "no function", "no body"]


class RouteHashes(Frozen):
    # Keyed by Route.key. A key in neither map was not asked about.
    hashes: dict[str, str]
    unhashed: dict[str, UnhashedReason]


class LanguageAdapter(Protocol):
    """Everything ARDE knows about one language. Each method holds an idiom of that language.

    Methods for rendering annotations, generating the spec and checking agent edits arrive with
    the tasks that implement them, so every method here has an implementation.

    Every method that takes a repo raises an ArdeError, before it reads anything, when a link in
    that repo leads out of it.
    """

    def detects(self, repo: Path) -> bool: ...

    def discovery_guidance(self) -> str:
        """What the discovery agent must know about the language to fill in a profile.

        The same text every time, with nothing from any repo in it.
        """
        ...

    def is_comment_only(self, old_file: Path, new_file: Path) -> bool:
        """True when the files differ only in ordinary comments and the annotations ARDE adds.

        Each adapter holds an allow-list of what that means in its language, and anything else
        counts as real. A file that is missing, unreadable, too large to read or not source code
        answers False.
        """
        ...

    def changed_declarations(self, old_repo: Path, new_repo: Path) -> list[str]:
        """Ids, as file::kind name, of the declarations added, removed or changed. Sorted."""
        ...

    def extract_inventory(self, repo: Path, profile: RepoProfile) -> Inventory:
        """What the service built from main_package holds. The repo must compile.

        Raises a ProfileProblem when the repo has no route, a route it cannot read, or a pattern or
        main_package that cannot be used.
        """
        ...

    def route_fingerprints(
        self, repo: Path, routes: Sequence[Route], *, main_package: str
    ) -> RouteHashes:
        """A hash per route, by Route.key, and the reason for each route that has none.

        The caller counts a route with no hash as changed.
        """
        ...

    def run_checks(self, repo: Path, profile: RepoProfile) -> CheckResult: ...

    def built_service(self, repo: Path, profile: RepoProfile) -> AbstractContextManager[Path]:
        """Builds the service and hands out the binary, in a new folder outside the repo.

        The folder is removed when the body ends, whether it returns, raises or is interrupted.
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
