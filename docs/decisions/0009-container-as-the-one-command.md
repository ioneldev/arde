# 0009: The container is the one command

Status: approved 2026-11-01.

**Decision.** `docker compose run --rm arde` is the way to run the tool. The image holds pinned Go, Python, uv, `swag` and git, so the host needs only Docker, an API key and network access.

**Why.** Anyone can reproduce a run with nothing else installed. Measured on Docker for Mac (arm64): the image built in 33 seconds, git, build, vet, test and the live service worked on a mounted repo, memory survived between runs in a named volume, and written files came back owned by the host user.

**Cost.** About 980 MB, and workarounds for line endings and commit identity. On Linux `ARDE_UID` and `ARDE_GID` must match the host user. The repo's code shares a container with the API key, so repos must be trusted (see [limitations](../limitations.md)).
