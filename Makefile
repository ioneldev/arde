.PHONY: check integration eval preflight image run runs extract

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src
	uv run pytest -m "not integration and not eval" -q

# gofmt -l prints the files it would change and exits 0, so the target tests its output. It covers
# the fixture services too, which the Go adapter tests read as real repos.
integration:
	unformatted="$$(gofmt -l tools/goinspect tests/fixtures)" && test -z "$$unformatted" || { echo "gofmt would change: $$unformatted (run gofmt -w on them)"; exit 1; }
	cd tools/goinspect && go vet ./...
	cd tools/goinspect && go test ./...
	uv run pytest -m integration -q

# --env-file hands .env to pytest alone, so the key never enters the shell's environment.
# -rP prints what passing tests printed, the spend and the trace folder, through the key guard.
eval:
	uv run --env-file .env pytest -m eval -q -rP

# The Docker path. Compose reads .env, for the key and REPO_PATH. Each target first checks what it
# needs, and the check only asks whether .env sets the key: it never reads the key out.
#   make preflight                    check Docker, Compose, the daemon, .env and the key
#   make image                        build the image, after any change to ARDE
#   make run                          document the local repo at REPO_PATH in .env
#   make run REPO=<folder>            document that local folder instead
#   make run REPO=<url> [BRANCH=b]    clone a remote repo and document it
#   make runs                         list the runs in the arde-home volume
#   make extract [RUN=<id>]           copy a run out of the volume, the latest by default
# ARGS passes more options to arde, as in ARGS="--skip-judge --max-usd 3".
COMPOSE_RUN = docker compose run --rm
# Listing and exporting need no key, so it is blanked, and a tar on stdout needs no terminal.
ARDE_RUNS = $(COMPOSE_RUN) -T -e ANTHROPIC_API_KEY= --entrypoint arde-runs arde
EXTRACTED = runs
# zsh leaves the ~ in REPO=~/x as typed, so make expands it.
REPO_GIVEN = $(patsubst ~%,$(HOME)%,$(REPO))
PREFLIGHT = sh scripts/preflight.sh

preflight:
	@$(PREFLIGHT) docker key
	@echo "preflight: Docker, Compose, the daemon, .env and the API key are ready."

image:
	@$(PREFLIGHT) docker
	docker compose build

# A command after the service name replaces the image's default, so a local run names /repo.
run:
	@$(PREFLIGHT) docker key
ifeq ($(strip $(REPO)),)
	$(COMPOSE_RUN) arde --repo /repo $(ARGS)
else
	@if [ -d "$(REPO_GIVEN)" ]; then \
		echo "Documenting the local repo $(abspath $(REPO_GIVEN))"; \
		REPO_PATH="$(abspath $(REPO_GIVEN))" $(COMPOSE_RUN) arde --repo /repo $(ARGS); \
	else \
		$(COMPOSE_RUN) arde --repo "$(REPO)" $(if $(BRANCH),--branch "$(BRANCH)") $(ARGS); \
	fi
endif

runs:
	@$(PREFLIGHT) docker
	@$(ARDE_RUNS) list

# Into runs/<run id>/: the report, the trace, the service logs, the changed files under changed/
# and changes.patch. Written to a file first, so a refused export leaves no half archive behind.
extract:
	@$(PREFLIGHT) docker
	@mkdir -p $(EXTRACTED)
	@$(ARDE_RUNS) export $(RUN) > $(EXTRACTED)/.export.tar || { rm -f $(EXTRACTED)/.export.tar; exit 1; }
	@tar -xf $(EXTRACTED)/.export.tar -C $(EXTRACTED) && rm $(EXTRACTED)/.export.tar
	@echo "Extracted into $(EXTRACTED)/."
