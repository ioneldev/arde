"""Fixtures for the Go adapter tests that need the Go toolchain."""

import shutil
from pathlib import Path

import pytest
from shop_support import SHOP

from arde.adapters.language.go.adapter import GoAdapter
from arde.adapters.language.go.goinspect_client import build_goinspect


@pytest.fixture(scope="session")
def goinspect_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_goinspect(tmp_path_factory.mktemp("arde-bin"))


@pytest.fixture
def adapter(goinspect_binary: Path) -> GoAdapter:
    return GoAdapter(goinspect_binary)


# A private copy of the fixture service, which a test may edit.
@pytest.fixture
def shop(tmp_path: Path) -> Path:
    copy = tmp_path / "shop"
    shutil.copytree(SHOP, copy)
    return copy
