"""Writes into a copy of a repo every annotation and tag the wiki and the inventory call for.

A later run renders the same repo again, so each file gets one pass that takes out what ARDE
wrote before and writes what the facts say now: an annotation line in the comment above any
function goes, a line the manifest lists in the comment above any field goes, and ARDE's own tag
pairs are set afresh on every field. Every position comes from goinspect's declarations, read
before the render, so text that only looks like a function is never taken for one.
"""

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from arde.adapters.child_process import describe_os_error
from arde.adapters.language.go import gofmt
from arde.adapters.language.go.errors import NoMainFunctionError, RenderError
from arde.adapters.language.go.goinspect_client import Declaration, DeclarationReport, StructField
from arde.adapters.language.go.struct_tags import OPTIONAL_PAIR, one_of_pair, retagged
from arde.adapters.language.go.templates import endpoint_block, field_comment, general_block
from arde.application.ports import RenderManifest, RenderOutcome, RenderRequest, RenderResult
from arde.domain.base import printable_name
from arde.domain.facts import EndpointFact, FieldFact, PolymorphicFact
from arde.domain.inventory import Inventory, ResponseField, Route

# A line swag reads as an annotation: a comment whose text starts with @.
_ANNOTATION = re.compile(r"[ \t]*//[ \t]+@")
# A directive as gofmt reads one. gofmt keeps directives at the end of a doc comment, after an
# empty comment line, so an annotation block goes above both.
_DIRECTIVE = re.compile(r"[ \t]*//(?:line |extern |export |[a-z0-9]+:[a-z0-9])")
_EMPTY_COMMENT = re.compile(r"[ \t]*//[ \t]*\r?")
_RAW_STRING = re.compile(r"`([^`]*)`")


def render(request: RenderRequest, declarations: DeclarationReport) -> RenderOutcome:
    source = _Source.of(request, declarations)
    plans = {
        file: _FilePlan.of(file, source, written_before=request.manifest.lines.get(file, ()))
        for file in _files_to_visit(request, source)
    }
    for place, block in _blocks(request, source):
        _plan_for(plans, place).blocks[place.line] = block
    for place, comments in _field_comments(request, source):
        _plan_for(plans, place).field_comments[place.line].extend(comments)
    for place, edit in _tag_edits(request, source):
        _plan_for(plans, place).set_tag(place.line, edit)
    return _outcome([_render_file(request.repo, plans[file]) for file in sorted(plans)])


class _Place(NamedTuple):
    file: str
    # Counted from 1, as goinspect counts.
    line: int


@dataclass(frozen=True)
class _Source:
    """Where goinspect found each function, type and field before the render, by file."""

    declarations: dict[str, list[Declaration]]
    fields: dict[str, list[StructField]]
    # The files of the service, which are the files the inventory's declarations are in.
    service_files: frozenset[str]

    @classmethod
    def of(cls, request: RenderRequest, report: DeclarationReport) -> "_Source":
        declarations: dict[str, list[Declaration]] = defaultdict(list)
        for declaration in report.declarations:
            declarations[declaration.file].append(declaration)
        fields: dict[str, list[StructField]] = defaultdict(list)
        for struct_field in report.fields:
            fields[struct_field.file].append(struct_field)
        of_the_service = set(request.inventory.declarations)
        service_files = {
            declaration.file
            for declaration in report.declarations
            if declaration.id in of_the_service and not declaration.file.endswith("_test.go")
        }
        return cls(dict(declarations), dict(fields), frozenset(service_files))

    def lists(self, file: str) -> bool:
        return file in self.declarations or file in self.fields

    def declared(self, file: str, *, kind: str) -> list[Declaration]:
        return [one for one in self.declarations.get(file, []) if one.kind == kind]

    def function_named(self, file: str, name: str) -> Declaration | None:
        return next((one for one in self.declared(file, kind="func") if one.name == name), None)

    def function_at(self, place: _Place) -> Declaration | None:
        functions = self.declared(place.file, kind="func")
        return next((one for one in functions if one.line == place.line), None)

    def type_named(self, file: str, name: str) -> Declaration | None:
        return next((one for one in self.declared(file, kind="type") if one.name == name), None)

    def field_of(self, response: ResponseField) -> StructField | None:
        candidates = self.fields.get(response.file, [])
        return next(
            (
                one
                for one in candidates
                if (one.struct, one.name) == (response.struct, response.name)
            ),
            None,
        )

    def service_fields(self) -> Iterator[StructField]:
        for file in sorted(self.service_files):
            yield from self.fields.get(file, [])


