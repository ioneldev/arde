"""What goinspect reports becomes the inventory. Nothing here runs Go: replies are written out."""

import pytest

from arde.adapters.language.go.goinspect_client import CallMatch, FieldReply
from arde.adapters.language.go.inventory_builder import (
    NoRoutesError,
    RouteNotUnderstoodError,
    SourceReading,
    build_inventory,
    unquote_string_literal,
)
from arde.domain.errors import ArdeError
from arde.domain.inventory import (
    INLINE_HANDLER,
    ErrorCode,
    Inventory,
    Param,
    ResponseField,
    Route,
)
from arde.domain.report import ExitCode


def call(kind: str, callee: str, *, line: int = 1, **args: str) -> CallMatch:
    return CallMatch(kind=kind, call=callee, file="main.go", line=line, args=args)


def registering(path: str, handler: str = "s.show", *, callee: str = "app.handle") -> CallMatch:
    return call("route", callee, path=path, handler=handler)


def field(
    struct: str,
    name: str,
    tag: str = "",
    go_type: str = "string",
    *,
    embedded: bool = False,
    file: str = "dto.go",
) -> FieldReply:
    return FieldReply(
        file=file,
        struct=struct,
        name=name,
        line=1,
        tag=tag,
        go_type=go_type,
        embedded=embedded,
    )


def inventory_of(
    *matches: CallMatch, fields: tuple[FieldReply, ...] = (), make_targets: tuple[str, ...] = ()
) -> Inventory:
    reading = SourceReading(matches=matches, fields=fields, make_targets=make_targets)
    return build_inventory(reading)


def routes_of(*matches: CallMatch) -> tuple[Route, ...]:
    return inventory_of(*matches).routes


def with_one_route(*matches: CallMatch) -> Inventory:
    return inventory_of(registering('"GET /ping"'), *matches)


def test_the_method_and_path_are_parsed_from_a_stdlib_pattern() -> None:
    assert routes_of(registering('"GET /{$}"')) == (
        Route(method="GET", path="/", handler="s.show"),
    )


def test_a_path_wildcard_is_kept() -> None:
    [route] = routes_of(registering('"GET /products/{id}"'))

    assert (route.method, route.path) == ("GET", "/products/{id}")


def test_a_trailing_slash_pattern_keeps_its_slash() -> None:
    [route] = routes_of(registering('"GET /files/{$}"'))

    assert route.path == "/files/"


@pytest.mark.parametrize(
    ("callee", "method"),
    [
        ("r.GET", "GET"),
        ("r.Get", "GET"),
        ("app.get", "GET"),
        ("router.Post", "POST"),
        ("e.DELETE", "DELETE"),
        ("r.Patch", "PATCH"),
        ("r.Head", "HEAD"),
        ("r.Options", "OPTIONS"),
    ],
)
def test_a_path_with_no_method_takes_it_from_the_end_of_the_call(callee: str, method: str) -> None:
    [route] = routes_of(registering('"/items"', callee=callee))

    assert (route.method, route.path) == (method, "/items")


@pytest.mark.parametrize("callee", ["mux.HandleFunc", "r.Handle", "r.Any", "route", "r.GetAll"])
def test_a_path_with_no_method_and_no_verb_in_the_call_is_any(callee: str) -> None:
    [route] = routes_of(registering('"/items"', callee=callee))

    assert route.method == "ANY"


def test_the_method_in_the_path_wins_over_the_verb_in_the_call() -> None:
    [route] = routes_of(registering('"GET /items"', callee="r.POST"))

    assert route.method == "GET"


def test_a_raw_string_path_is_read() -> None:
    [route] = routes_of(registering("`GET /raw/{id}`"))

    assert (route.method, route.path) == ("GET", "/raw/{id}")


def test_an_escape_in_a_quoted_path_is_decoded() -> None:
    [route] = routes_of(registering('"GET /\\u0041"'))

    assert route.path == "/A"


@pytest.mark.parametrize(
    "path",
    [
        "prefix",
        '"/a" + id',
        'fmt.Sprintf("/%s", name)',
        "pathFor(r)",
        "'x'",
        '"unterminated',
        '"a" "b"',
    ],
)
def test_a_path_that_is_not_a_string_literal_stops_the_run(path: str) -> None:
    with pytest.raises(RouteNotUnderstoodError) as stopped:
        routes_of(call("route", "app.handle", line=42, path=path, handler="s.show"))

    assert isinstance(stopped.value, ArdeError)
    assert "main.go:42" in str(stopped.value)
    assert "string literal" in str(stopped.value)
    assert stopped.value.exit_code == ExitCode.DISCOVERY_NEEDS_INPUT
    assert "route pattern" in stopped.value.next_action


def test_a_route_call_with_no_path_argument_stops_the_run() -> None:
    with pytest.raises(RouteNotUnderstoodError, match="path"):
        routes_of(call("route", "app.handle", handler="s.show"))


