.PHONY: check integration eval

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
