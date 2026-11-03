"""Each change in the mutations fixture still applies to the shop, and leaves a copy that builds.

A change that no longer applies fails loudly where it is used, but one that leaves a copy that does
not build would pass a check for the wrong reason.
"""

import subprocess
from pathlib import Path

import pytest
from fixtures.mutations import EVERY_MUTATION, Mutation
from render_support import copy_of_shop

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("mutation", EVERY_MUTATION, ids=lambda mutation: mutation.__name__)
def test_each_change_applies_and_the_copy_still_builds(mutation: Mutation, tmp_path: Path) -> None:
    repo = copy_of_shop(tmp_path / "shop")
    untouched = {path: path.read_bytes() for path in repo.rglob("*") if path.is_file()}

    mutation(repo)

    files = [path for path in repo.rglob("*") if path.is_file()]
    assert any(untouched.get(path) != path.read_bytes() for path in files)
    # vet compiles the tests too, which a build leaves out.
    vet = subprocess.run(
        ["go", "vet", "./..."], cwd=repo, capture_output=True, text=True, check=False
    )
    assert vet.returncode == 0, vet.stderr
