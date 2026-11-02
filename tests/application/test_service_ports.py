"""What a service config accepts and what a failed start carries. No process is started here."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from arde.application.ports import (
    ServiceConfig,
    ServiceHandle,
    ServiceStartError,
    ServiceStopError,
)
from arde.domain.errors import ArdeError
from arde.domain.report import ExitCode


def config(**changes: object) -> ServiceConfig:
    fields: dict[str, object] = {
        "binary": Path("/opt/service"),
        "cwd": Path("/srv/repo"),
        "port_env": "APP_PORT",
        "log": Path("/runs/service.log"),
    }
    return ServiceConfig.model_validate({**fields, **changes})


def test_a_service_is_given_ten_seconds_to_open_its_port_unless_told_otherwise() -> None:
    assert config().timeout_s == 10


@pytest.mark.parametrize(
    "port_env", ["", "APP PORT", "APP-PORT", "1PORT", "APP_PORT\n", "PORT=1", "$(rm -rf /)"]
)
def test_a_port_variable_that_is_not_a_code_identifier_is_refused(port_env: str) -> None:
    with pytest.raises(ValidationError, match="a code identifier"):
        config(port_env=port_env)


@pytest.mark.parametrize("timeout_s", [0, -1, float("nan"), float("inf")])
def test_a_timeout_that_is_not_positive_is_refused(timeout_s: float) -> None:
    with pytest.raises(ValidationError):
        config(timeout_s=timeout_s)


def test_a_config_names_the_folder_the_service_runs_in() -> None:
    with pytest.raises(ValidationError):
        ServiceConfig.model_validate(
            {"binary": "/opt/service", "port_env": "APP_PORT", "log": "/runs/service.log"}
        )


def test_a_handle_holds_the_address() -> None:
    handle = ServiceHandle(base_url="http://127.0.0.1:5000", port=5000)

    assert (handle.base_url, handle.port) == ("http://127.0.0.1:5000", 5000)


@pytest.mark.parametrize("error_type", [ServiceStartError, ServiceStopError])
def test_a_service_error_names_the_next_action(error_type: type[ArdeError]) -> None:
    error = error_type("It failed", next_action="Do this")  # type: ignore[call-arg]

    assert isinstance(error, ArdeError)
    assert (str(error), error.next_action) == ("It failed", "Do this")
    assert error.exit_code == ExitCode.CHECKS_FAILED
