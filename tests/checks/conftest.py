"""Fixtures for the checks tests that read a real spec or call a real service."""

import shutil
from pathlib import Path

import pytest
from annotated_support import ANNOTATED

from arde.adapters.language.go.swag import build_swag


@pytest.fixture(scope="session")
def swag_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_swag(tmp_path_factory.mktemp("arde-swag"))


# A private copy, because the spec tool writes into the repo it reads.
@pytest.fixture
def annotated(tmp_path: Path) -> Path:
    copy = tmp_path / "annotated"
    shutil.copytree(ANNOTATED, copy)
    return copy
