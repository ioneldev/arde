"""The input schema of add_facts, which offers the inventory's names as enums.

The enums guide the model towards names that exist. They decide nothing: validate_batch checks
every name again, and declarations and route links, too many for an enum, are checked only there.
"""

from typing import Annotated, Any, Literal, Union, cast

from pydantic import BaseModel, Field, WithJsonSchema, create_model

from arde.application.fact_names import FactNames
from arde.application.fact_validation import FactBatch
from arde.domain.facts import (
    EndpointFact,
    FieldFact,
    ParamFact,
    PolymorphicFact,
    ResponseFact,
    RuleFact,
    UnreachableFact,
)
from arde.domain.inventory import Inventory


def fact_batch_schema(inventory: Inventory) -> dict[str, object]:
    return _batch_model(FactNames.of(inventory)).model_json_schema()


# The types below are made from the inventory at run time, which no static type can name, so
# each is built through Any.
def _one_of(names: tuple[str, ...]) -> Any:
    """A text that must be one of names, written as an enum even when there is only one."""
    choice = cast(Any, Literal)[names]
    return cast(Any, Annotated)[choice, WithJsonSchema({"type": "string", "enum": list(names)})]


def _several(item: Any) -> Any:
    """A tuple of item, or an empty tuple when there is no item to hold."""
    return cast(Any, tuple)[()] if item is None else cast(Any, tuple)[item, ...]


def _narrowed[ModelT: BaseModel](model: type[ModelT], **fields: Any) -> type[ModelT]:
    """The model with each field given its narrower type, under the model's own name."""
    required: dict[str, Any] = {name: (type_, ...) for name, type_ in fields.items()}
    return create_model(model.__name__, __base__=model, **required)


def _batch_model(names: FactNames) -> type[FactBatch]:
    param = _narrowed(ParamFact, name=_one_of(names.params)) if names.params else None
    error_code = _one_of(names.error_codes) if names.error_codes else None
    response = _narrowed(ResponseFact, error_codes=_several(error_code))
    kinds: list[type[BaseModel]] = []
    if names.routes:
        endpoint = _narrowed(
            EndpointFact,
            route=_one_of(names.routes),
            params=_several(param),
            responses=_several(response),
        )
        kinds.append(endpoint)
    if names.structs:
        struct, field = _one_of(names.structs), _one_of(names.fields)
        kinds.append(_narrowed(FieldFact, struct=struct, field=field))
        kinds.append(_narrowed(PolymorphicFact, struct=struct, field=field))
    kinds.append(RuleFact)
    if error_code is not None:
        kinds.append(_narrowed(UnreachableFact, error_code=error_code))
    upserts: dict[str, Any] = {"upserts": (_several(_any_of(kinds)), ())}
    return create_model("FactBatch", __base__=FactBatch, **upserts)


# A discriminator needs a union, and a union of one kind is that kind.
def _any_of(kinds: list[type[BaseModel]]) -> Any:
    if len(kinds) == 1:
        return kinds[0]
    return cast(Any, Annotated)[cast(Any, Union)[tuple(kinds)], Field(discriminator="kind")]
