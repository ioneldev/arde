"""The inventory keeps what code read from the source, with the two names a field has."""

import pytest
from pydantic import BaseModel, ValidationError

from arde.domain.inventory import (
    DROPPED_LIMIT,
    FILE_LIMIT,
    NAME_LIMIT,
    PATH_LIMIT,
    STATUS_LIMIT,
    TYPE_LIMIT,
    ErrorCode,
    Inventory,
    Param,
    ResponseField,
    Route,
)

EMPTY_INVENTORY: dict[str, tuple[()]] = {
    "routes": (),
    "params": (),
    "error_codes": (),
    "response_fields": (),
    "env_vars": (),
    "make_targets": (),
}


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


HOSTILE_TEXT = [
    "line\nbreak",
    "carriage\rreturn",
    "esc\x1b[31m",
    "bell\x07",
    "nul\x00",
    "c1\x9b31m",
    "byte order\ufeffmark",
    "line\u2028separator",
    "paragraph\u2029separator",
]

FIELD_LIMITS: list[tuple[type[BaseModel], str, int]] = [
    (Route, "method", NAME_LIMIT),
    (Route, "path", PATH_LIMIT),
    (Route, "handler", NAME_LIMIT),
    (Param, "name", NAME_LIMIT),
    (ErrorCode, "status", STATUS_LIMIT),
    (ResponseField, "struct", NAME_LIMIT),
    (ResponseField, "name", NAME_LIMIT),
    (ResponseField, "json_name", NAME_LIMIT),
    (ResponseField, "declared_type", TYPE_LIMIT),
    (ResponseField, "file", FILE_LIMIT),
]

GOOD_MODELS: dict[type[BaseModel], dict[str, object]] = {
    Route: {"method": "GET", "path": "/x", "handler": "s.show"},
    Param: {"name": "qty"},
    ErrorCode: {"code": "bad_qty", "status": "400"},
    ResponseField: a_field().model_dump(),
}


def model_with(model: type[BaseModel], field: str, value: str) -> BaseModel:
    return model.model_validate({**GOOD_MODELS[model], field: value})


@pytest.mark.parametrize(("model", "field", "limit"), FIELD_LIMITS)
@pytest.mark.parametrize("text", HOSTILE_TEXT)
def test_no_field_read_from_a_repo_holds_a_control_character(
    model: type[BaseModel], field: str, limit: int, text: str
) -> None:
    with pytest.raises(ValidationError):
        model_with(model, field, text)


@pytest.mark.parametrize(("model", "field", "limit"), FIELD_LIMITS)
def test_a_field_read_from_a_repo_holds_text_up_to_its_limit_and_no_more(
    model: type[BaseModel], field: str, limit: int
) -> None:
    assert getattr(model_with(model, field, "x" * limit), field) == "x" * limit

    with pytest.raises(ValidationError):
        model_with(model, field, "x" * (limit + 1))


@pytest.mark.parametrize(
    "code", ["Internal server error", "bad code", "a\nb", "x" * (NAME_LIMIT + 1), ""]
)
def test_an_error_code_is_a_name_no_fact_would_refuse(code: str) -> None:
    with pytest.raises(ValidationError):
        ErrorCode(code=code, status="500")


@pytest.mark.parametrize("field", ["env_vars", "make_targets", "dropped"])
@pytest.mark.parametrize("text", HOSTILE_TEXT)
def test_the_inventorys_own_lists_refuse_a_control_character(field: str, text: str) -> None:
    with pytest.raises(ValidationError):
        Inventory.model_validate({**EMPTY_INVENTORY, field: (text,)})


@pytest.mark.parametrize(
    ("field", "limit"),
    [("env_vars", NAME_LIMIT), ("make_targets", NAME_LIMIT), ("dropped", DROPPED_LIMIT)],
)
def test_the_inventorys_own_lists_are_capped_too(field: str, limit: int) -> None:
    Inventory.model_validate({**EMPTY_INVENTORY, field: ("x" * limit,)})

    with pytest.raises(ValidationError):
        Inventory.model_validate({**EMPTY_INVENTORY, field: ("x" * (limit + 1),)})
