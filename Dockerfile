# syntax=docker/dockerfile:1
# ARDE in one image: Go, Python, uv, git and the two Go tools it runs, all pinned. compose.yaml
# builds the runtime stage. The dev stage adds the tests and their tools, to run make check and
# make integration on Linux.

# Go 1.27.1, which goinspect's go.mod needs. Both digests name the multi-platform index, so these
# lines build the same on arm64 and amd64.
FROM golang:1.27.1-bookworm@sha256:8d48e12ec56735e9358640898b9d9b9fcca110612ed8a5567438c0a1baa24e66 AS toolchain
COPY --from=ghcr.io/astral-sh/uv:0.9.27@sha256:143b40f4ab56a780f43377604702107b5a35f83a4453daf1e4be691358718a6a /uv /usr/local/bin/uv

# The base holds git and ps. The service runner's messages name lsof.
RUN apt-get update \
 && apt-get install --yes --no-install-recommends lsof \
 && rm -rf /var/lib/apt/lists/*

# Readable by any user, because the user is chosen at run time.
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
RUN uv python install 3.12 && chmod -R a+rX /opt/uv-python

# Built here, from the pinned source and modules ARDE would build them from, so a run needs no
# download and no build of its own. Each go.sum pins every module, so a changed one is refused.
COPY tools /app/tools
RUN --mount=type=cache,target=/root/.cache/go-build --mount=type=cache,target=/go/pkg/mod \
    cd /app/tools/goinspect && go build -buildvcs=false -o /usr/local/bin/goinspect . \
 && cd /app/tools/swag && go build -buildvcs=false -o /usr/local/bin/swag github.com/swaggo/swag/v2/cmd/swag

# A local repo mounted at /repo keeps its host owner, which on Linux can differ from ARDE_UID, and
# git refuses a repo another user owns, lest its settings run a command. You chose this repo, the
# mount is read-only, and ARDE reads a local repo with core.fsmonitor off, the setting measured to
# run one, so trusting it costs no more than a native run. A clone from file:///repo reads
# /repo/.git.
RUN git config --system --add safe.directory /repo \
 && git config --system --add safe.directory /repo/.git


FROM toolchain AS runtime
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# The dependencies first, so a change to ARDE's own code reuses this layer. The uv cache is a
# build cache, named on each line, so no layer keeps it and the home folder stays empty.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/uv-cache UV_CACHE_DIR=/uv-cache uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/uv-cache UV_CACHE_DIR=/uv-cache uv sync --frozen --no-dev

# /data is the named volume: ARDE's workspaces and Go's caches, kept between runs. The home folder
# is not in it, because git and go read settings from there for every repo, and one repo's code
# could otherwise change how the next one is cloned or built. 1777, as /tmp is, because the user
# is chosen at run time.
RUN mkdir -p /data /home/arde && chmod 1777 /data /home/arde
ENV PATH=/app/.venv/bin:$PATH \
    ARDE_HOME=/data \
    GOCACHE=/data/gocache \
    GOPATH=/data/gopath \
    HOME=/home/arde \
    ARDE_GOINSPECT=/usr/local/bin/goinspect \
    ARDE_SWAG=/usr/local/bin/swag \
    GOENV=off

USER 1000:1000
ENTRYPOINT ["arde"]
CMD ["--repo", "/repo"]


FROM runtime AS dev
USER root
COPY . /app
RUN --mount=type=cache,target=/uv-cache UV_CACHE_DIR=/uv-cache uv sync --frozen \
 && chmod -R a+rwX /app
USER 1000:1000
ENTRYPOINT []
CMD ["make", "check"]