@pytest.mark.parametrize(
    "path", ['"GET items"', '"get /items"', '"GET example.com/items"', '"items"', '""']
)
def test_a_path_that_does_not_start_with_a_slash_stops_the_run(path: str) -> None:
    with pytest.raises(RouteNotUnderstoodError, match="start with"):
        routes_of(registering(path))


@pytest.mark.parametrize("handler", ["s.show", "show", "handlers.Show", "pkg.Type.Method", "_x1"])
def test_a_plain_handler_name_is_kept(handler: str) -> None:
    [route] = routes_of(registering('"GET /x"', handler))

    assert route.handler == handler


@pytest.mark.parametrize(
    "handler",
    [
        "func(w http.ResponseWriter, r *http.Request) {\n\tw.WriteHeader(204)\n}",
        "http.HandlerFunc(s.show)",
        "logged(s.show)",
        "s.handlers[0]",
        "s.show()",
        "&handler{}",
        "nil, 1",
        "",
    ],
)
def test_a_handler_that_is_not_a_plain_name_is_inline(handler: str) -> None:
    [route] = routes_of(registering('"GET /x"', handler))

    assert route.handler == INLINE_HANDLER


def test_a_route_call_with_no_handler_argument_is_inline() -> None:
    [route] = routes_of(call("route", "app.handle", path='"GET /x"'))

    assert route.handler == INLINE_HANDLER


def test_the_same_route_registered_twice_is_listed_once_and_routes_are_sorted() -> None:
    found = routes_of(
        registering('"POST /b"'),
        registering('"GET /b"'),
        registering('"GET /a"'),
        registering('"GET /b"'),
    )

    assert [(route.path, route.method) for route in found] == [
        ("/a", "GET"),
        ("/b", "GET"),
        ("/b", "POST"),
    ]


def test_no_route_at_all_stops_the_run_and_asks_for_a_pattern() -> None:
    with pytest.raises(NoRoutesError) as stopped:
        inventory_of(call("param", "q.Get", name='"x"'))

    assert isinstance(stopped.value, ArdeError)
    assert stopped.value.exit_code == ExitCode.DISCOVERY_NEEDS_INPUT
    assert "pattern" in stopped.value.next_action


def test_a_reading_with_nothing_in_it_has_no_routes() -> None:
    with pytest.raises(NoRoutesError):
        build_inventory(SourceReading(matches=(), fields=(), make_targets=()))


def test_params_are_the_literal_names_without_duplicates_in_order() -> None:
    inventory = with_one_route(
        call("param", "q.Get", name='"qty"'),
        call("param", "q.Get", name="`currency`"),
        call("param", "q.Get", name='"qty"'),
    )

    assert inventory.params == (Param(name="currency"), Param(name="qty"))


def test_a_param_whose_name_is_not_a_literal_is_left_out() -> None:
    inventory = with_one_route(
        call("param", "q.Get", name="key"),
        call("param", "q.Get", name='"a" + suffix'),
        call("param", "q.Get"),
    )

    assert inventory.params == ()


def test_error_codes_keep_the_status_as_written() -> None:
    inventory = with_one_route(
        call("error", "refuse", code='"bad_qty"', status="http.StatusBadRequest"),
        call("error", "refuse", code='"gone"', status="410"),
        call("error", "refuse", code='"odd"', status="statusFor(err)"),
    )

    assert inventory.error_codes == (
        ErrorCode(code="bad_qty", status="http.StatusBadRequest"),
        ErrorCode(code="gone", status="410"),
        ErrorCode(code="odd", status="statusFor(err)"),
    )


def test_one_code_raised_in_many_places_is_listed_once_and_with_each_status() -> None:
    inventory = with_one_route(
        call("error", "refuse", code='"bad"', status="400"),
        call("error", "refuse", code='"bad"', status="400"),
        call("error", "refuse", code='"bad"', status="422"),
    )

    assert inventory.error_codes == (
        ErrorCode(code="bad", status="400"),
        ErrorCode(code="bad", status="422"),
    )


def test_an_error_whose_code_or_status_cannot_be_read_is_left_out() -> None:
    inventory = with_one_route(
        call("error", "refuse", code="problem.Code", status="400"),
        call("error", "refuse", status="400"),
        call("error", "refuse", code='"no_status"'),
    )

    assert inventory.error_codes == ()


def test_env_vars_are_the_literal_names_sorted_and_listed_once() -> None:
    inventory = with_one_route(
        call("env", "os.Getenv", name='"B_VAR"'),
        call("env", "setting", name='"A_VAR"'),
        call("env", "os.Getenv", name='"B_VAR"'),
    )

    assert inventory.env_vars == ("A_VAR", "B_VAR")


def test_an_env_var_read_through_a_name_in_a_variable_is_left_out() -> None:
    inventory = with_one_route(call("env", "os.Getenv", name="name"))

    assert inventory.env_vars == ()


def test_make_targets_pass_through() -> None:
    inventory = inventory_of(registering('"GET /x"'), make_targets=("serve", "unit"))

    assert inventory.make_targets == ("serve", "unit")


