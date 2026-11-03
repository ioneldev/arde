"""The allow-list and the code comparison that prove a run changed only what ARDE writes.

The rule itself is pure and runs with the fast tests. The measured cases run on rendered copies of
the shop fixture, through GoAdapter, and need the Go toolchain.
"""

import shutil
from pathlib import Path

import pytest
from fixtures.mutations import (
    add_a_go_file,
    add_another_tag_key,
    leave_the_pricing_tests_out,
    misalign_a_field,
    read_routes_the_old_way,
    rename_a_json_key,
    write_a_comment_arde_did_not_render,
)
from render_support import QUOTING, copy_of_shop, render_shop

from arde.adapters.language.go.adapter import GoAdapter
from arde.adapters.language.go.edit_checks import (
    CopyNotReadableError,
    UnexplainedLine,
    edit_findings,
    unexplained_lines,
)
from arde.adapters.language.go.goinspect_client import CompareReport, GoinspectClient
from arde.application.ports import EditCheckRequest, RenderManifest, RenderResult
from arde.domain.findings import Finding

FIELD = '\tNote string `json:"note,omitempty"`'
TAGGED_FIELD = '\tNote string `json:"note,omitempty" binding:"optional"`'


def unexplained(
    before: list[str],
    after: list[str],
    *,
    rendered: tuple[str, ...] = (),
    removable: tuple[str, ...] = (),
) -> list[UnexplainedLine]:
    return unexplained_lines(
        "\n".join(before), "\n".join(after), rendered=rendered, removable=removable
    )


# The rule


def test_lines_the_render_reports_writing_are_explained() -> None:
    before = ["func quote() {}"]
    after = ["\t// @Summary Price some pieces", "func quote() {}"]

    assert unexplained(before, after, rendered=("// @Summary Price some pieces",)) == []


def test_a_comment_line_the_render_did_not_write_is_not_explained() -> None:
    after = ["// a note nobody rendered", "func quote() {}"]

    assert unexplained(["func quote() {}"], after) == [
        UnexplainedLine(change="gained", number=1, text="// a note nobody rendered")
    ]


def test_a_line_the_render_wrote_once_is_explained_once() -> None:
    after = ["// @Produce json", "// @Produce json", "func quote() {}"]

    assert unexplained(["func quote() {}"], after, rendered=("// @Produce json",)) == [
        UnexplainedLine(change="gained", number=2, text="// @Produce json")
    ]


def test_a_directive_is_explained_only_if_the_render_wrote_it() -> None:
    after = ["//go:debug httpmuxgo121=1", "", "package main"]

    assert unexplained(["package main"], after, rendered=("",)) == [
        UnexplainedLine(change="gained", number=1, text="//go:debug httpmuxgo121=1")
    ]


def test_a_field_gaining_ardes_tag_pairs_is_explained() -> None:
    before = ["type Quote struct {", FIELD, '\tTraits any `json:"traits"`', "}"]
    after = [
        "type Quote struct {",
        TAGGED_FIELD,
        '\tTraits any `json:"traits" oneOf:"web.LeafTraits,web.CupTraits"`',
        "}",
    ]

    assert unexplained(before, after) == []


def test_a_field_losing_ardes_tag_pairs_is_explained() -> None:
    assert unexplained([TAGGED_FIELD], [FIELD]) == []


def test_a_field_whose_only_pair_was_ardes_keeps_empty_backquotes() -> None:
    assert unexplained(['\tTraits any `oneOf:"web.LeafTraits"`'], ["\tTraits any ``"]) == []


@pytest.mark.parametrize(
    "edited",
    [
        '\tNote string `json:"note,omitempty" validate:"x"`',
        '\tNote string `json:"notes,omitempty"`',
        '\tNote string `json:"note,omitempty" binding:"required"`',
        '\tNote int `json:"note,omitempty" binding:"optional"`',
        '\tNote string `json:"note,omitempty" binding:"optional"` // trailing',
    ],
)
def test_a_field_changed_beyond_ardes_pairs_is_not_explained(edited: str) -> None:
    assert unexplained([FIELD], [edited]) == [
        UnexplainedLine(change="gained", number=1, text=" ".join(edited.split())),
        UnexplainedLine(change="lost", number=1, text=" ".join(FIELD.split())),
    ]


def test_spacing_that_only_realigns_a_line_is_no_change() -> None:
    before = ['\tSKU string `json:"sku"`']
    after = ['\tSKU         string `json:"sku"`']

    assert unexplained(before, after) == []


def test_line_endings_are_no_change() -> None:
    assert unexplained_lines("a\r\nb\r\n", "a\nb\n", rendered=(), removable=()) == []


