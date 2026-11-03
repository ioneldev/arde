"""The JSON shape of each declared type the docs tests use, as a language adapter gives it, so the
API reference can be tested with no adapter at all."""

from arde.application.ports import JsonShape, StructRef

UNKNOWN = JsonShape(kind="unknown")


def an_object(name: str, package: str | None = None) -> JsonShape:
    return JsonShape(kind="object", struct=StructRef(name=name, package=package))


def an_array(element: str) -> JsonShape:
    return JsonShape(kind="array", element=element)


# Every type the shop's inventory and facts declare, with the kind each one is in JSON.
SHOP_SHAPES: dict[str, JsonShape] = {
    "int": JsonShape(kind="integer"),
    "int64": JsonShape(kind="integer"),
    "string": JsonShape(kind="string"),
    "any": UNKNOWN,
    "[]ItemView": an_array("ItemView"),
    "ItemView": an_object("ItemView"),
    "web.Quote": an_object("Quote", "web"),
    "web.Listing": an_object("Listing", "web"),
    "web.problem": an_object("problem", "web"),
    "LeafTraits": an_object("LeafTraits"),
    "CupTraits": an_object("CupTraits"),
    "web.LeafTraits": an_object("LeafTraits", "web"),
    "web.CupTraits": an_object("CupTraits", "web"),
}


def shop_shape(declared: str) -> JsonShape:
    """The shape the table holds. A type it lacks fails the test, naming the type."""
    return SHOP_SHAPES[declared]


# For a test about something else, where any type may come by.
def shape_or_unknown(declared: str) -> JsonShape:
    return SHOP_SHAPES.get(declared, UNKNOWN)
