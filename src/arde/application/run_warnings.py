"""What a run measured that does not stop it, but that a reader must know, one line each.

Names come from the repo, so each is escaped, and each list shows its first items and counts the
rest, because a repo can hold any number of them.
"""

from collections.abc import Iterator, Sequence

from arde.application.ports import CheckResult, RouteHashes
from arde.domain.base import printable_name
from arde.domain.inventory import Inventory

LISTED_AT_MOST = 50


def baseline_warnings(result: CheckResult) -> Iterator[str]:
    """What the repo's own checks leave unproven, measured before ARDE changed anything."""
    if result.tests_run == 0:
        yield (
            "The repo's own tests ran no test, so they prove nothing about its behaviour, and "
            "neither does their passing after ARDE's edits."
        )
    if result.tests_skipped:
        yield f"{len(result.tests_skipped)} tests skipped: {_listed(result.tests_skipped)}"
    if result.tests_listed is not None and result.tests_run < result.tests_listed:
        yield (
            f"Only {result.tests_run} of the {result.tests_listed} tests the toolchain lists ran "
            "to a pass or a fail. Benchmarks and examples count among those listed, and so does "
            "a test that ended the whole run early with exit 0."
        )
    if result.ignored_files:
        yield (
            "The build leaves out these files, so no check reads them: "
            f"{_listed(result.ignored_files)}"
        )
    if result.unchecked_modules:
        yield (
            "These folders hold a module of their own, which no check reached: "
            f"{_listed(result.unchecked_modules)}"
        )


def inventory_warnings(inventory: Inventory, hashes: RouteHashes) -> Iterator[str]:
    """What the profile found and could not use, and the routes a later run cannot compare."""
    yield from _each("Found but gave nothing", inventory.dropped)
    yield from _each("A pattern of the profile matched nothing", inventory.unmatched_patterns)
    for key, reason in sorted(hashes.unhashed.items()):
        yield (
            f"{printable_name(key)} has no fingerprint ({reason}), so every later run counts it "
            "as changed."
        )
    for route in inventory.routes:
        if route.shares_anchor_with:
            others = ", ".join(printable_name(other) for other in route.shares_anchor_with)
            yield (
                f"{printable_name(route.key)} shares its anchor with {others}, so it gets no "
                "comment block: one block would merge their operations in the spec."
            )
        elif route.handler_id is None:
            yield (
                f"{printable_name(route.key)} has no single function to carry its comment block, "
                "so the spec leaves it out. Its facts still reach the docs."
            )


def _each(title: str, lines: Sequence[str]) -> Iterator[str]:
    for line in lines[:LISTED_AT_MOST]:
        yield f"{title}: {printable_name(line)}"
    if len(lines) > LISTED_AT_MOST:
        yield f"{title}: and {len(lines) - LISTED_AT_MOST} more"


def _listed(names: Sequence[str]) -> str:
    shown = ", ".join(printable_name(name) for name in names[:LISTED_AT_MOST])
    rest = len(names) - LISTED_AT_MOST
    return f"{shown}, and {rest} more" if rest > 0 else shown