class _TagEdit(NamedTuple):
    field: StructField
    tag: str


@dataclass
class _FilePlan:
    """What the render takes out of one file and writes into it, by line number from 1."""

    file: str
    function_lines: tuple[int, ...]
    field_lines: tuple[int, ...]
    # The comment lines the last run wrote here, without their indentation.
    written_before: frozenset[str]
    blocks: dict[int, list[str]] = field(default_factory=dict)
    field_comments: defaultdict[int, list[str]] = field(default_factory=lambda: defaultdict(list))
    tags: dict[int, _TagEdit] = field(default_factory=dict)

    @classmethod
    def of(cls, file: str, source: _Source, *, written_before: Iterable[str]) -> "_FilePlan":
        return cls(
            file=file,
            function_lines=tuple(one.line for one in source.declared(file, kind="func")),
            field_lines=tuple(one.line for one in source.fields.get(file, [])),
            written_before=frozenset(written_before),
        )

    # Fields declared together, as in A, B string, share one tag.
    def set_tag(self, line: int, edit: _TagEdit) -> None:
        earlier = self.tags.get(line)
        if earlier is not None and earlier.tag != edit.tag:
            raise RenderError(
                f"Fields {earlier.field.name} and {edit.field.name} of {edit.field.struct} share "
                f"line {line} of {printable_name(self.file)} and one tag, and need different "
                "tags.",
                next_action="Declare each of them on a line of its own, then run again.",
            )
        self.tags[line] = edit


# The service's files, and any other file goinspect lists where the last run wrote. A file the
# manifest names that goinspect does not list is not touched: the manifest is not trusted to name
# a file of the repo.
def _files_to_visit(request: RenderRequest, source: _Source) -> set[str]:
    written_before = {file for file in request.manifest.lines if source.lists(file)}
    return set(source.service_files) | written_before


def _plan_for(plans: dict[str, _FilePlan], place: _Place) -> _FilePlan:
    plan = plans.get(place.file)
    if plan is None:
        raise RenderError(
            f"ARDE would write into {printable_name(place.file)}, which is not a file of the "
            "service.",
            next_action="This is a bug in ARDE: report it with the file named.",
        )
    return plan


# Blocks above functions


def _blocks(request: RenderRequest, source: _Source) -> Iterator[tuple[_Place, list[str]]]:
    main = source.function_named(_as_listed(request.main_file), "main")
    if main is None:
        raise NoMainFunctionError(request.main_file)
    yield _Place(main.file, main.line), general_block(request.title)
    for anchor, facts in _endpoint_facts_by_anchor(request).items():
        if source.function_at(anchor) is None:
            raise RenderError(
                f"The inventory puts a handler at line {anchor.line} of "
                f"{printable_name(anchor.file)}, where goinspect finds no function.",
                next_action="This is a bug in ARDE: report it with the route named.",
            )
        yield anchor, endpoint_block(facts)


# goinspect names files from the repo root with forward slashes and no leading "./".
def _as_listed(file: str) -> str:
    return PurePosixPath(file.replace("\\", "/")).as_posix()


# One function serving several routes carries one block with a router line for each, so facts
# are grouped by the function, in an order that does not depend on the wiki's.
def _endpoint_facts_by_anchor(request: RenderRequest) -> dict[_Place, list[EndpointFact]]:
    endpoints = [fact for fact in request.wiki.facts if isinstance(fact, EndpointFact)]
    by_anchor: dict[_Place, list[EndpointFact]] = defaultdict(list)
    for fact in sorted(endpoints, key=_path_then_method):
        anchor = _anchor_of(fact, request.inventory.routes)
        if anchor is not None:
            by_anchor[anchor].append(fact)
    return by_anchor


def _path_then_method(fact: EndpointFact) -> tuple[str, str]:
    method, path = fact.route.split(" ", 1)
    return path, method


