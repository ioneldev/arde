"""The environment tests give a Go command in place of the real one, which reads the toolchain."""

import os
from pathlib import Path

from arde.adapters.language.go.go_environment import GoCaches

GO_ENVIRONMENT = {"PATH": os.environ["PATH"], "GOPRIVATE": "corp.example/*", "GOENV": "off"}


def known_go_environment(repo: Path, caches: GoCaches | None = None) -> dict[str, str]:
    held = {} if caches is None else {"CACHES": f"{caches.build}|{caches.path}"}
    return {**GO_ENVIRONMENT, "FOR": str(repo), **held}
