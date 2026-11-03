"""Whether a profile fits the repo: code reads the repo with it, then builds and runs the service.

Discovery's submit check reads the repo the same way, so a profile it accepts fits here too, up to
the running service, which only this module starts.
"""

import re
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from arde.application.pipeline_deps import PipelineDeps
from arde.application.ports import (
    HttpRequest,
    LanguageAdapter,
    ProbeFailed,
    ProfileProblem,
    ServiceClient,
    ServiceConfig,
    ServiceHandle,
    ServiceStartError,
)
from arde.domain.base import Frozen, printable_name
from arde.domain.errors import ArdeError
from arde.domain.facts import ProbeRecord
from arde.domain.findings import Finding
from arde.domain.inventory import Inventory, Route
from arde.domain.profile import RepoProfile
from arde.domain.report import ExitCode

# Some ninety times the inventory of the largest real service measured, 12 KB. Explore carries
# the inventory on every turn, so one far past this spends the run's budget on reading it, and a
# repo can be written to produce one.
MAX_INVENTORY_BYTES = 1024 * 1024

# Several segments, so a route with one wildcard segment, such as /{slug}, cannot serve it.
MISSING_PATH = "/arde-path-that-cannot-exist/7c1d/9e"
# What a wildcard of a route's path is requested with: a value that a number and a name accept.
_WILDCARD = re.compile(r"\{[^{}]*\}")
_WILDCARD_VALUE = "1"
# A path that cannot exist falls under / in many routers, so the two requests would reach one
# handler, and their answers being alike would say nothing.
_ROOT = "/"


class ProfileCandidate(Frozen):
    repo: Path
    # With the override applied: the profile the run would use.
    profile: RepoProfile
    # Where the service's own output goes while it runs.
    service_log: Path


class MainFileNotInTheService(ProfileProblem):
    def __init__(self, profile: RepoProfile) -> None:
        super().__init__(
            f"main_file {printable_name(profile.main_file)} is not a file of the service built "
            f"from main_package {profile.main_package}: none of the service's declarations is in "
            "it.",
            next_action=(
                "Set main_file to the file of main_package that holds the service's entry point, "
                "as a path from the repo root."
            ),
        )


class InventoryTooLarge(ArdeError):
    def __init__(self, size: int) -> None:
        super().__init__(
            f"The service's inventory is {size} bytes as JSON, past the {MAX_INVENTORY_BYTES} "
            "ARDE shows an agent.",
            next_action=(
                "Point main_package at a smaller service in the profile override file, or report "
                "the size of this one, then run again."
            ),
            exit_code=ExitCode.BAD_USAGE,
        )


def read_inventory(repo: Path, profile: RepoProfile, *, adapter: LanguageAdapter) -> Inventory:
    """What the profile reads from the repo. Raises a ProfileProblem when it does not fit."""
    inventory = adapter.extract_inventory(repo, profile)
    if not _is_a_file_of_the_service(profile.main_file, inventory):
        raise MainFileNotInTheService(profile)
    return inventory


# Inventory.declarations names each one as file::kind name, with the file from the repo root.
# A backslash is not taken for a slash, because the tools that are later handed main_file read it
# as part of a name.
def _is_a_file_of_the_service(main_file: str, inventory: Inventory) -> bool:
    listed = PurePosixPath(main_file).as_posix()
    return any(declaration.startswith(f"{listed}::") for declaration in inventory.declarations)


def validate_profile(candidate: ProfileCandidate, deps: PipelineDeps) -> list[Finding]:
    """What is wrong with the profile on the repo and on the running service, for Discovery.

    The build comes first, because a package that is not a service can still be read. A profile
    that does not fit and a service that does not start come back as findings. Any other error
    stops the run, and so does a service that dies while it is used.
    """
    repo, profile = candidate.repo, candidate.profile
    try:
        with deps.adapter.built_service(repo, profile) as binary:
            inventory = read_inventory(repo, profile, adapter=deps.adapter)
            refuse_an_oversized(inventory)
            with deps.service_runner.running(_service_config(candidate, binary=binary)) as service:
                return _routes_that_answer_like_a_missing_path(
                    inventory.routes, service=service, client=deps.service_client
                )
    except ProfileProblem as problem:
        return [_profile_finding(problem)]
    except ServiceStartError as error:
        return [_start_finding(error, profile=profile)]


