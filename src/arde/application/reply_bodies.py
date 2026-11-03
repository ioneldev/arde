"""The JSON body of each reply, as rows a person reads: one per field, named by its path from the
body's root, with its JSON type and its description from the field facts.

The language adapter says what each declared type is in JSON, and this file finds the struct a
named type refers to among the inventory's. A type it cannot place is shown by its name and never
expanded, because a guess would describe fields the reply may not hold.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from arde.application.fact_names import fact_routes
from arde.application.ports import JsonShape, StructRef
from arde.domain.base import printable_name
from arde.domain.errors import ArdeError
from arde.domain.facts import FieldFact, PolymorphicFact, Wiki
from arde.domain.inventory import Inventory, ResponseField
from arde.domain.report import ExitCode

# How many structs deep a body is expanded, the body's own included. Each struct a reply holds
# twice is expanded twice, so the limit keeps a wide tree of them from filling the page.
DEPTH_LIMIT = 8
_SCALARS = frozenset({"integer", "number", "string", "boolean"})


class ReferenceTableError(ArdeError):
    """The wiki describes something the inventory cannot place, so no row can be written."""

    def __init__(self, message: str, *, next_action: str) -> None:
        super().__init__(message, next_action=next_action, exit_code=ExitCode.CHECKS_FAILED)


@dataclass(frozen=True)
class TypeLabel:
    """A type as a row shows it: words, then a declared type's name when it is shown by name."""

    words: str
    name: str | None = None


_AN_OBJECT = TypeLabel("object")


@dataclass(frozen=True)
class BodyRow:
    path: str
    type: TypeLabel
    always_present: bool
    description: str


@dataclass(frozen=True)
class Shape:
    """One struct a polymorphic field may hold, its fields named as paths under the field."""

    path: str
    name: str
    # None when the struct is not expanded here: no struct of the inventory has that name, it
    # holds the field itself, or it lies past the depth limit.
    rows: tuple[BodyRow, ...] | None


@dataclass(frozen=True)
class Body:
    # The type of the whole body, when it is not an object whose fields are the rows.
    root: TypeLabel | None
    rows: tuple[BodyRow, ...]
    # Every shape of every polymorphic field in the rows, and of those in its own shapes.
    shapes: tuple[Shape, ...]


@dataclass(frozen=True)
class ReplyStruct:
    """A struct of the inventory, with the place of each of its fields there."""

    folder: str
    package: str
    name: str
    fields: tuple[int, ...]

    @property
    def title(self) -> str:
        return f"{self.package}.{self.name}"


@dataclass(frozen=True)
class _Walk:
    """Where a walk is: the folder its types are written in, and the structs it is inside."""

    folder: str | None
    inside: tuple[ReplyStruct, ...]
    depth_limit: int

    def into(self, struct: ReplyStruct) -> "_Walk":
        return _Walk(struct.folder, (*self.inside, struct), self.depth_limit)

    def may_expand(self, struct: ReplyStruct) -> bool:
        return struct not in self.inside and len(self.inside) < self.depth_limit


@dataclass(frozen=True)
class _Value:
    label: TypeLabel
    rows: tuple[BodyRow, ...] = ()
    shapes: tuple[Shape, ...] = ()


