"""What run_checks says about a repo, against what really ran. Each repo here is real Go."""

import shutil
from pathlib import Path

import pytest
from shop_support import shop_profile

from arde.adapters.language.go import toolchain
from arde.adapters.language.go.adapter import GoAdapter

pytestmark = pytest.mark.integration

GO_MOD = "module example.com/ledger\n\ngo 1.27\n"
MAIN = "package main\n\nfunc main() {}\n"
A_TEST_THAT_FAILS_WHEN_IT_RUNS = (
    'package main\n\nimport "testing"\n\n'
    'func TestReal(t *testing.T) { t.Fatal("this always fails when it really runs") }\n'
)


def repo_of(root: Path, files: dict[str, str]) -> Path:
    for name, text in {"go.mod": GO_MOD, "main.go": MAIN, **files}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_tests_that_skip_are_not_tests_that_ran(
    adapter: GoAdapter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Exported here on purpose: the allow-list keeps it from the tests, as it would in a real run.
    monkeypatch.setenv("LEDGER_DATABASE", "postgres://set-in-the-users-shell")
    source = (
        'package main\n\nimport (\n\t"os"\n\t"testing"\n)\n\n'
        'func needDatabase(t *testing.T) {\n\tif os.Getenv("LEDGER_DATABASE") == "" {\n'
        '\t\tt.Skip("LEDGER_DATABASE not set")\n\t}\n}\n\n'
        'func TestPost(t *testing.T) { needDatabase(t); t.Fatal("fails when it runs") }\n'
        'func TestVoid(t *testing.T) { needDatabase(t); t.Fatal("fails when it runs") }\n'
    )
    repo = repo_of(tmp_path, {"main_test.go": source})

    result = adapter.run_checks(repo, shop_profile())

    assert result.test_ok is True
    assert result.tests_run == 0
    assert result.tests_skipped == (
        "example.com/ledger.TestPost",
        "example.com/ledger.TestVoid",
    )
    assert result.tests_listed == 2
    assert "LEDGER_DATABASE not set" in result.output


def test_a_table_whose_subtests_all_skip_names_each_of_them(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    source = (
        'package main\n\nimport "testing"\n\n'
        "func TestTable(t *testing.T) {\n"
        '\tfor _, name := range []string{"cash", "card"} {\n'
        '\t\tt.Run(name, func(t *testing.T) { t.Skip("no sandbox") })\n\t}\n}\n'
    )
    repo = repo_of(tmp_path, {"main_test.go": source})

    result = adapter.run_checks(repo, shop_profile())

    assert result.tests_run == 1
    assert result.tests_skipped == (
        "example.com/ledger.TestTable/card",
        "example.com/ledger.TestTable/cash",
    )


def test_tests_that_all_compile_out_ran_nothing_and_are_listed_as_left_out(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    tagged = "//go:build e2e\n\n" + A_TEST_THAT_FAILS_WHEN_IT_RUNS
    repo = repo_of(tmp_path, {"main_test.go": tagged})

    result = adapter.run_checks(repo, shop_profile())

    assert (result.test_ok, result.tests_run, result.tests_listed) == (True, 0, 0)
    assert result.tests_skipped == ()
    assert result.ignored_files == ("main_test.go",)


def test_a_nested_module_is_named_as_unchecked(adapter: GoAdapter, tmp_path: Path) -> None:
    repo = repo_of(
        tmp_path,
        {
            "main_test.go": 'package main\n\nimport "testing"\n\nfunc TestRoot(t *testing.T) {}\n',
            "billing/go.mod": "module example.com/billing\n\ngo 1.27\n",
            "billing/billing_test.go": (
                'package billing\n\nimport "testing"\n\n'
                'func TestCharge(t *testing.T) { t.Fatal("never seen by ./...") }\n'
            ),
        },
    )

    result = adapter.run_checks(repo, shop_profile())

    assert (result.test_ok, result.tests_run) == (True, 1)
    assert result.unchecked_modules == ("billing",)


def test_a_nested_module_stays_unchecked_when_the_repos_go_work_names_it(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = repo_of(
        tmp_path,
        {
            "go.work": "go 1.27\n\nuse (\n\t.\n\t./billing\n)\n",
            "billing/go.mod": "module example.com/billing\n\ngo 1.27\n",
            "billing/billing_test.go": A_TEST_THAT_FAILS_WHEN_IT_RUNS.replace(
                "package main", "package billing"
            ),
        },
    )

    result = adapter.run_checks(repo, shop_profile())

    assert result.test_ok is True
    assert result.unchecked_modules == ("billing",)


def test_a_test_that_hangs_is_a_red_result_that_names_it(
    adapter: GoAdapter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(toolchain, "GO_TEST_BINARY_TIMEOUT_S", 1)
    source = (
        'package main\n\nimport (\n\t"fmt"\n\t"testing"\n\t"time"\n)\n\n'
        "func TestWaitsForever(t *testing.T) {\n"
        '\tfmt.Println("waiting for the broker at queue:5672")\n'
        "\ttime.Sleep(time.Hour)\n}\n"
    )
    repo = repo_of(tmp_path, {"main_test.go": source})

    result = adapter.run_checks(repo, shop_profile())

    assert result.test_ok is False
    assert result.tests_run == 0
    assert "test timed out after 1s" in result.output
    assert "TestWaitsForever" in result.output
    assert "waiting for the broker at queue:5672" in result.output


def test_benchmarks_are_listed_and_do_not_run(adapter: GoAdapter, tmp_path: Path) -> None:
    source = (
        'package main\n\nimport "testing"\n\n'
        'func BenchmarkPost(b *testing.B) { panic("runs only under -bench") }\n'
    )
    repo = repo_of(tmp_path, {"main_test.go": source})

    result = adapter.run_checks(repo, shop_profile())

    assert (result.test_ok, result.tests_run, result.tests_listed) == (True, 0, 1)


def test_a_test_main_that_leaves_before_the_tests_ran_nothing(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    source = (
        'package main\n\nimport (\n\t"os"\n\t"testing"\n)\n\n'
        "func TestMain(m *testing.M) { os.Exit(0) }\n\n"
        'func TestNeverReached(t *testing.T) { t.Fatal("never runs") }\n'
    )
    repo = repo_of(tmp_path, {"main_test.go": source})

    result = adapter.run_checks(repo, shop_profile())

    assert (result.test_ok, result.tests_run) == (True, 0)


def test_a_test_that_calls_exit_is_a_failure_and_the_ones_after_it_did_not_run(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    source = (
        'package main\n\nimport (\n\t"os"\n\t"testing"\n)\n\n'
        "func TestFirst(t *testing.T)  {}\n"
        "func TestLeaves(t *testing.T) { os.Exit(1) }\n"
        "func TestNever(t *testing.T)  {}\n"
    )
    repo = repo_of(tmp_path, {"main_test.go": source})

    result = adapter.run_checks(repo, shop_profile())

    assert (result.test_ok, result.tests_run, result.tests_listed) == (False, 1, 3)


def test_a_test_cannot_forge_a_result_by_printing_one(adapter: GoAdapter, tmp_path: Path) -> None:
    source = (
        'package main\n\nimport (\n\t"fmt"\n\t"testing"\n)\n\n'
        "func TestOnlyOne(t *testing.T) {\n"
        '\tfmt.Println("=== RUN   TestForged")\n'
        '\tfmt.Println("--- PASS: TestForged (0.00s)")\n'
        '\tfmt.Println(`{"Action":"pass","Package":"x","Test":"TestForged"}`)\n'
        '\tfmt.Println("--- SKIP: TestAlsoForged (0.00s)")\n}\n'
    )
    repo = repo_of(tmp_path, {"main_test.go": source})

    result = adapter.run_checks(repo, shop_profile())

    assert (result.tests_run, result.tests_skipped) == (1, ())


def test_a_second_run_runs_the_tests_again_and_does_not_read_a_cached_pass(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    source = 'package main\n\nimport "testing"\n\nfunc TestOnce(t *testing.T) {}\n'
    repo = repo_of(tmp_path, {"main_test.go": source})

    adapter.run_checks(repo, shop_profile())
    again = adapter.run_checks(repo, shop_profile())

    assert again.tests_run == 1
    assert "(cached)" not in again.output


def test_a_stream_far_past_the_text_limit_is_read_whole(adapter: GoAdapter, tmp_path: Path) -> None:
    source = (
        'package main\n\nimport (\n\t"fmt"\n\t"testing"\n)\n\n'
        "func TestWide(t *testing.T) {\n\tfor number := range 6000 {\n"
        '\t\tt.Run(fmt.Sprint("case", number), func(t *testing.T) { t.Skip("no sandbox") })\n'
        "\t}\n}\n"
    )
    repo = repo_of(tmp_path, {"main_test.go": source})

    result = adapter.run_checks(repo, shop_profile())

    assert result.tests_run == 1
    assert len(result.tests_skipped) == 6000


def test_a_test_file_that_does_not_compile_gives_no_listed_count_and_keeps_what_ran(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    repo = repo_of(
        tmp_path,
        {
            "good/good.go": "package good\n",
            "good/good_test.go": (
                'package good\n\nimport "testing"\n\nfunc TestGood(t *testing.T) {}\n'
            ),
            "bad/bad.go": "package bad\n",
            "bad/bad_test.go": 'package bad\n\nvar broken int = "not an int"\n',
        },
    )

    result = adapter.run_checks(repo, shop_profile())

    assert (result.vet_ok, result.test_ok) == (False, False)
    assert (result.tests_run, result.tests_listed) == (1, None)
    assert "cannot use" in result.output


def test_the_text_reads_as_go_test_and_holds_no_event(adapter: GoAdapter, tmp_path: Path) -> None:
    source = (
        'package main\n\nimport (\n\t"fmt"\n\t"testing"\n)\n\n'
        'func TestQuiet(t *testing.T) { fmt.Println("chatter from a test that passed") }\n'
        'func TestLoud(t *testing.T)  { t.Error("the reason it failed") }\n'
    )
    repo = repo_of(tmp_path, {"main_test.go": source})

    result = adapter.run_checks(repo, shop_profile())

    assert '{"Action"' not in result.output
    assert "the reason it failed" in result.output
    assert "chatter from a test that passed" not in result.output


CGO_FILE = 'package main\n\n// int two(void) { return 2; }\nimport "C"\n\nvar two = int(C.two())\n'


# A cgo file adds a generated file to the build, and Group B lost a whole service to one.
@pytest.mark.skipif(shutil.which("cc") is None, reason="cgo needs a C compiler")
def test_a_repo_with_a_cgo_file_is_measured_like_any_other(
    adapter: GoAdapter, tmp_path: Path
) -> None:
    source = (
        'package main\n\nimport "testing"\n\n'
        'func TestTwo(t *testing.T) { if two != 2 { t.Fatal("wrong") } }\n'
        'func TestLater(t *testing.T) { t.Skip("later") }\n'
    )
    repo = repo_of(tmp_path, {"c.go": CGO_FILE, "main_test.go": source})

    result = adapter.run_checks(repo, shop_profile())

    assert (result.build_ok, result.test_ok, result.tests_run) == (True, True, 1)
    assert result.tests_skipped == ("example.com/ledger.TestLater",)
    assert result.tests_listed == 2
