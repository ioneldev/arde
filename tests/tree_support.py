"""File trees that are awkward to build by hand."""

import os
from pathlib import Path


def a_tree_too_deep_for_one_path(root: Path) -> None:
    """Goes past every platform's limit on a path, a folder at a time, as no single call can."""
    parent = os.open(root, os.O_RDONLY)
    for _ in range(90):
        os.mkdir("d" * 90, dir_fd=parent)
        child = os.open("d" * 90, os.O_RDONLY, dir_fd=parent)
        os.close(parent)
        parent = child
    os.close(parent)
