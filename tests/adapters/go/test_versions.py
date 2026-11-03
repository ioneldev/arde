import re

import pytest

from arde.application.ports import LanguageAdapter


@pytest.mark.integration
def test_the_trace_gets_the_version_of_go_swag_and_the_helper(adapter: LanguageAdapter) -> None:
    versions = adapter.tool_versions()

    assert set(versions) == {"go", "swag", "goinspect"}
    assert re.fullmatch(r"go\d+\.\d+(\.\d+)?\S*", versions["go"])
    assert versions["swag"] == "github.com/swaggo/swag/v2 v2.0.0-rc6"
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", versions["goinspect"])
