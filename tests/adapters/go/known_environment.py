"""The environment tests give a Go command in place of the real one, which reads the toolchain."""

import os
from pathlib import Path

GO_ENVIRONMENT = {"PATH": os.environ["PATH"], "GOPRIVATE": "corp.example/*", "GOENV": "off"}


def known_go_environment(repo: Path) -> dict[str, str]:
    return {**GO_ENVIRONMENT, "FOR": str(repo)}
