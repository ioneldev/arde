"""The inventory keeps what code read from the source, with the two names a field has."""

import pytest
from pydantic import ValidationError

from arde.domain.inventory import ResponseField


def test_a_response_field_keeps_the_go_name_and_the_json_name_apart() -> None:
    field = ResponseField(
        struct="Quote",
        name="TotalCents",
        json_name="total_cents",
        declared_type="int64",
        omitempty=False,
    )

    assert (field.name, field.json_name) == ("TotalCents", "total_cents")


def test_a_response_field_without_a_json_name_is_refused() -> None:
    with pytest.raises(ValidationError, match="json_name"):
        ResponseField.model_validate(
            {"struct": "Quote", "name": "TotalCents", "declared_type": "int64", "omitempty": False}
        )