@dataclass
class ReplyBodies:
    """The bodies of a service's replies. It records each struct a body expands."""

    inventory: Inventory
    json_shape: Callable[[str], JsonShape]
    descriptions: dict[int, str]
    variants: dict[int, tuple[str, ...]]
    structs: tuple[ReplyStruct, ...]
    reached: set[ReplyStruct] = field(default_factory=set)

    @classmethod
    def of(
        cls, wiki: Wiki, inventory: Inventory, *, json_shape: Callable[[str], JsonShape]
    ) -> "ReplyBodies":
        descriptions: dict[int, str] = {}
        variants: dict[int, tuple[str, ...]] = {}
        for fact in wiki.facts:
            if isinstance(fact, FieldFact):
                descriptions.setdefault(_inventory_index(fact, inventory), fact.description)
            elif isinstance(fact, PolymorphicFact):
                variants.setdefault(_inventory_index(fact, inventory), fact.variants)
        return cls(
            inventory=inventory,
            json_shape=json_shape,
            descriptions=descriptions,
            variants=variants,
            structs=_structs_of(inventory),
        )

    def of_response(self, declared_type: str, *, route: str) -> Body:
        """The body of a response a fact declares, read in the package of the route's handler."""
        walk = _Walk(folder=self._handler_folder(route), inside=(), depth_limit=DEPTH_LIMIT)
        value = self._value(declared_type, path="", walk=walk)
        root = None if value.label == _AN_OBJECT else value.label
        return Body(root=root, rows=value.rows, shapes=value.shapes)

    def struct_of_response(self, declared_type: str, *, route: str) -> ReplyStruct | None:
        """The struct a response's body is, or None when the body is no struct of the
        inventory."""
        shape = self.json_shape(declared_type)
        if shape.struct is None:
            return None
        return self._resolved(shape.struct, folder=self._handler_folder(route))

    def of_struct(self, struct: ReplyStruct) -> Body:
        """A struct's own fields, with every struct they hold shown by name."""
        walk = _Walk(folder=struct.folder, inside=(struct,), depth_limit=1)
        value = self._fields(struct, prefix="", walk=walk)
        return Body(root=None, rows=value.rows, shapes=value.shapes)

    def unreached(self) -> list[ReplyStruct]:
        return [struct for struct in self.structs if struct not in self.reached]

    def _fields(self, struct: ReplyStruct, *, prefix: str, walk: _Walk) -> _Value:
        rows: list[BodyRow] = []
        shapes: list[Shape] = []
        for index in struct.fields:
            reply = self.inventory.response_fields[index]
            path = _joined(prefix, reply.json_name)
            value = self._field_value(index, reply, path=path, walk=walk)
            description = self.descriptions.get(index, "")
            rows.append(BodyRow(path, value.label, not reply.may_be_absent, description))
            rows.extend(value.rows)
            shapes.extend(value.shapes)
        return _Value(_AN_OBJECT, tuple(rows), tuple(shapes))

    def _field_value(self, index: int, reply: ResponseField, *, path: str, walk: _Walk) -> _Value:
        variants = self.variants.get(index)
        if variants is None:
            return self._value(reply.declared_type, path=path, walk=walk)
        shapes = [shape for variant in variants for shape in self._shapes(variant, path, walk)]
        return _Value(TypeLabel(f"object, one of {len(variants)} shapes"), (), tuple(shapes))

    def _shapes(self, variant: str, path: str, walk: _Walk) -> Iterator[Shape]:
        struct = self._variant_struct(variant, folder=walk.folder)
        if struct is None or not walk.may_expand(struct):
            yield Shape(path, variant if struct is None else struct.name, None)
            return
        value = self._expanded(struct, prefix=path, walk=walk)
        yield Shape(path, struct.name, value.rows)
        yield from value.shapes

    # A variant is a name a fact wrote, not a type of the source, so a bare one that names no
    # struct of the holder's package names the one struct of that name the inventory holds, as
    # the facts' checks and the renderer read it.
    def _variant_struct(self, variant: str, *, folder: str | None) -> ReplyStruct | None:
        ref = self.json_shape(variant).struct
        if ref is None:
            return None
        own = self._resolved(ref, folder=folder)
        if own is not None or ref.package is not None:
            return own
        return _the_one(s for s in self.structs if s.name == ref.name)

    def _value(self, declared: str, *, path: str, walk: _Walk) -> _Value:
        shape = self.json_shape(declared)
        if shape.kind in _SCALARS:
            return _Value(TypeLabel(shape.kind))
        if shape.kind == "array" and shape.element is not None:
            items = self._value(shape.element, path=f"{path}[]", walk=walk)
            return _Value(_holding("array of ", items.label), items.rows, items.shapes)
        if shape.kind == "object" and shape.element is not None:
            values = self._value(shape.element, path=_joined(path, "*"), walk=walk)
            return _Value(_holding("object of ", values.label), values.rows, values.shapes)
        if shape.kind == "object" and shape.struct is not None:
            struct = self._resolved(shape.struct, folder=walk.folder)
            if struct is not None and walk.may_expand(struct):
                return self._expanded(struct, prefix=path, walk=walk)
        return _Value(TypeLabel("", declared))

    def _expanded(self, struct: ReplyStruct, *, prefix: str, walk: _Walk) -> _Value:
        self.reached.add(struct)
        return self._fields(struct, prefix=prefix, walk=walk.into(struct))

    # A bare name is a struct of the package it is written in. A qualified one names a package,
    # and two folders can hold packages of one name, so it must name exactly one struct.
    def _resolved(self, ref: StructRef, *, folder: str | None) -> ReplyStruct | None:
        if ref.package is None:
            return _the_one(s for s in self.structs if (s.folder, s.name) == (folder, ref.name))
        return _the_one(s for s in self.structs if (s.package, s.name) == (ref.package, ref.name))

    def _handler_folder(self, route: str) -> str | None:
        serving = [r for r in self.inventory.routes if route in fact_routes(r)]
        exact = [r for r in serving if r.key == route]
        handler_file = next((r.handler_file for r in (*exact, *serving)), None)
        return None if handler_file is None else _folder(handler_file)