def refuse_an_oversized(inventory: Inventory) -> None:
    """Raises InventoryTooLarge when the inventory is past what ARDE shows an agent."""
    size = len(inventory.model_dump_json().encode("utf-8"))
    if size > MAX_INVENTORY_BYTES:
        raise InventoryTooLarge(size)


def _service_config(candidate: ProfileCandidate, *, binary: Path) -> ServiceConfig:
    return ServiceConfig(
        binary=binary,
        cwd=candidate.repo,
        port_env=candidate.profile.port_env,
        log=candidate.service_log,
    )


def _profile_finding(problem: ProfileProblem) -> Finding:
    return Finding(
        check="profile_fits_the_repo",
        owner="discovery",
        subject="profile",
        message=str(problem),
        next_action=problem.next_action,
    )


# The port variable or the package is the likely fault, and the runner's message says which part
# failed: the service exited, never listened, or could not be run.
def _start_finding(error: ServiceStartError, *, profile: RepoProfile) -> Finding:
    return Finding(
        check="service_starts",
        owner="discovery",
        subject=profile.main_package,
        message=str(error),
        next_action=error.next_action,
    )


def _routes_that_answer_like_a_missing_path(
    routes: tuple[Route, ...], *, service: ServiceHandle, client: ServiceClient
) -> list[Finding]:
    missing = _get(MISSING_PATH, service=service, client=client)
    answers: dict[str, ProbeRecord | None] = {}
    findings: list[Finding] = []
    for route in routes:
        if route.path == _ROOT:
            continue
        path = _request_path(route)
        if path not in answers:
            answers[path] = _answer_or_none(path, service=service, client=client)
        answer = answers[path]
        if answer is not None and _alike(answer, missing):
            findings.append(_answers_like_a_missing_path(route, path=path, status=answer.status))
    return findings


def _request_path(route: Route) -> str:
    return quote(_WILDCARD.sub(_WILDCARD_VALUE, route.path), safe="/")


def _get(path: str, *, service: ServiceHandle, client: ServiceClient) -> ProbeRecord:
    return client.send_request(service, HttpRequest(method="GET", path_and_query=path))


# A route that gives no usable answer, such as one that streams until the time limit, answers
# unlike a missing path, which did answer.
def _answer_or_none(
    path: str, *, service: ServiceHandle, client: ServiceClient
) -> ProbeRecord | None:
    try:
        return _get(path, service=service, client=client)
    except ProbeFailed:
        return None


# A route's own not-found reply, such as a JSON body naming an unknown id, differs from the one a
# path nothing serves gets, so the whole reply is compared and not the status alone.
def _alike(answer: ProbeRecord, missing: ProbeRecord) -> bool:
    return (answer.status, answer.content_type, answer.body) == (
        missing.status,
        missing.content_type,
        missing.body,
    )


def _answers_like_a_missing_path(route: Route, *, path: str, status: int) -> Finding:
    return Finding(
        check="routes_answer_live",
        owner="discovery",
        subject=route.key,
        message=(
            f"{route.key}, requested as GET {path}, answered with status {status}, exactly as "
            f"{MISSING_PATH} does, a path no service has. Nothing may serve it, as with a route "
            "on a mux the service never serves, or under a prefix the profile does not read."
        ),
        next_action=(
            "Find where the service serves this route. Under a prefix or on a mux nothing "
            "serves, it cannot be documented yet: report that. If the route is real and answers "
            "this one request as a missing path, as a lookup of an unknown id can, this is a "
            "false alarm."
        ),
    )
