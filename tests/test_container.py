"""The container: the image compose builds, run as compose runs it, with only the model scripted.

Each test gets a compose project of its own, so a volume of its own, and a folder of its own as the
project directory. Compose reads that folder's .env, which holds a fake key, so no test can load
the real one. The image is built once per checkout under a test tag, so a test run never replaces
the image a person built. Every test skips, saying why, when Docker is not available.
"""

import gzip
import hashlib
import io
import json
import os
import secrets
import shutil
import subprocess
import tarfile
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from end_to_end_support import a_repo_of, commit_all
from shop_support import SHOP

from arde.domain.report import ExitCode, RunReport

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPO_ROOT / "compose.yaml"
TESTS_IN_CONTAINER = "/arde-tests"
FAKE_KEY = "sk-test-not-a-real-key-for-the-container-0123456789"
# Docker's default wait between SIGTERM and SIGKILL.
STOP_GRACE_S = 10
BUILD_TIMEOUT_S = 1800
RUN_TIMEOUT_S = 900
# The first run on an empty volume compiles the standard library into the Go cache.
SERVICE_START_TIMEOUT_S = 600


def _docker_is_available() -> str | None:
    """Why the tests cannot run here, or None when they can."""
    if shutil.which("docker") is None:
        return "docker is not on PATH"
    for check in (["docker", "info"], ["docker", "compose", "version"]):
        try:
            answered = subprocess.run(check, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return f"`{' '.join(check)}` did not answer within 60 s"
        if answered.returncode != 0:
            return f"`{' '.join(check)}` failed: {answered.stderr.strip()[:200]}"
    return None


@pytest.fixture(scope="session")
def docker() -> None:
    reason = _docker_is_available()
    if reason is not None:
        pytest.skip(f"Docker is not available: {reason}")


# One tag per checkout, so two checkouts testing at once never run each other's image.
def _test_tag(name: str) -> str:
    checkout = hashlib.sha256(str(REPO_ROOT).encode()).hexdigest()[:12]
    return f"arde-test-{name}:{checkout}"


def _build(context: Path, *, target: str, tag: str) -> None:
    built = subprocess.run(
        ["docker", "build", "--target", target, "--tag", tag, str(context)],
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT_S,
    )
    assert built.returncode == 0, built.stderr[-4000:]


@pytest.fixture(scope="session")
def image(docker: None) -> str:
    tag = _test_tag("runtime")
    _build(REPO_ROOT, target="runtime", tag=tag)
    return tag


@dataclass(frozen=True)
class Run:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def report(self) -> RunReport:
        return RunReport.model_validate_json(self.stdout)


@dataclass(frozen=True)
class Project:
    """One compose project, run as a person runs it, from a folder that holds a fake key."""

    name: str
    folder: Path
    image: str

    def compose(self, *arguments: str, timeout_s: float = RUN_TIMEOUT_S, **environment: str) -> Run:
        done = subprocess.run(
            self.command(*arguments),
            env=self.environment(**environment),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        return Run(done.returncode, done.stdout, done.stderr)

    def command(self, *arguments: str) -> list[str]:
        files = [part for path in self.compose_files() for part in ("--file", str(path))]
        project = ["--project-name", self.name, "--project-directory", str(self.folder)]
        return ["docker", "compose", *project, *files, *arguments]

    def compose_files(self) -> list[Path]:
        override = self.folder / "override.yaml"
        return [COMPOSE_FILE, override] if override.is_file() else [COMPOSE_FILE]

    # Only what docker itself needs, and never the key: compose takes that from the folder's .env.
    def environment(self, **extra: str) -> dict[str, str]:
        passed = ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")
        return {
            **{name: os.environ[name] for name in passed if name in os.environ},
            "ARDE_IMAGE": self.image,
            **extra,
        }

    def arde(self, *argv: str, entrypoint: str | None = None, **environment: str) -> Run:
        return self.compose(*self.run_options(entrypoint), "arde", *argv, **environment)

    def run_options(self, entrypoint: str | None, *, name: str | None = None) -> list[str]:
        options = [
            "run",
            "--rm",
            "--no-TTY",
            "--volume",
            f"{REPO_ROOT / 'tests'}:{TESTS_IN_CONTAINER}:ro",
        ]
        if entrypoint is not None:
            options += ["--entrypoint", entrypoint]
        if name is not None:
            options += ["--name", name]
        return options

    def shell(self, script: str, **environment: str) -> Run:
        return self.arde("-c", script, entrypoint="sh", **environment)

    def write_env_file(self, lines: Sequence[str]) -> None:
        (self.folder / ".env").write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")

    def write_override(self, text: str) -> None:
        (self.folder / "override.yaml").write_text(text, encoding="utf-8")


@pytest.fixture
def project(image: str, tmp_path: Path) -> Iterator[Project]:
    folder = tmp_path / "project"
    folder.mkdir()
    made = Project(name=f"arde-test-{secrets.token_hex(4)}", folder=folder, image=image)
    made.write_env_file([f"ANTHROPIC_API_KEY={FAKE_KEY}"])
    try:
        yield made
    finally:
        made.compose("down", "--volumes", "--remove-orphans", timeout_s=120)


# The command a person runs


def test_image_builds_and_prints_help(project: Project) -> None:
    helped = project.arde("--help")

    assert helped.exit_code == 0, helped.stderr
    assert helped.stdout.startswith("usage: arde")
    assert "--repo" in helped.stdout


def test_json_output_clean_on_stdout(project: Project) -> None:
    refused = project.arde("--repo", "/nowhere", "--json")

    assert refused.exit_code == ExitCode.BAD_USAGE, refused.stderr
    report = refused.report
    assert (report.status, report.exit_code) == ("failed", ExitCode.BAD_USAGE)
    assert "/nowhere" in report.message


def test_with_no_argument_it_documents_the_mounted_repo_and_says_when_there_is_none(
    project: Project,
) -> None:
    refused = project.arde()

    assert refused.exit_code == ExitCode.BAD_USAGE, refused.stderr
    assert "/repo is not a git repo" in refused.stderr


def test_tools_are_pinned(project: Project) -> None:
    script = (
        'go version; go version -m "$ARDE_SWAG"; python --version; uv --version; '
        'test -x "$ARDE_GOINSPECT" && echo goinspect ok; '
        'for tool in ps lsof git; do command -v "$tool"; done'
    )
    listed = project.shell(script)

    assert listed.exit_code == 0, listed.stderr
    assert "go version go1.27.1 linux/" in listed.stdout
    assert "mod\tgithub.com/swaggo/swag/v2\tv2.0.0-rc6\t" in listed.stdout
    assert "Python 3.12." in listed.stdout
    assert "uv 0.9.27" in listed.stdout
    assert "goinspect ok" in listed.stdout
    assert [Path(line).name for line in listed.stdout.splitlines()[-3:]] == ["ps", "lsof", "git"]


# The key


def test_api_key_comes_from_env_file_only(project: Project) -> None:
    # Past the key check, a path that does not exist is the next thing refused.
    with_key = project.arde("--repo", "/nowhere", "--json")
    project.write_env_file(["# no key here"])
    without_key = project.arde("--repo", "/nowhere", "--json")

    assert "There is no folder at /nowhere" in with_key.report.message
    assert "ANTHROPIC_API_KEY is not set" in without_key.report.message


def test_no_layer_or_history_of_either_image_holds_a_key_from_the_build_folder(
    docker: None, tmp_path: Path
) -> None:
    planted = "sk-test-planted-in-the-build-folder-" + secrets.token_hex(8)
    context = _a_copy_of_the_checkout(tmp_path / "context")
    for name in (".env", ".env.local"):
        (context / name).write_text(f"ANTHROPIC_API_KEY={planted}\n", encoding="utf-8")

    for target in ("runtime", "dev"):
        tag = _test_tag(f"planted-{target}")
        _build(context, target=target, tag=tag)
        assert not _image_holds(tag, planted.encode()), f"the {target} image holds the key"


# Only the files git would list, so the real .env, which git ignores, is never read or copied.
def _a_copy_of_the_checkout(dest: Path) -> Path:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    for name in filter(None, listed.split("\0")):
        source = REPO_ROOT / name
        if source.is_file():
            (dest / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest / name)
    return dest


def _image_holds(tag: str, secret: bytes) -> bool:
    described = subprocess.run(
        ["docker", "image", "inspect", tag], capture_output=True, check=True
    ).stdout
    history = subprocess.run(
        ["docker", "history", "--no-trunc", tag], capture_output=True, check=True
    ).stdout
    if secret in described or secret in history:
        return True
    saved = subprocess.Popen(["docker", "save", tag], stdout=subprocess.PIPE)
    assert saved.stdout is not None
    try:
        with tarfile.open(fileobj=saved.stdout, mode="r|") as archive:
            return any(_member_holds(archive, member, secret) for member in archive)
    finally:
        saved.stdout.close()
        saved.wait()


# A layer may be compressed, and the key could straddle two chunks.
def _member_holds(archive: tarfile.TarFile, member: tarfile.TarInfo, secret: bytes) -> bool:
    extracted = archive.extractfile(member)
    if extracted is None:
        return False
    stream = cast(io.BufferedReader, extracted)
    source = gzip.GzipFile(fileobj=stream) if stream.peek(2)[:2] == b"\x1f\x8b" else stream
    tail = b""
    while chunk := source.read(1 << 20):
        if secret in tail + chunk:
            return True
        tail = chunk[-len(secret) :]
    return False


# The volume


def test_workspace_survives_between_runs(project: Project) -> None:
    project.shell("echo kept > /data/marker")

    kept = project.shell("cat /data/marker")

    assert (kept.exit_code, kept.stdout) == (0, "kept\n"), kept.stderr


# The home folder holds what git and go read for every repo, such as ~/.gitconfig, so it must not
# outlive a run: one repo's code could otherwise change how the next repo is cloned.
def test_home_does_not_survive_between_runs(project: Project) -> None:
    written = project.shell('echo "[user]" > "$HOME/.gitconfig" && cat "$HOME/.gitconfig"')

    looked = project.shell('test -e "$HOME/.gitconfig" && echo kept || echo gone')

    assert written.stdout == "[user]\n", written.stderr
    assert looked.stdout == "gone\n", looked.stderr


# Memory


# Swap counts too, or a runaway child would swap before the limit stopped it.
def test_the_container_and_every_child_share_a_memory_limit_of_4_gib_and_no_swap(
    project: Project,
) -> None:
    limits = project.shell("cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.swap.max")

    assert limits.stdout.split() == [str(4 * 2**30), "0"], limits.stderr


# Go refuses a constant string past 2 GB, but keeps every constant it computes, so twelve distinct
# 64 MiB ones take the compiler past the smaller limit set here. With the shipped 4 GiB limit,
# six of 1 GiB did the same in 2 minutes; the kernel killed the compiler, and ARDE said so.
BOMB_LIMIT = "512m"


def a_repo_whose_build_eats_memory(folder: Path) -> Path:
    doubled = [f"\ts{n} = s{n - 1} + s{n - 1}" for n in range(1, 23)]
    distinct = [f'\tt{n} = s22 + "{n}"' for n in range(12)]
    lengths = ", ".join(f"len(t{n})" for n in range(12))
    source = [
        "package main",
        "",
        "const (",
        '\ts0 = "0123456789abcdef"',
        *doubled,
        *distinct,
        ")",
        "",
        f"func main() {{ println({lengths}) }}",
        "",
    ]
    folder.mkdir()
    (folder / "go.mod").write_text("module example.com/bomb\n\ngo 1.22\n", encoding="utf-8")
    (folder / "main.go").write_text("\n".join(source), encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=folder, check=True)
    subprocess.run(["git", "add", "--all"], cwd=folder, check=True)
    commit = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "b"]
    subprocess.run(commit, cwd=folder, check=True)
    return folder


def test_a_build_that_eats_memory_is_killed_and_arde_says_so(
    project: Project, tmp_path: Path
) -> None:
    repo = a_repo_whose_build_eats_memory(tmp_path / "bomb")
    project.write_override(
        "services:\n  arde:\n    network_mode: none\n"
        f"    mem_limit: {BOMB_LIMIT}\n    memswap_limit: {BOMB_LIMIT}\n"
    )

    ran = project.arde("--repo", "/repo", "--json", REPO_PATH=str(repo))

    assert ran.exit_code == ExitCode.CHECKS_FAILED, ran.stderr[-4000:]
    assert "does not build" in ran.report.message
    assert "compile: signal: killed" in ran.report.message


# What the container does not isolate


# A known limit, kept as a test so that it stays in sight: the container is no boundary between
# ARDE and the repo's code. No child gets an ANTHROPIC_ variable, but every child runs as ARDE's own
# user, so a repo's test can read the key from the environment the init process started with, as
# it could from ARDE's own. The fix is to run the repo's code as another user (DESIGN.md, one more
# week). When this test fails, the limit has moved, and docs/limitations.md must say so.
READS_THE_KEY_OF_THE_INIT_PROCESS = """package main

import (
	"os"
	"strings"
	"testing"
)

func TestTheKeyOfTheInitProcess(t *testing.T) {
	environ, err := os.ReadFile("/proc/1/environ")
	if err != nil {
		t.Fatalf("cannot read /proc/1/environ: %v", err)
	}
	for _, entry := range strings.Split(string(environ), "\\x00") {
		if strings.HasPrefix(entry, "ANTHROPIC_API_KEY=sk-") {
			t.Fatal("ANTHROPIC_API_KEY is readable in /proc/1/environ")
		}
	}
}
"""


def test_a_repos_test_can_read_the_api_key_from_the_init_process(
    project: Project, tmp_path: Path
) -> None:
    repo = a_repo_of(SHOP, tmp_path / "shop")
    (repo / "key_test.go").write_text(READS_THE_KEY_OF_THE_INIT_PROCESS, encoding="utf-8")
    commit_all(repo, "read the key")
    project.write_override("services:\n  arde:\n    network_mode: none\n")

    ran = project.arde("--repo", "/repo", "--json", REPO_PATH=str(repo))

    assert ran.exit_code == ExitCode.CHECKS_FAILED, ran.stderr[-4000:]
    assert "ANTHROPIC_API_KEY is readable in /proc/1/environ" in ran.report.message


# Whole runs


def test_a_scripted_first_run_offline_on_an_empty_volume_succeeds_and_commits(
    project: Project, tmp_path: Path
) -> None:
    volume = _a_repo_owned_by_another_user(project, tmp_path)
    project.write_override(
        f"services:\n  arde:\n    network_mode: none\n    volumes:\n"
        f"      - {volume}:/repo:ro\n"
        f"volumes:\n  {volume}: {{}}\n"
    )

    ran = project.arde(
        f"{TESTS_IN_CONTAINER}/scripted_first_run.py",
        "--repo",
        "file:///repo",
        "--json",
        entrypoint="python",
    )

    assert ran.exit_code == ExitCode.OK, ran.stderr[-4000:]
    report = ran.report
    assert (report.status, report.findings) == ("success", ())
    assert report.branch is not None and report.commit is not None
    committed = project.arde(
        "-C",
        report.result_dir,
        "show",
        "--name-only",
        "--format=",
        report.branch,
        entrypoint="git",
    )
    assert {"docs/swagger.json", "docs/api.md"} <= set(committed.stdout.split()), committed.stderr
    workspace = Path(report.run_dir).parents[1]
    kept = project.shell(f"ls /data; test -d {workspace}/cache/gocache && echo own cache")
    assert kept.stdout.split() == ["workspaces", "own", "cache"], kept.stderr


# On a Linux host the mounted files keep the host user's id, which can differ from ARDE_UID, and git
# refuses a repo another user owns. Docker Desktop shows a mount as owned by whoever reads it, so a
# volume filled by another user stands in for the Linux host here.
def _a_repo_owned_by_another_user(project: Project, tmp_path: Path) -> str:
    name = "foreign-repo"
    repo = a_repo_of(SHOP, tmp_path / "shop")
    project.write_override(
        f"services:\n  arde:\n    volumes:\n      - {name}:/foreign\n      - {repo}:/shop:ro\n"
        f"volumes:\n  {name}: {{}}\n"
    )
    filled = project.compose(
        "run",
        "--rm",
        "--no-TTY",
        "--user",
        "0:0",
        "--entrypoint",
        "sh",
        "arde",
        "-c",
        "cp -a /shop/. /foreign/ && chown -R 4242:4242 /foreign",
    )
    assert filled.exit_code == 0, filled.stderr
    return name


def test_docker_stop_leaves_no_service(project: Project, tmp_path: Path) -> None:
    repo = a_repo_of(SHOP, tmp_path / "shop")
    container = f"{project.name}-stopped"
    command = project.command(
        *project.run_options("python", name=container),
        "arde",
        f"{TESTS_IN_CONTAINER}/scripted_arde.py",
        "/tmp/service.json",
        "--repo",
        "/repo",
    )
    process = subprocess.Popen(
        command,
        env=project.environment(REPO_PATH=str(repo)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        service = _wait_for_the_service(container, process)
        listening = _in_container(
            container, "lsof", "-nP", f"-iTCP:{service['port']}", "-sTCP:LISTEN"
        )
        assert listening.returncode == 0, listening.stderr
        stopped_at = time.monotonic()
        subprocess.run(["docker", "stop", "--time", str(STOP_GRACE_S), container], check=True)
        _, err = process.communicate(timeout=60)
        took = time.monotonic() - stopped_at
    finally:
        if process.poll() is None:
            process.kill()

    assert process.returncode == 130, err[-4000:]
    assert took < STOP_GRACE_S
    assert "interrupted" in err
    last = project.shell("tail -n 1 /data/workspaces/*/runs/*/trace/trace.jsonl")
    assert json.loads(last.stdout)["status"] == "interrupted", last.stderr


def _wait_for_the_service(container: str, process: subprocess.Popen[str]) -> dict[str, object]:
    deadline = time.monotonic() + SERVICE_START_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"arde ended before its service ran: {process.communicate()}")
        seen = _in_container(container, "cat", "/tmp/service.json")
        if seen.returncode == 0 and seen.stdout:
            value: dict[str, object] = json.loads(seen.stdout)
            return value
        time.sleep(0.5)
    raise AssertionError("arde's service never started")


def _in_container(container: str, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", container, *argv], capture_output=True, text=True, timeout=60
    )
