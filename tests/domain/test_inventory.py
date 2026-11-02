"""The inventory keeps what code read from the source, with the two names a field has."""

import pytest
from pydantic import ValidationError

from arde.domain.inventory import Inventory, ResponseField


def a_field(**changes: object) -> ResponseField:
    fields: dict[str, object] = {
        "struct": "Quote",
        "name": "TotalCents",
        "json_name": "total_cents",
        "declared_type": "int64",
        "omitempty": False,
        "file": "web/payloads.go",
    }
    return ResponseField.model_validate({**fields, **changes})


def test_a_response_field_keeps_the_go_name_and_the_json_name_apart() -> None:
    field = a_field()

    assert (field.name, field.json_name) == ("TotalCents", "total_cents")


def test_a_response_field_without_a_json_name_is_refused() -> None:
    fields = a_field().model_dump()
    del fields["json_name"]

    with pytest.raises(ValidationError, match="json_name"):
        ResponseField.model_validate(fields)


def test_a_response_field_says_which_file_declares_its_struct() -> None:
    assert a_field(file="v2/web/payloads.go").file == "v2/web/payloads.go"


def test_a_response_field_without_a_file_is_refused() -> None:
    fields = a_field().model_dump()
    del fields["file"]

    with pytest.raises(ValidationError, match="file"):
        ResponseField.model_validate(fields)


def test_an_inventory_has_nothing_dropped_unless_it_says_so() -> None:
    inventory = Inventory(
        routes=(),
        params=(),
        error_codes=(),
        response_fields=(),
        env_vars=(),
        make_targets=(),
    )

    assert inventory.dropped == ()
