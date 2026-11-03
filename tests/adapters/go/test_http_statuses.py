"""The status an error code's status stands for, as Go source writes it."""

import re
import subprocess
from pathlib import Path

import pytest

from arde.adapters.language.go.http_statuses import NET_HTTP_STATUSES, http_status


@pytest.mark.parametrize(
    ("written", "status"),
    [
        ("http.StatusNotFound", 404),
        ("http.StatusInternalServerError", 500),
        ("404", 404),
        (" 503 ", 503),
    ],
)
def test_a_net_http_constant_or_a_number_reads_as_its_status(written: str, status: int) -> None:
    assert http_status(written) == status


@pytest.mark.parametrize(
    "written", ["status", "http.StatusNope", "web.StatusNotFound", "statusOf(err)", "600", "4040"]
)
def test_anything_else_reads_as_no_status(written: str) -> None:
    assert http_status(written) is None


@pytest.mark.integration
def test_the_table_is_the_one_net_http_declares() -> None:
    goroot = subprocess.run(
        ["go", "env", "GOROOT"], capture_output=True, text=True, check=True
    ).stdout.strip()
    source = (Path(goroot) / "src" / "net" / "http" / "status.go").read_text(encoding="utf-8")

    declared = re.findall(r"^\s*(Status\w+)\s*=\s*(\d+)", source, re.MULTILINE)

    assert {name: int(value) for name, value in declared} == NET_HTTP_STATUSES
