"""The repo profile between runs: profile.yaml as Discovery wrote it, and the owner's override.

The override is profile.override.yaml, written by hand. Each key it holds replaces the discovered
value, and it is the one place extra_test_commands can come from.
"""

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import ValidationError

from arde.application.ports import RunStore
from arde.domain.base import describe_problems, printable_name
from arde.domain.errors import ArdeError
from arde.domain.profile import DiscoveredProfile, RepoProfile
from arde.domain.report import ExitCode

_SCHEMA_VERSION = DiscoveredProfile.model_fields["schema_version"].default
_DISCOVERED_FIELDS = set(DiscoveredProfile.model_fields)

# Every value of a profile is checked on its own, so an override that fits one valid profile fits
# them all. Checking it against this one finds a bad override before any model is paid for.
_ANY_VALID_PROFILE = DiscoveredProfile(
    main_package=".", main_file="main", port_env="PORT", patterns=()
)


class ProfileFileError(ArdeError):
    def __init__(self, source: str, problem: str, *, next_action: str) -> None:
        super().__init__(
            f"Cannot use {source}: {problem}", next_action=next_action, exit_code=ExitCode.BAD_USAGE
        )


def _fix_the_profile(store: RunStore) -> str:
    return (
        f"Delete {store.profile_path}, and the next run discovers the profile again. To correct "
        f"one value, write it in {store.override_path} instead, which ARDE never writes."
    )


def _fix_the_override(source: str) -> str:
    return (
        f"Fix {source}: it holds the keys of a profile that the owner sets by hand, plus "
        "extra_test_commands. Then run again."
    )


def load_profile(store: RunStore) -> RepoProfile | None:
    """The saved profile with the override applied, or None when the next run is a first run."""
    saved = _read_mapping(store.profile_path, next_action=_fix_the_profile(store))
    if saved is None or saved.get("schema_version") != _SCHEMA_VERSION:
        return None
    try:
        discovered = DiscoveredProfile.model_validate(saved)
    except ValidationError as error:
        problem = describe_problems(error, whole="the file")
        raise ProfileFileError(
            str(store.profile_path), problem, next_action=_fix_the_profile(store)
        ) from None
    return merge_override(discovered, load_override(store))


def save_profile(store: RunStore, profile: DiscoveredProfile) -> None:
    """Writes what Discovery found. A merged profile is cut back to that, without the override."""
    discovered = profile.model_dump(mode="json", include=_DISCOVERED_FIELDS)
    _replace_file(store.profile_path, yaml.safe_dump(discovered, sort_keys=False))


def load_override(store: RunStore) -> dict[str, object]:
    """What the owner set by hand, checked now, or nothing when there is no override file."""
    source = str(store.override_path)
    override = _read_mapping(store.override_path, next_action=_fix_the_override(source)) or {}
    try:
        _merged(_ANY_VALID_PROFILE, override)
    except ValidationError as error:
        problem = describe_problems(error, whole="the file")
        raise ProfileFileError(source, problem, next_action=_fix_the_override(source)) from None
    return override


def merge_override(profile: DiscoveredProfile, override: Mapping[str, object]) -> RepoProfile:
    """The profile the run uses: each value the owner set replaces the discovered one."""
    try:
        return _merged(profile, override)
    except ValidationError as error:
        source = "the profile override"
        problem = describe_problems(error, whole="the override")
        raise ProfileFileError(source, problem, next_action=_fix_the_override(source)) from None


def _merged(profile: DiscoveredProfile, override: Mapping[str, object]) -> RepoProfile:
    discovered = profile.model_dump(include=_DISCOVERED_FIELDS)
    return RepoProfile.model_validate({**discovered, **override})


def _read_mapping(path: Path, *, next_action: str) -> dict[str, object] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        raise ProfileFileError(
            str(path), printable_name(str(error)), next_action=next_action
        ) from None
    try:
        content = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ProfileFileError(
            str(path), f"it is not YAML: {error}", next_action=next_action
        ) from None
    if content is None:
        return {}
    if not isinstance(content, dict):
        problem = f"it holds a {type(content).__name__}, where a mapping of keys was expected"
        raise ProfileFileError(str(path), problem, next_action=next_action)
    return content


# Written beside the file and renamed over it, so a run stopped mid-write leaves the old profile.
def _replace_file(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as file:
            file.write(text)
        os.replace(file.name, path)
    except OSError as error:
        raise ProfileFileError(
            str(path),
            printable_name(str(error)),
            next_action="Make the workspace folder writable, then run again.",
        ) from None