def _structs_of(inventory: Inventory) -> tuple[ReplyStruct, ...]:
    places: dict[tuple[str, str], list[int]] = {}
    packages: dict[tuple[str, str], str] = {}
    for index, reply in enumerate(inventory.response_fields):
        key = (_folder(reply.file), reply.struct)
        places.setdefault(key, []).append(index)
        packages[key] = reply.package
    return tuple(
        ReplyStruct(folder=folder, package=packages[folder, name], name=name, fields=tuple(fields))
        for (folder, name), fields in places.items()
    )


def _the_one(structs: Iterator[ReplyStruct]) -> ReplyStruct | None:
    found = list(structs)
    return found[0] if len(found) == 1 else None


def _folder(file: str) -> str:
    return PurePosixPath(file).parent.as_posix()


def _joined(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key


# "array of " and integer give "array of integers", and a name stays as it is: "array of `Item`".
def _holding(words: str, held: TypeLabel) -> TypeLabel:
    if not held.words:
        return TypeLabel(words, held.name)
    first, space, rest = held.words.partition(" ")
    return TypeLabel(f"{words}{first}s{space}{rest}", held.name)


# A fact names a field by its struct and its name in the source, and two packages can each declare
# a struct by one name: the fact's links then say which file holds the one it means.
def _inventory_index(fact: FieldFact | PolymorphicFact, inventory: Inventory) -> int:
    named = [
        index
        for index, reply in enumerate(inventory.response_fields)
        if (reply.struct, reply.name) == (fact.struct, fact.field)
    ]
    linked_files = {declaration.split("::", 1)[0] for declaration in fact.links.declarations}
    linked_files.update(fact.links.files)
    if len(named) > 1:
        named = [index for index in named if inventory.response_fields[index].file in linked_files]
    if len(named) == 1:
        return named[0]
    field_name = f"{fact.struct}.{fact.field}"
    if not named:
        raise ReferenceTableError(
            f"The wiki describes field {field_name}, which no reply of the service holds.",
            next_action="Remove the fact from the wiki, then run again.",
        )
    raise ReferenceTableError(
        f"The wiki describes field {field_name}, which several structs of that name hold, and "
        f"fact {printable_name(fact.id)} does not link the declaration of the one it means.",
        next_action="Link the fact to the declaration of its struct, then run again.",
    )
