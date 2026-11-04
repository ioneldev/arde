#!/bin/sh
# What a Docker make target needs, checked before it starts anything, so a missing tool or key is
# named with its fix before compose builds or starts a container. Plain sh, because the Docker path
# must work on a machine that has only Docker and make.
#
#   sh scripts/preflight.sh docker   Docker, the Compose plugin, a running daemon, and .env, which
#                                    compose refuses to run without
#   sh scripts/preflight.sh key      .env sets ANTHROPIC_API_KEY to a value
#
# The key is never read out: grep -q prints nothing and the value is never held in a variable, so
# it cannot reach the terminal, the environment or a log. Compose hands it to the container.
set -u

# Tests point this at an env file of their own, so no test reads the real .env.
env_file="${ARDE_ENV_FILE:-.env}"

# A line that sets the key, bare, quoted or after export. A value that is empty, blank, only
# quotes or a comment does not count.
key_line='^[[:space:]]*(export[[:space:]]+)?ANTHROPIC_API_KEY=[[:space:]]*["'"'"']?[^"'"'"'[:space:]#]'

not_ready() {
    printf 'preflight: %s\n  next: %s\n' "$1" "$2" >&2
    exit 1
}

check_docker() {
    command -v docker >/dev/null 2>&1 ||
        not_ready "docker is not installed, or not on PATH." \
            "Install Docker Desktop, or Docker Engine with its Compose plugin, then run again."
    docker compose version >/dev/null 2>&1 ||
        not_ready "docker compose is not available." \
            "Install the Docker Compose v2 plugin, which runs as docker compose, then run again."
    docker info >/dev/null 2>&1 ||
        not_ready "Docker is not running, or this user cannot reach it." \
            "Start Docker Desktop or the Docker service, then run again. On Linux, the user must be in the docker group."
    check_env_file
}

check_env_file() {
    [ -f "$env_file" ] ||
        not_ready "$env_file is missing, and docker compose reads the API key from it." \
            "Run cp .env.example .env, put your Anthropic API key in it, then run again."
}

check_key() {
    check_env_file
    grep -Eq "$key_line" "$env_file" ||
        not_ready "$env_file sets no ANTHROPIC_API_KEY." \
            "Put your key in $env_file as ANTHROPIC_API_KEY=<your key>, then run again. Keys are made at https://console.anthropic.com."
}

for check in "$@"; do
    case "$check" in
        docker) check_docker ;;
        key) check_key ;;
        *)
            printf 'preflight: no check named %s.\n  next: Name docker, key or both.\n' "$check" >&2
            exit 2
            ;;
    esac
done