# A route registered with no method serves every one, so a fact names one of its verbs. A route
# with no single function to carry a block, such as a function written in place or one whose
# function other routes reach through different wrapping, gets none.
def _anchor_of(fact: EndpointFact, routes: Sequence[Route]) -> _Place | None:
    _, path = fact.route.split(" ", 1)
    serving = [route for route in routes if route.key == fact.route] or [
        route for route in routes if route.method == "ANY" and route.path == path
    ]
    if not serving:
        raise RenderError(
            f"The wiki describes {fact.route}, and no route of the inventory serves it.",
            next_action=(
                "ARDE deletes the facts of a route that is gone before it renders, so this is a "
                "bug: report it. Deleting the workspace makes the next run a first run."
            ),
        )
    anchors = {
        _Place(route.handler_file, route.handler_line)
        for route in serving
        if route.handler_id is not None
        and route.handler_file is not None
        and route.handler_line is not None
    }
    return anchors.pop() if len(anchors) == 1 else None


# Comments above fields


def _field_comments(request: RenderRequest, source: _Source) -> Iterator[tuple[_Place, list[str]]]:
    for fact in request.wiki.facts:
        if isinstance(fact, FieldFact):
            struct_field = _struct_field_of(fact, request.inventory, source)
            _require_a_line_of_its_own(struct_field, source)
            yield _Place(struct_field.file, struct_field.line), field_comment(fact.description)


def _struct_field_of(
    fact: FieldFact | PolymorphicFact, inventory: Inventory, source: _Source
) -> StructField:
    response = _response_field_of(fact, inventory)
    struct_field = source.field_of(response)
    if struct_field is None:
        raise RenderError(
            f"The inventory lists field {response.struct}.{response.name} in "
            f"{printable_name(response.file)}, where goinspect finds no such field.",
            next_action="This is a bug in ARDE: report it with the field named.",
        )
    return struct_field


# A fact names a struct by name, and two packages can each declare one by that name: the fact's
# link to the struct's declaration then says which.
def _response_field_of(fact: FieldFact | PolymorphicFact, inventory: Inventory) -> ResponseField:
    named = [
        response
        for response in inventory.response_fields
        if (response.struct, response.name) == (fact.struct, fact.field)
    ]
    if not named:
        raise RenderError(
            f"The wiki describes field {fact.struct}.{fact.field}, which is no response field "
            "of the service.",
            next_action="Remove the fact from the wiki, then run again.",
        )
    linked = [response for response in named if _type_id(response) in fact.links.declarations]
    if len(named) > 1 and len(linked) != 1:
        raise RenderError(
            f"The wiki describes field {fact.struct}.{fact.field}, which {len(named)} structs of "
            "that name hold, and the fact's links do not say which one it means.",
            next_action=(
                "Link the fact to the declaration of its struct, as file::type Name, then run "
                "again."
            ),
        )
    return named[0] if len(named) == 1 else linked[0]


def _type_id(response: ResponseField) -> str:
    return f"{response.file}::type {response.struct}"


# A struct written on one line has no line above a field that is not above the struct itself.
def _require_a_line_of_its_own(struct_field: StructField, source: _Source) -> None:
    struct = source.type_named(struct_field.file, struct_field.struct)
    if struct is not None and struct.line == struct_field.line:
        raise RenderError(
            f"Field {struct_field.struct}.{struct_field.name} is on the line of its struct, in "
            f"{printable_name(struct_field.file)}, so a comment above it would describe the "
            "struct.",
            next_action=(
                f"Put each field of {struct_field.struct} on a line of its own, as gofmt does, "
                "then run again."
            ),
        )


# Tags on fields


def _tag_edits(request: RenderRequest, source: _Source) -> Iterator[tuple[_Place, _TagEdit]]:
    wanted = _wanted_pairs(request)
    for struct_field in source.service_fields():
        pairs = wanted.get((struct_field.file, struct_field.struct, struct_field.name), [])
        tag = retagged(struct_field.tag, pairs)
        if tag is None:
            raise RenderError(
                f"ARDE cannot read the tag of field {struct_field.struct}.{struct_field.name} "
                f"in {printable_name(struct_field.file)} the way the next run's comparison "
                f"reads it, and has {' '.join(pairs)} to add to it.",
                next_action=(
                    'Write the tag as key:"value" pairs with a space between them, no key twice '
                    "and no backslash, then run again."
                ),
            )
        if tag != struct_field.tag:
            yield _Place(struct_field.file, struct_field.line), _TagEdit(struct_field, tag)


