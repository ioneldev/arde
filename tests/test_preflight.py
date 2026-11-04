"""What a Docker make target checks before it starts anything: each missing tool or setting is
named with its fix.

The script runs with a stand-in docker on PATH, so no test needs Docker, and with an env file of
the test's own, so no test reads the real .env.
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "preflight.sh"
KEY = "sk-ant-test-0123456789-not-a-real-key"
A_SET_KEY = f"REPO_PATH=\nANTHROPIC_API_KEY={KEY}\n"


def a_docker(bin_dir: Path, *, compose: bool, daemon: bool) -> None:
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f"  compose) exit {0 if compose else 1} ;;\n"
        f"  info) exit {0 if daemon else 1} ;;\n"
        "esac\n"
        "exit 0\n"
    )
    docker.chmod(0o755)


def preflight(
    tmp_path: Path,
    *checks: str,
    env_text: str | None = A_SET_KEY,
    docker: bool = True,
    compose: bool = True,
    daemon: bool = True,
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    if docker:
        a_docker(bin_dir, compose=compose, daemon=daemon)
    env_file = tmp_path / ".env"
    if env_text is not None:
        env_file.write_text(env_text)
    return subprocess.run(
        ["/bin/sh", str(SCRIPT), *checks],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "ARDE_ENV_FILE": str(env_file)},
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_ready_machine_passes_quietly(tmp_path: Path) -> None:
    done = preflight(tmp_path, "docker", "key")

    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


@pytest.mark.parametrize(
    ("setup", "problem", "fix"),
    [
        ({"docker": False}, "docker is not installed", "Install Docker Desktop"),
        ({"compose": False}, "docker compose is not available", "Compose v2 plugin"),
        ({"daemon": False}, "Docker is not running", "Start Docker"),
        ({"env_text": None}, ".env is missing", "cp .env.example .env"),
    ],
)
def test_each_missing_part_of_docker_is_named_with_its_fix(
    tmp_path: Path, setup: dict[str, object], problem: str, fix: str
) -> None:
    done = preflight(tmp_path, "docker", **setup)  # type: ignore[arg-type]

    first, second = done.stderr.splitlines()
    assert done.returncode == 1
    assert first.startswith("preflight: ") and problem in first
    assert second.startswith("  next: ") and fix in second


@pytest.mark.parametrize(
    "env_text",
    [
        "",
        "ANTHROPIC_API_KEY=\n",
        "ANTHROPIC_API_KEY= \n",
        'ANTHROPIC_API_KEY=""\n',
        "ANTHROPIC_API_KEY=''\n",
        "ANTHROPIC_API_KEY= # the key goes here\n",
        f"# ANTHROPIC_API_KEY={KEY}\n",
        f"OTHER_ANTHROPIC_API_KEY={KEY}\n",
    ],
)
def test_a_key_that_is_not_set_stops_the_run(tmp_path: Path, env_text: str) -> None:
    done = preflight(tmp_path, "key", env_text=env_text)

    assert done.returncode == 1
    assert "sets no ANTHROPIC_API_KEY" in done.stderr
    assert "ANTHROPIC_API_KEY=<your key>" in done.stderr


@pytest.mark.parametrize(
    "env_text",
    [
        f"ANTHROPIC_API_KEY={KEY}\n",
        f'ANTHROPIC_API_KEY="{KEY}"\n',
        f"export ANTHROPIC_API_KEY={KEY}\n",
        f"  ANTHROPIC_API_KEY={KEY}",
    ],
)
def test_a_key_that_is_set_passes(tmp_path: Path, env_text: str) -> None:
    assert preflight(tmp_path, "key", env_text=env_text).returncode == 0


def test_the_key_never_reaches_the_output(tmp_path: Path) -> None:
    passed = preflight(tmp_path, "docker", "key")
    failed = preflight(tmp_path, "docker", "key", daemon=False)

    for done in (passed, failed):
        assert KEY not in done.stdout + done.stderr
        assert KEY[:16] not in done.stdout + done.stderr


def test_listing_and_extracting_need_no_key(tmp_path: Path) -> None:
    assert preflight(tmp_path, "docker", env_text="ANTHROPIC_API_KEY=\n").returncode == 0


def test_a_check_it_does_not_know_is_a_mistake(tmp_path: Path) -> None:
    done = preflight(tmp_path, "tools")

    assert done.returncode == 2
    assert "preflight: no check named tools" in done.stderr


@pytest.mark.parametrize(
    ("target", "checks"),
    [("image", "docker"), ("run", "docker key"), ("runs", "docker"), ("extract", "docker")],
)
def test_each_docker_target_runs_its_checks_first(target: str, checks: str) -> None:
    planned = subprocess.run(
        ["make", "--no-print-directory", "-n", target],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert planned.stdout.splitlines()[0] == f"sh scripts/preflight.sh {checks}"
