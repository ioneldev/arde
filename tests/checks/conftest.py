"""The language adapter, as the cli wires it, for the checks' tests that need the Go toolchain."""

import pytest

from arde.adapters.language.go.adapter import go_adapter
from arde.application.ports import LanguageAdapter


@pytest.fixture(scope="session")
def adapter(tmp_path_factory: pytest.TempPathFactory) -> LanguageAdapter:
    return go_adapter(tmp_path_factory.mktemp("arde-bin"))