# swag reads a polymorphic field from its oneOf tag, and treats a field that may be left out of
# a reply as required unless its binding says optional.
def _wanted_pairs(request: RenderRequest) -> dict[tuple[str, str, str], list[str]]:
    wanted: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for fact in request.wiki.facts:
        if isinstance(fact, PolymorphicFact):
            response = _response_field_of(fact, request.inventory)
            wanted[_field_key(response)].append(one_of_pair(fact.variants))
    for response in request.inventory.response_fields:
        if response.may_be_absent:
            wanted[_field_key(response)].append(OPTIONAL_PAIR)
    return wanted


def _field_key(response: ResponseField) -> tuple[str, str, str]:
    return response.file, response.struct, response.name


# One file


@dataclass(frozen=True)
class _RenderedFile:
    file: str
    before: str
    after: str
    # The comment lines ARDE wrote, in file order, without their indentation.
    written: tuple[str, ...]


def _render_file(repo: Path, plan: _FilePlan) -> _RenderedFile:
    path = repo / plan.file
    before = _read(path, plan.file)
    lines, written = _rendered_lines(before.split("\n"), plan)
    after = "\n".join(lines)
    if after != before and _is_laid_out_by_gofmt(before, plan.file):
        after = _laid_out_like(before, after, plan.file)
    if after != before:
        _write(path, after, plan.file)
    return _RenderedFile(plan.file, before, after, written)


# Lines are split on "\n" alone, which is where Go and goinspect end a line, and a CRLF line
# keeps its "\r". Positions are those of the lines as read, so nothing shifts while they are used.
def _rendered_lines(lines: list[str], plan: _FilePlan) -> tuple[list[str], tuple[str, ...]]:
    removed = _removed(lines, plan)
    inserted = _inserted(lines, plan, removed=removed)
    replaced = {
        line - 1: _with_new_tag(lines[line - 1], edit, plan.file)
        for line, edit in plan.tags.items()
    }
    rendered: list[str] = []
    written: list[str] = []
    for index, line in enumerate(lines):
        rendered.extend(inserted.get(index, ()))
        written.extend(new_line.strip() for new_line in inserted.get(index, ()))
        if index not in removed:
            rendered.append(replaced.get(index, line))
    return rendered, tuple(written)


def _removed(lines: list[str], plan: _FilePlan) -> set[int]:
    removed: set[int] = set()
    for line in plan.function_lines:
        above = _comment_above(lines, line - 1)
        removed.update(index for index in above if _ANNOTATION.match(lines[index]))
    for line in plan.field_lines:
        above = _comment_above(lines, line - 1)
        removed.update(index for index in above if lines[index].strip() in plan.written_before)
    return removed


# A field comment a person already wrote is not written twice, and is not claimed as ARDE's.
def _inserted(lines: list[str], plan: _FilePlan, *, removed: set[int]) -> dict[int, list[str]]:
    ending = "\r" if any(line.endswith("\r") for line in lines) else ""
    inserted: dict[int, list[str]] = defaultdict(list)
    for line, block in plan.blocks.items():
        index = _function_index(lines, line, plan.file)
        inserted[_block_position(lines, index)].extend(
            _indented(block, like=lines[index], ending=ending)
        )
    for line, comments in plan.field_comments.items():
        index = line - 1
        kept = {lines[i].strip() for i in _comment_above(lines, index) if i not in removed}
        new = [comment for comment in comments if comment not in kept]
        inserted[index].extend(_indented(new, like=lines[index], ending=ending))
    return inserted


def _comment_above(lines: list[str], index: int) -> range:
    start = index
    while start > 0 and lines[start - 1].lstrip().startswith("//"):
        start -= 1
    return range(start, index)


def _function_index(lines: list[str], line: int, file: str) -> int:
    index = line - 1
    if not lines[index].lstrip().startswith("func"):
        raise RenderError(
            f"Line {line} of {printable_name(file)} names a function but does not start it, so "
            "a comment above it would not be the function's.",
            next_action=(
                "Write the function's func, receiver and name on one line, as gofmt does, then "
                "run again."
            ),
        )
    return index