def test_annotations_manifest_lines_and_empty_comments_may_be_lost() -> None:
    before = [
        "\t// How many pieces were priced",
        "// @Summary Old summary",
        "//",
        "//nolint:funlen",
        "func quote() {}",
    ]
    after = ["//nolint:funlen", "func quote() {}"]

    lost = unexplained(before, after, removable=("// How many pieces were priced",))

    assert lost == []


def test_a_line_no_render_wrote_may_not_be_lost() -> None:
    before = ["// quote prices some pieces.", "//nolint:funlen", "func quote() {}"]
    after = ["func quote() {}"]

    assert unexplained(before, after) == [
        UnexplainedLine(change="lost", number=1, text="// quote prices some pieces."),
        UnexplainedLine(change="lost", number=2, text="//nolint:funlen"),
    ]


# The findings, on small trees with goinspect's answer given

NOTHING_COMPARED = CompareReport(changed=(), added=(), removed=())
NOTHING_RENDERED = RenderResult(added_lines={}, tag_changes={}, changed_files=())


def trees(root: Path, before: dict[str, bytes], after: dict[str, bytes]) -> EditCheckRequest:
    for side, files in (("before", before), ("after", after)):
        (root / side).mkdir()
        for name, content in files.items():
            path = root / side / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    return EditCheckRequest(
        before=root / "before",
        after=root / "after",
        render=NOTHING_RENDERED,
        manifest=RenderManifest(),
    )


def test_each_file_goinspect_reports_is_a_finding_owned_by_code(tmp_path: Path) -> None:
    request = trees(tmp_path, {}, {})
    compared = CompareReport(changed=("web/a.go",), added=("web/b.go",), removed=("web/c.go",))

    findings = edit_findings(request, compared)

    assert [(finding.check, finding.owner, finding.subject) for finding in findings] == [
        ("agent_edits", "code", "web/b.go"),
        ("agent_edits", "code", "web/c.go"),
        ("agent_edits", "code", "web/a.go"),
    ]
    assert all(finding.next_action for finding in findings)


def test_a_module_file_that_is_new_gone_or_changed_is_a_finding(tmp_path: Path) -> None:
    before = {"go.mod": b"module example.com/shop\n", "go.sum": b""}
    after = {"go.mod": b"module example.com/shop\n\ngo 1.23\n", "go.work": b"go 1.23\n"}

    findings = edit_findings(trees(tmp_path, before, after), NOTHING_COMPARED)

    assert [finding.subject for finding in findings] == ["go.mod", "go.sum", "go.work"]


def test_lines_are_compared_in_go_files_and_not_in_other_files(tmp_path: Path) -> None:
    before = {"main.go": b"package main\n\nfunc main() {}\n", "README.md": b"# Shop\n"}
    after = {
        "main.go": b"package main\n\n// unrendered\nfunc main() {}\n",
        "README.md": b"# Shop API\n",
    }

    findings = edit_findings(trees(tmp_path, before, after), NOTHING_COMPARED)

    assert [finding.subject for finding in findings] == ["main.go"]
    assert "gained 1 line" in findings[0].message
    assert "line 3: // unrendered" in findings[0].message


def test_a_changed_file_that_is_not_utf8_is_a_finding(tmp_path: Path) -> None:
    before = {"main.go": b'package main\n\nvar s = "\xff"\n'}
    after = {"main.go": b'package main\n\nvar s = "\xfe"\n'}

    findings = edit_findings(trees(tmp_path, before, after), NOTHING_COMPARED)

    assert [finding.subject for finding in findings] == ["main.go"]
    assert "not UTF-8" in findings[0].message


def test_a_copy_that_cannot_be_read_stops_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = trees(tmp_path, {"main.go": b"package main\n"}, {"main.go": b"package main\n"})

    def refuse(path: Path) -> bytes:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(Path, "read_bytes", refuse)

    with pytest.raises(CopyNotReadableError, match="Permission denied") as stopped:
        edit_findings(request, NOTHING_COMPARED)
    assert stopped.value.next_action


# The measured cases, on rendered copies of the shop


class Copies:
    """The input of a run, and its output with the shop's facts rendered into it."""

    def __init__(self, adapter: GoAdapter, root: Path) -> None:
        self.before = copy_of_shop(root / "before")
        self.after = copy_of_shop(root / "after")
        self.render = render_shop(adapter, self.after).result


@pytest.fixture
def copies(adapter: GoAdapter, tmp_path: Path) -> Copies:
    return Copies(adapter, tmp_path)


def findings_of(
    adapter: GoAdapter, copies: Copies, *, manifest: RenderManifest | None = None
) -> list[Finding]:
    request = EditCheckRequest(
        before=copies.before,
        after=copies.after,
        render=copies.render,
        manifest=manifest or RenderManifest(),
    )
    return adapter.check_agent_edits(request)