def fields_of(*fields: FieldReply) -> tuple[ResponseField, ...]:
    return with_one_route_and(fields).response_fields


def with_one_route_and(fields: tuple[FieldReply, ...]) -> Inventory:
    return inventory_of(registering('"GET /ping"'), fields=fields)


def test_a_tagged_field_keeps_the_go_name_the_json_name_and_the_declared_type() -> None:
    [found] = fields_of(field("Quote", "TotalCents", 'json:"total_cents"', "int64"))

    assert found == ResponseField(
        struct="Quote",
        name="TotalCents",
        json_name="total_cents",
        declared_type="int64",
        omitempty=False,
    )


def test_omitempty_is_read_from_the_options_of_the_tag() -> None:
    found = fields_of(
        field("Quote", "Note", 'json:"note,omitempty"'),
        field("Quote", "Id", 'json:"id"'),
    )

    assert {f.name: f.omitempty for f in found} == {"Note": True, "Id": False}


def test_omitzero_also_marks_a_field_that_may_be_absent() -> None:
    [found] = fields_of(field("Quote", "Note", 'json:"note,omitzero"'))

    assert found.omitempty is True


def test_a_tag_with_options_and_no_name_uses_the_go_name() -> None:
    [found] = fields_of(field("Quote", "Note", 'json:",omitempty"'))

    assert (found.json_name, found.omitempty) == ("Note", True)


def test_a_field_of_type_any_is_kept_as_written() -> None:
    [found] = fields_of(field("Quote", "Details", 'json:"details"', "any"))

    assert found.declared_type == "any"


def test_the_json_tag_is_found_among_other_keys() -> None:
    [found] = fields_of(field("Quote", "Qty", 'binding:"required" json:"qty,omitempty" db:"q"'))

    assert (found.json_name, found.omitempty) == ("qty", True)


def test_a_json_text_inside_another_tags_value_is_not_the_json_tag() -> None:
    [found] = fields_of(field("Quote", "Qty", 'doc:"see json:" json:"qty"'))

    assert found.json_name == "qty"


def test_a_field_with_a_dash_name_is_left_out_and_dash_comma_is_a_field_named_dash() -> None:
    found = fields_of(
        field("Quote", "Secret", 'json:"-"'),
        field("Quote", "Minus", 'json:"-,"'),
    )

    assert [f.json_name for f in found] == ["-"]
    assert [f.name for f in found] == ["Minus"]


def test_an_unexported_field_is_left_out() -> None:
    found = fields_of(field("Quote", "Id", 'json:"id"'), field("Quote", "cache", 'json:"cache"'))

    assert [f.name for f in found] == ["Id"]


def test_an_embedded_field_is_left_out() -> None:
    found = fields_of(
        field("Quote", "Id", 'json:"id"'),
        field("Quote", "Stamp", "", "Stamp", embedded=True),
        field("Quote", "Meta", 'json:"meta"', "Meta", embedded=True),
    )

    assert [f.name for f in found] == ["Id"]


def test_an_exported_field_with_no_tag_in_a_tagged_struct_is_listed_under_its_go_name() -> None:
    found = {
        f.name: f for f in fields_of(field("Quote", "Id", 'json:"id"'), field("Quote", "Currency"))
    }

    untagged = found["Currency"]
    assert (untagged.json_name, untagged.omitempty) == ("Currency", False)


def test_a_struct_with_no_json_tag_at_all_is_not_a_response() -> None:
    found = fields_of(
        field("Settings", "Port", 'yaml:"port"'),
        field("Settings", "Verbose", ""),
    )

    assert found == ()


def test_a_struct_in_a_test_file_is_not_a_response() -> None:
    found = fields_of(field("decoded", "Total", 'json:"total"', file="web/handlers_test.go"))

    assert found == ()


def test_fields_are_sorted_by_struct_then_name_and_listed_once() -> None:
    found = fields_of(
        field("Quote", "Total", 'json:"total"'),
        field("Listing", "Items", 'json:"items"'),
        field("Quote", "Id", 'json:"id"'),
        field("Quote", "Id", 'json:"id"'),
    )

    assert [(f.struct, f.name) for f in found] == [
        ("Listing", "Items"),
        ("Quote", "Id"),
        ("Quote", "Total"),
    ]


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ('"abc"', "abc"),
        ("`abc`", "abc"),
        ('""', ""),
        ('"a\\tb"', "a\tb"),
        ('"\\u00e9"', "\u00e9"),
        ("`a\\tb`", "a\\tb"),
    ],
)
def test_a_go_string_literal_is_unquoted(text: str, value: str) -> None:
    assert unquote_string_literal(text) == value


@pytest.mark.parametrize(
    "text", ["abc", '"a" + "b"', '"a" "b"', '"abc', "'a'", "name", 'f("a")', "", '"\\z"', "1"]
)
def test_text_that_is_not_one_string_literal_is_not_unquoted(text: str) -> None:
    assert unquote_string_literal(text) is None
