"""The interfaces the application needs from the outside world. Adapters implement them."""

import re
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import AfterValidator, BaseModel, Field

from arde.application.models_config import RoleConfig
from arde.application.probe_log import ProbeLog
from arde.domain.base import CodeIdentifier, Frozen, MainFile, SingleLine
from arde.domain.errors import ArdeError
from arde.domain.facts import ProbeRecord, Wiki
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


# Rendering. A later run renders the same repo again, so the render says what it wrote, for that
# run to replace, and what it changed, for the checks to tell its lines from anyone else's.


class RenderManifest(Frozen):
    """The comment lines ARDE wrote, by file from the repo root, without their indentation.

    The pipeline keeps it between runs, because a comment written above a field carries no mark
    that tells it from one a person wrote.
    """

    lines: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class RenderRequest(Frozen):
    repo: Path
    wiki: Wiki
    inventory: Inventory
    # What the last run wrote, which this one removes before it writes.
    manifest: RenderManifest
    # The file that holds the service's entry point, where the API's own annotations go.
    main_file: MainFile
    title: SingleLine


class RenderResult(Frozen):
    """What the render changed, measured on each file by comparing it before and after.

    Lines are compared with their spacing collapsed, so a line that only moved or was realigned
    counts as unchanged.
    """

    # By file, every comment line the file gained.
    added_lines: dict[str, tuple[str, ...]]
    # By file, every line of code the file gained. ARDE changes code only by setting its own tags
    # on fields, so each is a field's line with its new tag.
    tag_changes: dict[str, tuple[str, ...]]
    changed_files: tuple[str, ...]


class RenderOutcome(Frozen):
    result: RenderResult
    # What this run wrote, for the next run to remove.
    manifest: RenderManifest


# Evidence. Anyone can write anything in a comment or a string literal, planted instructions
# included, so a quote is evidence only where it holds code.

# "not source" is a file whose syntax the adapter does not read, such as a README, and "outside
# the build" a source file the service is not built from, such as test data.
QuoteContent = Literal["code", "comment", "string literal", "not source", "outside the build"]


class SourceQuote(Frozen):
    """Where a quote appears in one file of the repo."""

    # From the repo root. Its name says which syntax the file is written in.
    file: str
    # The file's text, with each CRLF line ending read as LF.
    source: str
    # Each place the quote appears, as start and end offsets into source.
    places: tuple[tuple[int, int], ...]
    # The files the inventory's declarations name, which are the files the service is built from.
    build_files: frozenset[str]


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

    def render_annotations(self, request: RenderRequest) -> RenderOutcome:
        """Writes into the repo the annotations and tags that the wiki and the inventory call for.

        It first removes what the last run wrote, so rendering the same facts again changes
        nothing, and an annotation whose fact is gone goes with it. A route whose handler has no
        single function to carry an annotation gets none. Only ordinary comment lines and the
        language's allowed tags are added.
        """
        ...

    def run_checks(self, repo: Path, profile: RepoProfile) -> CheckResult: ...

    def built_service(self, repo: Path, profile: RepoProfile) -> AbstractContextManager[Path]:
        """Builds the service and hands out the binary, in a new folder outside the repo.

        The folder is removed when the body ends, whether it returns, raises or is interrupted.
        """
        ...

    def generate_spec(self, repo: Path, profile: RepoProfile) -> Path:
        """Writes the repo's OpenAPI file from its annotations, and returns the path of its JSON.

        The YAML form is written beside it, and nothing else in the repo changes. A tool that
        cannot read the annotations raises an ArdeError that quotes the end of what it printed.
        """
        ...

    def quote_content(self, quote: SourceQuote) -> QuoteContent:
        """What the quote holds, read with the file's syntax.

        It is code when any of its places has something outside every comment and string literal,
        else a comment or a string literal. It is "not source" in a file whose syntax the adapter
        does not read, and "outside the build" in a source file that build_files does not name.
        It reads only the text it is given, so it opens no file and needs no gate.
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
    """The service would not stop, or something still holds its port after the stop."""


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


# Printable ASCII with no space, so nothing in it can end the request line or start a header.
_PATH_AND_QUERY = re.compile(r"/[!-~]*")


def _require_path_and_query(text: str) -> str:
    if _PATH_AND_QUERY.fullmatch(text) is None:
        raise ValueError(
            "must be a path and query that starts with /, such as /items?page=2, with no scheme "
            "or host, and with every space, control character and character outside ASCII "
            "percent-encoded"
        )
    return text


class HttpRequest(Frozen):
    """One request to a running service, which a model may have chosen.

    The address is the service's own, so only the path and query come from here. GET and HEAD
    alone, because a request must not change what the service holds.
    """

    method: Literal["GET", "HEAD"]
    path_and_query: Annotated[str, AfterValidator(_require_path_and_query)]


# Lives with the port, not the adapter, because application code catches it.
class ProbeFailed(ServiceError):
    """No usable reply came back.

    There was no connection or no answer in time, the reply was not HTTP, or its body was too
    large to record.
    """


class ServiceClient(Protocol):
    def send_request(self, service: ServiceHandle, request: HttpRequest) -> ProbeRecord:
        """Sends one request to the service and returns what came back, the whole body included.

        Raises ProbeFailed when no usable reply came back. A redirect is returned, not followed.
        """
        ...


class AgentToolbox(Protocol):
    """The tools an agent may call.

    They read the repo and probe the service, and none of them writes a file or runs a command.
    """

    def repo_tools(self, root: Path) -> tuple[Tool, ...]:
        """list_files, read_file and grep, on the files inside root alone."""
        ...

    def http_probe_tool(self, service: ServiceHandle, log: ProbeLog) -> Tool:
        """http_probe, which sends GET and HEAD to that service alone and records each reply."""
        ...


class RunStore(Protocol):
    """Where one target repo's state lives between runs, and where each run keeps its copies.

    The profile, wiki and section stores read and write the paths it names. Naming a path creates
    nothing: the store that writes a file makes its folder.
    """

    @property
    def profile_path(self) -> Path: ...

    @property
    def override_path(self) -> Path: ...

    @property
    def wiki_dir(self) -> Path: ...

    @property
    def sections_path(self) -> Path: ...

    @property
    def manifest_path(self) -> Path: ...

    @property
    def snapshot_dir(self) -> Path:
        """The tree as it stood after the last successful run. It exists once has_snapshot is."""
        ...

    def new_run_dir(self) -> Path:
        """A new, empty folder for one run: its copies, trace and logs."""
        ...

    def input_copy(self, run_dir: Path) -> Path:
        """Where the run's copy of the repo as it was goes. The copy step creates it."""
        ...

    def output_copy(self, run_dir: Path) -> Path:
        """Where the run's copy with ARDE's edits goes. The copy step creates it."""
        ...

    def has_snapshot(self) -> bool: ...

    def save_snapshot(self, tree: Path) -> None:
        """Makes tree the snapshot in one rename, moving it, and only after a run succeeded.

        The last snapshot stays whole until that rename, so a failure before it keeps it.
        """
        ...
