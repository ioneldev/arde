"""What the tests know about the annotated fixture: where it is, its profile and its spec."""

import json
from pathlib import Path
from typing import Any

from arde.adapters.language.go.swag import run_swag
from arde.domain.profile import RepoProfile

ANNOTATED = Path(__file__).resolve().parent / "fixtures" / "annotated"


def annotated_profile() -> RepoProfile:
    return RepoProfile(
        main_package="./cmd/aviary",
        main_file="cmd/aviary/main.go",
        port_env="AVIARY_PORT",
        patterns=(),
    )


def annotated_spec(repo: Path, *, swag: Path) -> Any:
    """The spec the pinned spec tool writes from the annotations of a copy of the fixture."""
    path = run_swag(repo, annotated_profile(), swag=swag)
    return json.loads(path.read_text(encoding="utf-8"))