def code_differs(goinspect_binary: Path, copies: Copies) -> bool:
    return bool(GoinspectClient(goinspect_binary).compare(copies.before, copies.after).changed)


@pytest.mark.integration
def test_comment_only_output_passes(adapter: GoAdapter, copies: Copies) -> None:
    assert copies.render.added_lines and copies.render.tag_changes

    assert findings_of(adapter, copies) == []


@pytest.mark.integration
def test_allowed_tags_pass_ast_check(
    adapter: GoAdapter, goinspect_binary: Path, copies: Copies
) -> None:
    assert copies.render.tag_changes["web/payloads.go"]

    assert not code_differs(goinspect_binary, copies)
    assert findings_of(adapter, copies) == []


@pytest.mark.integration
def test_a_later_render_that_drops_facts_passes(adapter: GoAdapter, tmp_path: Path) -> None:
    # The input is what the last run left. The later render takes out a block of annotations, a
    # field comment the manifest lists and a oneOf pair.
    before = copy_of_shop(tmp_path / "before")
    first = render_shop(adapter, before)
    after = tmp_path / "after"
    shutil.copytree(before, after)

    second = render_shop(adapter, after, facts=(QUOTING,), manifest=first.manifest)

    payloads = (after / "web" / "payloads.go").read_text()
    assert "// How many pieces" not in payloads and "oneOf" not in payloads
    assert "@Summary List the items" not in (after / "web" / "handlers.go").read_text()
    request = EditCheckRequest(
        before=before, after=after, render=second.result, manifest=first.manifest
    )
    assert adapter.check_agent_edits(request) == []


@pytest.mark.integration
def test_go_debug_directive_rejected_by_allow_list(
    adapter: GoAdapter, goinspect_binary: Path, copies: Copies
) -> None:
    read_routes_the_old_way(copies.after)

    findings = findings_of(adapter, copies)

    assert not code_differs(goinspect_binary, copies)
    assert [(finding.subject, finding.owner) for finding in findings] == [("main.go", "code")]
    assert "//go:debug httpmuxgo121=1" in findings[0].message


@pytest.mark.integration
def test_go_build_ignore_rejected_by_allow_list(
    adapter: GoAdapter, goinspect_binary: Path, copies: Copies
) -> None:
    leave_the_pricing_tests_out(copies.after)

    findings = findings_of(adapter, copies)

    assert not code_differs(goinspect_binary, copies)
    assert [finding.subject for finding in findings] == ["pricing/pricing_test.go"]
    assert "//go:build ignore" in findings[0].message


@pytest.mark.integration
def test_added_line_not_rendered_rejected(adapter: GoAdapter, copies: Copies) -> None:
    write_a_comment_arde_did_not_render(copies.after)

    findings = findings_of(adapter, copies)

    assert [finding.subject for finding in findings] == ["web/server.go"]
    assert "// find looks an item up by its SKU." in findings[0].message


@pytest.mark.integration
def test_other_tag_key_rejected(adapter: GoAdapter, copies: Copies) -> None:
    add_another_tag_key(copies.after)

    findings = findings_of(adapter, copies)

    assert {finding.subject for finding in findings} == {"web/payloads.go"}
    assert any('validate:"x"' in finding.message for finding in findings)


@pytest.mark.integration
def test_edited_json_tag_rejected(adapter: GoAdapter, copies: Copies) -> None:
    rename_a_json_key(copies.after)

    findings = findings_of(adapter, copies)

    assert {finding.subject for finding in findings} == {"web/payloads.go"}
    assert any('json:"name"' in finding.message for finding in findings)


@pytest.mark.integration
def test_new_go_file_rejected(adapter: GoAdapter, copies: Copies) -> None:
    add_a_go_file(copies.after)

    findings = findings_of(adapter, copies)

    assert [(finding.subject, finding.owner) for finding in findings] == [("web/extra.go", "code")]


@pytest.mark.integration
def test_comment_lines_the_render_result_does_not_list_are_rejected(
    adapter: GoAdapter, copies: Copies
) -> None:
    copies.render = RenderResult(added_lines={}, tag_changes={}, changed_files=())

    findings = findings_of(adapter, copies)

    assert {finding.subject for finding in findings} == {
        "main.go",
        "web/handlers.go",
        "web/payloads.go",
    }


# Formatting


@pytest.mark.integration
def test_format_issues_lists_files_gofmt_would_change_or_cannot_parse(
    adapter: GoAdapter, shop: Path
) -> None:
    assert adapter.format_issues(shop) == set()
    misalign_a_field(shop)
    (shop / "broken").mkdir()
    (shop / "broken" / "broken.go").write_text("package broken\n\nfunc (\n", encoding="utf-8")

    assert adapter.format_issues(shop) == {"web/payloads.go", "broken/broken.go"}