def _block_position(lines: list[str], index: int) -> int:
    position = index
    while position > 0 and _DIRECTIVE.match(lines[position - 1]):
        position -= 1
    if position < index:
        while position > 0 and _EMPTY_COMMENT.fullmatch(lines[position - 1]):
            position -= 1
    return position


def _indented(texts: Sequence[str], *, like: str, ending: str) -> list[str]:
    indentation = like[: len(like) - len(like.lstrip())]
    return [f"{indentation}{text}{ending}" for text in texts]


def _with_new_tag(line: str, edit: _TagEdit, file: str) -> str:
    found = _RAW_STRING.search(line)
    if found is None or found[1] != edit.field.tag:
        raise RenderError(
            f"ARDE finds no tag in backquotes for field {edit.field.struct}.{edit.field.name} "
            f"on line {edit.field.line} of {printable_name(file)}, where it would set its own.",
            next_action=(
                "Give the field a tag in backquotes on its own line, such as its json key, then "
                "run again."
            ),
        )
    return f"{line[: found.start()]}`{edit.tag}`{line[found.end() :]}"


# Laying out


# A file that gofmt would change already is left as its author laid it out. Mixed line endings
# count as not laid out, because restoring them after gofmt would change every LF line.
def _is_laid_out_by_gofmt(text: str, file: str) -> bool:
    crlf = text.count("\r\n")
    if crlf and crlf != text.count("\n"):
        return False
    lf_text = text.replace("\r\n", "\n")
    return gofmt.formatted(lf_text, shown_as=file) == lf_text


def _laid_out_like(before: str, after: str, file: str) -> str:
    laid_out = gofmt.formatted(after.replace("\r\n", "\n"), shown_as=file)
    return laid_out.replace("\n", "\r\n") if "\r\n" in before else laid_out


def _read(path: Path, file: str) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        raise RenderError(
            f"{printable_name(file)} is not UTF-8, which Go source must be.",
            next_action="Save the file as UTF-8, then run again.",
        ) from error
    except OSError as error:
        raise RenderError(
            f"Cannot read {printable_name(file)}: {describe_os_error(error)}.",
            next_action="Make the file readable, then run again.",
        ) from error


# Writing through a link would change the file it points to, under another name.
def _write(path: Path, text: str, file: str) -> None:
    if path.is_symlink():
        raise RenderError(
            f"{printable_name(file)} is a link, and ARDE writes no file through a link.",
            next_action="Replace the link with the file it stands for, then run again.",
        )
    try:
        path.write_bytes(text.encode("utf-8"))
    except OSError as error:
        raise RenderError(
            f"Cannot write {printable_name(file)}: {describe_os_error(error)}.",
            next_action="Make the file writable, then run again.",
        ) from error


# What changed


def _outcome(files: Sequence[_RenderedFile]) -> RenderOutcome:
    changed = [one for one in files if one.after != one.before]
    added_lines: dict[str, tuple[str, ...]] = {}
    tag_changes: dict[str, tuple[str, ...]] = {}
    for one in changed:
        gained = _gained_lines(one.before, one.after)
        comments = tuple(line for line in gained if line.startswith("//"))
        code = tuple(line for line in gained if not line.startswith("//"))
        if comments:
            added_lines[one.file] = comments
        if code:
            tag_changes[one.file] = code
    return RenderOutcome(
        result=RenderResult(
            added_lines=added_lines,
            tag_changes=tag_changes,
            changed_files=tuple(one.file for one in changed),
        ),
        manifest=RenderManifest(lines={one.file: one.written for one in files if one.written}),
    )


# Each line of the new text that the old text does not account for, with its spacing collapsed.
def _gained_lines(before: str, after: str) -> list[str]:
    unmatched = Counter(_collapsed(line) for line in before.split("\n"))
    gained: list[str] = []
    for line in after.split("\n"):
        collapsed = _collapsed(line)
        if unmatched[collapsed] > 0:
            unmatched[collapsed] -= 1
        else:
            gained.append(collapsed)
    return gained


def _collapsed(line: str) -> str:
    return " ".join(line.split())
